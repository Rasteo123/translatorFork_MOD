# -*- coding: utf-8 -*-
"""Системные окна: поиск серий системных строк и их оформление."""

import re

from gemini_translator.utils.system_windows import (
    DetectorSettings,
    classify_kind,
    find_windows,
    is_key_value,
)


def _chapter(*paragraphs):
    body = "\n\n".join(f"<p>{text}</p>" for text in paragraphs)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n<head><title>t</title></head>\n'
        f"<body>\n<h1>Глава 1</h1>\n\n{body}\n</body>\n</html>\n"
    )


def test_single_bracketed_paragraph_is_a_notice_window():
    html = _chapter("Он шёл домой.", "[Динь! Очки эмоций +333]", "Он остановился.")

    windows = find_windows(html)

    assert len(windows) == 1
    assert windows[0].kind == "notice"
    assert windows[0].lines == ["[Динь! Очки эмоций +333]"]


def test_consecutive_bracketed_paragraphs_form_one_window():
    html = _chapter(
        "Прошло время.",
        "[Динь! Начать слияние?]",
        "[Начать слияние?]",
        "[…]",
        "Он молчал.",
    )

    windows = find_windows(html)

    assert [window.lines for window in windows] == [
        ["[Динь! Начать слияние?]", "[Начать слияние?]", "[…]"],
    ]


def test_prose_between_bracketed_lines_splits_windows():
    html = _chapter("[Динь! Первое]", "Он подумал.", "[Динь! Второе]")

    windows = find_windows(html)

    assert [window.lines for window in windows] == [["[Динь! Первое]"], ["[Динь! Второе]"]]


def test_key_value_lines_after_header_join_the_window_as_status():
    html = _chapter(
        "[Динь! Личная панель данных открыта!]",
        "[Хозяин: Цзян Юй]",
        "[Уровень: Нет]",
        "[Очки эмоций: 666]",
        "Он присвистнул.",
    )

    windows = find_windows(html)

    assert len(windows) == 1
    assert windows[0].kind == "status"
    assert len(windows[0].lines) == 4


def test_unbracketed_key_value_lines_continue_a_window_with_header():
    html = _chapter(
        "◆ СТАТУС ПЕРСОНАЖА ◆",
        "Имя: Ёдыре | Раса: Человек | Уровень: 14",
        "Очки здоровья: 100/100",
        "Уровень повысился? Сразу на два?",
    )

    windows = find_windows(html)

    assert len(windows) == 1
    assert windows[0].lines == [
        "◆ СТАТУС ПЕРСОНАЖА ◆",
        "Имя: Ёдыре | Раса: Человек | Уровень: 14",
        "Очки здоровья: 100/100",
    ]


def test_header_without_data_lines_is_not_a_window():
    html = _chapter("Уровень повысился? Сразу на два?", "Раньше оповещений не было.")

    assert find_windows(html) == []


def test_brackets_inside_prose_are_ignored():
    html = _chapter("Он вспомнил [сон] и пошёл дальше [к дому].", "[Динь!] сказал голос и умолк.")

    assert find_windows(html) == []


def test_single_bracketed_lines_can_be_disabled():
    html = _chapter("[Динь! Одна строка]", "Текст.", "[Динь! Первая]", "[Динь! Вторая]")

    windows = find_windows(html, DetectorSettings(single_bracketed=False))

    assert [window.lines for window in windows] == [["[Динь! Первая]", "[Динь! Вторая]"]]


def test_exclude_pattern_skips_paragraphs():
    html = _chapter("[прим. пер.: игра слов]", "[Динь! Награда получена]")

    windows = find_windows(html, DetectorSettings(exclude_pattern=r"прим\.\s*пер"))

    assert [window.lines for window in windows] == [["[Динь! Награда получена]"]]


def test_existing_block_is_not_detected_again():
    html = _chapter("Текст.") + ""
    html = html.replace(
        "<p>Текст.</p>",
        '<p>Текст.</p>\n\n<div data-sys="notice" style="color:#fff;">[Динь! Уже оформлено]</div>',
    )

    assert find_windows(html) == []


def test_candidate_offsets_cover_the_source_paragraphs():
    html = _chapter("Начало.", "[Динь! Первая]", "[Динь! Вторая]", "Конец.")

    window = find_windows(html)[0]

    assert html[window.start:window.end] == "<p>[Динь! Первая]</p>\n\n<p>[Динь! Вторая]</p>"


def test_prose_sentence_with_trigger_word_is_not_a_header():
    html = _chapter(
        "Ему не терпелось узнать, до какого уровня его поднимут эти очки!",
        "[Динь! Панель открыта!]",
        "[Хозяин: Цзян Юй]",
    )

    assert [window.lines for window in find_windows(html)] == [["[Динь! Панель открыта!]", "[Хозяин: Цзян Юй]"]]


def test_quoted_chat_lines_are_not_key_values():
    html = _chapter("«Мадо: Этот отряд бесстыжий!»", "«Пейн: Офигеть!»")

    assert find_windows(html) == []


def test_script_style_dialogue_is_not_key_value():
    html = _chapter("[Динь! Ответ]", "Дедушка Ван: «Твою мать!»", "Молодой даос: — …")

    assert [window.lines for window in find_windows(html)] == [["[Динь! Ответ]"]]


def test_line_ending_with_colon_does_not_start_a_window():
    html = _chapter("Стоило подумать, как система ответила:", "[Динь! Смотри сам.]")

    assert [window.lines for window in find_windows(html)] == [["[Динь! Смотри сам.]"]]


# --- оформление -------------------------------------------------------------

from gemini_translator.utils.system_windows import (  # noqa: E402
    DEFAULT_TEMPLATES,
    render_window,
)


def test_render_is_a_single_line_div_with_kind_and_box_style():
    block = render_window(["[Динь! Очки эмоций +333]"], "notice")

    assert "\n" not in block
    assert block.startswith('<div data-sys="notice"')
    assert block.endswith("</div>")
    assert "border-left:8px solid" in block
    assert "text-align:center" in block


def test_render_single_line_has_no_title_and_no_brackets():
    block = render_window(["[Динь! Очки эмоций +333]"], "notice")

    assert "Динь! Очки эмоций +333" in block
    assert "[" not in block.split(">", 1)[1]
    assert "<b" not in block


def test_render_first_non_key_value_line_becomes_title():
    block = render_window(["[Динь! Личная панель данных открыта!]", "[Хозяин: Цзян Юй]"], "status")

    assert re.search(r"<b [^>]*>◆ ДИНЬ! ЛИЧНАЯ ПАНЕЛЬ ДАННЫХ ОТКРЫТА! ◆</b><br />", block)


def test_render_long_first_line_stays_a_row_not_a_title():
    first = "[Динь! Обнаружен постыдный Хозяин. Начать слияние с Системой Постыдного Усиления?]"
    block = render_window([first, "[Динь! Начать слияние?]"], "notice")

    assert "letter-spacing" not in block
    assert "Динь! Обнаружен постыдный Хозяин." in block


def test_render_key_value_rows_get_bold_labels():
    block = render_window(["[Динь! Панель]", "[Хозяин: Цзян Юй]", "[Уровень: Нет]"], "status")

    assert re.search(r"<b [^>]*>Хозяин:</b> Цзян Юй", block)
    assert re.search(r"<b [^>]*>Уровень:</b> Нет", block)


def test_render_status_joins_short_key_values_into_columns():
    block = render_window(
        ["◆ СТАТУС ◆", "Имя: Ёдыре", "Раса: Человек", "Уровень: 14", "Очки здоровья: 100/100"],
        "status",
    )

    assert "Ёдыре\u00a0|\u00a0<b" in block
    assert block.count("<br />") == 2


def test_render_keeps_explicit_separators_of_a_line():
    block = render_window(["◆ СТАТУС ◆", "Имя: Ёдыре | Раса: Человек"], "status")

    assert re.search(r"<b [^>]*>Имя:</b> Ёдыре \| <b [^>]*>Раса:</b> Человек", block)


def test_render_long_plain_lines_are_italic_when_window_has_title():
    description = "Эксклюзивный навык иномирца. После полового акта опыт увеличивается."
    block = render_window(["[Навык: «Благословение Рюнара»]", "[Тип: Пассивный]", f"[{description}]"], "skill")

    assert f"<i>{description}</i>" in block


def test_render_escapes_text_and_keeps_source_html_in_attribute():
    block = render_window(["[Динь! <b>жирный</b> & конец]"], "notice", source_html="<p>[Динь! <b>жирный</b> &amp; конец]</p>")

    assert "&lt;b&gt;жирный&lt;/b&gt; &amp; конец" in block
    assert 'data-sys-orig="&lt;p&gt;[Динь! &lt;b&gt;жирный&lt;/b&gt; &amp;amp; конец]&lt;/p&gt;"' in block


def test_render_uses_template_colors():
    templates = dict(DEFAULT_TEMPLATES)
    templates["notice"] = dict(DEFAULT_TEMPLATES["notice"], border="#123456", background="#654321")

    block = render_window(["[Динь!]"], "notice", templates=templates)

    assert "border:2px solid #123456" in block
    assert "background:#654321" in block


def test_render_title_case_follows_template():
    achievement = render_window(["[Достижение: новый титул]", "[Титул: «Садист»]"], "achievement")
    skill = render_window(["[Навык: «Благословение»]", "[Тип: Пассивный]"], "skill")

    assert "★ ДОСТИЖЕНИЕ: НОВЫЙ ТИТУЛ ★" in achievement
    assert "◆ Навык: «Благословение» ◆" in skill


# --- применение и снятие ----------------------------------------------------

from gemini_translator.utils.system_windows import (  # noqa: E402
    apply_windows,
    strip_windows,
)


def test_apply_replaces_runs_and_leaves_the_rest_byte_identical():
    html = _chapter("Начало.", "[Динь! Первая]", "[Динь! Вторая]", "Конец.")
    windows = find_windows(html)

    result, count = apply_windows(html, windows)

    assert count == 1
    before, after = html.split("<p>[Динь! Первая]</p>\n\n<p>[Динь! Вторая]</p>")
    assert result.startswith(before) and result.endswith(after)
    block = result[len(before):len(result) - len(after)]
    assert block.startswith('<div data-sys="notice"') and block.endswith("</div>")
    assert "\n" not in block


def test_apply_then_scan_finds_nothing_new():
    html = _chapter("[Динь! Одна]", "Текст.", "[Динь! Две]")

    result, _ = apply_windows(html, find_windows(html))

    assert find_windows(result) == []


def test_strip_restores_the_original_html_exactly():
    html = _chapter("Начало.", "[Динь! <i>Первая</i> &amp; вторая]", "[Хозяин: Цзян Юй]", "Конец.")
    wrapped, _ = apply_windows(html, find_windows(html))

    restored, count = strip_windows(wrapped)

    assert count == 1
    assert restored == html


def test_apply_only_selected_candidates():
    html = _chapter("[Динь! Одна]", "Текст.", "[Динь! Две]")
    windows = find_windows(html)

    result, count = apply_windows(html, [windows[1]])

    assert count == 1
    assert "<p>[Динь! Одна]</p>" in result
    assert "<p>[Динь! Две]</p>" not in result


def test_apply_skips_candidate_whose_source_changed():
    html = _chapter("[Динь! Одна]", "Текст.")
    windows = find_windows(html)
    edited = html.replace("[Динь! Одна]", "[Динь! Другая]")

    result, count = apply_windows(edited, windows)

    assert count == 0
    assert result == edited


def test_apply_uses_kind_override_from_candidate():
    html = _chapter("[Динь! Одна]")
    windows = find_windows(html)
    windows[0].kind = "achievement"

    result, _ = apply_windows(html, windows)

    assert '<div data-sys="achievement"' in result


# --- файлы и проект ---------------------------------------------------------

import json  # noqa: E402
import os  # noqa: E402

from gemini_translator.utils.system_windows import (  # noqa: E402
    process_chapter_file,
    project_chapter_files,
)


def test_process_chapter_file_apply_then_strip_round_trips_on_disk(tmp_path):
    path = tmp_path / "chapter1_translated_gemini.html"
    original = _chapter("Начало.", "[Динь! Первая]", "[Хозяин: Цзян Юй]", "Конец.")
    path.write_text(original, encoding="utf-8")

    applied = process_chapter_file(path, mode="apply")
    wrapped = path.read_text(encoding="utf-8")
    stripped = process_chapter_file(path, mode="strip")

    assert applied == 1 and stripped == 1
    assert 'data-sys="notice"' in wrapped
    assert path.read_text(encoding="utf-8") == original


def test_process_chapter_file_apply_takes_selected_candidates(tmp_path):
    path = tmp_path / "chapter1_translated_gemini.html"
    path.write_text(_chapter("[Динь! Одна]", "Текст.", "[Динь! Две]"), encoding="utf-8")
    candidates = find_windows(path.read_text(encoding="utf-8"))

    count = process_chapter_file(path, mode="apply", candidates=[candidates[0]])

    assert count == 1
    assert "<p>[Динь! Две]</p>" in path.read_text(encoding="utf-8")


def _project(tmp_path, entries):
    project = tmp_path / "book"
    (project / "OEBPS").mkdir(parents=True)
    translation_map = {}
    for original, versions in entries.items():
        translation_map[original] = {}
        for suffix, exists in versions.items():
            rel_path = f"OEBPS/{os.path.basename(original).split('.')[0]}{suffix}"
            translation_map[original][suffix] = rel_path
            if exists:
                (project / rel_path).write_text(_chapter("Текст."), encoding="utf-8")
    (project / "translation_map.json").write_text(json.dumps(translation_map, ensure_ascii=False), encoding="utf-8")
    return project


def test_project_chapter_files_prefer_validated_and_sort_naturally(tmp_path):
    project = _project(tmp_path, {
        "OEBPS/chapter10.xhtml": {"_translated_gemini.html": True},
        "OEBPS/chapter2.xhtml": {"_translated_gemini.html": True, "_validated.html": True},
        "OEBPS/chapter3.xhtml": {"_translated_gemini.html": False},
    })

    chapters = project_chapter_files(project)

    assert [(original, os.path.basename(path)) for original, path in chapters] == [
        ("OEBPS/chapter2.xhtml", "chapter2_validated.html"),
        ("OEBPS/chapter10.xhtml", "chapter10_translated_gemini.html"),
    ]


# --- проект целиком ---------------------------------------------------------

from gemini_translator.utils.system_windows import (  # noqa: E402
    apply_project,
    scan_project,
    strip_project,
)


def _windows_project(tmp_path):
    project = _project(tmp_path, {
        "OEBPS/chapter1.xhtml": {"_translated_gemini.html": True},
        "OEBPS/chapter2.xhtml": {"_translated_gemini.html": True},
    })
    (project / "OEBPS/chapter1_translated_gemini.html").write_text(
        _chapter("Текст.", "[Динь! Одна]", "Ещё текст.", "[Динь! Две]"), encoding="utf-8",
    )
    return project


def test_scan_project_lists_every_chapter_with_its_candidates(tmp_path):
    project = _windows_project(tmp_path)
    seen = []

    scans = scan_project(project, progress=lambda done, total, original: seen.append((done, total)))

    assert [(scan.original, scan.title, len(scan.candidates)) for scan in scans] == [
        ("OEBPS/chapter1.xhtml", "Глава 1", 2),
        ("OEBPS/chapter2.xhtml", "Глава 1", 0),
    ]
    assert seen == [(1, 2), (2, 2)]


def test_apply_project_wraps_only_the_selected_candidates(tmp_path):
    project = _windows_project(tmp_path)
    scans = scan_project(project)
    first = scans[0]

    chapters, windows = apply_project([(first.path, [first.candidates[1]])])
    html = (project / "OEBPS/chapter1_translated_gemini.html").read_text(encoding="utf-8")

    assert (chapters, windows) == (1, 1)
    assert "<p>[Динь! Одна]</p>" in html
    assert 'data-sys="notice"' in html


def test_strip_project_restores_every_chapter(tmp_path):
    project = _windows_project(tmp_path)
    original = (project / "OEBPS/chapter1_translated_gemini.html").read_text(encoding="utf-8")
    scans = scan_project(project)
    apply_project([(scans[0].path, scans[0].candidates)])

    chapters, windows = strip_project(project)

    assert (chapters, windows) == (1, 2)
    assert (project / "OEBPS/chapter1_translated_gemini.html").read_text(encoding="utf-8") == original


# --- образцы и страница предпросмотра ----------------------------------------

from gemini_translator.utils.system_windows import (  # noqa: E402
    SAMPLE_WINDOWS,
    render_preview_document,
)


def test_samples_cover_every_kind_and_render_with_titles():
    assert set(SAMPLE_WINDOWS) == {"status", "skill", "notice", "levelup", "achievement"}
    for kind, lines in SAMPLE_WINDOWS.items():
        block = render_window(lines, kind)
        assert "letter-spacing" in block, kind


def test_preview_document_lists_extra_blocks_before_samples():
    document = render_preview_document(DEFAULT_TEMPLATES, extra=[(["[Динь! Хозяин найден]"], "notice")])

    assert document.startswith("<!DOCTYPE html>")
    assert document.index("Хозяин найден") < document.index('data-sys="status"')
    assert document.count("<div data-sys=") == 1 + len(SAMPLE_WINDOWS)
    assert "data-sys-orig" not in document


# --- формы из «Реинкарнации в злого дракона» ----------------------------------

def test_bracketed_line_with_punctuation_outside_counts_and_keeps_it_inside():
    html = _chapter("Текст.", "[Покупка совершена, доспех установлен].", "Ещё текст.")

    windows = find_windows(html)
    block = render_window(windows[0].lines, windows[0].kind)

    assert [window.lines for window in windows] == [["[Покупка совершена, доспех установлен]."]]
    assert "Покупка совершена, доспех установлен." in block
    assert "]" not in block.split(">", 1)[1]


def test_bracketed_line_with_attribution_is_prose():
    html = _chapter("[Вы поразительно догадливы!], – искренне польстила система.", "[Динь! Ответ]")

    assert [window.lines for window in find_windows(html)] == [["[Динь! Ответ]"]]


def test_keyed_lines_form_a_window_with_bold_terms():
    html = _chapter(
        "Список особенностей:",
        "[Вождь] – лидер племени.",
        "[Метание] – бросают камни, оружие и всё подряд.",
        "[Текущая благосклонность]: 80 (обожание)",
        "Он присвистнул.",
    )

    windows = find_windows(html)
    block = render_window(windows[0].lines, windows[0].kind)

    assert len(windows) == 1 and len(windows[0].lines) == 3
    assert re.search(r"<b [^>]*>Вождь</b> – лидер племени\.", block)
    assert re.search(r"<b [^>]*>Текущая благосклонность:</b> 80 \(обожание\)", block)


def test_keyed_line_with_sentence_inside_brackets_is_dialogue():
    html = _chapter("[Какой молодой?!] – голос на том конце взвился: [Отпустите его!]", "[Динь! Ответ]")

    assert [window.lines for window in find_windows(html)] == [["[Динь! Ответ]"]]


def test_bracket_group_list_is_a_data_line_rendered_without_brackets():
    html = _chapter("[Динь! Наложены дебаффы:]", "[Тошнота], [Отравление], [Потеря обоняния].")

    windows = find_windows(html)
    block = render_window(windows[0].lines, windows[0].kind)

    assert len(windows) == 1 and len(windows[0].lines) == 2
    assert "Тошнота, Отравление, Потеря обоняния." in block


def test_multi_paragraph_bracket_span_is_one_window():
    html = _chapter(
        "Система показала список.",
        "[Доступные для исследования технологии:",
        "(1) Закаленное вооружение (требуется рудник)",
        "(2) Кровь магического дракона (требуется кровь)]",
        "Су Нянь задумался.",
    )

    windows = find_windows(html)
    block = render_window(windows[0].lines, windows[0].kind)

    assert [len(window.lines) for window in windows] == [3]
    assert "Доступные для исследования технологии" in block
    assert "технологии:" not in block
    assert "(2) Кровь магического дракона (требуется кровь)<" in block
    assert "[" not in block.split(">", 1)[1] and "]" not in block.split(">", 1)[1]


def test_span_opener_with_attribution_is_not_a_span():
    html = _chapter(
        "[Для производства требуется 10 очков системы!] – раздался голос.",
        "— М-м? — Су Нянь нахмурился.",
        "Система тут же исправилась: [Ошибка в расчетах…]",
    )

    assert find_windows(html) == []


def test_span_without_closing_within_reach_is_ignored():
    html = _chapter("[Начало без конца", *["Обычный абзац." for _ in range(24)], "конец]")

    assert find_windows(html) == []


def test_prose_ending_with_colon_does_not_start_a_run():
    html = _chapter(
        "Произошло чудо: на скале возник фундамент, и система тут же подала голос:",
        "[Для постройки требуется: камень – 10 тонн]",
    )

    assert [window.lines for window in find_windows(html)] == [["[Для постройки требуется: камень – 10 тонн]"]]


def test_system_speech_line_is_not_a_header():
    html = _chapter("Система: «…»", "[Динь! Обнаружен призыв]")

    assert [window.lines for window in find_windows(html)] == [["[Динь! Обнаружен призыв]"]]


def test_emoticon_values_are_not_key_values():
    html = _chapter("Пастер-Нореджи: (º Д º*)", "Анна: Σ(っ °Д °;)っ")

    assert find_windows(html) == []


def test_default_exclusion_skips_author_notes_but_keeps_system_notes():
    html = _chapter(
        "[Примечание автора: спасибо за подарки]",
        "Текст.",
        "[P.S. по сюжету прошло пять дней].",
        "Текст.",
        "[Примечание: карта черпает силы носителя]",
    )

    assert [window.lines for window in find_windows(html)] == [["[Примечание: карта черпает силы носителя]"]]


def test_short_values_are_still_key_values():
    assert is_key_value("[Уровень: 3]")
    assert is_key_value("Ранг: F")
    assert not is_key_value("Анна: Σ(っ °Д °;)っ")


def test_levelup_needs_the_level_up_phrase_itself():
    assert classify_kind(["[Повышение уровня]", "[Уровень повышен +2]"]) == "levelup"
    assert classify_kind(["[Тюрьма Страданий]", "[Раз в сутки характеристики уровня повышаются на 5%]"]) != "levelup"


def test_default_exclusion_skips_chapter_end_markers():
    html = _chapter("[Динь! Награда]", "Текст.", "[Конец главы]", "Текст.", "[Продолжение следует…]")

    assert [window.lines for window in find_windows(html)] == [["[Динь! Награда]"]]



# --- формы из аудита тринадцати книг --------------------------------------------

def test_status_card_lines_ending_with_periods_form_a_window():
    html = _chapter("Титул: Нет.", "Раса: Лавовый дракон.", "Здоровье: 31/31.", "Он усмехнулся.")

    windows = find_windows(html)

    assert len(windows) == 1 and windows[0].kind == "status"
    assert windows[0].lines == ["Титул: Нет.", "Раса: Лавовый дракон.", "Здоровье: 31/31."]


def test_prose_with_colon_and_period_is_not_a_status_card():
    html = _chapter("Он сказал: привет.", "Она ответила: пока.")

    assert find_windows(html) == []


def test_quoted_key_is_a_key_value_line():
    html = _chapter("[Имя: Цзян Ю]", "«Уровень»: средний уровень первого ранга.")

    windows = find_windows(html)
    block = render_window(windows[0].lines, windows[0].kind)

    assert len(windows) == 1 and len(windows[0].lines) == 2
    assert re.search(r"<b [^>]*>Уровень:</b> средний уровень первого ранга\.", block)


def test_guillemet_system_message_is_a_window():
    html = _chapter(
        "Он замер.",
        "«Динь! Ваш новый чит доставлен! Пожалуйста, распишитесь в получении!»",
        "«Система, ты можешь объяснить, в чем твоя польза?»",
        "«Система?»",
        "«Динь!»",
    )

    windows = find_windows(html)
    block = render_window(windows[0].lines, windows[0].kind)

    assert [window.lines for window in windows] == [["«Динь! Ваш новый чит доставлен! Пожалуйста, распишитесь в получении!»"]]
    assert "Динь! Ваш новый чит доставлен! Пожалуйста, распишитесь в получении!" in block
    assert "«" not in block.split(">", 1)[1]


def test_dash_bracket_system_line_is_a_window_but_spell_shout_is_not():
    html = _chapter(
        "— [Динь! Анализ завершен. Обнаружен талант первого ранга: «Выносливость»].",
        "— [Шторм Душ]!",
    )

    windows = find_windows(html)
    block = render_window(windows[0].lines, windows[0].kind)

    assert [len(window.lines) for window in windows] == [1]
    assert block.split(">", 1)[1].startswith("Динь! Анализ завершен.")
    assert "Выносливость»]." not in block and "Выносливость»." in block


def test_nested_bracket_groups_inside_a_full_line():
    line = "[Система: Динь! Поздравляем игрока с получением [карты здания] × 1 и [специальной карты] × 1]"
    html = _chapter(line)

    windows = find_windows(html)
    block = render_window(windows[0].lines, windows[0].kind)

    assert [window.lines for window in windows] == [[line]]
    assert "получением [карты здания] × 1" in block


def test_keyed_line_with_semicolon_stats_is_data():
    html = _chapter(
        "[Мечник Света Магада]; Уровень: 85. Класс: Механический гуманоид.",
        "[Мечник Теней Лунгада]; Уровень: 85. Класс: Механический гуманоид.",
    )

    assert [len(window.lines) for window in find_windows(html)] == [2]


def test_parenthetical_lines_are_not_key_values():
    html = _chapter("(Благодарность за донаты: Лилит)", "(Благодарность за лунный билет: книжный друг)")

    assert find_windows(html) == []


def test_default_exclusion_covers_author_note_abbreviations():
    html = _chapter("[Прим. Авт.: по сеттингу третьей части]", "(P.S.: Папа научил меня этому.)", "【Серийные Прыжки】")

    assert [window.lines for window in find_windows(html)] == [["【Серийные Прыжки】"]]


def test_span_reaches_twenty_paragraphs():
    html = _chapter("[Список:", *[f"({index}) пункт" for index in range(1, 18)], "последний пункт]", "Проза.")

    assert [len(window.lines) for window in find_windows(html)] == [19]


# --- уточнения после аудита тринадцати книг ---------------------------------------

def test_sound_only_quotes_are_not_windows():
    html = _chapter("«Динь-дон! Динь-дон!»", "«Динь… динь… динь!»", "«Динь! Ваш чит доставлен, распишитесь в получении!»")

    assert [window.lines for window in find_windows(html)] == [["«Динь! Ваш чит доставлен, распишитесь в получении!»"]]


def test_quoted_thought_with_a_generic_first_word_is_not_a_window():
    html = _chapter(
        "«Открыть через десять дней, иначе пеняйте на себя!»",
        "«Задание нельзя откладывать, нужно поторапливаться…»",
        "«Задание: собрать десять лунных трав до заката.»",
        "«Новое задание получено: спасти старосту деревни.»",
    )

    assert [window.lines for window in find_windows(html)] == [[
        "«Задание: собрать десять лунных трав до заката.»",
        "«Новое задание получено: спасти старосту деревни.»",
    ]]


def test_dashed_incantations_are_not_windows():
    html = _chapter(
        "— [Обнажись, сияющий клинок короля, повелитель магических мечей]!",
        "Он взмахнул мечом.",
        "— [У тебя осталось всего триста баллов. Подумай хорошенько].",
        "— [Суждения Системы непогрешимы].",
        "— [Вниманию жителей города Ботон! В нашем городе распространяется вирус.]",
    )

    windows = find_windows(html)

    assert [len(window.lines) for window in windows] == [3]
    assert windows[0].lines[0].startswith("— [У тебя осталось")


def test_book_metadata_lines_are_not_status_cards():
    html = _chapter("Название: Герой, призванный в покои Владыки.", "Автор: Старик-пройдоха Юй.", "Статус: Завершено.")
    heading = _chapter("Глава 9: Нина.", "Титул: Нет.")

    assert find_windows(html) == []
    assert find_windows(heading) == []


def test_default_exclusion_skips_donation_thanks():
    html = _chapter("Благодарю за донаты: 2021, Годзилла.", "Благодарю за пожертвования пользователей: 2021, 2022")

    assert find_windows(html) == []


def test_book_info_page_is_not_a_status_card():
    html = _chapter(
        "Название: Я посмотрю, насколько отважен этот Герой.",
        "Автор: Сверхзвуковой Бульдозер.",
        "Статус: Завершено.",
        "Количество слов: 2,1 миллиона.",
        "Количество глав: 1274.",
        "ID книги: 7253043603310644263.",
        "Просмотров: 19178.",
    )

    assert find_windows(html) == []


def test_stat_line_ending_with_ellipsis_stays_in_the_card():
    html = _chapter(
        "Описание было кратким:",
        "Возраст: неизвестен.",
        "Сила: Предельный Доуло.",
        "Статус: глава Культа Священной Магии Смерти, блюститель государства, государственный наставник…",
        "Помимо этого, выделялись броские слова.",
    )

    windows = find_windows(html)

    assert [len(window.lines) for window in windows] == [3]
    assert windows[0].lines[-1].startswith("Статус: глава")


def test_prose_with_colon_and_long_capitalised_value_is_not_key_value():
    html = _chapter(
        "Было очевидно: Ди Тянь обращался не к мертвецам перед собой, а к тому, кто управлял ими.",
        "Он доверял лишь себе: Никто не мог помочь.",
    )

    assert find_windows(html) == []
    assert not is_key_value("Итог: он проиграл…")


def test_martial_soul_card_with_long_values_is_a_status():
    html = _chapter(
        "Вечером он вкратце подвёл итог своим силам.",
        "Боевой дух: Духовные Глаза, золотое вторичное пробуждение, обладает способностью пожирания, духовная сила – выше двадцатого уровня.",
        "Боевой дух: Сфера Божественного Сознания, без духовных колец, на текущем этапе крайне слаба, но потенциал безграничен.",
        "Телосложение: под питанием Тысячелетних Сердец Ивы и усилением от возвращения Духовных Глаз прочность тела поднялась "
        "до уровня среднего генерала. Сила удара кулаком превысила шестнадцать тысяч килограммов, а скорость достигла ста тридцати метров в секунду.",
        "Он остался доволен.",
    )

    windows = find_windows(html)

    assert [(window.kind, len(window.lines)) for window in windows] == [("status", 3)]
