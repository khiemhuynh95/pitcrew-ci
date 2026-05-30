"""Control-plane agent definition.

INVARIANT (CLAUDE.md #6): this file is CONSTANT. Adding a capability never edits agent.py —
a skill is a SKILL.md folder (auto-discovered, Milestone 1.5), an integration is an MCP
registry block. At Milestone 0 it is the bare seed: a single LlmAgent over LM Studio, the
seed every team agent (QA, triage, coding, team-lead) is later grown from.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

# Load .env from the repo root (one level up from control_plane/).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_API_BASE = os.environ.get("LMSTUDIO_API_BASE", "http://127.0.0.1:1234/v1")
_MODEL = os.environ.get("LMSTUDIO_MODEL", "google/gemma-4-e4b")

# LM Studio exposes an OpenAI-compatible API; route LiteLLM through its `openai/` provider.
#
# NOTE (CLAUDE.md invariant #4): `localhost`/127.0.0.1 is correct ONLY while running on the
# host, as at Milestone 0. Once the control plane is containerized (Milestone 1+),
# LMSTUDIO_API_BASE MUST become the LM Studio host's LAN IP — inside a container `localhost`
# resolves to the container itself, not the model host. Keep it in config, never hardcode.
root_agent = LlmAgent(
    name="pitcrew",
    model=LiteLlm(
        model=f"openai/{_MODEL}",
        api_base=_API_BASE,
        api_key="lm-studio",  # LM Studio ignores the key, but the OpenAI client requires one.
    ),
    instruction=(
        "You are PitCrew, the conversational seed of a self-hosted, self-healing CI/CD agent "
        "team. At this milestone you have no tools yet — answer clearly and concisely, and say "
        "so plainly when a request would need a capability you do not yet have."
    ),
)
