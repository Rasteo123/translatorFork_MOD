"""Characterization + mechanism tests for finding-mcp-bench-cli_design_3-cli-mcp-option-vocabulary-trip.

The bug: PROJECT_OPTIONS / COMMON_RUN_OPTIONS in gemini_translator/mcp/commands.py used to be a
hand-copied second list of the flags registered by cli.py's _add_common_project_args /
_add_common_run_args, and a *third* hand-copied membership list (BOOL_OPTIONS / REPEATED_OPTIONS)
described how each dest should render into argv. A flag added only to cli.py's argparse
functions would silently never reach argv when the same tool was invoked through the MCP layer
(build_cli_command / _append_options only iterated over the hardcoded tables), even though the
MCP JSON schema's additionalProperties: True happily accepts the extra property. Worse: even once
the flag *did* reach argv, a store_true/append action rendered wrong (e.g. "--flag True" or
"--flag "['a', 'b']""), because BOOL_OPTIONS/REPEATED_OPTIONS membership was a separate guess, not
read off the actual argparse action.

The fix: `_derive_options` reads (dest, flag, kind) straight off a scratch parser built by cli.py's
own registration function, where `kind` ("bool" / "repeated" / "value") comes from the real
argparse action type (store_true/store_false -> bool, action="append" -> repeated, everything else
-> value). PROJECT_OPTIONS / COMMON_RUN_OPTIONS are computed once, at import time, directly from
`_add_common_project_args` / `_add_common_run_args` - cli.py is the single source of truth for
both the *names* and the *rendering* of these two option groups. Builders reuse these two module
constants instead of re-deriving them (or re-declaring bool/append membership) at every call site.

NOTE for maintainers: the two `test_*_match_cli_common_*_args` tests below are a snapshot of
today's cli.py flags, not a contract - adding a legitimate flag to `_add_common_project_args` or
`_add_common_run_args` is expected to require updating the literal tuple here too. That is not a
regression; it just means cli.py grew a flag and this snapshot needs a one-line update to match.
"""

from __future__ import annotations

import gemini_translator.cli as cli
import gemini_translator.mcp.commands as commands


def test_project_options_match_cli_common_project_args():
    """Snapshot of today's cli.py flags, not a contract - see module docstring.

    `offset`/`limit` carry a 4th `value_type="int"` element because cli.py declares them with
    `type=int` - see OptionSpec's docstring for what that field is for.
    """
    assert commands.PROJECT_OPTIONS == (
        commands.OptionSpec("epub", "--epub", "value"),
        commands.OptionSpec("project", "--project", "value"),
        commands.OptionSpec("chapters", "--chapters", "value"),
        commands.OptionSpec("chapter", "--chapter", "repeated"),
        commands.OptionSpec("offset", "--offset", "value", "int"),
        commands.OptionSpec("limit", "--limit", "value", "int"),
    )


def test_common_run_options_match_cli_common_run_args():
    """Snapshot of today's cli.py flags, not a contract - see module docstring.

    `workers`/`rpm`/`task_size`/`splits` carry `value_type="int"` and `temperature` carries
    `value_type="float"` because cli.py declares each with the matching `type=`.
    """
    assert commands.COMMON_RUN_OPTIONS == (
        commands.OptionSpec("provider", "--provider", "value"),
        commands.OptionSpec("model", "--model", "value"),
        commands.OptionSpec("api_key", "--api-key", "repeated"),
        commands.OptionSpec("api_key_file", "--api-key-file", "value"),
        commands.OptionSpec("all_keys", "--all-keys", "bool"),
        commands.OptionSpec("workers", "--workers", "value", "int"),
        commands.OptionSpec("rpm", "--rpm", "value", "int"),
        commands.OptionSpec("temperature", "--temperature", "value", "float"),
        commands.OptionSpec("mode", "--mode", "value"),
        commands.OptionSpec("task_size", "--task-size", "value", "int"),
        commands.OptionSpec("splits", "--splits", "value", "int"),
        commands.OptionSpec("force_accept", "--force-accept", "bool"),
        commands.OptionSpec("json_epub", "--json-epub", "bool"),
        commands.OptionSpec("prompt_file", "--prompt-file", "value"),
        commands.OptionSpec("glossary", "--glossary", "value"),
        commands.OptionSpec("settings_json", "--settings-json", "value"),
    )


def test_common_start_options_is_the_concatenation_of_the_three_option_groups():
    """COMMON_START_OPTIONS (consumed by server.py to generate COMMON_START_PROPERTIES) must be
    exactly PROJECT_OPTIONS + COMMON_RUN_OPTIONS + GLOBAL_OPTIONS - not an independently
    maintained list that merely happens, today, to agree with them."""
    assert commands.COMMON_START_OPTIONS == (
        commands.PROJECT_OPTIONS + commands.COMMON_RUN_OPTIONS + commands.GLOBAL_OPTIONS
    )


def test_derive_options_captures_int_value_type():
    def register(parser):
        parser.add_argument("--probe-int", type=int)

    specs = commands._derive_options(register)
    assert specs == (commands.OptionSpec("probe_int", "--probe-int", "value", "int"),)


def test_derive_options_captures_float_value_type():
    def register(parser):
        parser.add_argument("--probe-float", type=float)

    specs = commands._derive_options(register)
    assert specs == (commands.OptionSpec("probe_float", "--probe-float", "value", "float"),)


def test_project_options_is_actually_derived_from_cli_registrar_not_a_hand_copy():
    """Recomputing straight from cli._add_common_project_args right now must match the stored
    module constant exactly - proving PROJECT_OPTIONS is not an independently maintained copy
    that merely happens, today, to agree with cli.py."""
    assert commands.PROJECT_OPTIONS == commands._derive_options(cli._add_common_project_args)


def test_common_run_options_is_actually_derived_from_cli_registrar_not_a_hand_copy():
    assert commands.COMMON_RUN_OPTIONS == commands._derive_options(cli._add_common_run_args)


def test_derive_options_marks_store_true_flag_as_bool():
    def register(parser):
        parser.add_argument("--probe-bool", action="store_true")

    specs = commands._derive_options(register)
    assert specs == (commands.OptionSpec("probe_bool", "--probe-bool", "bool"),)


def test_derive_options_marks_append_flag_as_repeated():
    def register(parser):
        parser.add_argument("--probe-append", action="append")

    specs = commands._derive_options(register)
    assert specs == (commands.OptionSpec("probe_append", "--probe-append", "repeated"),)


def test_derive_options_marks_plain_flag_as_value():
    def register(parser):
        parser.add_argument("--probe-value")

    specs = commands._derive_options(register)
    assert specs == (commands.OptionSpec("probe_value", "--probe-value", "value"),)


def test_append_options_renders_bool_and_repeated_correctly_from_a_derived_group():
    """Reproduces the reviewer's exact failure scenario against a hand-built canary registrar:
    before this fix, a store_true flag rendered as the bogus pair '--probe-bool True' and an
    append flag rendered as a single stringified list '--probe-append "['a', 'b']"' instead of
    repeating the flag once per item - a real CLI parser would reject the former as an
    unrecognized positional and misparse the latter as one literal string.
    """

    def register(parser):
        parser.add_argument("--probe-bool", action="store_true")
        parser.add_argument("--probe-append", action="append")
        parser.add_argument("--probe-value")

    specs = commands._derive_options(register)
    argv: list[str] = []
    commands._append_options(
        argv,
        {"probe_bool": True, "probe_append": ["a", "b"], "probe_value": "x"},
        specs,
    )
    assert argv == [
        "--probe-bool",
        "--probe-append",
        "a",
        "--probe-append",
        "b",
        "--probe-value",
        "x",
    ]


def test_append_options_omits_falsy_bool_flag_from_a_derived_group():
    def register(parser):
        parser.add_argument("--probe-bool", action="store_true")

    specs = commands._derive_options(register)
    argv: list[str] = []
    commands._append_options(argv, {"probe_bool": False}, specs)
    assert argv == []


def test_derive_options_not_recomputed_on_every_build_call(monkeypatch):
    """PROJECT_OPTIONS / COMMON_RUN_OPTIONS must be computed once (at import time) and reused by
    every builder - not re-derived (re-parsing cli.py's registrars from scratch) on every
    `build_cli_command` call, which would both waste work and leave dead exported constants that
    nothing actually uses at build time.
    """
    calls: list[object] = []
    original = commands._derive_options

    def counting(register):
        calls.append(register)
        return original(register)

    monkeypatch.setattr(commands, "_derive_options", counting)

    base_args = {"epub": "/books/book.epub", "project": "/books/project"}
    commands.build_cli_command("start_translation", dict(base_args))
    commands.build_cli_command("start_glossary_generation", dict(base_args))
    commands.build_cli_command("start_untranslated_fix", dict(base_args))
    commands.build_cli_command("start_consistency_check", dict(base_args))
    commands.build_cli_command("start_epub_build", dict(base_args))

    assert calls == []


def test_new_flag_added_only_in_cli_common_run_args_would_reach_mcp_argv_correctly():
    """Mechanism-level guarantee for the finding's core bug, without touching the already-computed
    module constants (see test_derive_options_not_recomputed_on_every_build_call above for why
    patching the imported cli functions after import can no longer move PROJECT_OPTIONS /
    COMMON_RUN_OPTIONS): re-deriving straight from a registrar that adds one more flag - the way
    cli._add_common_run_args would look after a future edit - proves that flag reaches argv, with
    the right rendering, automatically.
    """

    def future_add_common_run_args(parser):
        cli._add_common_run_args(parser)
        parser.add_argument("--dedup-probe-flag", help="test-only canary flag")

    specs = commands._derive_options(future_add_common_run_args)
    by_dest = {spec.dest: spec for spec in specs}
    assert by_dest["dedup_probe_flag"] == commands.OptionSpec("dedup_probe_flag", "--dedup-probe-flag", "value")

    argv: list[str] = []
    commands._append_options(argv, {"dedup_probe_flag": "probe-value"}, specs)
    assert argv == ["--dedup-probe-flag", "probe-value"]
