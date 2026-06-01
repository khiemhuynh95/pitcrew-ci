"""Unit tests for the semantic cache plugin's control flow — no Redis, no model (fakes only).

Covers the behaviour that matters for correctness and the fail-open guarantee: a miss stores and a
repeat hits; a near-miss below threshold does not serve; embeddings/Redis being unavailable falls
through to a normal model call; and partial/error responses are never cached.
"""

from __future__ import annotations

import asyncio

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from control_plane.semantic_cache import RedisVectorCache, SemanticCachePlugin


def _req(text: str) -> LlmRequest:
    return LlmRequest(
        model="openai/m", contents=[types.Content(role="user", parts=[types.Part(text=text)])]
    )


def _resp(text: str, **kw) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]), **kw)


class FakeEmbedder:
    """Returns a fixed vector per text, or None to simulate the embedding endpoint being down."""

    def __init__(self, available: bool = True) -> None:
        self.available = available

    async def embed(self, text: str):
        if not self.available:
            return None
        return [float(len(text)), 1.0, 2.0]


class FakeStore:
    """In-memory stand-in for RedisVectorCache with a controllable nearest-neighbour similarity."""

    def __init__(self, similarity: float = 1.0) -> None:
        self.similarity = similarity
        self.entries: list[tuple[str, str, str]] = []  # (key, response_json, model_tag)

    async def lookup(self, embedding, model_tag):
        for _key, response_json, tag in reversed(self.entries):
            if tag == model_tag:
                return response_json, self.similarity
        return None

    async def store(self, key, embedding, text, response_json, model_tag):
        self.entries.append((key, response_json, model_tag))

    async def close(self):
        pass


def _plugin(embedder, store, threshold=0.97, enabled=True):
    return SemanticCachePlugin(embedder, store, threshold, enabled=enabled)


def test_miss_then_hit_skips_model():
    async def run():
        store = FakeStore(similarity=1.0)
        p = _plugin(FakeEmbedder(), store)
        # First call: miss (nothing stored yet).
        assert (
            await p.before_model_callback(callback_context=None, llm_request=_req("hello")) is None
        )
        await p.after_model_callback(callback_context=None, llm_response=_resp("world"))
        # Repeat: hit, served from cache.
        hit = await p.before_model_callback(callback_context=None, llm_request=_req("hello"))
        assert hit is not None
        assert hit.content.parts[0].text == "world"
        assert p.hits == 1 and p.misses == 1

    asyncio.run(run())


def test_below_threshold_is_a_miss():
    async def run():
        store = FakeStore(similarity=0.90)  # a near-neighbour, but under the 0.97 threshold
        store.entries.append(("k", _resp("stale").model_dump_json(), "openai_m"))
        p = _plugin(FakeEmbedder(), store)
        assert (
            await p.before_model_callback(callback_context=None, llm_request=_req("hello")) is None
        )

    asyncio.run(run())


def test_fail_open_when_embeddings_unavailable():
    async def run():
        store = FakeStore(similarity=1.0)
        p = _plugin(FakeEmbedder(available=False), store)
        # No embedding -> never even consults the store; just a normal (model) call.
        assert (
            await p.before_model_callback(callback_context=None, llm_request=_req("hello")) is None
        )
        await p.after_model_callback(callback_context=None, llm_response=_resp("world"))
        assert store.entries == []

    asyncio.run(run())


def test_disabled_plugin_is_inert():
    async def run():
        store = FakeStore(similarity=1.0)
        p = _plugin(FakeEmbedder(), store, enabled=False)
        assert await p.before_model_callback(callback_context=None, llm_request=_req("hi")) is None
        await p.after_model_callback(callback_context=None, llm_response=_resp("x"))
        assert store.entries == []

    asyncio.run(run())


def test_partial_and_error_responses_not_cached():
    async def run():
        store = FakeStore(similarity=1.0)
        p = _plugin(FakeEmbedder(), store)
        # Miss sets the pending key...
        await p.before_model_callback(callback_context=None, llm_request=_req("hello"))
        # ...but a streaming partial must not be stored.
        await p.after_model_callback(
            callback_context=None, llm_response=_resp("chunk", partial=True)
        )
        assert store.entries == []

        await p.before_model_callback(callback_context=None, llm_request=_req("hello"))
        await p.after_model_callback(
            callback_context=None, llm_response=LlmResponse(error_code="X")
        )
        assert store.entries == []

    asyncio.run(run())


def test_empty_prompt_is_skipped():
    async def run():
        store = FakeStore(similarity=1.0)
        p = _plugin(FakeEmbedder(), store)
        empty = LlmRequest(model="openai/m", contents=[])
        assert await p.before_model_callback(callback_context=None, llm_request=empty) is None

    asyncio.run(run())


def test_redis_vector_cache_fails_open_without_server():
    """A real store pointed at a dead Redis must not raise — lookup/store quietly no-op."""

    async def run():
        store = RedisVectorCache("redis://127.0.0.1:6390/0", ttl_seconds=60)  # nothing listening
        assert await store.lookup([0.1, 0.2, 0.3], "openai_m") is None
        await store.store("k", [0.1, 0.2, 0.3], "t", "{}", "openai_m")  # no exception
        await store.close()

    asyncio.run(run())
