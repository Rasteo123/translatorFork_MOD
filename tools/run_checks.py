from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FAILURE_TAIL_LINES = 80
RUFF_RUNTIME_RULES = "F821,F822,F823,F601,F602,F631,F632,E9"


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(errors="backslashreplace")


def _github_escape(message: str) -> str:
    return (
        str(message)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _output_tail(output: str) -> str:
    lines = str(output or "").splitlines()
    if len(lines) <= FAILURE_TAIL_LINES:
        return "\n".join(lines)
    return "\n".join(lines[-FAILURE_TAIL_LINES:])


def _emit_github_error(message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    print(f"::error file=tools/run_checks.py,line=1::{_github_escape(message)}", flush=True)


def _subprocess_creationflags(platform_name: str | None = None) -> int:
    platform_name = os.name if platform_name is None else platform_name
    if platform_name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def _run(label: str, command: list[str]) -> int:
    print(f"[checks] {label}", flush=True)
    run_kwargs = {
        "cwd": PROJECT_ROOT,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    creationflags = _subprocess_creationflags()
    if creationflags:
        run_kwargs["creationflags"] = creationflags
    completed = subprocess.run(command, **run_kwargs)
    output = completed.stdout or ""
    if output:
        print(output, end="" if output.endswith("\n") else "\n", flush=True)
    if completed.returncode:
        summary = f"{label} failed with exit code {completed.returncode}"
        print(f"[checks] {summary}", file=sys.stderr)
        output_summary = _output_tail(output)
        _emit_github_error(f"{summary}\n{output_summary}" if output_summary else summary)
    return completed.returncode


def _env_release_mode_enabled() -> bool:
    value = (
        os.environ.get("GT_RUN_CHECKS_RELEASE")
        or os.environ.get("GT_RELEASE_METADATA_MODE")
        or ""
    ).strip().lower()
    return value in {"1", "true", "yes", "release", "strict"}


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(description="Run project smoke checks.")
    parser.add_argument(
        "--release",
        action="store_true",
        help="Run release metadata checks in strict release mode.",
    )
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

    release_metadata_command = [sys.executable, "-m", "gemini_translator.scripts.check_release_metadata"]
    if args.release or _env_release_mode_enabled():
        release_metadata_command.append("--release")

    checks = [
        (
            "release metadata",
            release_metadata_command,
        )
    ]

    if not args.skip_tests:
        checks.append(
            (
                "ruff runtime safety",
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    ".",
                    "--select",
                    RUFF_RUNTIME_RULES,
                ],
            )
        )
        pytest_args = list(args.pytest_args)
        if pytest_args[:1] == ["--"]:
            pytest_args = pytest_args[1:]
        if pytest_args:
            checks.append(("pytest", [sys.executable, "-m", "pytest", "-q", *pytest_args]))
        else:
            checks.append(("pytest", [sys.executable, "-m", "pytest", "-q"]))

    for label, command in checks:
        exit_code = _run(label, command)
        if exit_code:
            return exit_code

    print("[checks] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
