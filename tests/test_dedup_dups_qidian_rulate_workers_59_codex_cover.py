# -*- coding: utf-8 -*-
"""Тесты для устранения дубля запуска Codex-подпроцесса обложки.

Находка dups-qidian_rulate_workers-59 /
qidian-tools/design/5-codex-cover-worker-run-duplica: CodexCoverGenerateWorker.run
и CodexCoverTranslateWorker.run дублировали ~40 строк запуска subprocess.run,
логирования stdout/stderr, проверки returncode и поиска сгенерированного
файла через _find_generated_cover.

Канонизация: общая логика вынесена в qidian_rulate.workers._execute_codex_cover_command,
принимающую только различающиеся между воркерами данные (action_label для
текста сообщения об отсутствующем файле).

Тест test_both_codex_cover_workers_route_through_shared_executor обязан
ПАДАТЬ до рефакторинга (каждый run() вызывал subprocess.run напрямую, общей
функции не существовало) и ПРОХОДИТЬ после.
"""
import subprocess

import pytest

from qidian_rulate import workers
from qidian_rulate.workers import CodexCoverGenerateWorker, CodexCoverTranslateWorker


def _patch_common(monkeypatch, tmp_path):
    monkeypatch.setattr(workers, "_build_codex_cover_exec_command", lambda *_a, **_k: ["codex"])
    monkeypatch.setattr(workers, "_append_codex_prompt", lambda *_a, **_k: None)
    monkeypatch.setattr(workers, "_project_root", lambda: tmp_path)


# --- Характеризационные тесты канонической _execute_codex_cover_command ---


def test_execute_codex_cover_command_returns_generated_path_on_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workers.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(["codex"], 0, "ok-stdout", ""),
    )
    expected = tmp_path / "cover.png"
    monkeypatch.setattr(workers, "_find_generated_cover", lambda *_a, **_k: expected)
    logs = []

    result = workers._execute_codex_cover_command(
        ["codex"],
        project_root=tmp_path,
        output_dir=tmp_path,
        target_path=tmp_path / "target.png",
        log=lambda level, message: logs.append((level, message)),
        action_label="обложки",
    )

    assert result == expected
    assert any("ok-stdout" in message for _level, message in logs)


def test_execute_codex_cover_command_raises_on_nonzero_returncode(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workers.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(["codex"], 1, "", "boom"),
    )
    monkeypatch.setattr(workers, "_find_generated_cover", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="код"):
        workers._execute_codex_cover_command(
            ["codex"],
            project_root=tmp_path,
            output_dir=tmp_path,
            target_path=tmp_path / "target.png",
            log=lambda *_a, **_k: None,
            action_label="обложки",
        )


def test_execute_codex_cover_command_missing_file_message_uses_action_label(monkeypatch, tmp_path):
    monkeypatch.setattr(
        workers.subprocess,
        "run",
        lambda *_a, **_k: subprocess.CompletedProcess(["codex"], 0, "", ""),
    )
    monkeypatch.setattr(workers, "_find_generated_cover", lambda *_a, **_k: None)

    with pytest.raises(RuntimeError, match="переведённой обложки"):
        workers._execute_codex_cover_command(
            ["codex"],
            project_root=tmp_path,
            output_dir=tmp_path,
            target_path=tmp_path / "target.png",
            log=lambda *_a, **_k: None,
            action_label="переведённой обложки",
        )


# --- Тест-маршрутизация: оба воркера обязаны звать общую функцию ---


def test_both_codex_cover_workers_route_through_shared_executor(monkeypatch, tmp_path):
    _patch_common(monkeypatch, tmp_path)
    calls = []

    def fake_execute(command, *, project_root, output_dir, target_path, log, action_label):
        calls.append(action_label)
        return tmp_path / f"{action_label}.png"

    monkeypatch.setattr(workers, "_execute_codex_cover_command", fake_execute)

    def fail_subprocess_run(*_a, **_k):
        raise AssertionError(
            "run() должен вызывать subprocess.run только внутри _execute_codex_cover_command"
        )

    monkeypatch.setattr(workers.subprocess, "run", fail_subprocess_run)

    generate_worker = CodexCoverGenerateWorker("prompt", title_ru="Title", output_dir=tmp_path)
    generate_worker.log = lambda *_a, **_k: None
    generate_worker.run()

    translate_worker = CodexCoverTranslateWorker(
        "",
        "Title",
        source_image_data=_png_bytes(),
        output_dir=tmp_path,
    )
    translate_worker.log = lambda *_a, **_k: None
    translate_worker.run()

    assert len(calls) == 2, f"ожидались вызовы общей функции обоими воркерами, получено: {calls}"


def _png_bytes() -> bytes:
    from PyQt6.QtGui import QImage
    import tempfile
    from pathlib import Path

    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(0xFF112233)
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "src.png"
        image.save(str(path), "PNG")
        return path.read_bytes()
