"""Cluster-51 dedup: ai_bridge.utc_now() must be jobs.utc_now(), not a local copy.

Characterization + routing tests for the utc_now() duplication between
gemini_translator/mcp/jobs.py (canonical) and gemini_translator/mcp/ai_bridge.py
(local copy, byte-identical body: `datetime.now(timezone.utc).isoformat()`).
"""
from __future__ import annotations

from datetime import datetime

from gemini_translator.mcp import ai_bridge
from gemini_translator.mcp.jobs import utc_now


def test_jobs_utc_now_returns_iso_utc_timestamp():
    """Characterization: canonical utc_now() returns an ISO-8601 UTC timestamp."""
    stamp = utc_now()

    parsed = datetime.fromisoformat(stamp)
    assert parsed.utcoffset().total_seconds() == 0


def test_ai_bridge_create_gui_ai_task_reads_module_level_utc_now(tmp_path, monkeypatch):
    """create_gui_ai_task() must consult the module-level name `utc_now`, not an
    inlined datetime.now(timezone.utc).isoformat() call.

    Because ai_bridge uses `from .jobs import utc_now`, patching this name has to
    target ai_bridge's own namespace (that is where the call is looked up) —
    patching gemini_translator.mcp.jobs.utc_now would not propagate here, which is
    exactly why the identity test below is the real duplication check.
    """
    sentinel = "2000-01-01T00:00:00+00:00"
    monkeypatch.setattr(ai_bridge, "utc_now", lambda: sentinel)

    task = ai_bridge.create_gui_ai_task(tmp_path, {"prompt": "hello"})

    assert task.created_at == sentinel
    assert task.updated_at == sentinel


def test_ai_bridge_has_no_local_utc_now_definition():
    """Routing/anti-duplication check: ai_bridge.utc_now must be the exact
    canonical function object from jobs.py (imported), not a separately defined
    copy with identical body.

    Before the fix, ai_bridge.py defines its own utc_now() -> a distinct function
    object -> this assertion fails (RED). After the fix, ai_bridge does
    `from .jobs import utc_now`, binding the very same object -> passes.
    """
    assert ai_bridge.utc_now is utc_now
