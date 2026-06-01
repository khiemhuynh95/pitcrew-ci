"""Unit tests for the Milestone-2 runtime wiring — the config WE assemble (no model/Redis needed).

The compaction *behaviour* is ADK's; what we own is that build_app() wires the right knobs and that
the session service is persistent with its data dir created. agent.py must also stay frozen.
"""

from __future__ import annotations

from pathlib import Path

from google.adk.sessions import DatabaseSessionService

from control_plane import runtime
from control_plane.semantic_cache import SemanticCachePlugin


def test_build_app_wires_compaction_resumability_and_cache(monkeypatch):
    monkeypatch.setenv("COMPACTION_INTERVAL", "7")
    monkeypatch.setenv("COMPACTION_OVERLAP", "2")
    app = runtime.build_app()

    assert app.name == runtime.APP_NAME
    assert app.resumability_config is not None and app.resumability_config.is_resumable is True

    comp = app.events_compaction_config
    assert comp is not None
    assert comp.compaction_interval == 7
    assert comp.overlap_size == 2
    assert comp.summarizer is not None  # LlmEventSummarizer attached

    cache_plugins = [p for p in (app.plugins or []) if isinstance(p, SemanticCachePlugin)]
    assert len(cache_plugins) == 1
    assert cache_plugins[0].name == "semantic_cache"


def test_build_session_service_is_persistent_and_creates_dir(tmp_path, monkeypatch):
    db = tmp_path / "nested" / "sessions.db"
    url = f"sqlite+aiosqlite:///{db.as_posix()}"
    monkeypatch.setenv("SESSION_DB_URL", url)

    svc = runtime.build_session_service()
    assert isinstance(svc, DatabaseSessionService)
    assert (tmp_path / "nested").is_dir()  # parent dir created for the SQLite file


def test_agent_module_has_no_runtime_imports():
    """agent.py stays the constant definition — persistence/cache/compaction live in runtime.py."""
    src = Path(runtime.__file__).with_name("agent.py").read_text(encoding="utf-8")
    for forbidden in ("DatabaseSessionService", "SemanticCachePlugin", "EventsCompactionConfig"):
        assert forbidden not in src, f"{forbidden} leaked into agent.py (should be in runtime.py)"
