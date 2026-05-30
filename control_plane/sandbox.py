"""Native sandbox tools (Milestone 1).

The agent's "hands": run_shell / write_file / read_file / copy_out / finish. Every one runs
INSIDE the disposable workload container via the narrow exec service (CLAUDE.md invariant #3) —
the control-plane process itself never executes model-directed shell/file ops. This is the thin
client; the isolation lives in the container + exec service, not here.

NOTE: the plan foresees a thin `RemoteEnvironment(BaseEnvironment)` adapter tracking ADK's
(experimental) Environment Toolset. We keep the isolation ours and expose plain FunctionTools for
now; swapping in the ADK adapter later is a client-internal change, not an agent.py edit.
"""

from __future__ import annotations

import mimetypes
import os

import httpx
from google.adk.tools.tool_context import ToolContext
from google.genai import types


def _err(resp: httpx.Response) -> dict:
    """Build a structured error from a non-2xx exec-service response (JSON or plain-text body)."""
    if resp.headers.get("content-type", "").startswith("application/json"):
        detail = resp.json().get("detail", resp.text)
    else:
        detail = resp.text
    return {"error": detail or f"HTTP {resp.status_code}", "status": resp.status_code}


def _client() -> httpx.AsyncClient:
    # Read env lazily (at call time) so this module is independent of .env load order.
    url = os.environ.get("EXEC_SERVICE_URL", "http://127.0.0.1:8800").rstrip("/")
    token = os.environ.get("EXEC_SERVICE_TOKEN", "")
    timeout = float(os.environ.get("EXEC_HTTP_TIMEOUT", "320"))
    return httpx.AsyncClient(
        base_url=url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )


async def run_shell(command: str) -> dict:
    """Run a shell command inside the sandbox workload container (cwd is the sandbox /work).

    Use for builds, tests, inspecting files, and any command-line work. Returns exit_code,
    stdout and stderr. The command CANNOT reach the host, the repo, or the control plane.
    """
    async with _client() as c:
        resp = await c.post("/exec", json={"command": command})
    return resp.json() if resp.status_code == 200 else _err(resp)


async def write_file(path: str, content: str) -> dict:
    """Write a UTF-8 text file at `path` (relative to the sandbox /work). Creates parent dirs."""
    async with _client() as c:
        resp = await c.post("/write", json={"path": path, "content": content})
    return resp.json() if resp.status_code == 200 else _err(resp)


async def read_file(path: str) -> dict:
    """Read a UTF-8 text file at `path` (relative to the sandbox /work)."""
    async with _client() as c:
        resp = await c.post("/read", json={"path": path})
    return resp.json() if resp.status_code == 200 else _err(resp)


async def copy_out(path: str, artifact_name: str, tool_context: ToolContext) -> dict:
    """Persist a sandbox file as a durable artifact, named `artifact_name`.

    Use this to save your final output (a report, a build product) OUT of the disposable
    sandbox before finishing — anything not copied out is lost when the container is wiped.
    """
    import base64

    async with _client() as c:
        resp = await c.post("/copy_out", json={"path": path})
    if resp.status_code != 200:
        return _err(resp)
    payload = resp.json()
    raw = base64.b64decode(payload["content_b64"])
    mime = mimetypes.guess_type(artifact_name)[0] or "application/octet-stream"
    version = await tool_context.save_artifact(
        artifact_name, types.Part(inline_data=types.Blob(mime_type=mime, data=raw))
    )
    return {"artifact_name": artifact_name, "version": version, "size": payload["size"]}


def finish(summary: str, tool_context: ToolContext) -> dict:
    """Call when the goal is complete (or cannot proceed). Stops the agent loop cleanly.

    `summary` should state what was accomplished and name any artifacts copied out.
    """
    tool_context.actions.escalate = True
    return {"status": "finished", "summary": summary}
