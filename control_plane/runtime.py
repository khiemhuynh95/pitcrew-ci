"""Runtime wiring (Milestone 2): persistence + compaction + semantic cache, on one App + Runner.

`agent.py` stays the CONSTANT agent *definition* (the Workflow `root_agent`); this module is the
runtime that runs it for real — the seam that holds everything the plan's Scale band adds without
touching the agent:

  * **Persistence / resume** — a `DatabaseSessionService` (SQLite URI by default; swap to
    `postgres://` in Phase 9) makes a killed run resumable. `ResumabilityConfig(is_resumable=True)`
    tells the graph engine to checkpoint, so a fresh process pointed at the same DB + session id
    picks up where it stopped.
  * **Context compaction** — `EventsCompactionConfig` + `LlmEventSummarizer` periodically replaces
    old raw events with an LLM summary so a long loop never overruns the small local context
    window (not optional for a many-step local-model agent).
  * **Semantic cache** — the `SemanticCachePlugin` (Redis + embeddings) short-circuits repeated
    model calls. Registered as an App plugin so it wraps the Runner, fail-open.

`adk web` (the dev-debug surface) still loads `root_agent` directly and runs without these — the
Milestone-2 DoD is demonstrated through the persistent Runner built here (see scripts/).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from google.adk.apps import App
from google.adk.apps.app import EventsCompactionConfig, ResumabilityConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.artifacts import InMemoryArtifactService
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.adk.sessions.base_session_service import BaseSessionService

from control_plane.agent import root_agent
from control_plane.model import build_model
from control_plane.semantic_cache import build_semantic_cache_plugin

APP_NAME = "pitcrew"


def _compaction_config() -> EventsCompactionConfig:
    """Compact every COMPACTION_INTERVAL events, keeping COMPACTION_OVERLAP for continuity."""
    return EventsCompactionConfig(
        summarizer=LlmEventSummarizer(llm=build_model()),
        compaction_interval=int(os.environ.get("COMPACTION_INTERVAL", "12")),
        overlap_size=int(os.environ.get("COMPACTION_OVERLAP", "3")),
    )


def build_app() -> App:
    """The control-plane App: the constant agent + Scale-band runtime config + the cache plugin."""
    return App(
        name=APP_NAME,
        root_agent=root_agent,
        plugins=[build_semantic_cache_plugin()],
        events_compaction_config=_compaction_config(),
        resumability_config=ResumabilityConfig(is_resumable=True),
    )


def build_session_service(db_url: str | None = None) -> BaseSessionService:
    """Persistent session store. SQLite by default (zero infra); any SQLAlchemy URL works.

    DatabaseSessionService runs on SQLAlchemy's *async* engine, so the URL needs an async driver:
    `sqlite+aiosqlite:///...` locally, `postgresql+asyncpg://...` in Phase 9.
    """
    url = db_url or os.environ.get("SESSION_DB_URL", "sqlite+aiosqlite:///./data/sessions.db")
    # Ensure the SQLite file's parent directory exists (DatabaseSessionService won't create it).
    if url.startswith("sqlite"):
        db_path = Path(urlparse(url).path.lstrip("/"))
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return DatabaseSessionService(db_url=url)


def build_runner(
    session_service: BaseSessionService | None = None,
    app: App | None = None,
) -> Runner:
    """A Runner over the persistent session store + the App's compaction/cache/resumability.

    `session_service`/`app` are injectable so a demo can reuse ONE store across two Runner instances
    (the kill-and-resume proof) without rebuilding the App each time.
    """
    return Runner(
        app=app or build_app(),
        session_service=session_service or build_session_service(),
        artifact_service=InMemoryArtifactService(),
    )
