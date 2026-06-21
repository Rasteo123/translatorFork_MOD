from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(label: str, command: list[str]) -> int:
    print(f"[checks] {label}", flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT)
    if completed.returncode:
        print(f"[checks] {label} failed with exit code {completed.returncode}", file=sys.stderr)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run project smoke checks.")
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Run only checks that do not require pytest or project test dependencies.",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Extra arguments forwarded to pytest. Prefix with -- to separate them.",
    )
    args = parser.parse_args(argv)

    checks = [
        (
            "release metadata",
            [sys.executable, "-m", "gemini_translator.scripts.check_release_metadata"],
        )
    ]

    if not args.skip_tests:
        pytest_args = list(args.pytest_args)
        if pytest_args[:1] == ["--"]:
            pytest_args = pytest_args[1:]
        checks.append(("pytest", [sys.executable, "-m", "pytest", "-q", *pytest_args]))

    for label, command in checks:
        exit_code = _run(label, command)
        if exit_code:
            return exit_code

    print("[checks] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
