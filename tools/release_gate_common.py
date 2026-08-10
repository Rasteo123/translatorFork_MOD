# -*- coding: utf-8 -*-
"""Общие проверки релизных инструментов (только stdlib)."""
import re
import sys

_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_FINAL_RE = re.compile(r"^\d+\.\d+\.\d+$")
_HEX40_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class GateError(Exception):
    pass


def fail(message: str) -> None:
    raise GateError(message)


def read_version(version_file: str) -> str:
    try:
        with open(version_file, "r", encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        fail(f"cannot read version file {version_file}: {e}")
    match = _VERSION_RE.search(source)
    if not match:
        fail(f"__version__ not found in {version_file}")
    return match.group(1)


def require_final_version(version: str) -> None:
    if not _FINAL_RE.match(version):
        fail(f"version {version!r} is not a final X.Y.Z release version")


def require_tag_matches(tag: str, version: str) -> None:
    if tag != f"v{version}":
        fail(f"tag {tag!r} does not match v{version} from version file")


def require_commit(commit: str) -> None:
    if not _HEX40_RE.match(commit or ""):
        fail(f"commit {commit!r} is not a 40-hex SHA")


def run_gate(fn) -> int:
    """Выполняет fn(); GateError печатается с префиксом RELEASE-GATE:."""
    try:
        fn()
    except GateError as e:
        print(f"RELEASE-GATE: {e}", file=sys.stderr)
        return 1
    return 0
