"""Milestone 1 DoD demo.

Part A (autonomy): give the dynamic-workflow loop a goal; it works in the sandbox, copies out an
artifact, and stops cleanly at finish() (escalate), not the iteration budget.
Part B (isolation): probe the exec service directly to prove the workload container cannot escape
/work or reach the host/control-plane filesystem.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from control_plane.agent import root_agent  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GOAL = (
    "Create a file named report.txt in the sandbox containing two lines: first the output of the "
    "shell command `date -u`, and second the exact text 'hello from the pitcrew sandbox'. Then "
    "read the file back to verify its contents. Then copy it out as an artifact named report.txt. "
    "Then finish with a one-line summary."
)
APP = "pitcrew"
USER = "demo"


async def part_a() -> bool:
    runner = InMemoryRunner(agent=root_agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id=USER)

    calls: list[str] = []
    finished = False
    async for event in runner.run_async(
        user_id=USER,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=GOAL)]),
    ):
        if not (event.content and event.content.parts):
            continue
        for p in event.content.parts:
            if p.function_call:
                calls.append(p.function_call.name)
                print(f"  CALL {p.function_call.name}({dict(p.function_call.args)})")
            if p.function_response:
                resp = p.function_response.response
                short = str(resp)
                print(f"  RESP {p.function_response.name} -> {short[:200]}")
                if p.function_response.name == "finish":
                    finished = True
            if p.text and p.text.strip():
                print(f"  TEXT {p.text.strip()[:200]}")

    keys = await runner.artifact_service.list_artifact_keys(
        app_name=APP, user_id=USER, session_id=session.id
    )
    print(f"\n  tool calls: {calls}")
    print(f"  artifacts saved: {keys}")
    print(f"  stopped via finish(): {finished}")

    ok = finished and "report.txt" in keys
    if ok and "report.txt" in keys:
        art = await runner.artifact_service.load_artifact(
            app_name=APP, user_id=USER, session_id=session.id, filename="report.txt"
        )
        if art and art.inline_data:
            print(f"  artifact contents:\n    {art.inline_data.data.decode('utf-8', 'replace')}")
    return ok


def part_b() -> bool:
    url = os.environ["EXEC_SERVICE_URL"].rstrip("/")
    token = os.environ["EXEC_SERVICE_TOKEN"]
    h = {"Authorization": f"Bearer {token}"}
    ok = True

    # 1. No-token request is rejected.
    r = httpx.post(f"{url}/exec", json={"command": "echo hi"}, timeout=10)
    print(f"  no-token /exec -> {r.status_code} (expect 401)")
    ok &= r.status_code == 401

    # 2. Path traversal out of /work is rejected.
    r = httpx.post(
        f"{url}/write", headers=h, json={"path": "../../etc/pwn", "content": "x"}, timeout=10
    )
    print(f"  write ../../etc/pwn -> {r.status_code} (expect 400)")
    ok &= r.status_code == 400

    # 3. The host repo is invisible inside the container (no mount back to control plane/host).
    host_marker = "/c/Users/Dell Server/Desktop/python/pitcrew-ci/CLAUDE.md"
    r = httpx.post(
        f"{url}/exec",
        headers=h,
        json={"command": f"cat '{host_marker}' 2>&1; echo EXIT=$?"},
        timeout=15,
    )
    body = r.json()
    print(
        f"  cat host CLAUDE.md inside container -> exit nonzero / not found: {body.get('stdout', '').strip()[:120]}"
    )
    ok &= "No such file" in body.get("stdout", "") or body.get("exit_code", 0) != 0

    return ok


async def main() -> int:
    print("== Part A: autonomous sandbox goal ==")
    a = await part_a()
    print("\n== Part B: workload isolation probes ==")
    b = part_b()
    print(f"\nPart A (autonomy + artifact + clean stop): {'PASS' if a else 'FAIL'}")
    print(f"Part B (isolation: auth + no escape + no host access): {'PASS' if b else 'FAIL'}")
    return 0 if (a and b) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
