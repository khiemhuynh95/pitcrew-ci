"""Milestone 1.5 DoD demo.

Gives the agent a goal that needs a browser and shows the capability layer working end to end:
  * the agent loads the right SKILL (browser-ops) — progressive disclosure, not always-on,
  * it drives the headless browser through the tool_filter'd Playwright verbs,
  * it finishes cleanly.

Adding the browser + skills required ZERO change to agent.py — they came from capabilities.py
(a SKILL.md folder + an MCP registry block). Email send is wired the same way but stays disabled
until SMTP_* creds are set in .env (it sends real mail), so it is reported, not exercised here.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from control_plane.agent import root_agent  # noqa: E402
from control_plane.capabilities import load_skill_toolset  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

GOAL = (
    "Use a browser to open https://example.com and report the exact text of the page's main "
    "heading (the top-level h1). Check which skills you have first and load the relevant one. "
    "When you have the heading, call finish with the heading text in your summary."
)
APP = "pitcrew"
USER = "demo"


async def main() -> int:
    # Progressive disclosure: only skill names/descriptions are in context up front.
    st = load_skill_toolset()
    print("== Available skills (name + description only until loaded) ==")
    for name, skill in st._skills.items():
        print(f"  - {name}: {skill.frontmatter.description.strip().splitlines()[0]}")

    runner = InMemoryRunner(agent=root_agent, app_name=APP)
    session = await runner.session_service.create_session(app_name=APP, user_id=USER)

    calls: list[str] = []
    loaded_skills: list[str] = []
    finished = False

    print("\n== Run ==")
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
                args = dict(p.function_call.args)
                if p.function_call.name == "load_skill":
                    loaded_skills.append(str(args.get("skill_name") or args.get("name") or args))
                print(f"  CALL {p.function_call.name}({args})")
            if p.function_response and p.function_response.name == "finish":
                finished = True
                print(f"  RESP finish -> {str(p.function_response.response)[:200]}")
            if p.text and p.text.strip():
                print(f"  TEXT {p.text.strip()[:200]}")

    browser_calls = [c for c in calls if c.startswith("browser_")]
    print(f"\n  tool calls: {calls}")
    print(f"  skills loaded on demand: {loaded_skills}")
    print(f"  browser verbs used: {browser_calls}")
    print(f"  stopped via finish(): {finished}")

    triggered_browser_skill = "browser-ops" in loaded_skills or "web-research" in loaded_skills
    ok = triggered_browser_skill and bool(browser_calls) and finished
    print(
        f"\nMilestone 1.5 DoD (skill triggered + filtered browser drive + clean stop): "
        f"{'PASS' if ok else 'PARTIAL/FAIL'}"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
