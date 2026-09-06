from __future__ import annotations

import argparse
from dataclasses import dataclass
import sys
from typing import Any, Callable, NamedTuple

from ..cli import _add_common_project_args, _add_common_run_args
from ..utils.helpers import as_list


class CommandBuildError(ValueError):
    """Raised when MCP tool arguments cannot be mapped to a CLI command."""


@dataclass(frozen=True)
class BuiltCommand:
    job_type: str
    argv: list[str]
    project: str | None
    epub: str | None
    metadata: dict[str, Any]


class OptionSpec(NamedTuple):
    """One CLI option, as understood by both cli.py's argparse setup and the MCP argv builder.

    `kind` carries the argparse *action* semantics that `_append_options` needs to render the
    flag correctly: "bool" for a store_true/store_false switch (render the bare flag, no value),
    "repeated" for action="append" (render the flag once per item), "value" for anything else
    (render the flag once with str(value)). Deriving `kind` from the real argparse action -
    instead of separately hand-listing dests in a BOOL_OPTIONS/REPEATED_OPTIONS dict - is what
    keeps a flag's *rendering* in sync with cli.py, not just its name.

    `value_type` carries argparse's `type=` callable, when it is exactly `int` or `float`
    (`None` otherwise: no declared type, a str-ish flag, or an action - like "bool"/"repeated" -
    for which a scalar value type is not meaningful). It is not used by `_append_options`
    (values are always rendered with `str(value)`); it exists so a consumer with no hand-curated
    schema entry for a dest - see `server.py`'s `COMMON_START_PROPERTIES` - can still pick a
    reasonable JSON-schema type instead of defaulting every unknown "value" option to a bare
    string.
    """

    dest: str
    flag: str
    kind: str
    value_type: str | None = None


def _action_kind(action: argparse.Action) -> str:
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):  # noqa: SLF001
        return "bool"
    if isinstance(action, argparse._AppendAction) or action.nargs in ("*", "+"):  # noqa: SLF001
        return "repeated"
    return "value"


def _action_value_type(action: argparse.Action) -> str | None:
    if action.type is int:
        return "int"
    if action.type is float:
        return "float"
    return None


def _derive_options(register: Callable[[argparse.ArgumentParser], None]) -> tuple[OptionSpec, ...]:
    """Derive OptionSpecs straight from a cli.py argparse registration function.

    cli.py's argparse setup is the actual, executable definition of these flags. Reading dest,
    flag, action kind AND declared value type off a scratch parser built by that same function
    (instead of hand-copying a second literal table here) is what keeps MCP argv-building - and
    the MCP JSON-schema property table in server.py - in sync with cli.py: a flag added only to
    cli.py's registration function - of any action type, including store_true and append - now
    automatically renders correctly through `_append_options` instead of being silently dropped,
    or rendered as a bogus positional, when a tool call is built through the MCP layer.

    Called once per registrar, at import time, to build PROJECT_OPTIONS / COMMON_RUN_OPTIONS
    below - not re-derived on every `build_cli_command` call.
    """
    parser = argparse.ArgumentParser(add_help=False)
    register(parser)
    specs: list[OptionSpec] = []
    for action in parser._actions:  # noqa: SLF001 - argparse has no public action introspection API
        flag = next((opt for opt in action.option_strings if opt.startswith("--")), None)
        if flag is None:
            continue
        specs.append(
            OptionSpec(
                dest=action.dest,
                flag=flag,
                kind=_action_kind(action),
                value_type=_action_value_type(action),
            )
        )
    return tuple(specs)


# Computed once, at import time, directly from cli.py's own argparse registration functions -
# the single source of truth for these two option groups. Builders below reuse these constants
# instead of re-deriving them on every call.
PROJECT_OPTIONS = _derive_options(_add_common_project_args)

COMMON_RUN_OPTIONS = _derive_options(_add_common_run_args)

CLI_PREFIX = [sys.executable, "-m", "gemini_translator.cli", "--compact"]
GLOBAL_OPTIONS = (
    OptionSpec("settings_profile", "--settings-profile", "value"),
    OptionSpec("settings_dir", "--settings-dir", "value"),
)

# The full set of dests every `start_*` MCP tool schema needs to expose - PROJECT_OPTIONS and
# COMMON_RUN_OPTIONS above, plus the two global settings-scope flags. This is the single ordered
# source of truth server.py's COMMON_START_PROPERTIES is generated from: a flag added to either
# cli.py registrar flows into PROJECT_OPTIONS/COMMON_RUN_OPTIONS (see _derive_options) and from
# there into COMMON_START_OPTIONS with no matching edit needed in server.py.
COMMON_START_OPTIONS: tuple[OptionSpec, ...] = PROJECT_OPTIONS + COMMON_RUN_OPTIONS + GLOBAL_OPTIONS

TRANSLATION_OPTIONS = (
    OptionSpec("timeout", "--timeout", "value"),
    OptionSpec("verbose", "--verbose", "bool"),
)

GLOSSARY_CORRECTION_REASON = (
    "The current glossary correction flow is UI-driven and has no validated headless CLI command."
)


def build_cli_command(tool_name: str, args: dict) -> BuiltCommand:
    tool_args = dict(args or {})

    if tool_name == "start_glossary_review_or_correction":
        metadata = _metadata(tool_name, tool_args)
        metadata.update(
            {
                "unsupported_in_this_build": True,
                "reason": GLOSSARY_CORRECTION_REASON,
            }
        )
        return BuiltCommand(
            job_type="glossary_correction",
            argv=[],
            project=tool_args.get("project"),
            epub=tool_args.get("epub"),
            metadata=metadata,
        )

    builders = {
        "start_translation": _build_translation,
        "start_glossary_generation": _build_glossary_generation,
        "start_untranslated_fix": _build_untranslated_fix,
        "start_consistency_check": _build_consistency,
        "start_epub_build": _build_epub_build,
    }
    builder = builders.get(tool_name)
    if builder is None:
        raise CommandBuildError(f"Unsupported MCP tool: {tool_name}")

    _require_common_args(tool_args)
    return builder(tool_name, tool_args)


def _build_translation(tool_name: str, args: dict) -> BuiltCommand:
    return _build_command(
        tool_name,
        args,
        job_type="translation",
        subcommand="translate",
        option_groups=(PROJECT_OPTIONS, COMMON_RUN_OPTIONS, TRANSLATION_OPTIONS),
    )


_GLOSSARY_GENERATION_OPTIONS = (
    OptionSpec("batch_size", "--batch-size", "value"),
    OptionSpec("merge_mode", "--merge-mode", "value"),
    OptionSpec("new_terms_limit", "--new-terms-limit", "value"),
    OptionSpec("glossary_prompt_file", "--glossary-prompt-file", "value"),
    OptionSpec("timeout", "--timeout", "value"),
    OptionSpec("verbose", "--verbose", "bool"),
)


def _build_glossary_generation(tool_name: str, args: dict) -> BuiltCommand:
    return _build_command(
        tool_name,
        args,
        job_type="glossary_generation",
        subcommand="glossary-generate",
        option_groups=(PROJECT_OPTIONS, COMMON_RUN_OPTIONS, _GLOSSARY_GENERATION_OPTIONS),
    )


_UNTRANSLATED_FIX_OPTIONS = (
    OptionSpec("suffix", "--suffix", "value"),
    OptionSpec("exceptions", "--exceptions", "value"),
    OptionSpec("fix_prompt_file", "--fix-prompt-file", "value"),
    OptionSpec("batch_size", "--batch-size", "value"),
    OptionSpec("max_context_chars", "--max-context-chars", "value"),
    OptionSpec("dry_run", "--dry-run", "bool"),
    OptionSpec("timeout", "--timeout", "value"),
    OptionSpec("verbose", "--verbose", "bool"),
)


def _build_untranslated_fix(tool_name: str, args: dict) -> BuiltCommand:
    return _build_command(
        tool_name,
        args,
        job_type="untranslated_fix",
        subcommand="untranslated-fix",
        option_groups=(PROJECT_OPTIONS, COMMON_RUN_OPTIONS, _UNTRANSLATED_FIX_OPTIONS),
    )


_CONSISTENCY_OPTIONS = (
    OptionSpec("suffix", "--suffix", "value"),
    OptionSpec("consistency_mode", "--consistency-mode", "value"),
    OptionSpec("glossary_first", "--glossary-first", "bool"),
    OptionSpec("chunk_size", "--chunk-size", "value"),
    OptionSpec("no_source", "--no-source", "bool"),
    OptionSpec("fix", "--fix", "bool"),
    OptionSpec("write", "--write", "bool"),
    OptionSpec("confidences", "--confidences", "repeated"),
)


def _build_consistency(tool_name: str, args: dict) -> BuiltCommand:
    if args.get("write") and not args.get("fix"):
        raise CommandBuildError("write requires fix")

    return _build_command(
        tool_name,
        args,
        job_type="consistency",
        subcommand="consistency",
        option_groups=(PROJECT_OPTIONS, COMMON_RUN_OPTIONS, _CONSISTENCY_OPTIONS),
    )


_EPUB_BUILD_OPTIONS = (
    OptionSpec("epub", "--epub", "value"),
    OptionSpec("project", "--project", "value"),
    OptionSpec("output", "--output", "value"),
    OptionSpec("provider", "--provider", "value"),
    OptionSpec("suffix", "--suffix", "value"),
    OptionSpec("chapter", "--chapter", "repeated"),
    OptionSpec("offset", "--offset", "value"),
    OptionSpec("limit", "--limit", "value"),
    OptionSpec("strict", "--strict", "bool"),
)


def _build_epub_build(tool_name: str, args: dict) -> BuiltCommand:
    return _build_command(
        tool_name,
        args,
        job_type="epub_build",
        subcommand="build-epub",
        option_groups=(_EPUB_BUILD_OPTIONS,),
    )


def _build_command(
    tool_name: str,
    args: dict,
    *,
    job_type: str,
    subcommand: str,
    option_groups: tuple[tuple[OptionSpec, ...], ...],
) -> BuiltCommand:
    argv = [*_base_argv(args), subcommand]
    for options in option_groups:
        _append_options(argv, args, options)
    return BuiltCommand(
        job_type=job_type,
        argv=argv,
        project=str(args["project"]),
        epub=str(args["epub"]),
        metadata=_metadata(tool_name, args),
    )


def _base_argv(args: dict) -> list[str]:
    if _has_value(args.get("settings_profile")) and _has_value(args.get("settings_dir")):
        raise CommandBuildError("settings_profile and settings_dir are mutually exclusive")
    argv = list(CLI_PREFIX)
    _append_options(argv, args, GLOBAL_OPTIONS)
    return argv


def _require_common_args(args: dict) -> None:
    if not args.get("epub"):
        raise CommandBuildError("epub is required")
    if not args.get("project"):
        raise CommandBuildError("project is required")


def _append_options(argv: list[str], args: dict, options: tuple[OptionSpec, ...]) -> None:
    for spec in options:
        if spec.dest not in args:
            continue
        value = args[spec.dest]
        if spec.kind == "bool":
            if value:
                argv.append(spec.flag)
        elif spec.kind == "repeated":
            for item in as_list(value, sort_sets=True):
                if _has_value(item):
                    argv.extend([spec.flag, str(item)])
        elif _has_value(value):
            argv.extend([spec.flag, str(value)])


def _has_value(value: Any) -> bool:
    return value is not None and value is not False and value != ""


def _metadata(tool_name: str, args: dict) -> dict[str, Any]:
    return {
        "tool": tool_name,
        "requested_chapters": args.get("chapters"),
        "chapter_filters": [str(item) for item in as_list(args.get("chapter"), sort_sets=True) if _has_value(item)],
    }


__all__ = [
    "COMMON_RUN_OPTIONS",
    "COMMON_START_OPTIONS",
    "PROJECT_OPTIONS",
    "BuiltCommand",
    "CommandBuildError",
    "OptionSpec",
    "build_cli_command",
]
