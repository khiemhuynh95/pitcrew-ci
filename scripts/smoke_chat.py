"""Non-interactive Milestone-0 smoke test: run ONE turn through an ADK Runner against LM Studio.

`make agent` (adk web) is the interactive DoD surface; this proves the same wiring
(LiteLlm -> LM Studio -> reply) end-to-end without a browser, so it can run in CI later.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on sys.path

# Windows consoles default to cp1252; model replies may contain emoji/Unicode.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from control_plane.agent import root_agent  # noqa: E402

_PROMPT = "In one short sentence, introduce yourself."


async def main() -> int:
    runner = InMemoryRunner(agent=root_agent, app_name="pitcrew")
    session = await runner.session_service.create_session(app_name="pitcrew", user_id="smoke")

    reply = ""
    async for event in runner.run_async(
        user_id="smoke",
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=_PROMPT)]),
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    reply += part.text

    reply = reply.strip()
    if not reply:
        print("FAIL: no text reply from the model.")
        return 1
    print(f"PROMPT: {_PROMPT}")
    print(f"REPLY:  {reply}")
    print("\nsmoke-chat passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
