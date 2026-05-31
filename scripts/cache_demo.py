"""Milestone 2 DoD — semantic cache (a repeated/similar prompt is served without the model).

Drives the SemanticCachePlugin the same way the Runner does — before_model_callback (lookup) and
after_model_callback (store) — but with hand-built requests so the proof is deterministic and fast
(it exercises the Redis vector store + LM Studio embeddings, not the slow chat model):

  call 1  identical prompt   -> MISS  (before returns None; the model would run; we store the answer)
  call 2  identical prompt   -> HIT   (before returns the cached LlmResponse; the model is skipped)
  call 3  unrelated prompt   -> MISS  (different meaning; the model would run)

Requires Redis up (`make up`) and an embedding model loaded in LM Studio (LMSTUDIO_EMBED_MODEL).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402
from google.adk.models.llm_request import LlmRequest  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types  # noqa: E402

from control_plane.semantic_cache import build_semantic_cache_plugin  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

MODEL = "openai/" + os.environ.get("LMSTUDIO_MODEL", "google/gemma-4-e4b")


def _req(text: str) -> LlmRequest:
    return LlmRequest(
        model=MODEL, contents=[types.Content(role="user", parts=[types.Part(text=text)])]
    )


def _resp(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))


async def main() -> int:
    plugin = build_semantic_cache_plugin()
    if not plugin._enabled:
        print("Semantic cache is disabled (SEMANTIC_CACHE_ENABLED=0). Nothing to demo.")
        return 1

    # A unique tag avoids colliding with entries cached by earlier demo runs.
    tag = os.urandom(4).hex()
    prompt = f"[demo {tag}] What is the capital of France?"
    answer = "The capital of France is Paris."
    unrelated = f"[demo {tag}] Summarize the plot of Hamlet in one sentence."

    print("== call 1: first time we see the prompt ==")
    hit1 = await plugin.before_model_callback(callback_context=None, llm_request=_req(prompt))
    print(f"  before_model -> {'HIT' if hit1 else 'MISS (model would run)'}")
    await plugin.after_model_callback(callback_context=None, llm_response=_resp(answer))
    print("  stored the model's answer in Redis")

    print("\n== call 2: identical prompt repeated ==")
    hit2 = await plugin.before_model_callback(callback_context=None, llm_request=_req(prompt))
    served = hit2.content.parts[0].text if hit2 else None
    print(f"  before_model -> {'HIT, model SKIPPED' if hit2 else 'MISS'}; served: {served!r}")

    print("\n== call 3: unrelated prompt ==")
    hit3 = await plugin.before_model_callback(callback_context=None, llm_request=_req(unrelated))
    print(f"  before_model -> {'HIT' if hit3 else 'MISS (model would run)'}")

    await plugin._store.close()

    ok = (hit1 is None) and (hit2 is not None and served == answer) and (hit3 is None)
    print(f"\n  hits={plugin.hits} misses={plugin.misses}")
    print(f"Semantic cache (repeat served from cache, model skipped): {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
