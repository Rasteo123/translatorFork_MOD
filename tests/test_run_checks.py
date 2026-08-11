import sys
from types import SimpleNamespace

import tools.run_checks as run_checks


def test_run_checks_runs_default_pytest_once(monkeypatch):
    commands = []

    def fake_run(command, cwd, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(run_checks.subprocess, "run", fake_run)

    assert run_checks.main([]) == 0
    assert commands == [
        [sys.executable, "-m", "gemini_translator.scripts.check_release_metadata"],
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            ".",
            "--select",
            run_checks.RUFF_RUNTIME_RULES,
        ],
        [sys.executable, "-m", "pytest", "-q"],
    ]


def test_run_checks_keeps_explicit_pytest_args_in_one_process(monkeypatch):
    commands = []

    def fake_run(command, cwd, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(run_checks.subprocess, "run", fake_run)

    assert run_checks.main(["--", "tests/test_updater.py", "-k", "manifest"]) == 0
    assert commands == [
        [sys.executable, "-m", "gemini_translator.scripts.check_release_metadata"],
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            ".",
            "--select",
            run_checks.RUFF_RUNTIME_RULES,
        ],
        [sys.executable, "-m", "pytest", "-q", "tests/test_updater.py", "-k", "manifest"],
    ]


def test_run_checks_emits_github_annotation_with_failure_tail(monkeypatch, capsys):
    def fake_run(command, cwd, **_kwargs):
        return SimpleNamespace(returncode=2, stdout="line 1\nline 2\nline 3\n")

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(run_checks.subprocess, "run", fake_run)

    assert run_checks._run("pytest", [sys.executable, "-m", "pytest"]) == 2

    captured = capsys.readouterr()
    assert "[checks] pytest failed with exit code 2" in captured.err
    assert "::error file=tools/run_checks.py,line=1::" in captured.out
    assert "pytest failed with exit code 2%0Aline 1%0Aline 2%0Aline 3" in captured.out


def test_run_checks_isolates_windows_child_process_group(monkeypatch):
    monkeypatch.setattr(run_checks.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)

    assert run_checks._subprocess_creationflags("nt") == 512
