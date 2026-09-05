"""Регресс: _safe_name(chapter_id) схлопывает разные chapter_id в один каталог.

Например, не-ASCII имена файлов внутри EPUB ('OEBPS/Text/第一章.xhtml' и
'OEBPS/Text/第二章.xhtml') после _safe_name дают одно и то же имя каталога
('OEBPS_Text_.xhtml'). Без coollision-safe выбора каталога backup_chapter либо
молча возвращал чужой бэкап (и последующий undo_chapter затирал перевод одной
главы оригинальным текстом другой), либо, в промежуточном варианте фикса,
бросал RepairStoreError и полностью останавливал ремонт и весь результат QA
для второй и последующих коллизирующих глав. Правильное поведение: у каждой
коллизирующей главы должен быть собственный, независимый бэкап.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gemini_translator.qa.repair_store import (
    AppliedRepair,
    RepairStore,
    RepairStoreError,
    content_digest,
)


@pytest.fixture()
def store(tmp_path: Path) -> RepairStore:
    return RepairStore(tmp_path / "backups", session_id="session-1")


def _commit_repair(store: RepairStore, chapter_id: str, chapter_path: Path, patch_id: str, new_text: str) -> None:
    """Смоделировать одно закоммиченное автоматическое исправление главы."""
    backup = store.backup_chapter(chapter_id, chapter_path)
    before = content_digest(chapter_path.read_bytes())
    chapter_path.write_text(new_text, encoding="utf-8")
    after = content_digest(chapter_path.read_bytes())
    store.record_applied(
        AppliedRepair(
            patch_id=patch_id,
            chapter_id=chapter_id,
            session_id=store.session_id,
            chapter_path=chapter_path,
            backup_path=backup.path,
            before_sha256=before,
            after_sha256=after,
            inserted_text="",
        )
    )


def test_colliding_chapter_ids_get_independent_backups(tmp_path: Path, store: RepairStore):
    """Разные chapter_id, схлопывающиеся _safe_name в одно имя каталога, обязаны
    получить РАЗНЫЕ каталоги бэкапов — без отказа чинить и без общей главы.
    """
    chapter_a_id = "OEBPS/Text/第一章.xhtml"
    chapter_b_id = "OEBPS/Text/第二章.xhtml"

    chapter_a = tmp_path / "chapter_a.html"
    chapter_a.write_text("<p>PEREVOD GLAVY A</p>", encoding="utf-8")
    chapter_b = tmp_path / "chapter_b.html"
    chapter_b.write_text("<p>PEREVOD GLAVY B</p>", encoding="utf-8")

    # До фикса корневой причины (промежуточный вариант) второй вызов бросал
    # RepairStoreError и полностью терял ремонт и результат QA главы B. Сейчас
    # обе главы обязаны чиниться независимо, без исключений.
    _commit_repair(store, chapter_a_id, chapter_a, "patch-a", "<p>A fixed</p>")
    _commit_repair(store, chapter_b_id, chapter_b, "patch-b", "<p>B fixed</p>")

    assert chapter_a.read_bytes() == b"<p>A fixed</p>"
    assert chapter_b.read_bytes() == b"<p>B fixed</p>"

    # На диске под коллизирующим именем не должно быть ровно одного каталога —
    # у каждой главы обязан появиться собственный (например, с хеш-суффиксом).
    backup_dirs = sorted(p for p in (tmp_path / "backups").iterdir() if p.is_dir())
    assert len(backup_dirs) == 2, f"ожидались раздельные каталоги бэкапов, получено: {backup_dirs}"

    # undo главы A обязан восстановить именно главу A и не задеть главу B.
    result_a = store.undo_chapter(chapter_a_id)
    assert result_a.status == "restored"
    assert chapter_a.read_bytes() == b"<p>PEREVOD GLAVY A</p>"
    assert chapter_b.read_bytes() == b"<p>B fixed</p>"

    # ...и наоборот: undo главы B не должен трогать уже восстановленную главу A.
    result_b = store.undo_chapter(chapter_b_id)
    assert result_b.status == "restored"
    assert chapter_b.read_bytes() == b"<p>PEREVOD GLAVY B</p>"
    assert chapter_a.read_bytes() == b"<p>PEREVOD GLAVY A</p>"


def test_hard_double_collision_still_raises_instead_of_corrupting(tmp_path: Path, store: RepairStore, monkeypatch):
    """Последний рубеж: если даже каталог с хеш-суффиксом занят чужим chapter_id
    (двойная коллизия — и первичное имя, и хеш-суффикс совпали у ТРЁХ разных
    chapter_id), хранилище обязано отказать, а не подменить чужой бэкап.
    """
    import gemini_translator.qa.repair_store as repair_store_module

    chapter_a_id = "chapter-a"
    chapter_b_id = "chapter-b"
    chapter_c_id = "chapter-c"

    chapter_a = tmp_path / "chapter_a.html"
    chapter_a.write_text("<p>A</p>", encoding="utf-8")
    chapter_b = tmp_path / "chapter_b.html"
    chapter_b.write_text("<p>B</p>", encoding="utf-8")
    chapter_c = tmp_path / "chapter_c.html"
    chapter_c.write_text("<p>C</p>", encoding="utf-8")

    # Подделываем _safe_name и sha256 так, чтобы ВСЕ три chapter_id схлопывались
    # в одно и то же первичное имя каталога и в один и тот же хеш-суффикс —
    # воспроизводим маловероятную двойную коллизию без перебора реальных строк
    # с одинаковым sha256.
    monkeypatch.setattr(repair_store_module, "_safe_name", lambda value: "same")
    monkeypatch.setattr(
        repair_store_module.hashlib,
        "sha256",
        lambda data: type("_FixedHash", (), {"hexdigest": lambda self: "deadbeef"})(),
    )

    # A занимает первичный каталог "same".
    store.backup_chapter(chapter_a_id, chapter_a)
    # B не совпадает с A в первичном каталоге -> уходит в хеш-каталог "same-deadbeef".
    store.backup_chapter(chapter_b_id, chapter_b)
    # C не совпадает с A в первичном каталоге и не совпадает с B в хеш-каталоге —
    # подставлять уже некуда, хранилище обязано отказать.
    with pytest.raises(RepairStoreError):
        store.backup_chapter(chapter_c_id, chapter_c)

    # Ни один из уже созданных бэкапов не должен быть испорчен попыткой C.
    assert chapter_a.read_bytes() == b"<p>A</p>"
    assert chapter_b.read_bytes() == b"<p>B</p>"


def test_record_applied_accepts_chapter_id_with_surrounding_whitespace(tmp_path: Path, store: RepairStore):
    """record_applied передаёт chapter_id без нормализации (в отличие от
    backup_chapter/undo_chapter/applied_patch_ids), а backup_chapter сохраняет в
    метаданные уже обрезанный chapter_id. Сверка в _load_metadata обязана это
    учитывать, иначе оформление главы с окаймляющими пробелами в chapter_id
    падает на record_applied, хотя backup_chapter для того же chapter_id
    отработал успешно.
    """
    raw_chapter_id = " Text/ch1.xhtml "
    chapter_path = tmp_path / "chapter.html"
    chapter_path.write_text("<p>original</p>", encoding="utf-8")

    backup = store.backup_chapter(raw_chapter_id, chapter_path)
    before = content_digest(chapter_path.read_bytes())
    chapter_path.write_text("<p>fixed</p>", encoding="utf-8")
    after = content_digest(chapter_path.read_bytes())

    # Не должно бросать RepairStoreError о "разных" chapter_id — это одна и та
    # же глава, просто переданная с пробелами по краям.
    store.record_applied(
        AppliedRepair(
            patch_id="patch-1",
            chapter_id=raw_chapter_id,
            session_id=store.session_id,
            chapter_path=chapter_path,
            backup_path=backup.path,
            before_sha256=before,
            after_sha256=after,
            inserted_text="",
        )
    )

    assert store.applied_patch_ids(raw_chapter_id) == ("patch-1",)
