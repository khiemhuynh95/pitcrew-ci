"""Milestone 2 DoD — context compaction (a long run doesn't overrun the small local window).

ADK runs sliding-window compaction at the END of each invocation: once `COMPACTION_INTERVAL` new
invocations have accumulated on a session, the LlmEventSummarizer replaces the older raw events with
ONE summary `CompactedEvent`, so the context the model sees stays bounded no matter how long the
agent keeps working a repo's session over many runs.

We drive ADK's *exact* post-invocation routine — `_run_compaction_for_sliding_window`, the function
the Runner calls after every run — over a session pre-loaded with several invocations' worth of raw
events. (We synthesize the prior invocations rather than execute many slow agent runs: the local
worker can take a long, unbounded time per run, and what this milestone must prove is that the
compaction we wired into the App actually summarizes accumulated history and shrinks the context —
which is independent of how the worker behaves.) One real summarizer model call does the compaction.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("COMPACTION_INTERVAL", "2")  # compact once 2 invocations have accumulated
os.environ.setdefault("COMPACTION_OVERLAP", "1")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402
from google.adk.apps.compaction import _run_compaction_for_sliding_window  # noqa: E402
from google.adk.events.event import Event  # noqa: E402
from google.genai import types  # noqa: E402

from control_plane.runtime import APP_NAME, build_app, build_session_service  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
logging.getLogger("google_adk").setLevel(logging.ERROR)

_DATA = Path(__file__).resolve().parent.parent / "data"
_DB_URL = f"sqlite+aiosqlite:///{(_DATA / 'compaction_demo.db').as_posix()}"
USER = "demo"
N_INVOCATIONS = 3  # > COMPACTION_INTERVAL so a compaction fires


def _text_chars(events) -> int:
    return sum(
        len(p.text)
        for e in events
        for p in (e.content.parts if e.content and e.content.parts else [])
        if p.text
    )


def _compaction_events(events) -> list:
    return [e for e in events if e.actions and e.actions.compaction]


async def _add_invocation(svc, session, i: int, ts: float) -> None:
    """Append one invocation's worth of raw events (a user turn + a verbose model turn)."""
    inv = f"inv-{i}"
    user = Event(
        invocation_id=inv,
        author="user",
        timestamp=ts,
        content=types.Content(
            role="user", parts=[types.Part(text=f"Task {i}: investigate failure #{i}.")]
        ),
    )
    model = Event(
        invocation_id=inv,
        author="pitcrew_worker",
        timestamp=ts + 0.1,
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text=(
                        f"Run {i}: read logs, reproduced the error, inspected the stack trace, "
                        f"checked recent commits, and outlined a candidate fix. " * 40
                    )
                )
            ],
        ),
    )
    await svc.append_event(session=session, event=user)
    await svc.append_event(session=session, event=model)


async def main() -> int:
    _DATA.mkdir(parents=True, exist_ok=True)
    Path(_DATA / "compaction_demo.db").unlink(missing_ok=True)

    svc = build_session_service(_DB_URL)
    app = build_app()
    interval = app.events_compaction_config.compaction_interval
    print(
        f"compaction_interval={interval} (compact once {interval} invocations have accumulated)\n"
    )

    session = await svc.create_session(app_name=APP_NAME, user_id=USER)
    base = time.time()
    for i in range(1, N_INVOCATIONS + 1):
        await _add_invocation(svc, session, i, base + i)
        session = await svc.get_session(app_name=APP_NAME, user_id=USER, session_id=session.id)
        print(
            f"after invocation {i}: raw events={len(session.events)}, "
            f"context chars={_text_chars(session.events)}"
        )

    chars_before = _text_chars(session.events)
    print(
        "\nrunning ADK's post-invocation sliding-window compaction (one summarizer model call)..."
    )
    await _run_compaction_for_sliding_window(app, session, svc)

    session = await svc.get_session(app_name=APP_NAME, user_id=USER, session_id=session.id)
    comps = _compaction_events(session.events)
    ok = len(comps) > 0
    if ok:
        summary = comps[-1].actions.compaction.compacted_content
        text = " ".join(p.text for p in (summary.parts or []) if p.text) if summary else ""
        print(
            f"\nappended {len(comps)} CompactedEvent(s): {len(text)} chars of summary replace "
            f"~{chars_before} chars of raw history"
        )
        print(f"  summary: {text[:300]!r}")
    print(f"\nContext compaction (history summarized, window bounded): {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
