"""Системные окна не должны вставать на вырожденном вводе.

Sonar (S8786) отметил в ``system_windows`` и выгрузке на Rulate регулярки,
которые длинную серию пробелов, точек или букв без совпадения проходят заново
с каждой её позиции — время растёт квадратично. До правки 8 000 символов
такой серии стоили 0,07–0,54 с. До оформления окна пробелы не доходят:
``render_window`` схлопывает их в каждой строке. А серия букв или точек
доходит, и зацикленный ответ модели бывает целой главой из одного символа
(см. ``test_typography_degenerate_input_cost``). Выгрузка на Rulate берёт
HTML главы как есть.
"""

from __future__ import annotations

import time

from gemini_translator.ui.dialogs.rulate_export import EPUBConverterThread
from gemini_translator.utils import system_windows as sw

# Щедро: до правки такие серии разбирались десятки секунд, после — миллисекунды.
BUDGET_SECONDS = 3.0


def _spent(call) -> float:
    started = time.perf_counter()
    call()
    return time.perf_counter() - started


def test_sentence_end_stays_quick_on_long_word():
    text = "а" * 60_000 + " . Б"

    assert _spent(lambda: sw._sentence_end(text, text.index("."))) < BUDGET_SECONDS
    assert sw._sentence_end("Готово. Дальше", 6)
    # Сокращение не конец фразы — в том числе сразу после цифры: «5ед. Б».
    assert not sw._sentence_end("Ур. 5 Сила", 2)
    assert not sw._sentence_end("5ед. Б", 3)


def test_chat_header_with_dot_tail_stays_quick():
    lines = ["Сообщение от: Кен" + "." * 120_000 + "x", "[Кен]: Привет.", "[Рен]: Привет."]

    assert _spent(lambda: sw.chat_account_owner([], lines)) < BUDGET_SECONDS
    # Хвост «.]» к имени не относится: отправитель Кен, аккаунт — Рена.
    assert sw.chat_account_owner([], ["[Сообщение от: Кен.]", "[Кен]: Привет.", "[Рен]: Привет."]) == "Рен"


def test_rulate_plain_text_stays_quick_on_space_run_in_system_block():
    block = f'<div {sw.BLOCK_ATTR}="status" data-sys-orig="&lt;p&gt;Сила&lt;/p&gt;">Сила:{" " * 40_000}5\n</div>'
    converter = EPUBConverterThread("book.epub")
    texts = []

    spent = _spent(lambda: texts.append(converter._html_to_plain_text(f"<body>{block}</body>")))

    assert spent < BUDGET_SECONDS, f"{spent:.1f} с на серии пробелов в окне"
    assert texts[0].startswith(f'<div {sw.BLOCK_ATTR}="status">Сила:')
    assert "data-sys-orig" not in texts[0] and "\n" not in texts[0]
