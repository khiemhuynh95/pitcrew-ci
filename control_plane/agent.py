"""Control-plane agent definition.

The settled orchestration backbone (CLAUDE.md invariant #1): a `LoopAgent` wrapping a single
`LlmAgent` worker with native sandbox tools. No sub_agents/routing — specialization will come
from Skills (Milestone 1.5), at which point THIS FILE BECOMES CONSTANT (invariant #6): adding a
capability is a SKILL.md folder or an MCP registry block, never an edit here.

The worker reasons in the control plane (where the model + guardrails live); its hands
(run_shell/write_file/read_file/copy_out) execute in the disposable workload container via the
exec service. The loop terminates on `finish()` (escalate) or `max_iterations`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import LlmAgent, LoopAgent
from google.adk.models.lite_llm import LiteLlm

from control_plane.guard import before_tool_guard
from control_plane.sandbox import copy_out, finish, read_file, run_shell, write_file

# Load .env from the repo root (one level up from control_plane/).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_API_BASE = os.environ.get("LMSTUDIO_API_BASE", "http://127.0.0.1:1234/v1")
_MODEL = os.environ.get("LMSTUDIO_MODEL", "google/gemma-4-e4b")
_MAX_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS", "15"))

# LM Studio exposes an OpenAI-compatible API; route LiteLLM through its `openai/` provider.
#
# NOTE (CLAUDE.md invariant #4): localhost/127.0.0.1 is correct ONLY while the control plane runs
# on the host (Milestones 0-1). Once it is containerized (M3+), LMSTUDIO_API_BASE MUST become the
# LM Studio host's LAN IP — inside a container localhost is the container itself, not the model.
_model = LiteLlm(
    model=f"openai/{_MODEL}",
    api_base=_API_BASE,
    api_key="lm-studio",  # LM Studio ignores the key, but the OpenAI client requires one.
)

_worker = LlmAgent(
    name="pitcrew_worker",
    model=_model,
    instruction=(
        "You are PitCrew, an autonomous worker operating inside a disposable sandbox (a Linux "
        "container). You are given a goal and must accomplish it yourself, step by step, using "
        "your tools.\n\n"
        "Tools: run_shell (run a command in the sandbox), write_file / read_file (text files "
        "under the sandbox /work dir), copy_out (save a sandbox file as a durable artifact before "
        "you finish — anything not copied out is lost), and finish (call when done or stuck).\n\n"
        "Rules: work in small concrete steps and inspect results before moving on. Save any final "
        "output with copy_out. When the goal is achieved (or you genuinely cannot proceed), call "
        "finish with a short summary of what you did and which artifacts you saved. Do not ask the "
        "user questions — you are autonomous."
    ),
    tools=[run_shell, write_file, read_file, copy_out, finish],
    before_tool_callback=before_tool_guard,
)

# LoopAgent re-invokes the worker until it calls finish() (escalate) or max_iterations is hit.
root_agent = LoopAgent(
    name="pitcrew",
    sub_agents=[_worker],
    max_iterations=_MAX_ITERATIONS,
)
