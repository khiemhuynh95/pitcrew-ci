"""Guardrail tests for before_tool_guard — prove it BLOCKS the bad case (HANDOFF §6)."""

from __future__ import annotations

import pytest

from control_plane.guard import before_tool_guard


class _Actions:
    def __init__(self) -> None:
        self.escalate = False


class _Ctx:
    def __init__(self) -> None:
        self.state: dict = {}
        self.actions = _Actions()


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_allows_benign_command():
    ctx = _Ctx()
    assert before_tool_guard(_Tool("run_shell"), {"command": "ls -la"}, ctx) is None
    assert ctx.actions.escalate is False


def test_denylist_blocks_rm_rf_root():
    ctx = _Ctx()
    result = before_tool_guard(_Tool("run_shell"), {"command": "rm -rf / "}, ctx)
    assert result is not None and "denylist" in result["error"]
    # A denied single command does NOT halt the loop — the agent may try another approach.
    assert ctx.actions.escalate is False


def test_kill_switch_halts(monkeypatch):
    monkeypatch.setenv("AGENT_KILL_SWITCH", "1")
    ctx = _Ctx()
    result = before_tool_guard(_Tool("run_shell"), {"command": "ls"}, ctx)
    assert result is not None and "kill switch" in result["error"]
    assert ctx.actions.escalate is True


def test_step_budget_escalates(monkeypatch):
    monkeypatch.setenv("AGENT_KILL_SWITCH", "0")
    monkeypatch.setenv("AGENT_MAX_STEPS", "2")
    ctx = _Ctx()
    tool = _Tool("run_shell")
    assert before_tool_guard(tool, {"command": "echo 1"}, ctx) is None
    assert before_tool_guard(tool, {"command": "echo 2"}, ctx) is None
    result = before_tool_guard(tool, {"command": "echo 3"}, ctx)
    assert result is not None and "step budget" in result["error"]
    assert ctx.actions.escalate is True


def test_time_budget_escalates(monkeypatch):
    monkeypatch.setenv("AGENT_KILL_SWITCH", "0")
    # Negative budget => any elapsed (incl. 0.0 on the first call) exceeds it: deterministic.
    monkeypatch.setenv("AGENT_MAX_SECONDS", "-1")
    ctx = _Ctx()
    result = before_tool_guard(_Tool("run_shell"), {"command": "echo 1"}, ctx)
    assert result is not None and "time budget" in result["error"]
    assert ctx.actions.escalate is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
