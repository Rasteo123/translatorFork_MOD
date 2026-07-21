import runpy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_standalone_markdown_viewer_resolves_help_path():
    namespace = runpy.run_path(
        str(PROJECT_ROOT / "gemini_translator" / "utils" / "markdown_viewer.py")
    )

    assert namespace["HELP_FILE_PATH"] == PROJECT_ROOT / "README.md"
