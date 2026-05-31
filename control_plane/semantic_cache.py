"""Semantic response cache (Milestone 2) — skip the model on a repeated/similar prompt.

The mechanism is ADK-native (HANDOFF §E / plan step 3): a global **Plugin** whose
`before_model_callback` returns a cached `LlmResponse` to short-circuit the model call (ADK's
documented "intervene" pattern), and whose `after_model_callback` stores each fresh response. What
*we* build is only the store + lookup: prompts are embedded via an LM Studio embedding model and
matched by cosine similarity in **Redis 8**'s built-in vector index (RediSearch). A hit skips
LM Studio entirely — direct latency + load relief for the slowest component.

**Fail-open by construction.** Embeddings unavailable, Redis down, the search module missing, a
dimension change — every failure path returns `None` (cache miss / no store), so the agent runs
exactly as if the cache weren't there. The cache may never break the happy path; it only ever
*saves* a model call. Disable entirely with `SEMANTIC_CACHE_ENABLED=0`.

Why a Plugin and not an agent callback: the cache is cross-cutting (every model call, every agent)
and must not be pre-empted oddly — a Plugin hook wraps the Runner, which is exactly the altitude
the governance Plugin (M3) also occupies. (Trap noted in HANDOFF: a Plugin hook returning non-None
SKIPS the agent-level callback — intended here, since a hit must short-circuit.)
"""

from __future__ import annotations

import hashlib
import os
import struct
import time
from contextvars import ContextVar

import httpx
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin

# Carries the (key, embedding, model_tag) computed on a MISS in before_model_callback through to
# after_model_callback, which stores the fresh response. A ContextVar (not an instance dict) keeps
# this correct under concurrency: each async task sees its own value, and before/after for one
# model call run sequentially in the same task.
_PENDING: ContextVar[dict | None] = ContextVar("pitcrew_semcache_pending", default=None)

_INDEX = "idx:pitcrew_semcache"
_PREFIX = "pitcrew_semcache:"


def _f32_bytes(vec: list[float]) -> bytes:
    """Pack a float vector as the little-endian FLOAT32 blob RediSearch expects."""
    return struct.pack(f"<{len(vec)}f", *vec)


def _sanitize_tag(value: str) -> str:
    """RediSearch TAG-safe form of a model id (slashes/dots/hyphens would need escaping)."""
    return "".join(c if c.isalnum() else "_" for c in value)


def _canonical_prompt(req: LlmRequest) -> str:
    """A stable text rendering of the request to embed + hash.

    Includes the system instruction and tool names, not just the user turns, so a cache entry can
    never bleed across two agents that send similar user text under different instructions/tools.
    """
    parts: list[str] = []
    cfg = getattr(req, "config", None)
    sys_inst = getattr(cfg, "system_instruction", None) if cfg else None
    if sys_inst:
        parts.append(f"SYS:{sys_inst}")
    for content in req.contents or []:
        texts = [p.text for p in (content.parts or []) if getattr(p, "text", None)]
        if texts:
            parts.append(f"{content.role}:{' '.join(texts)}")
    # Tool surface affects what the model may answer; fold the (sorted) tool names in.
    tools = sorted((req.tools_dict or {}).keys()) if getattr(req, "tools_dict", None) else []
    if tools:
        parts.append("TOOLS:" + ",".join(tools))
    return "\n".join(parts)


class EmbeddingClient:
    """Thin async client for LM Studio's OpenAI-compatible /embeddings endpoint. Fail-open."""

    def __init__(self, api_base: str, model: str, timeout: float = 30.0) -> None:
        self._url = api_base.rstrip("/") + "/embeddings"
        self._model = model
        self._timeout = timeout

    async def embed(self, text: str) -> list[float] | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                resp = await c.post(
                    self._url,
                    headers={"Authorization": "Bearer lm-studio"},
                    json={"model": self._model, "input": text},
                )
            if resp.status_code != 200:
                return None
            return resp.json()["data"][0]["embedding"]
        except Exception:  # noqa: BLE001 — cache must never raise into the agent (fail-open).
            return None


class RedisVectorCache:
    """Cosine-KNN response store over Redis 8's built-in vector index. Fail-open everywhere."""

    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        self._url = redis_url
        self._ttl = ttl_seconds
        self._client = None  # lazy: created on first use so import never needs a live Redis
        self._index_dim: int | None = None  # the dim the index was created with

    def _redis(self):
        if self._client is None:
            import redis.asyncio as aioredis  # local import keeps redis optional at import time

            # Pin RESP2: redis-py's FT.SEARCH result parser (`.search().docs`) only understands the
            # RESP2 array reply. Redis 8 + redis-py negotiate RESP3 by default, under which the
            # search reply is a map and `.docs` comes back empty (silent cache misses). This client
            # is dedicated to the cache, so forcing protocol 2 here is local and safe.
            self._client = aioredis.from_url(self._url, protocol=2)
        return self._client

    async def _ensure_index(self, dim: int) -> bool:
        """Create the vector index once (idempotent). Returns False if the engine can't (fail-open)."""
        if self._index_dim == dim:
            return True
        from redis.commands.search.field import NumericField, TagField, TextField, VectorField
        from redis.commands.search.index_definition import IndexDefinition, IndexType

        r = self._redis()
        schema = (
            VectorField(
                "embedding",
                "FLAT",
                {"TYPE": "FLOAT32", "DIM": dim, "DISTANCE_METRIC": "COSINE"},
            ),
            TextField("text"),
            TextField("response"),
            TagField("model"),
            NumericField("created"),
        )
        definition = IndexDefinition(prefix=[_PREFIX], index_type=IndexType.HASH)
        try:
            await r.ft(_INDEX).create_index(schema, definition=definition)
        except Exception as e:  # noqa: BLE001
            # "Index already exists" is success; anything else (no search module, etc.) = fail-open.
            if "Index already exists" not in str(e):
                return False
        self._index_dim = dim
        return True

    async def lookup(self, embedding: list[float], model_tag: str) -> tuple[str, float] | None:
        """Nearest cached response for the same model. Returns (response_json, similarity) or None."""
        if not await self._ensure_index(len(embedding)):
            return None
        from redis.commands.search.query import Query

        try:
            q = (
                Query(f"(@model:{{{model_tag}}})=>[KNN 1 @embedding $vec AS dist]")
                .sort_by("dist")
                .return_fields("response", "dist")
                .dialect(2)
            )
            res = (
                await self._redis()
                .ft(_INDEX)
                .search(q, query_params={"vec": _f32_bytes(embedding)})
            )
        except Exception:  # noqa: BLE001
            return None
        if not res.docs:
            return None
        doc = res.docs[0]
        # RediSearch COSINE returns distance = 1 - cosine_similarity.
        similarity = 1.0 - float(doc.dist)
        return doc.response, similarity

    async def store(
        self, key: str, embedding: list[float], text: str, response_json: str, model_tag: str
    ) -> None:
        if not await self._ensure_index(len(embedding)):
            return
        try:
            r = self._redis()
            name = _PREFIX + key
            await r.hset(
                name,
                mapping={
                    "embedding": _f32_bytes(embedding),
                    "text": text,
                    "response": response_json,
                    "model": model_tag,
                    "created": int(time.time()),
                },
            )
            if self._ttl > 0:
                await r.expire(name, self._ttl)
        except Exception:  # noqa: BLE001
            return

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:  # noqa: BLE001
                pass


class SemanticCachePlugin(BasePlugin):
    """Cache LM Studio responses by prompt meaning; serve repeats without calling the model."""

    def __init__(
        self,
        embedder: EmbeddingClient,
        store: RedisVectorCache,
        similarity_threshold: float,
        enabled: bool = True,
    ) -> None:
        super().__init__(name="semantic_cache")
        self._embedder = embedder
        self._store = store
        self._threshold = similarity_threshold
        self._enabled = enabled
        self.hits = 0
        self.misses = 0

    async def before_model_callback(
        self, *, callback_context, llm_request: LlmRequest
    ) -> LlmResponse | None:
        _PENDING.set(None)
        if not self._enabled:
            return None
        prompt = _canonical_prompt(llm_request)
        if not prompt.strip():
            return None
        embedding = await self._embedder.embed(prompt)
        if embedding is None:
            return None  # embeddings down -> fail-open, model is called normally
        model_tag = _sanitize_tag(getattr(llm_request, "model", "") or "default")

        hit = await self._store.lookup(embedding, model_tag)
        if hit is not None and hit[1] >= self._threshold:
            self.hits += 1
            try:
                return LlmResponse.model_validate_json(hit[0])
            except Exception:  # noqa: BLE001 — corrupt entry: treat as a miss, fall through.
                pass
        # Miss: remember what to store once the model answers.
        self.misses += 1
        key = hashlib.sha256(f"{model_tag}\n{prompt}".encode()).hexdigest()
        _PENDING.set({"key": key, "embedding": embedding, "text": prompt, "model": model_tag})
        return None

    async def after_model_callback(
        self, *, callback_context, llm_response: LlmResponse
    ) -> LlmResponse | None:
        pending = _PENDING.get()
        if pending is None or not self._enabled:
            return None
        _PENDING.set(None)
        # Only cache a complete, successful, content-bearing response — never a streaming partial
        # or an error frame.
        if getattr(llm_response, "partial", None):
            return None
        if llm_response.error_code or llm_response.content is None:
            return None
        await self._store.store(
            pending["key"],
            pending["embedding"],
            pending["text"],
            llm_response.model_dump_json(exclude_none=True),
            pending["model"],
        )
        return None


def build_semantic_cache_plugin() -> SemanticCachePlugin:
    """Construct the plugin from env (the single config surface for the cache)."""
    from control_plane.model import api_base

    enabled = os.environ.get("SEMANTIC_CACHE_ENABLED", "1") not in ("0", "false", "False", "")
    embedder = EmbeddingClient(
        api_base=api_base(),
        model=os.environ.get("LMSTUDIO_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5"),
    )
    store = RedisVectorCache(
        redis_url=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"),
        ttl_seconds=int(os.environ.get("SEMANTIC_CACHE_TTL_SECONDS", "86400")),
    )
    threshold = float(os.environ.get("SEMANTIC_CACHE_SIMILARITY", "0.97"))
    return SemanticCachePlugin(embedder, store, threshold, enabled=enabled)
