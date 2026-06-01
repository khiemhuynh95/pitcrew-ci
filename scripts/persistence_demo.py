"""Milestone 2 DoD — persistence + resume (kill mid-run, restart, resume the same session).

Run as two REAL processes against one SQLite session DB (a genuine restart), driven by the
Makefile `resume` target:

  phase1  create a session, start the agent, and once a few events have been persisted, hard-exit
          with os._exit() — a true crash mid-run (no cleanup). The session id is written to disk.
  phase2  a fresh process opens a NEW DatabaseSessionService over the same DB, shows the events
          survived the crash, then resumes the SAME session (run_async with new_message=None) and
          shows the run continues — more events append to the same session.

This proves persistence + resume regardless of how the small local worker behaves (we bound by
event count / wall-clock, not by the worker reaching finish()).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402
from google.genai import types  # noqa: E402

from control_plane.runtime import (  # noqa: E402
    APP_NAME,
    build_app,
    build_runner,
    build_session_service,
)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.getLogger("google_adk").setLevel(logging.ERROR)
# Bounding the resume loop with `break` closes the run generator mid-span; OpenTelemetry logs a
# noisy (harmless) context-detach error on that teardown. Silence it — the run itself is fine.
logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)

_DATA = Path(__file__).resolve().parent.parent / "data"
_DB_URL = f"sqlite+aiosqlite:///{(_DATA / 'resume_demo.db').as_posix()}"
_SID_FILE = _DATA / "resume_demo.session"
USER = "demo"
GOAL = (
    "Explore the sandbox: run `uname -a`, then `pwd`, then create a file progress.txt listing the "
    "steps you have taken so far, reading it back to confirm. Then copy it out as an artifact "
    "named progress.txt and finish with a summary."
)
_CRASH_AFTER_EVENTS = 4  # hard-exit once this many events have been persisted (simulated crash)


async def phase1() -> int:
    _DATA.mkdir(parents=True, exist_ok=True)
    Path(_DATA / "resume_demo.db").unlink(missing_ok=True)
    _SID_FILE.unlink(missing_ok=True)

    svc = build_session_service(_DB_URL)
    runner = build_runner(session_service=svc, app=build_app())
    session = await svc.create_session(app_name=APP_NAME, user_id=USER)
    _SID_FILE.write_text(session.id, encoding="utf-8")
    print(
        f"phase1: session {session.id} — running until {_CRASH_AFTER_EVENTS} events, then crashing"
    )

    seen = 0
    async for event in runner.run_async(
        user_id=USER,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=GOAL)]),
    ):
        for p in event.content.parts if event.content and event.content.parts else []:
            if p.function_call:
                print(f"  phase1 CALL {p.function_call.name}")
        seen += 1
        if seen >= _CRASH_AFTER_EVENTS:
            persisted = len(
                (
                    await svc.get_session(app_name=APP_NAME, user_id=USER, session_id=session.id)
                ).events
            )
            print(f"  >>> SIMULATED CRASH after {persisted} persisted events (os._exit) <<<")
            sys.stdout.flush()
            os._exit(0)  # hard kill — no flush/cleanup, exactly like a real crash mid-run
    print("  (run finished before the crash point)")
    return 0


async def phase2() -> int:
    if not _SID_FILE.is_file():
        print("phase2: no session file — run phase1 first.")
        return 1
    sid = _SID_FILE.read_text(encoding="utf-8").strip()

    # A fresh service over the same DB == the process restarted.
    svc = build_session_service(_DB_URL)
    before = await svc.get_session(app_name=APP_NAME, user_id=USER, session_id=sid)
    n_before = len(before.events)
    print(f"phase2: reopened session {sid} after restart — {n_before} events survived the crash")
    if n_before == 0:
        print("Persistence: FAIL (no events survived)")
        return 1

    runner = build_runner(session_service=svc, app=build_app())
    print("phase2: resuming the same session (run_async, new_message=None) ...")
    deadline = time.time() + int(os.environ.get("RESUME_DEMO_SECONDS", "90"))
    produced = 0
    async for event in runner.run_async(user_id=USER, session_id=sid, new_message=None):
        for p in event.content.parts if event.content and event.content.parts else []:
            if p.function_call:
                print(f"  phase2 CALL {p.function_call.name}")
        produced += 1
        if time.time() > deadline or produced > 30:
            break
    await runner.close()

    after = await svc.get_session(app_name=APP_NAME, user_id=USER, session_id=sid)
    n_after = len(after.events)
    print(f"phase2: session now has {n_after} events ({n_after - n_before} appended on resume)")
    ok = n_before > 0 and n_after > n_before
    print(
        f"Persistence + resume (survived crash, same session continued): {'PASS' if ok else 'FAIL'}"
    )
    return 0 if ok else 1


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "phase1"
    if phase == "phase1":
        return asyncio.run(phase1())
    if phase == "phase2":
        return asyncio.run(phase2())
    print(f"unknown phase {phase!r}; use phase1 or phase2")
    return 2


if __name__ == "__main__":
    sys.exit(main())
