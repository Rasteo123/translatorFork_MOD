"""Кластер дедупликации cluster-19: load_job / load_gui_ai_task.

Обе функции читали JSON-файл задачи с диска и парсили его через
собственный ``from_dict`` — идентичная процедура под разными именами
в gemini_translator/mcp/jobs.py и gemini_translator/mcp/ai_bridge.py.

Эти тесты фиксируют:
(а) поведение канонической реализации ``load_task_json`` в
    gemini_translator/mcp/_json_io.py (характеризационные тесты —
    какие крайние случаи различали копии: отсутствующий файл,
    битый JSON, успешный парсинг через переданный from_dict);
(б) маршрутизацию — что load_job и load_gui_ai_task действительно
    делегируют чтение в load_task_json, а не хранят свою копию
    логики чтения+json.loads.
"""

from __future__ import annotations

import json

import pytest

from gemini_translator.mcp import _json_io
from gemini_translator.mcp import ai_bridge as ai_bridge_module
from gemini_translator.mcp import jobs as jobs_module
from gemini_translator.mcp.ai_bridge import GuiAiTask, gui_ai_task_path, load_gui_ai_task, save_gui_ai_task
from gemini_translator.mcp.jobs import JobRecord, job_path, load_job, save_job


# --- (а) характеризационные тесты канонической реализации ------------------


def test_load_task_json_parses_payload_via_from_dict(tmp_path):
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"value": 42}), encoding="utf-8")

    result = _json_io.load_task_json(path, lambda payload: payload["value"] * 2)

    assert result == 84


def test_load_task_json_missing_file_propagates_file_not_found(tmp_path):
    missing = tmp_path / "does_not_exist.json"

    with pytest.raises(FileNotFoundError):
        _json_io.load_task_json(missing, lambda payload: payload)


def test_load_task_json_invalid_json_propagates_decode_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        _json_io.load_task_json(path, lambda payload: payload)


def test_load_task_json_does_not_swallow_from_dict_errors(tmp_path):
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"id": "x"}), encoding="utf-8")

    def boom(payload):
        raise KeyError("status")

    with pytest.raises(KeyError):
        _json_io.load_task_json(path, boom)


# --- (б) тест-маршрутизация: бывшие места вызова обязаны идти через ---------
# --- каноническую load_task_json, а не через собственную копию логики ------


def test_load_job_routes_through_canonical_load_task_json(tmp_path, monkeypatch):
    job = JobRecord(
        id="job_test",
        type="translate",
        status="queued",
        created_at="2026-01-01T00:00:00+00:00",
        argv=["--foo"],
    )
    save_job(tmp_path, job)

    calls = []
    original = _json_io.load_task_json

    def spy(path, from_dict):
        calls.append(path)
        return original(path, from_dict)

    # Патчим имя, привязанное в jobs.py через `from ._json_io import
    # load_task_json` — если load_job хранит собственную копию логики
    # чтения+json.loads вместо вызова load_task_json, этот патч ни на что
    # не повлияет и тест упадёт (calls останется пустым).
    monkeypatch.setattr(jobs_module, "load_task_json", spy)

    loaded = load_job(tmp_path, job.id)

    assert loaded.id == job.id
    assert calls == [job_path(tmp_path, job.id)]


def test_load_gui_ai_task_routes_through_canonical_load_task_json(tmp_path, monkeypatch):
    task = GuiAiTask(
        id="gui_ai_test",
        status="pending",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        prompt="hello",
    )
    save_gui_ai_task(tmp_path, task)

    calls = []
    original = _json_io.load_task_json

    def spy(path, from_dict):
        calls.append(path)
        return original(path, from_dict)

    monkeypatch.setattr(ai_bridge_module, "load_task_json", spy)

    loaded = load_gui_ai_task(tmp_path, task.id)

    assert loaded.id == task.id
    assert calls == [gui_ai_task_path(tmp_path, task.id)]
