"""Control-plane agent definition.

The settled orchestration backbone (CLAUDE.md invariant #1 / HANDOFF §1.1): the worker is a single
(non-deprecated) `LlmAgent`; the bounded outer loop is an ADK **dynamic workflow** — a plain-Python
`while step < budget and not finished: await ctx.run_node(worker)` driven from a `FunctionNode`,
wrapped in a `Workflow` so the graph engine gives checkpoint/resume for free. This REPLACES the
deprecated `LoopAgent` (removed in ADK 2.x per the build-plan update). No sub_agents/routing —
specialization will come from Skills (Milestone 1.5), at which point THIS FILE BECOMES CONSTANT
(invariant #6): adding a capability is a SKILL.md folder or an MCP registry block, never an edit here.

The worker reasons in the control plane (where the model + guardrails live); its hands
(run_shell/write_file/read_file/copy_out) execute in the disposable workload container via the
exec service. The loop terminates when an event escalates — `finish()` sets `escalate` on success,
and the `before_tool_guard` sets it on a budget/kill-switch breach — or when the step budget is hit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.models.lite_llm import LiteLlm
from google.adk.workflow import START, Workflow, node

from control_plane.guard import before_tool_guard
from control_plane.sandbox import copy_out, finish, read_file, run_shell, write_file

# Load .env from the repo root (one level up from control_plane/).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_API_BASE = os.environ.get("LMSTUDIO_API_BASE", "http://127.0.0.1:1234/v1")
_MODEL = os.environ.get("LMSTUDIO_MODEL", "google/gemma-4-e4b")
_MAX_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS", "15"))

# A nudge for re-invocations: a single_turn worker node does NOT carry prior-iteration history
# (the wrapper sets include_contents='none'), but the workload container's files persist, so tell
# it to re-inspect the sandbox rather than restart from scratch.
_CONTINUE = (
    "Continue working toward the original goal. Inspect the sandbox (run_shell / read_file) to see "
    "the progress you have already made, then take the next step. Call finish when the goal is met "
    "or you genuinely cannot proceed."
)

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

# The worker. Run as a workflow node it is single_turn by default (ADK sets mode='single_turn' and
# include_contents='none'): each `ctx.run_node` call is one self-contained worker turn whose own
# reason -> tool -> observe cycle runs to completion internally. Not a sub_agent, not routed.
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


@node(name="pitcrew_driver", rerun_on_resume=True)
async def _drive(ctx: Context, node_input: Any) -> dict:
    """The bounded outer loop (the LoopAgent replacement).

    Re-invokes the worker via `ctx.run_node` until an event escalates (`finish()` on success, or the
    guard on a budget/kill-switch breach — exactly the signal the old `LoopAgent` watched) or the
    iteration budget is spent. `rerun_on_resume=True` is required for `ctx.run_node`.
    """
    finished = False
    summary = ""
    step = 0
    # Scan only events produced after this point so we ignore the user goal the runner appended.
    cursor = len(ctx.session.events)

    while step < _MAX_ITERATIONS and not finished:
        worker_input = node_input if step == 0 else _CONTINUE
        await ctx.run_node(_worker, node_input=worker_input)
        step += 1

        events = ctx.session.events
        for event in events[cursor:]:
            if event.actions and getattr(event.actions, "escalate", None):
                finished = True
            for fr in event.get_function_responses():
                if fr.name == "finish":
                    summary = (fr.response or {}).get("summary", "") or summary
        cursor = len(events)

    return {"finished": finished, "steps": step, "summary": summary}


# The dynamic-workflow loop, on the graph engine from day one (HANDOFF trap: never LoopAgent). The
# single-node graph hands checkpoint/resume to the Workflow orchestrator; Milestone 6.5 graduates
# this to a multi-node static graph only if/when the worker's phases branch.
root_agent = Workflow(name="pitcrew", edges=[(START, _drive)])
