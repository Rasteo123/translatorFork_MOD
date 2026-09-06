"""Characterization + routing tests for the third copy of finding
finding-mcp-bench-cli_design_3-cli-mcp-option-vocabulary-trip.

Recap of the finding: the option vocabulary shared by every ``start_*`` MCP tool used to be
hand-copied in three independent places - ``cli.py``'s own argparse registrars (the real,
executable source of truth), ``gemini_translator/mcp/commands.py``'s ``PROJECT_OPTIONS`` /
``COMMON_RUN_OPTIONS`` (fixed by an earlier pass: ``_derive_options`` now reads them straight off
``cli.py``'s registration functions - see
``test_dedup_finding_mcp_bench_cli_design_3_cli_mcp_option_vocabulary_trip.py``), and
``gemini_translator/mcp/server.py``'s ``COMMON_START_PROPERTIES`` dict - a third, hand-maintained
JSON-schema property table that a new cli.py flag never reached (it was only visible to callers
because every tool schema also sets ``additionalProperties: True``).

The fix: ``commands.py`` now exports ``COMMON_START_OPTIONS`` - the concatenation of
``PROJECT_OPTIONS + COMMON_RUN_OPTIONS + GLOBAL_OPTIONS`` - as the single ordered list of dests
every ``start_*`` tool schema must expose. ``server.py`` keeps only a hand-authored
dest -> {"type", "description"} lookup for the *curated* (valuable, human-written) property
descriptions, and derives ``COMMON_START_PROPERTIES`` by walking ``COMMON_START_OPTIONS`` and
falling back to a type inferred from each spec's ``kind``/``value_type`` for any dest with no
curated entry - so a brand-new cli.py flag now reaches the schema automatically instead of being
silently dropped until someone remembers to hand-edit ``server.py`` too.

Test (a) below is a snapshot of today's ``COMMON_START_PROPERTIES`` content - a characterization
invariant: today's schema must stay byte-for-byte the same (same keys, same types, same
descriptions) across the refactor from a hand list to a generated one. Test (b) is the
routing/mechanism proof and is the one required to go RED before the fix and GREEN after: a
dest that exists only because it was added to ``commands.COMMON_RUN_OPTIONS`` (standing in for a
flag freshly added to ``cli.py``'s own ``_add_common_run_args``) must automatically appear in the
``start_translation`` tool's JSON schema, with no matching edit to ``server.py`` required.
"""

from __future__ import annotations

import importlib

import gemini_translator.mcp.commands as commands
import gemini_translator.mcp.server as server


# ---------------------------------------------------------------------------
# (a) Characterization: snapshot of today's COMMON_START_PROPERTIES content
# ---------------------------------------------------------------------------


def test_common_start_properties_snapshot_matches_todays_hand_written_schema():
    """Today's 24 keys/types/descriptions must survive the hand-list -> generated refactor
    unchanged. This is a content snapshot (dict equality - order is irrelevant), not a contract:
    a legitimate new cli.py flag is expected to add a key here, which is exactly what test (b)
    below exercises.
    """
    assert server.COMMON_START_PROPERTIES == {
        "epub": {"type": "string", "description": "Path to the source EPUB."},
        "project": {"type": "string", "description": "Path to the translator project directory."},
        "chapters": {"type": ["integer", "string"], "description": "Maximum number of chapters to process."},
        "chapter": {
            "type": ["array", "integer", "string"],
            "description": "Specific chapter number or numbers to process.",
        },
        "offset": {"type": ["integer", "string"], "description": "Chapter offset for batch processing."},
        "limit": {"type": ["integer", "string"], "description": "Chapter limit for batch processing."},
        "provider": {"type": "string", "description": "AI provider name."},
        "model": {"type": "string", "description": "Provider model name."},
        "api_key": {"type": ["array", "string"], "description": "Provider API key or keys."},
        "api_key_file": {"type": "string", "description": "Path to a file containing API keys."},
        "all_keys": {"type": "boolean", "description": "Use all configured API keys."},
        "workers": {"type": ["integer", "string"], "description": "Worker count."},
        "rpm": {"type": ["integer", "string"], "description": "Requests per minute limit."},
        "temperature": {"type": ["number", "string"], "description": "Generation temperature."},
        "mode": {"type": "string", "description": "Translation or processing mode."},
        "task_size": {"type": ["integer", "string"], "description": "Task size for generated batches."},
        "splits": {"type": ["integer", "string"], "description": "Split count for generated batches."},
        "force_accept": {"type": "boolean", "description": "Accept generated output without interactive review."},
        "json_epub": {"type": "boolean", "description": "Use JSON EPUB project data."},
        "prompt_file": {"type": "string", "description": "Path to a prompt file."},
        "glossary": {"type": "string", "description": "Path to glossary data."},
        "settings_json": {"type": "string", "description": "Path to settings JSON."},
        "settings_profile": {"type": "string", "description": "Named settings profile."},
        "settings_dir": {"type": "string", "description": "Settings directory."},
    }


def test_common_start_properties_key_set_matches_common_start_options_dests():
    """COMMON_START_PROPERTIES must expose exactly the dests commands.COMMON_START_OPTIONS lists -
    no orphan hand-written key that cli.py no longer registers, no missing key for one it does."""
    assert set(server.COMMON_START_PROPERTIES) == {spec.dest for spec in commands.COMMON_START_OPTIONS}


# ---------------------------------------------------------------------------
# (b) Routing: a flag known only to commands.py's option groups reaches the schema
# ---------------------------------------------------------------------------


def test_new_option_in_common_run_options_reaches_start_translation_schema(monkeypatch):
    """Must be RED before the fix (server.py's COMMON_START_PROPERTIES was a hand-written dict,
    deaf to commands.py's option groups) and GREEN after (server.py generates it by walking
    commands.COMMON_START_OPTIONS).

    Stands in for "a flag freshly added to cli.py's _add_common_run_args": patches
    commands.COMMON_RUN_OPTIONS (as PROJECT_OPTIONS/COMMON_RUN_OPTIONS are themselves computed
    once at commands.py import time - see
    test_derive_options_not_recomputed_on_every_build_call in the sibling test module - so is
    their concatenation, COMMON_START_OPTIONS) and reloads server.py, which must pick the new
    dest up with no server.py-side edit.
    """
    extra = commands.OptionSpec("dedup_probe_new_flag", "--dedup-probe-new-flag", "value")
    patched_common_run_options = commands.COMMON_RUN_OPTIONS + (extra,)
    monkeypatch.setattr(commands, "COMMON_RUN_OPTIONS", patched_common_run_options)
    monkeypatch.setattr(
        commands,
        "COMMON_START_OPTIONS",
        commands.PROJECT_OPTIONS + patched_common_run_options + commands.GLOBAL_OPTIONS,
    )

    try:
        reloaded_server = importlib.reload(server)
        tool = next(t for t in reloaded_server.TOOL_DEFINITIONS if t["name"] == "start_translation")
        assert "dedup_probe_new_flag" in tool["inputSchema"]["properties"]
        assert "dedup_probe_new_flag" in reloaded_server.COMMON_START_PROPERTIES
    finally:
        # Restore commands.py's real constants before reloading server.py back to normal, so a
        # later test (in this file or any other) never observes the canary flag or a server
        # module reloaded against patched data.
        monkeypatch.undo()
        importlib.reload(server)
