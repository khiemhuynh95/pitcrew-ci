"""Tests for the capability loader (Milestone 1.5).

The loader is the convention layer behind invariant #6 (agent.py is constant). These tests prove
the two behaviors the DoD rests on: a SKILL.md folder is auto-discovered with zero code change, and
the MCP registry honors enabled/placement plus the tool_filter allowlist.
"""

from __future__ import annotations

from pathlib import Path

from control_plane import capabilities
from control_plane.capabilities import (
    load_mcp_toolsets,
    load_skill_toolset,
)

_REPO_SKILLS = Path(__file__).resolve().parent.parent / "control_plane" / "skills"


def _write_skill(base: Path, name: str, description: str = "A test skill for X.") -> None:
    d = base / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nDo the thing.\n",
        encoding="utf-8",
    )


def test_real_skills_are_discovered():
    """The two shipped skills load from their folders."""
    ts = load_skill_toolset(_REPO_SKILLS)
    assert ts is not None
    assert set(ts._skills.keys()) == {"browser-ops", "web-research"}


def test_dropping_a_skill_folder_needs_no_code_change(tmp_path):
    """DoD: adding a dummy skill folder works with zero code change — just discovery."""
    assert load_skill_toolset(tmp_path) is None  # empty dir → nothing

    _write_skill(tmp_path, "dummy-skill")
    ts = load_skill_toolset(tmp_path)
    assert ts is not None and "dummy-skill" in ts._skills

    _write_skill(tmp_path, "another-skill")
    ts = load_skill_toolset(tmp_path)
    assert set(ts._skills.keys()) == {"dummy-skill", "another-skill"}


def test_folder_without_skill_md_is_ignored(tmp_path):
    (tmp_path / "not-a-skill").mkdir()
    (tmp_path / "not-a-skill" / "README.md").write_text("nope", encoding="utf-8")
    assert load_skill_toolset(tmp_path) is None


def _registry(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "mcp_servers.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_only_enabled_servers_load(tmp_path):
    reg = _registry(
        tmp_path,
        """
servers:
  on:
    enabled: true
    placement: control_plane
    command: echo
    args: ["hi"]
    tool_filter: [a, b]
  off:
    enabled: false
    placement: control_plane
    command: echo
""",
    )
    toolsets = load_mcp_toolsets(reg)
    assert len(toolsets) == 1
    assert toolsets[0].tool_filter == ["a", "b"]


def test_placement_filter(tmp_path):
    reg = _registry(
        tmp_path,
        """
servers:
  cp:
    enabled: true
    placement: control_plane
    command: echo
  wl:
    enabled: true
    placement: workload
    command: echo
""",
    )
    assert len(load_mcp_toolsets(reg)) == 2  # default: all enabled
    assert len(load_mcp_toolsets(reg, plane="control_plane")) == 1
    assert len(load_mcp_toolsets(reg, plane="workload")) == 1


def test_env_vars_are_expanded_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    reg = _registry(
        tmp_path,
        """
servers:
  email:
    enabled: true
    placement: control_plane
    command: echo
    env:
      SMTP_HOST: ${SMTP_HOST}
""",
    )
    ts = load_mcp_toolsets(reg)[0]
    # The expanded value rides on the stdio server params the toolset was built with.
    params = ts._connection_params.server_params
    assert params.env["SMTP_HOST"] == "smtp.example.test"


def test_missing_registry_returns_empty(tmp_path):
    assert load_mcp_toolsets(tmp_path / "nope.yaml") == []


def test_shipped_registry_filters_playwright_to_allowlist():
    """The real registry only exposes the read/navigate/interact verbs — no broad surface."""
    toolsets = load_mcp_toolsets(capabilities._REGISTRY)
    assert len(toolsets) == 1  # playwright enabled, email disabled
    flt = set(toolsets[0].tool_filter)
    assert "browser_navigate" in flt and "browser_snapshot" in flt
    # No file-download / install / pdf verbs slipped in.
    assert not any(v in flt for v in ("browser_file_upload", "browser_install", "browser_pdf_save"))
