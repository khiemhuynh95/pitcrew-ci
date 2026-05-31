"""Shared LM Studio model construction (Milestone 2).

`agent.py` builds its worker model inline and stays CONSTANT (invariant #6). Milestone 2's runtime
wiring needs the SAME model for two new jobs — the context-compaction summarizer
(`LlmEventSummarizer`) and (read here for the api_base/key) — so the construction lives here once
and is reused, rather than editing the frozen agent definition or copy-pasting the LiteLlm block.

Same notes as agent.py apply: LM Studio is OpenAI-compatible (route via LiteLLM's `openai/`
provider); the api_base is localhost ONLY while the control plane runs on the host (M0-2) and MUST
become the LM Studio host's LAN IP once containerized (M3, invariant #4).
"""

from __future__ import annotations

import os

from google.adk.models.lite_llm import LiteLlm


def api_base() -> str:
    """The LM Studio OpenAI-compatible base URL (e.g. http://<ip>:1234/v1)."""
    return os.environ.get("LMSTUDIO_API_BASE", "http://127.0.0.1:1234/v1")


def build_model(model_id: str | None = None) -> LiteLlm:
    """A LiteLlm pointed at the LM Studio chat model (defaults to LMSTUDIO_MODEL)."""
    model = model_id or os.environ.get("LMSTUDIO_MODEL", "google/gemma-4-e4b")
    return LiteLlm(
        model=f"openai/{model}",
        api_base=api_base(),
        api_key="lm-studio",  # LM Studio ignores the key, but the OpenAI client requires one.
    )
