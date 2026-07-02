from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Any


class CommandBuildError(ValueError):
    """Raised when MCP tool arguments cannot be mapped to a CLI command."""


@dataclass(frozen=True)
class BuiltCommand:
    job_type: str
    argv: list[str]
    project: str | None
    epub: str | None
    metadata: dict[str, Any]


PROJECT_OPTIONS = (
    ("epub", "--epub"),
    ("project", "--project"),
    ("chapters", "--chapters"),
    ("chapter", "--chapter"),
    ("offset", "--offset"),
    ("limit", "--limit"),
)

COMMON_RUN_OPTIONS = (
    ("provider", "--provider"),
    ("model", "--model"),
    ("api_key", "--api-key"),
    ("api_key_file", "--api-key-file"),
    ("all_keys", "--all-keys"),
    ("workers", "--workers"),
    ("rpm", "--rpm"),
    ("temperature", "--temperature"),
    ("mode", "--mode"),
    ("task_size", "--task-size"),
    ("splits", "--splits"),
    ("force_accept", "--force-accept"),
    ("json_epub", "--json-epub"),
    ("prompt_file", "--prompt-file"),
    ("glossary", "--glossary"),
    ("settings_json", "--settings-json"),
)

BOOL_OPTIONS = {
    "all_keys": "--all-keys",
    "dry_run": "--dry-run",
    "fix": "--fix",
    "force_accept": "--force-accept",
    "glossary_first": "--glossary-first",
    "json_epub": "--json-epub",
    "no_source": "--no-source",
    "strict": "--strict",
    "verbose": "--verbose",
    "write": "--write",
}

REPEATED_OPTIONS = {"api_key", "chapter", "confidences"}
CLI_PREFIX = [sys.executable, "-m", "gemini_translator.cli", "--compact"]
GLOBAL_OPTIONS = (
    ("settings_profile", "--settings-profile"),
    ("settings_dir", "--settings-dir"),
)
TRANSLATION_OPTIONS = (
    ("timeout", "--timeout"),
    ("verbose", "--verbose"),
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


def _build_glossary_generation(tool_name: str, args: dict) -> BuiltCommand:
    glossary_options = (
        ("batch_size", "--batch-size"),
        ("merge_mode", "--merge-mode"),
        ("new_terms_limit", "--new-terms-limit"),
        ("glossary_prompt_file", "--glossary-prompt-file"),
        ("timeout", "--timeout"),
        ("verbose", "--verbose"),
    )
    return _build_command(
        tool_name,
        args,
        job_type="glossary_generation",
        subcommand="glossary-generate",
        option_groups=(PROJECT_OPTIONS, COMMON_RUN_OPTIONS, glossary_options),
    )


def _build_untranslated_fix(tool_name: str, args: dict) -> BuiltCommand:
    untranslated_options = (
        ("suffix", "--suffix"),
        ("exceptions", "--exceptions"),
        ("fix_prompt_file", "--fix-prompt-file"),
        ("batch_size", "--batch-size"),
        ("max_context_chars", "--max-context-chars"),
        ("dry_run", "--dry-run"),
        ("timeout", "--timeout"),
        ("verbose", "--verbose"),
    )
    return _build_command(
        tool_name,
        args,
        job_type="untranslated_fix",
        subcommand="untranslated-fix",
        option_groups=(PROJECT_OPTIONS, COMMON_RUN_OPTIONS, untranslated_options),
    )


def _build_consistency(tool_name: str, args: dict) -> BuiltCommand:
    if args.get("write") and not args.get("fix"):
        raise CommandBuildError("write requires fix")

    consistency_options = (
        ("suffix", "--suffix"),
        ("consistency_mode", "--consistency-mode"),
        ("glossary_first", "--glossary-first"),
        ("chunk_size", "--chunk-size"),
        ("no_source", "--no-source"),
        ("fix", "--fix"),
        ("write", "--write"),
        ("confidences", "--confidences"),
    )
    return _build_command(
        tool_name,
        args,
        job_type="consistency",
        subcommand="consistency",
        option_groups=(PROJECT_OPTIONS, COMMON_RUN_OPTIONS, consistency_options),
    )


def _build_epub_build(tool_name: str, args: dict) -> BuiltCommand:
    build_options = (
        ("epub", "--epub"),
        ("project", "--project"),
        ("output", "--output"),
        ("provider", "--provider"),
        ("suffix", "--suffix"),
        ("chapter", "--chapter"),
        ("offset", "--offset"),
        ("limit", "--limit"),
        ("strict", "--strict"),
    )
    return _build_command(
        tool_name,
        args,
        job_type="epub_build",
        subcommand="build-epub",
        option_groups=(build_options,),
    )


def _build_command(
    tool_name: str,
    args: dict,
    *,
    job_type: str,
    subcommand: str,
    option_groups: tuple[tuple[tuple[str, str], ...], ...],
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


def _append_options(argv: list[str], args: dict, options: tuple[tuple[str, str], ...]) -> None:
    for key, flag in options:
        if key not in args:
            continue
        value = args[key]
        if key in BOOL_OPTIONS:
            if value:
                argv.append(flag)
        elif key in REPEATED_OPTIONS:
            for item in _as_list(value):
                if _has_value(item):
                    argv.extend([flag, str(item)])
        elif _has_value(value):
            argv.extend([flag, str(value)])


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return sorted(value)
    return [value]


def _has_value(value: Any) -> bool:
    return value is not None and value is not False and value != ""


def _metadata(tool_name: str, args: dict) -> dict[str, Any]:
    return {
        "tool": tool_name,
        "requested_chapters": args.get("chapters"),
        "chapter_filters": [str(item) for item in _as_list(args.get("chapter")) if _has_value(item)],
    }


__all__ = [
    "BOOL_OPTIONS",
    "COMMON_RUN_OPTIONS",
    "PROJECT_OPTIONS",
    "BuiltCommand",
    "CommandBuildError",
    "build_cli_command",
]
