from __future__ import annotations

import re
import sys
from pathlib import Path

from gemini_translator.version import APP_VERSION, __version__


RELEASE_NOTES_PATTERN = re.compile(r"^RELEASE_NOTES_v(?P<version>\d+\.\d+\.\d+)\.md$")


def _version_tuple(value: str) -> tuple[int, ...]:
    base_version = str(value).strip().removeprefix("v").split("-", 1)[0]
    parts = base_version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Expected semantic version with three parts, got: {value!r}")
    return tuple(int(part) for part in parts)


def _release_note_versions(project_root: Path) -> list[tuple[int, ...]]:
    versions = []
    for path in project_root.glob("RELEASE_NOTES_v*.md"):
        match = RELEASE_NOTES_PATTERN.match(path.name)
        if match:
            versions.append(_version_tuple(match.group("version")))
    return sorted(versions)


def check_release_metadata(project_root: Path | None = None) -> list[str]:
    root = project_root or Path(__file__).resolve().parents[2]
    errors = []

    if APP_VERSION != f"V {__version__}":
        errors.append(
            f"APP_VERSION mismatch: expected 'V {__version__}', got {APP_VERSION!r}"
        )

    release_versions = _release_note_versions(root)
    if not release_versions:
        errors.append("No RELEASE_NOTES_v*.md files found.")
        return errors

    current_version = _version_tuple(__version__)
    latest_release_version = max(release_versions)
    if current_version < latest_release_version:
        latest = ".".join(str(part) for part in latest_release_version)
        errors.append(
            f"Application version {__version__!r} is older than latest release notes v{latest}."
        )

    return errors


def main() -> int:
    errors = check_release_metadata()
    if errors:
        for error in errors:
            print(f"[release-metadata] {error}", file=sys.stderr)
        return 1
    print(f"[release-metadata] OK: {APP_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
