"""Capability loader (Milestone 1.5) — the convention layer that makes agent.py CONSTANT.

Two altitudes of the Build band (HANDOFF §1.5):

  * **Skills = packaging.** Every folder under ``skills/`` that contains a ``SKILL.md`` is
    auto-discovered and loaded into ONE ``SkillToolset``, which discloses skills *progressively*
    (only name/description reach the model until it loads a skill's full instructions on demand).
  * **MCP = supply.** Every ENABLED block in ``config/mcp_servers.yaml`` becomes one
    ``tool_filter``-ed ``McpToolset`` (the filter is the first governance lever — the model only
    sees the verbs we allow).

This is the whole point of the milestone: adding a capability is dropping a ``SKILL.md`` folder or
adding a registry block — never editing ``agent.py`` (invariant #6). ``agent.py`` calls
``load_capabilities()`` once and is frozen.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from google.adk.skills import load_skill_from_dir
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.mcp_tool import McpToolset, StdioConnectionParams
from google.adk.tools.skill_toolset import SkillToolset
from mcp import StdioServerParameters

_HERE = Path(__file__).resolve().parent
_SKILLS_DIR = _HERE / "skills"
_REGISTRY = _HERE / "config" / "mcp_servers.yaml"


def load_skill_toolset(skills_dir: Path = _SKILLS_DIR) -> SkillToolset | None:
    """Auto-discover every ``skills/<name>/SKILL.md`` and bundle them into one SkillToolset.

    A folder *is* the unit of discovery: drop one in and it loads with zero code change. Returns
    None when there are no skills so the caller can omit an empty toolset.
    """
    if not skills_dir.is_dir():
        return None
    skills = [
        load_skill_from_dir(child)
        for child in sorted(skills_dir.iterdir())
        if (child / "SKILL.md").is_file()
    ]
    return SkillToolset(skills=skills) if skills else None


def _expand_env(value: Any) -> Any:
    """Expand ``${VAR}`` references in registry env values from the process environment.

    Secrets live in the environment (SOPS-decrypted at container start, Milestone 3), never in this
    YAML — the registry only names them.
    """
    return os.path.expandvars(value) if isinstance(value, str) else value


def load_mcp_toolsets(registry: Path = _REGISTRY, plane: str | None = None) -> list[McpToolset]:
    """Build one ``tool_filter``-ed McpToolset per ENABLED block in the registry.

    Args:
      registry: path to the stdio MCP registry YAML.
      plane: if given (``"control_plane"`` / ``"workload"``), only load blocks whose ``placement``
        matches — used once the two-container split is real (Milestone 3). Until then the default
        (None) loads every enabled block on the control-plane host, per the M1.5 plan step.
    """
    if not registry.is_file():
        return []
    spec = yaml.safe_load(registry.read_text(encoding="utf-8")) or {}

    toolsets: list[McpToolset] = []
    for block in (spec.get("servers") or {}).values():
        if not block.get("enabled", False):
            continue
        if plane is not None and block.get("placement") != plane:
            continue
        env = {k: _expand_env(v) for k, v in (block.get("env") or {}).items()}
        server_params = StdioServerParameters(
            command=block["command"],
            args=list(block.get("args") or []),
            env=env or None,
        )
        tool_filter = list(block.get("tool_filter") or []) or None
        toolsets.append(
            McpToolset(
                connection_params=StdioConnectionParams(server_params=server_params),
                tool_filter=tool_filter,
            )
        )
    return toolsets


def load_capabilities(plane: str | None = None) -> list[BaseToolset]:
    """All packaged capabilities for an agent: the SkillToolset (if any) + the MCP toolsets.

    The native sandbox tools (run_shell/…) stay in agent.py; everything that grows over time comes
    from here, so agent.py never changes when a skill or MCP server is added.
    """
    tools: list[BaseToolset] = []
    skill_toolset = load_skill_toolset()
    if skill_toolset is not None:
        tools.append(skill_toolset)
    tools.extend(load_mcp_toolsets(plane=plane))
    return tools
