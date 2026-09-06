# -*- coding: utf-8 -*-
"""Тесты для устранения дубля эвристики извлечения описания Qidian.

Находка dups-qidian_rulate_workers-59 /
qidian-tools/design/4-qidian-description-heuristic-d: алгоритм поиска
описания книги по заголовкам-маркерам и стоп-строкам был реализован дважды -
один раз в Python (_extract_qidian_description_from_body и её помощники,
работающие с QIDIAN_DESCRIPTION_HEADERS/QIDIAN_DESCRIPTION_STOP_LINES), и ещё
раз с теми же литеральными множествами в инжектируемом JS
(descriptionFromBody() внутри _QIDIAN_EXTRACT_SCRIPT).

_select_qidian_description уже пробует Python-эвристику
(_extract_qidian_description_from_body(payload["body_text"])) ПЕРВОЙ, и
использует payload["description"] (результат JS-дубля) только как фолбэк.
Поскольку оба алгоритма применялись к одному и тому же body_text и были
идентичны на момент находки, JS-копия была полностью избыточна и рисковала
разойтись при будущей правке только одного места (см. failure_scenario
находки). Канонизация: JS больше не пересчитывает описание сам - он лишь
возвращает сырой body_text и простые DOM-фолбэки, а итоговое решение
принимает единственная Python-реализация.

test_qidian_extract_script_has_no_duplicate_description_heuristic обязан
ПАДАТЬ до рефакторинга (JS-скрипт содержит descriptionFromBody() с теми же
литералами) и ПРОХОДИТЬ после удаления дубля.
"""
from qidian_rulate.workers import (
    QIDIAN_DESCRIPTION_STOP_LINES,
    _QIDIAN_EXTRACT_SCRIPT,
    _extract_qidian_description_from_body,
    _select_qidian_description,
)


def test_qidian_extract_script_has_no_duplicate_description_heuristic():
    assert "descriptionFromBody" not in _QIDIAN_EXTRACT_SCRIPT, (
        "JS-дубль эвристики извлечения описания (descriptionFromBody) должен быть "
        "удалён - извлечение теперь делает только Python (_extract_qidian_description_from_body)"
    )
    # Один из ключевых литералов стоп-строк не должен всплывать заново как
    # отдельный JS Set - иначе кто-то просто переименовал функцию.
    stop_line_sample = next(iter(QIDIAN_DESCRIPTION_STOP_LINES))
    assert _QIDIAN_EXTRACT_SCRIPT.count(stop_line_sample) <= 1, (
        "Литералы стоп-строк описания не должны дублироваться внутри JS-скрипта"
    )


def test_qidian_extract_script_still_returns_body_text_and_description_fields():
    # Возвращаемый объект по-прежнему должен нести сырой body_text (на нём
    # работает каноническая Python-эвристика) и упрощённый description-фолбэк
    # (для сайтов, где Python-эвристика не находит заголовок).
    assert "body_text: fullBodyText" in _QIDIAN_EXTRACT_SCRIPT
    assert "description," in _QIDIAN_EXTRACT_SCRIPT or "description:" in _QIDIAN_EXTRACT_SCRIPT


def test_python_body_extraction_wins_over_stale_description_candidate():
    # Симулирует ситуацию из failure_scenario находки: JS-кандидат
    # (payload["description"]) устарел/расходится с телом страницы, но
    # каноническая Python-эвристика по body_text всё равно даёт верный
    # результат и должна побеждать как первый кандидат.
    body_text = (
        "作品简介\n"
        "Герой начинает путь в новом мире.\n"
        "男生月票榜"
    )
    payload = {
        "body_text": body_text,
        "description": "устаревший JS-кандидат, не совпадающий с body_text",
    }

    result = _select_qidian_description(payload, title="Title", author="Author")

    assert result == _extract_qidian_description_from_body(body_text)
    assert "устаревший" not in result
