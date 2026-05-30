"""Narrow exec service for the workload container (Milestone 1).

The ONLY interface between the trusted control plane and the untrusted, disposable workload
container (CLAUDE.md invariant #3). Deliberately NOT a Docker socket and NOT Docker-in-Docker:
a small token-authenticated HTTP API whose every operation is confined to the sandbox WORKDIR
(`/work`). The model never enters this container; this service never touches the host repo or
the control-plane process — it only sees its own ephemeral `/work` tmpfs.

Endpoints (all but /healthz require `Authorization: Bearer <EXEC_SERVICE_TOKEN>`):
  GET  /healthz                      -> liveness (no auth)
  POST /exec      {command,timeout}  -> run a shell command in /work
  POST /write     {path,content}     -> write a file under /work
  POST /read      {path}             -> read a text file under /work
  POST /copy_out  {path}             -> return file bytes (base64) for the control plane to persist
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

WORKDIR = Path(os.environ.get("WORKDIR", "/work")).resolve()
TOKEN = os.environ.get("EXEC_SERVICE_TOKEN", "")
MAX_OUTPUT_BYTES = int(os.environ.get("EXEC_MAX_OUTPUT_BYTES", str(64 * 1024)))
DEFAULT_TIMEOUT = int(os.environ.get("EXEC_DEFAULT_TIMEOUT", "60"))
MAX_TIMEOUT = int(os.environ.get("EXEC_MAX_TIMEOUT", "300"))

app = FastAPI(title="pitcrew workload exec service", version="0.1.0")


def _auth(authorization: str = Header(default="")) -> None:
    """Reject anything without the shared bearer token (only the control plane holds it)."""
    if not TOKEN or authorization != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


def _resolve(path: str) -> Path:
    """Resolve a caller path under WORKDIR, rejecting any escape (path traversal, abs paths)."""
    candidate = (WORKDIR / path).resolve()
    if candidate != WORKDIR and WORKDIR not in candidate.parents:
        raise HTTPException(status_code=400, detail="path escapes sandbox workdir")
    return candidate


def _truncate(raw: bytes) -> tuple[str, bool]:
    if len(raw) > MAX_OUTPUT_BYTES:
        return raw[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"), True
    return raw.decode("utf-8", "replace"), False


class ExecRequest(BaseModel):
    command: str
    timeout: int = Field(default=DEFAULT_TIMEOUT, ge=1, le=MAX_TIMEOUT)


class WriteRequest(BaseModel):
    path: str
    content: str


class PathRequest(BaseModel):
    path: str


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok", "workdir": str(WORKDIR)}


@app.post("/exec", dependencies=[Depends(_auth)])
async def exec_command(req: ExecRequest) -> dict:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh",
        "-c",
        req.command,
        cwd=str(WORKDIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=req.timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(status_code=408, detail=f"command timed out after {req.timeout}s")
    stdout, out_trunc = _truncate(out)
    stderr, err_trunc = _truncate(err)
    return {
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": out_trunc or err_trunc,
    }


@app.post("/write", dependencies=[Depends(_auth)])
async def write_file(req: WriteRequest) -> dict:
    target = _resolve(req.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = req.content.encode("utf-8")
    target.write_bytes(data)
    return {"path": str(target.relative_to(WORKDIR)), "bytes_written": len(data)}


@app.post("/read", dependencies=[Depends(_auth)])
async def read_file(req: PathRequest) -> dict:
    target = _resolve(req.path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    content, truncated = _truncate(target.read_bytes())
    return {"path": str(target.relative_to(WORKDIR)), "content": content, "truncated": truncated}


@app.post("/copy_out", dependencies=[Depends(_auth)])
async def copy_out(req: PathRequest) -> dict:
    target = _resolve(req.path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    raw = target.read_bytes()
    return {
        "path": str(target.relative_to(WORKDIR)),
        "size": len(raw),
        "content_b64": base64.b64encode(raw).decode("ascii"),
    }
