"""before_tool_callback guard (Milestone 1) — the first governance layer.

Bounds every tool call: a step budget, a wall-clock budget, a command denylist, and the global
kill-switch (CLAUDE.md invariants #7/#14, HANDOFF §5 AGENT_KILL_SWITCH). Returning a dict from a
before_tool_callback SHORT-CIRCUITS the tool (ADK uses the dict as the result); budget/kill-switch
breaches also set `escalate` to stop the LoopAgent cleanly.

In Milestone 3 this logic migrates into the global governance Plugin on the Runner; it lives as a
per-agent callback for now. The denylist is defense-in-depth — the real boundary is the workload
container itself (no host access, caps dropped). Config over code: budgets come from env.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

# Denied shell patterns (defense-in-depth on top of the sandbox). Plain tuple = the low-code
# surface the plan calls for.
_DENY_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p)
    for p in (
        r"\brm\s+-rf\s+/(?:\s|$)",  # rm -rf /
        r"\bmkfs\b",  # format a filesystem
        r"\bdd\b.*\bof=/dev/",  # overwrite a device
        r":\(\)\s*\{.*\};:",  # classic fork bomb
        r"\bshutdown\b|\breboot\b",  # halt the box
    )
)

_STEPS_KEY = "guard:steps"
_START_KEY = "guard:start_ts"


def before_tool_guard(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> Optional[dict]:
    state = tool_context.state

    # Global kill-switch — checked at the start of every agent action.
    if os.environ.get("AGENT_KILL_SWITCH", "0") == "1":
        tool_context.actions.escalate = True
        return {"error": "kill switch engaged (AGENT_KILL_SWITCH=1); halting."}

    max_steps = int(os.environ.get("AGENT_MAX_STEPS", "25"))
    max_seconds = int(os.environ.get("AGENT_MAX_SECONDS", "300"))

    now = time.time()
    if _START_KEY not in state:
        state[_START_KEY] = now

    # Wall-clock budget.
    elapsed = now - state[_START_KEY]
    if elapsed > max_seconds:
        tool_context.actions.escalate = True
        return {"error": f"time budget exceeded ({elapsed:.0f}s > {max_seconds}s); halting."}

    # Step budget (count this call).
    steps = int(state.get(_STEPS_KEY, 0)) + 1
    state[_STEPS_KEY] = steps
    if steps > max_steps:
        tool_context.actions.escalate = True
        return {"error": f"step budget exceeded ({steps} > {max_steps}); halting."}

    # Command denylist (run_shell only) — deny the one call, let the agent try another approach.
    if tool.name == "run_shell":
        command = str(args.get("command", ""))
        for pat in _DENY_PATTERNS:
            if pat.search(command):
                return {"error": f"command blocked by denylist (pattern: {pat.pattern})."}

    return None  # allow
