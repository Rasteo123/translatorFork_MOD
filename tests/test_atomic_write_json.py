"""atomic_write_json: служебный JSON книги записывается целиком или не записывается вовсе.

Большинство служебных файлов в папке книги (глоссарий, пометки, кэши, карта
сгенерированного глоссария) писались через open(path, "w"). Такой вызов
усекает файл ещё до записи, и любой сбой посередине оставлял на диске
обрезанный JSON вместо прежних данных.
"""

import pytest

from gemini_translator.utils import io_utils


def _fail_replace(*_args):
    raise OSError("disk full")


def test_failed_commit_keeps_previous_json_file(tmp_path, monkeypatch):
    target = tmp_path / "project_glossary.json"
    target.write_text('[{"original": "Alpha"}]', encoding="utf-8")
    monkeypatch.setattr(io_utils.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="disk full"):
        io_utils.atomic_write_json(target, [{"original": "Beta"}], indent=2)

    assert target.read_text(encoding="utf-8") == '[{"original": "Alpha"}]'
    assert [path.name for path in tmp_path.iterdir()] == ["project_glossary.json"]


def test_written_json_stays_readable_for_hand_editing(tmp_path):
    target = tmp_path / "project_glossary.json"

    io_utils.atomic_write_json(
        target, {"rus": "Альфа", "original": "阿尔法"}, indent=2, sort_keys=True
    )

    assert target.read_bytes() == (
        '{\n  "original": "阿尔法",\n  "rus": "Альфа"\n}'.encode("utf-8")
    )


def test_unserializable_value_fails_without_touching_the_file(tmp_path):
    target = tmp_path / "user_problem_terms.json"
    target.write_text("[]", encoding="utf-8")

    with pytest.raises(TypeError):
        io_utils.atomic_write_json(target, [{"id": "1", "note": object()}], indent=2)

    assert target.read_text(encoding="utf-8") == "[]"
    assert [path.name for path in tmp_path.iterdir()] == ["user_problem_terms.json"]
