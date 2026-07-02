import sys

import pytest

from gemini_translator.mcp.commands import CommandBuildError, build_cli_command


def test_build_translation_command_uses_compact_cli():
    command = build_cli_command(
        "start_translation",
        {
            "epub": "/books/book.epub",
            "project": "/books/project",
            "provider": "gemini",
            "model": "Gemini 2.5 Flash",
            "chapters": "pending",
            "chapter": ["OEBPS/ch1.xhtml"],
            "workers": 2,
            "force_accept": True,
        },
    )

    assert command.job_type == "translation"
    assert command.project == "/books/project"
    assert command.epub == "/books/book.epub"
    assert command.argv[:4] == [sys.executable, "-m", "gemini_translator.cli", "--compact"]
    assert command.argv[4:] == [
        "translate",
        "--epub",
        "/books/book.epub",
        "--project",
        "/books/project",
        "--chapters",
        "pending",
        "--chapter",
        "OEBPS/ch1.xhtml",
        "--provider",
        "gemini",
        "--model",
        "Gemini 2.5 Flash",
        "--workers",
        "2",
        "--force-accept",
    ]


def test_build_untranslated_fix_defaults_to_writing_files():
    command = build_cli_command(
        "start_untranslated_fix",
        {
            "epub": "/books/book.epub",
            "project": "/books/project",
            "batch_size": 25,
        },
    )

    assert "untranslated-fix" in command.argv
    assert "--dry-run" not in command.argv
    assert "--batch-size" in command.argv
    assert "25" in command.argv


@pytest.mark.parametrize(
    ("key", "flag", "value"),
    [
        ("settings_profile", "--settings-profile", "mcp-tests"),
        ("settings_dir", "--settings-dir", "/tmp/mcp-settings"),
    ],
)
def test_build_command_emits_global_settings_before_subcommand(key, flag, value):
    command = build_cli_command(
        "start_translation",
        {
            "epub": "/books/book.epub",
            "project": "/books/project",
            key: value,
        },
    )

    assert command.argv[:6] == [
        sys.executable,
        "-m",
        "gemini_translator.cli",
        "--compact",
        flag,
        value,
    ]
    assert command.argv[6] == "translate"


def test_build_translation_command_supports_verbose():
    command = build_cli_command(
        "start_translation",
        {
            "epub": "/books/book.epub",
            "project": "/books/project",
            "verbose": True,
        },
    )

    assert "--verbose" in command.argv


def test_build_translation_command_supports_timeout_and_parser_accepts_it():
    from gemini_translator.cli import build_parser

    command = build_cli_command(
        "start_translation",
        {
            "epub": "/books/book.epub",
            "project": "/books/project",
            "timeout": 120,
        },
    )

    assert "--timeout" in command.argv
    timeout_index = command.argv.index("--timeout")
    assert command.argv[timeout_index + 1] == "120"

    parsed = build_parser().parse_args(command.argv[4:])
    assert parsed.timeout == 120


def test_build_command_rejects_mutually_exclusive_settings_scope():
    with pytest.raises(CommandBuildError, match="settings_profile"):
        build_cli_command(
            "start_translation",
            {
                "epub": "/books/book.epub",
                "project": "/books/project",
                "settings_profile": "mcp-tests",
                "settings_dir": "/tmp/mcp-settings",
            },
        )


def test_build_consistency_write_requires_fix():
    with pytest.raises(CommandBuildError, match="write requires fix"):
        build_cli_command(
            "start_consistency_check",
            {
                "epub": "/books/book.epub",
                "project": "/books/project",
                "write": True,
                "fix": False,
            },
        )


def test_build_epub_command_supports_output_and_strict():
    command = build_cli_command(
        "start_epub_build",
        {
            "epub": "/books/book.epub",
            "project": "/books/project",
            "output": "/books/out.epub",
            "strict": True,
        },
    )

    assert command.job_type == "epub_build"
    assert command.argv[-3:] == ["--output", "/books/out.epub", "--strict"]


def test_unknown_tool_is_rejected():
    with pytest.raises(CommandBuildError, match="Unsupported MCP tool"):
        build_cli_command("not_a_tool", {})


def test_unsupported_glossary_correction_metadata_uses_common_shape():
    command = build_cli_command(
        "start_glossary_review_or_correction",
        {
            "epub": "/books/book.epub",
            "project": "/books/project",
            "chapters": "translated",
            "chapter": ["OEBPS/ch1.xhtml", "OEBPS/ch2.xhtml"],
        },
    )

    assert command.metadata["tool"] == "start_glossary_review_or_correction"
    assert command.metadata["unsupported_in_this_build"] is True
    assert command.metadata["requested_chapters"] == "translated"
    assert command.metadata["chapter_filters"] == ["OEBPS/ch1.xhtml", "OEBPS/ch2.xhtml"]
