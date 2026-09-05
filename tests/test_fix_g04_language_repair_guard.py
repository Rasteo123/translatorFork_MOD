"""Регресс на два дефекта записи языковых исправлений в service.py (группа g04).

1) qa-a/bugs/1-language-repair-unguarded-writ: если запись подтверждённой
   языковой правки не удаётся, а откатная запись падает следом (та же
   причина сбоя ФС), исключение раньше уходило из ``check_chapter`` мимо
   ``_record`` — весь результат прохода по главе терялся.
2) qa-a/bugs/2-language-repair-failure-not-de: коды предупреждений
   ``language_repair_not_written``/``language_repair_not_recorded`` не
   входили в ``DEFERRED_WARNINGS``, поэтому глава с несохранённой правкой
   помечалась как «checked», а не «deferred», и не переоткрывалась
   следующим финальным проходом.

Хелперы для сборки минимального ``TranslationQualityService`` переиспользуются
из tests/qa/test_translation_quality_service.py через importlib (без
изменения этого модуля и без сети/реальных настроек пользователя).
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

_HELPERS_PATH = (
    Path(__file__).resolve().parent / "qa" / "test_translation_quality_service.py"
)
_spec = importlib.util.spec_from_file_location(
    "_g04_service_helpers", _HELPERS_PATH
)
assert _spec is not None and _spec.loader is not None
_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_helpers)

from gemini_translator.qa import service as qa_service  # noqa: E402
from gemini_translator.qa.language_validation import (  # noqa: E402
    LanguageQaResult,
    LanguageReplacement,
    apply_language_replacements,
)
from gemini_translator.qa.repair_store import atomic_write_bytes  # noqa: E402
from gemini_translator.qa.service import (  # noqa: E402
    DEFERRED_WARNINGS,
    QaOptions,
    _chapter_status,
)
from gemini_translator.utils.epub_json import build_translation_payload  # noqa: E402


class _LanguageFixer:
    """Языковой чекер, всегда предлагающий одну подтверждённую замену."""

    async def check_chapter(self, request, *, rule_candidates=(), nlp_analysis=None):
        blocks = build_translation_payload(request.document_model)["blocks"]
        replacement = LanguageReplacement(
            "issue-1", blocks[-1]["id"], "сразу ушёл", "тут же ушёл"
        )
        return LanguageQaResult(
            chapter_id=request.chapter_id,
            applied=(replacement,),
            preview_model=apply_language_replacements(
                request.document_model, (replacement,)
            ),
        )


def _make_chapter(tmp_path: Path) -> Path:
    path = tmp_path / "chapter-1.html"
    path.write_text(_helpers._CHAPTER_HTML, encoding="utf-8")
    return path


def _make_service(tmp_path: Path):
    return _helpers._service(
        tmp_path, aligner=_helpers._CleanAligner(), language=_LanguageFixer()
    )


def _run_check(service, request):
    return asyncio.run(
        service.check_chapter(request, QaOptions(), _helpers.CancellationToken())
    )


def test_write_language_repairs_survives_a_failed_rollback(tmp_path):
    """Сбой отката после сбоя record_applied не должен ронять check_chapter.

    Симулируем коррелированный сбой ФС: запись новой правки проходит,
    ``record_applied`` падает, и попытка отката тем же ``atomic_write_bytes``
    падает тоже (реалистично при ENOSPC/только-чтение — обе операции бьются
    об одну и ту же причину). До фикса это исключение уходило из
    ``check_chapter`` необработанным, и результат прохода по главе терялся
    целиком (``_record`` не вызывался).
    """
    chapter = _make_chapter(tmp_path)
    service, journal, journal_path = _make_service(tmp_path)
    request = _helpers._request(chapter)

    def broken_record_applied(applied):
        raise OSError("no space left on device")

    service._store.record_applied = broken_record_applied  # noqa: SLF001

    calls = {"n": 0}
    real_atomic_write_bytes = atomic_write_bytes

    def flaky_atomic_write_bytes(path, data):
        calls["n"] += 1
        if calls["n"] == 1:
            # Первая запись — сама правка — должна пройти, иначе сценарий
            # вообще не доходит до попытки отката.
            return real_atomic_write_bytes(path, data)
        # Вторая запись — попытка отката внутри except-ветки — падает той
        # же самой причиной, что и record_applied.
        raise OSError("no space left on device")

    qa_service.atomic_write_bytes = flaky_atomic_write_bytes
    try:
        result = _run_check(service, request)
    finally:
        qa_service.atomic_write_bytes = real_atomic_write_bytes

    # Главное: исключение не должно было покинуть check_chapter.
    assert calls["n"] == 2
    assert "language_repair_not_written" in result.warnings
    # _record должен был отработать — результат прохода не потерян.
    assert journal_path.exists()
    assert "chapter-1" in journal.metrics or "chapter-1" in journal.chapter_states


def test_unwritten_language_repair_defers_the_chapter(tmp_path):
    """Несохранённая языковая правка обязана вернуть главу в очередь.

    Коды 'language_repair_not_written' и 'language_repair_not_recorded'
    должны входить в DEFERRED_WARNINGS: иначе глава с одобренной моделью,
    но не записанной на диск правкой считается 'checked' и больше никогда
    не перепроверяется (см. select_final_pass_chapters в
    chapter_qa_coordinator.py, который не выбирает повторно MEDIUM-главу
    с неизменившимся текстом файла).
    """
    assert "language_repair_not_written" in DEFERRED_WARNINGS
    assert "language_repair_not_recorded" in DEFERRED_WARNINGS

    chapter = _make_chapter(tmp_path)
    service, _journal, _journal_path = _make_service(tmp_path)
    request = _helpers._request(chapter)

    def broken_record_applied(applied):
        raise OSError("journal is not writable")

    service._store.record_applied = broken_record_applied  # noqa: SLF001

    result = _run_check(service, request)

    assert "language_repair_not_recorded" in result.warnings
    # Файл должен быть откачен (штатный успешный откат) — сама правка не
    # применена, но статус главы обязан требовать повторной проверки.
    assert chapter.read_bytes() == _helpers._CHAPTER_HTML.encode("utf-8")
    assert _chapter_status(result) == "deferred"
