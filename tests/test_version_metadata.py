from pathlib import Path
import importlib.util


def _version_tuple(value: str) -> tuple[int, ...]:
    base = value.strip().removeprefix("v").split("-", 1)[0]
    return tuple(int(part) for part in base.split("."))


def test_main_uses_shared_app_version():
    import gemini_translator
    from gemini_translator.version import APP_VERSION, __version__

    main_path = Path(__file__).resolve().parents[1] / "main.py"
    spec = importlib.util.spec_from_file_location("translatorfork_main_version_check", main_path)
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    assert APP_VERSION == f"V {__version__}"
    assert gemini_translator.APP_VERSION == APP_VERSION
    assert gemini_translator.__version__ == __version__
    assert main.APP_VERSION == APP_VERSION


def test_app_version_is_not_older_than_latest_release_notes():
    from gemini_translator.version import __version__

    release_versions = [
        _version_tuple(path.stem.removeprefix("RELEASE_NOTES_"))
        for path in Path(__file__).resolve().parents[1].glob("RELEASE_NOTES_v*.md")
    ]

    assert release_versions
    assert _version_tuple(__version__) >= max(release_versions)
