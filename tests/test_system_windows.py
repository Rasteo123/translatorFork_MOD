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


def test_strip_restores_paragraphs_after_beautifulsoup_requoted_the_source():
    # Сборка EPUB пересобирает главу через BeautifulSoup: значение атрибута
    # с двойными кавычками внутри он пишет в одинарных кавычках.
    from bs4 import BeautifulSoup

    html = _chapter("Начало.", "[Динь! Первая]", "Конец.").replace(
        "<p>[Динь", '<p class="no-indent">[Динь'
    )
    wrapped, _ = apply_windows(html, find_windows(html))
    requoted = str(BeautifulSoup(wrapped, "html.parser"))
    assert "data-sys-orig='" in requoted

    restored, count = strip_windows(requoted)

    assert count == 1
    assert '<p class="no-indent">[Динь! Первая]</p>' in restored


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
    # Переводы строк как на Windows. Кандидатов ищем в тех же байтах, что читают
    # scan_project и process_chapter_file: read_text сменил бы \r\n на \n, и
    # смещения кандидатов разошлись бы с файлом.
    path.write_text(_chapter("[Динь! Одна]", "Текст.", "[Динь! Две]"), encoding="utf-8", newline="\r\n")
    candidates = find_windows(path.read_bytes().decode("utf-8"))

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


def test_numeric_values_after_an_unknown_key_are_data():
    html = _chapter(
        "Миньон ближнего боя: 445 здоровья, 12 силы атаки, броня 2, сопротивление магии 0.",
        "Миньон дальнего боя: 280 здоровья, 23 силы атаки, броня 0.",
        "Он всё запомнил.",
    )

    assert [(window.kind, len(window.lines)) for window in find_windows(html)] == [("status", 2)]


# --- сверка с исходником -------------------------------------------------------

from gemini_translator.utils.system_windows import (  # noqa: E402
    source_paragraphs,
    source_marked_indices,
)


def _source(*paragraphs):
    body = "".join(f"<p>{text}</p>" for text in paragraphs)
    return f'<html><body><h1>第1章</h1>{body}</body></html>'


def test_source_paragraphs_from_p_tags_and_from_br_separated_text():
    assert source_paragraphs(_source("一", "【二】", "三")) == ["一", "【二】", "三"]
    body = "<html><body><h1>第1章</h1> 一行。<br /><br /> 【标记】！ <br /><br /> 三行。</body></html>"
    assert source_paragraphs(body) == ["一行。", "【标记】！", "三行。"]


def test_source_marked_quoted_line_becomes_a_window():
    source = _source("翌日，太阳升起。", "【你获得了标记】！", "他愣住了。")
    html = _chapter("На следующий день взошло солнце.", "«Вы нашли метку»!", "Он замер.")

    windows = find_windows(html, source_html=source)
    block = render_window(windows[0].lines, windows[0].kind)

    assert [window.lines for window in windows] == [["«Вы нашли метку»!"]]
    assert windows[0].origin == "source"
    assert "Вы нашли метку!" in block and "«" not in block.split(">", 1)[1]


def test_source_marked_shout_or_bare_name_stays_prose():
    source = _source("翌日，太阳升起。", "【标记】！", "他愣住了。")
    html = _chapter("На следующий день взошло солнце.", "«Метка»!", "Он замер.")

    assert find_windows(html, source_html=source) == []


def test_source_marks_skip_dialogue_lines_and_garbage():
    source = _source("他大喊。", "【福音！】", "【就这？】", "他笑了。")
    html = _chapter("Он закричал.", "— Евангелие!", "***", "Он засмеялся.")

    assert find_windows(html, source_html=source) == []


def test_source_marks_survive_a_merged_paragraph():
    source = _source("第一段很长很长很长很长很长很长。", "第二段也很长很长很长很长很长很长。", "第三段还是很长很长很长很长很长。", "【『追猎』判定中……『追猎』成功！】", "结束。")
    html = _chapter(
        "Первый абзац длинный, очень длинный, и второй абзац с ним слился в один длинный абзац перевода.",
        "Третий абзац тоже довольно длинный, как и полагается абзацу прозы.",
        "«Проверка „Преследования“… Успех!»",
        "Конец.",
    )

    windows = find_windows(html, source_html=source)

    assert [window.lines for window in windows] == [["«Проверка „Преследования“… Успех!»"]]


def test_source_marks_do_not_hide_bracket_detection_and_join_runs():
    source = _source("【血量：90%】", "【锻炼中……臂力+1】", "他点头。")
    html = _chapter("[Здоровье: 90%]", "«Тренировка… сила рук +1»", "Он кивнул.")

    windows = find_windows(html, source_html=source)

    assert [window.lines for window in windows] == [["[Здоровье: 90%]", "«Тренировка… сила рук +1»"]]
    assert windows[0].origin == "brackets"


def test_source_marked_indices_reports_positions_by_bracket_family():
    source = _source("一", "【二】", "三", "[四]")
    html = _chapter("Один.", "«Два».", "Три.", "«Четыре».")

    assert source_marked_indices(html, source) == {"【": {1}, "[": {3}}


def test_scan_project_ignores_a_source_family_the_translation_never_keeps(tmp_path):
    import zipfile

    from gemini_translator.utils.system_windows import trusted_source_families

    project = _windows_project(tmp_path)
    (project / "OEBPS/chapter1_translated_gemini.html").write_text(
        _chapter("Утро.", "[Здоровье: 90%]", "«Дорогой, чего застыл?»", "Он замер."), encoding="utf-8",
    )
    (project / "OEBPS/chapter2_translated_gemini.html").write_text(
        _chapter("Вечер.", "[Очки тени: 3]", "«Хм! Вот укушу!»", "Она засмеялась."), encoding="utf-8",
    )
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("OEBPS/chapter1.xhtml", _source("早上。", "[血量：90%]", "【亲爱的，愣着干嘛？】", "他愣住了。"))
        archive.writestr("OEBPS/chapter2.xhtml", _source("晚上。", "[影点：3]", "【哼！咬你哦！】", "她笑了。"))

    scans = scan_project(project, source_epub=str(epub))

    assert [[window.lines for window in scan.candidates] for scan in scans] == [[["[Здоровье: 90%]"]], [["[Очки тени: 3]"]]]
    assert trusted_source_families({"[": (2, 2), "【": (0, 2)}) == {"["}
    assert trusted_source_families({"【": (1, 4)}) == {"【"}
    assert trusted_source_families({"【": (0, 1)}) == set()
    assert trusted_source_families({"【": (9, 10)}) == set()


def test_scan_project_reads_the_source_epub(tmp_path):
    import zipfile

    project = _windows_project(tmp_path)
    (project / "OEBPS/chapter2_translated_gemini.html").write_text(
        _chapter("Утро.", "«Вы нашли метку»!", "Он замер."), encoding="utf-8",
    )
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("OEBPS/chapter1.xhtml", _source("文字。", "【叮！一】", "还有文字。", "【叮！二】"))
        archive.writestr("OEBPS/chapter2.xhtml", _source("早上。", "【标记】！", "他愣住了。"))

    scans = scan_project(project, source_epub=str(epub))

    assert [len(scan.candidates) for scan in scans] == [2, 1]
    assert scans[1].candidates[0].origin == "source"


def test_find_source_epub_picks_the_archive_that_holds_the_chapters(tmp_path):
    import zipfile

    from gemini_translator.utils.system_windows import find_source_epub

    project = _windows_project(tmp_path)
    with zipfile.ZipFile(project / "Книга (RU).epub", "w") as archive:
        archive.writestr("OEBPS/chapter1_translated.xhtml", "<p>перевод</p>")
    with zipfile.ZipFile(project / "Книга.epub", "w") as archive:
        archive.writestr("OEBPS/chapter1.xhtml", _source("一"))
        archive.writestr("OEBPS/chapter2.xhtml", _source("二"))
    (project / "Тест.epub").write_bytes(b"not a zip")

    assert find_source_epub(project) == str(project / "Книга.epub")
    assert find_source_epub(tmp_path) is None


def test_find_source_epub_prefers_an_archive_without_translation_marks(tmp_path):
    import zipfile

    from gemini_translator.utils.system_windows import find_source_epub

    import os

    project = _windows_project(tmp_path)
    for name in ("Книга (RU).epub", "Книга (перевод).epub", "Книга.epub"):
        with zipfile.ZipFile(project / name, "w") as archive:
            archive.writestr("OEBPS/chapter1.xhtml", _source("一"))
            archive.writestr("OEBPS/chapter2.xhtml", _source("二"))
        os.utime(project / name, (1_700_000_000, 1_700_000_000))

    assert find_source_epub(project) == str(project / "Книга.epub")


def test_find_source_epub_takes_the_earliest_foreign_archive_by_date(tmp_path):
    import os
    import time
    import zipfile

    from gemini_translator.utils.system_windows import find_source_epub

    project = _windows_project(tmp_path)
    now = time.time()
    russian = _chapter("Перевод главы, целиком по-русски, длинный абзац текста для проверки.", "И ещё один абзац перевода.")
    for name, age_days, text in (
        ("Книга (RU).epub", 1, _source("一二三四五六七八九十", "二")),
        ("Книга.epub", 30, _source("第一章很长的一段文字", "第二段文字")),
        ("Книга 2.epub", 10, _source("一二三四五六七八九十", "二")),
        ("Старый перевод.epub", 60, russian),
    ):
        with zipfile.ZipFile(project / name, "w") as archive:
            archive.writestr("OEBPS/chapter1.xhtml", text)
            archive.writestr("OEBPS/chapter2.xhtml", text)
        stamp = now - age_days * 86400
        os.utime(project / name, (stamp, stamp))

    assert find_source_epub(project) == str(project / "Книга.epub")


def test_default_exclusion_skips_end_of_book_and_reader_thanks():
    html = _chapter("[Благодарность за поддержку читателей]", "Текст.", "【Конец книги】", "Текст.", "[Динь! Награда]")

    assert [window.lines for window in find_windows(html)] == [["[Динь! Награда]"]]


# --- одиночные строки характеристик ---------------------------------------------

def test_single_numeric_stat_line_is_a_window():
    html = _chapter(
        "Взгляд упал на нижнюю строку.",
        "Значение богатства: 4502.",
        "До этого у него было двадцать семь юаней.",
        "Базовые характеристики: Сила 75, Ловкость 74, Телосложение 78, Интеллект 68.",
        "Очки: 100.",
    )

    windows = find_windows(html)

    assert [(window.kind, window.lines) for window in windows] == [
        ("status", ["Значение богатства: 4502."]),
        ("status", ["Базовые характеристики: Сила 75, Ловкость 74, Телосложение 78, Интеллект 68.", "Очки: 100."]),
    ]


def test_single_stat_line_needs_a_short_numeric_value():
    html = _chapter(
        "Награда оказалась весьма щедрой: помимо 2000 баллов системы, он получил пакет опыта навыков, который давно хотел.",
        "Сила: главное оружие героя.",
        "Он понял: осталось 3 дня.",
    )

    assert find_windows(html) == []


# --- карточки статуса: шапка с уровнем, разделы, маркеры -------------------------

from gemini_translator.utils.system_windows import (  # noqa: E402
    is_bullet_line,
    is_level_header,
    is_section_label,
)


def test_status_card_with_level_header_sections_and_bullets_is_one_window():
    # «Рефреш», глава 2: карточка между двумя «…».
    card = [
        "Шарль, Ур. 1.",
        "Сила: I40 → I50 | Выносливость: I50 → I60 | Ловкость: I70 → I80 | Скорость: I33 → I50 | Магия: I0 → I0.",
        "Магия: 【 】",
        "Навыки:",
        "【Тяжелое Бремя】",
        "· Снижает вес экипировки, незначительно повышает выносливость.",
        "· Чем больше вес, тем сильнее эффект.",
        "【？？？】",
    ]
    html = _chapter("Шарль взял лист и впился в него глазами.", "…", *card, "…", "— Ох… ну и жалкие характеристики.")

    windows = find_windows(html)

    assert [(window.kind, window.lines) for window in windows] == [("status", card)]


def test_magic_section_keeps_its_name_bullets_and_chant_line():
    # «Рефреш», глава 20: характеристики и раздел магии разделены «…».
    stats = [
        "Шарль – Ур. 1.",
        "Сила: I78 → H129.",
        "Выносливость: H157 → F377.",
        "Ловкость: I97 → H101.",
        "Проворство: I66 → I84.",
        "Магия: I0 → I0.",
    ]
    magic = [
        "Магия:",
        "«Колесо Чудес» / Miracle Wheel.",
        "· Магия призыва.",
        "· Расходует дух в зависимости от призываемой магии.",
        "Текст заклинания: отсутствует (произвольный).",
        "Навыки: «Хрупкий Студент», «Иду Куда Хочу».",
    ]
    html = _chapter("…", *stats, "…", *magic, "…", "— Ну и ну.")

    windows = find_windows(html)

    # «…» делит части одного листа статуса: окно одно.
    assert [window.lines for window in windows] == [stats + ["…"] + magic]
    assert windows[0].kind == "status"


def test_stat_keys_take_quoted_values_and_bracket_placeholders():
    # «Рефреш», глава 75.
    card = [
        "Лилирука Эрде. Ур. 1.",
        "Сила: I11 → H105 / Выносливость: I25 → H100 / Ловкость: I45 → G154 / Скорость: I47 → H109 / Магия: I20 → I89.",
        "Магия: «Золушка.»",
        "Навыки: «Закулисный Герой.»",
    ]
    html = _chapter("…", *card, "…", "— Господин Шарль?")

    assert [window.lines for window in find_windows(html)] == [card]
    assert is_key_value("Магия: 【 】")
    assert not is_key_value("Шарль: «Привет!»")
    assert not is_key_value("Система: «Добро пожаловать»")


def test_quoted_section_labels_and_skill_entries_with_bullets():
    # «Рефреш», глава 232.
    card = [
        "Альфия. Ур. 3.",
        "Сила: S999 → I0.",
        "Выносливость: S999 → I0.",
        "Развиваемые способности: «Магическое мастерство C», «Сопротивление Аномалиям E», «Исцеление I».",
        "«Магия.»",
        "«Сатанас Верион», «Силентиум Эдем», «Генос Ангелус».",
        "«Навыки.»",
        "«Цена Таланта», «Разделенная Судьба».",
    ]
    skill = [
        "«Новичок Восьмого Уровня»:",
        "• Активируется при игре в «волка в овечьей шкуре».",
        "• При расчете опыта расчет ведется исходя из уровня продемонстрированной силы.",
    ]
    html = _chapter(
        "Итоговая панель замерла на значениях:",
        *card,
        "«Гармония Света» (увеличивает мощь магии, снижает расход духа).",
        *skill,
        "…",
        "— Двойное Возвышение?!",
    )

    assert [window.lines for window in find_windows(html)] == [card, skill]


def test_skill_names_with_bullets_follow_a_section_label():
    # «Рефреш», глава 152.
    skills = [
        "Навыки:",
        "«Тепло.»",
        "· Постоянно и значительно восстанавливает Дух себе и союзникам.",
        "«Аура Вампиризма.»",
        "· Союзники в радиусе действия восстанавливают выносливость и здоровье в размере 20% от нанесенного урона.",
    ]
    html = _chapter(
        "Энергия, поднявшаяся от поверженных врагов, начала вливаться в тела союзников.",
        *skills,
        "Сочетание двух навыков на восстановление делало их практически неуязвимыми.",
    )

    assert [window.lines for window in find_windows(html)] == [skills]


def test_level_line_label_or_single_bullet_alone_are_not_windows():
    html = _chapter(
        "Шарль, Ур. 1.",
        "Он вздохнул и убрал лист.",
        "Навыки:",
        "Он долго думал о том, какие навыки ему пригодятся в подземелье, и так ничего и не решил.",
        "Он вспомнил одно:",
        "· купить еды.",
        "«Тепло.»",
        "Потом лёг спать.",
    )

    assert find_windows(html) == []


def test_card_line_helpers():
    assert is_level_header("Шарль, Ур. 1.")
    assert is_level_header("Зард, уровень 8.")
    assert not is_level_header("Ур. 1 → Ур. 2 → Ур. 3!")
    assert not is_level_header("— Шарль, Ур. 1.")
    assert not is_level_header("Таллис глянул на свой уровень – lv1.")
    assert is_level_header("Римуру – Потенциальная способность: Ур. 3.")
    assert is_section_label("Навыки:")
    assert is_section_label("«Магия.»")
    assert is_section_label("«Инстинкт Игрока»:")
    assert not is_section_label("Шарль моргнул:")
    assert not is_section_label("«Впрочем, неважно».")
    assert is_bullet_line("· Магия призыва.")
    assert is_bullet_line("• Активируется при игре.")
    assert not is_bullet_line("— Магия призыва.")
    assert not is_bullet_line("······")


# --- сверка с исходником: скобки исходника — не всегда система ---------------------

def test_source_marks_skip_spell_shouts_names_chat_and_lead_ins():
    # В «Рефреше» 【】 исходника — имена заклинаний и песнопения, в «Полоске»
    # ещё и чат; переводчик передал их кавычками.
    html = _chapter(
        "Лили села и повторила жест.",
        "«Вана Сейдр»!",
        "«Тепло.»",
        "Дядя Ли: «Какие ощущения от ожерелья?»",
        "Шарль моргнул:",
        "«Отворись же, Пятый Сад! Прозвучи, Девятая Песнь…»",
        "«Вы проходите обряд очищения особой силой… Обряд завершен.»",
        "«Индекс загрязнения: 0.»",
        "Он прочитал сообщения.",
        "«Лично я считаю, что вы слишком сильно выставились».",
        "«Борьба за каждый балл – залог успеха всей жизни».",
    )

    windows = find_windows(html, source_marks={1, 2, 3, 4, 5, 6, 7, 9, 10})

    assert [window.lines for window in windows] == [[
        "«Вы проходите обряд очищения особой силой… Обряд завершен.»",
        "«Индекс загрязнения: 0.»",
    ]]
    assert windows[0].origin == "source"


def test_render_section_labels_as_bold_rows_and_unquoted_titles():
    block = render_window(
        ["Шарль, Ур. 1.", "Магия: 【 】", "Навыки:", "【Тяжелое Бремя】", "· Снижает вес экипировки.", "«Магия.»"],
        "status",
    )
    skill = render_window(["«Новичок Восьмого Уровня»:", "• Активируется при игре."], "skill")

    assert "◆ ШАРЛЬ, УР. 1. ◆" in block
    assert re.search(r"<b [^>]*>Навыки:</b><br />Тяжелое Бремя<br />", block)
    assert re.search(r"<b [^>]*>Магия:</b></div>$", block)
    assert "◆ Новичок Восьмого Уровня ◆" in skill
    starred = render_window(["Навыки:", "* «Пламенная Душа» (пассивный навык):", "· Эффект."], "skill")
    assert re.search(r"<b [^>]*>Пламенная Душа \(пассивный навык\):</b>", starred)


# --- «Рефреш»: приросты, описания предметов и навыков ------------------------------

from gemini_translator.utils import system_windows as system_windows_module  # noqa: E402


def test_level_increment_line_heads_the_card():
    # «Рефреш», глава 303.
    card = ["Шарль: lv2→lv3.", "Сила: SS1001→I0.", "Выносливость: SSS1452→I0."]
    html = _chapter("Благословение на спине ожило, цифры поплыли…", *card, "…")

    windows = find_windows(html)

    assert [window.lines for window in windows] == [card]
    assert windows[0].kind == "levelup"


def test_lone_level_increment_is_a_window_but_rank_letters_are_not():
    html = _chapter(
        "Вспышка…",
        "Ур. 1 → Ур. 2 → Ур. 3!",
        "Шарль заполнил тело Альфии Великими Деяниями.",
        "Повышение уровня: 5 → 6!",
        "Он выдохнул.",
        "Lv7 → lv8!",
        "Он улыбнулся.",
        "S → A → B → … I0.",
    )

    windows = find_windows(html)

    assert [window.lines for window in windows] == [["Ур. 1 → Ур. 2 → Ур. 3!"], ["Повышение уровня: 5 → 6!"], ["Lv7 → lv8!"]]
    assert {window.kind for window in windows} == {"levelup"}


def test_stat_value_of_quoted_names_and_empty_cells_is_data():
    # «Рефреш», глава 343.
    assert is_key_value("Магия: «Эйнсел», [ ], [ ].")


def test_level_header_makes_a_status_card():
    card = ["Фильвис Шалия, уровень 1.", "Сила: I 0 / Выносливость: I 0 / Ловкость: I 0 / Проворство: I 0 / Магия: I 0."]
    html = _chapter("Проявился совершенно новый интерфейс.", *card, "— Вы поразительны.")

    windows = find_windows(html)

    assert [(window.kind, window.lines) for window in windows] == [("status", card)]


def test_starred_skill_entry_with_note_joins_the_card():
    # «Рефреш», глава 315: пункты со звёздочкой и пояснением в скобках.
    lines = [
        "Навыки: «Перегрузка», «Аура Гениальности».",
        "* «Пламенная Душа» (пассивный навык Лины из DOTA):",
        "· Каждый раз, когда заклинание поражает врага, слегка увеличивает скорость атаки и передвижения.",
        "Максимальное количество зарядов: 3/уровень.",
        "· Каждое использование магии обновляет время действия эффекта.",
    ]
    html = _chapter("Он вытянул несколько способностей.", *lines, "… …", "Что касается Бочи…")

    assert [window.lines for window in find_windows(html)] == [lines]


def test_skill_name_with_note_or_exclamation_heads_its_bullets():
    pegasus = [
        "«Наследие Пегаса» (Реликвия Пегаса)",
        "· Действует постоянно.",
        "· Восприятие Громовой Сети: обнаружение целей в радиусе 400 метров. (Изменяется с уровнем)",
    ]
    spin = ["«Крути до победного!»", "· Дополнительно дает 20 попыток обновления!"]
    html = _chapter("— Ещё один новый навык?", *pegasus, "…", "Зажглась аномальная способность:", *spin, "Охренеть…")

    assert [window.lines for window in find_windows(html)] == [pegasus, spin]


def test_starred_bullets_continue_a_skill():
    # «Рефреш», глава 479.
    skill = [
        "«Принцесса Мести»:",
        "* Свободная активация;",
        "* Значительное усиление атаки против монстров;",
        "* Эффективность растет пропорционально силе ненависти.",
    ]
    html = _chapter("Их решением было просто запретить ей использовать эту мощь.", *skill, "Этот навык был вовсе не таким простым.")

    assert [window.lines for window in find_windows(html)] == [skill]


def test_bullet_run_is_a_window_with_its_short_heading_lines():
    # «Рефреш», главы 205, 314 и 362.
    armor = [
        "Нагрудник [«Скрытность» (2)]",
        "Руна 7 Таль + руна 5 Эт.",
        "· +25% к скорости бега и ходьбы, скорости сотворения заклинаний.",
        "· Сопротивление яду +30%.",
    ]
    auras = [
        "• Красный круг «Казан Души Клинка» умеренно повышал Силу и Магию.",
        "• «Аура Шипов» преобразовывала малую часть урона от ближних атак в магический урон.",
    ]
    sword = [
        "Tal-Thul-Ort-Amn.",
        "Первый ранг · Двуручный меч с широким лезвием.",
        "· Значительное усиление навыков и магических эффектов (ур.+2);",
        "· Среднее увеличение магической силы и выносливости;",
    ]
    html = _chapter(
        "Например, тот комплект, что сейчас держал в руках Шарль:",
        *armor,
        "… …",
        "Альфия прислушалась к своим ощущениям, разбирая дарованное Шарлем усиление:",
        *auras,
        "Альфия поочередно проверяла эффекты и не скрывала удивления:",
        "…",
        *sword,
        "…",
    )

    assert [window.lines for window in find_windows(html)] == [armor, auras, sword]


def test_increments_render_one_per_row_like_danmachi_statuses():
    one_line = render_window(
        ["Шарль, Ур. 1.", "Сила: I40 → I50 | Выносливость: I50 → I60 | Магия: I0 → I0."], "status",
    )
    slashes = render_window(["Лилирука Эрде. Ур. 1.", "Сила: I11 → H105 / Выносливость: I25 → H100."], "status")
    separate = render_window(["Альфия. Ур. 3.", "Сила: S999 → I0.", "Выносливость: S999 → I0.", "Ловкость: S999 → I0."], "status")

    for block, count in ((one_line, 3), (slashes, 2), (separate, 3)):
        rows = block.split("<br />")[1:]
        assert len(rows) == count
        assert all(row.count("</b>") == 1 for row in rows)
    assert "Выносливость:</b> I50 → I60" in one_line
    assert system_windows_module._SEPARATOR not in separate


def test_card_parts_split_by_ellipsis_lines_stay_one_window():
    # «Рефреш», глава 10: части карточки разделены строками «…».
    card = ["Скорость: I 50 → I 66.", "Магия: I 0 → I 0.", "…", "Магия: 【 】", "…", "Навыки:", "【Хрупкий Студент】"]
    html = _chapter("Цифры замелькали.", *card, "…", "— Ну и ну.")

    assert [window.lines for window in find_windows(html)] == [card]


def test_dashed_section_word_between_header_and_stats_joins_the_card():
    # «Рефреш», глава 47.
    card = ["Шарль lv1.", "— Характеристики…", "Сила: H129 → H151.", "Выносливость: F377 → B705."]
    html = _chapter("Цифры стремительно замелькали.", *card, "Он выдохнул.")

    assert [window.lines for window in find_windows(html)] == [card]


def test_quoted_name_header_and_lone_magic_lines():
    sage = ["«Мудрец» (имя изменено), lv1.", "Сила: F358.", "Выносливость: S999."]
    html = _chapter(
        "Мышцы лица Фелс дёрнулись, она приняла пергамент.",
        *sage,
        "Она долго молчала.",
        "Магия: «Потерянный Котенок».",
        "Это было заклинание Ани.",
        "Магия – это чудо, неизведанное, аномалия.",
    )

    assert [window.lines for window in find_windows(html)] == [sage, ["Магия: «Потерянный Котенок»."]]
    assert not system_windows_module.is_single_stat_line("Глава 302. Основное задание: «Тематическая зона»")
    assert not system_windows_module.is_single_stat_line("Имя автора: «Серьезная, Суровая, Нестрогая».")
    assert not system_windows_module.is_single_stat_line("Весь класс: «??»")
    assert not system_windows_module.is_single_stat_line("Весь класс: «…»")


def test_three_word_label_heads_a_list_of_skill_names():
    # «Рефреш», глава 315.
    skills = [
        "Способности Пищевой Цепи:",
        "«Казан Души Клинка.»",
        "«Аура Шипов.»",
        "* «Дикое Сердце» (Beastmaster из DOTA):",
        "· Воодушевляет ближайших союзников, значительно повышая скорость их атаки.",
    ]
    html = _chapter("Что касается Бочи…", *skills, "Он кивнул.")

    assert [window.lines for window in find_windows(html)] == [skills]


def test_window_never_ends_on_an_ellipsis_line():
    html = _chapter("[Динь! Первое]", "[Динь! Второе]", "… …", "[Конец главы]")

    assert [window.lines for window in find_windows(html)] == [["[Динь! Первое]", "[Динь! Второе]"]]


def test_rank_values_render_one_per_row_but_ordinary_stats_keep_columns():
    ranks = render_window(["«Мудрец» (имя изменено), lv1.", "Сила: F358.", "Выносливость: S999.", "Ловкость: G297."], "status")
    slashes = render_window(["Фильвис Шалия, уровень 1.", "Сила: I 0 / Выносливость: I 0 / Магия: I 0."], "status")
    ordinary = render_window(["◆ СТАТУС ◆", "Имя: Ёдыре", "Раса: Человек", "Уровень: 14"], "status")

    assert len(ranks.split("<br />")) == 4
    assert len(slashes.split("<br />")) == 4
    assert system_windows_module._SEPARATOR in ordinary


# --- встроенные исключения и поле пользователя ---------------------------------

def test_builtin_exclusions_apply_even_with_an_empty_user_pattern():
    # Первые версии страницы сохраняли пустое поле, и встроенные исключения пропадали.
    html = _chapter("[Динь! Награда]", "Текст.", "[Конец главы]", "Ещё текст.", "[Примечание автора: спасибо за донат]")

    windows = find_windows(html, DetectorSettings(exclude_pattern=""))

    assert [window.lines for window in windows] == [["[Динь! Награда]"]]


def test_saved_default_exclusions_become_an_empty_user_field():
    assert system_windows_module.user_exclude_pattern("") == ""
    assert system_windows_module.user_exclude_pattern(system_windows_module.DEFAULT_EXCLUDE) == ""
    for old in system_windows_module.LEGACY_EXCLUDE_DEFAULTS:
        assert system_windows_module.user_exclude_pattern(old) == ""
    assert system_windows_module.user_exclude_pattern(r"^\[Реклама") == r"^\[Реклама"


def test_reference_list_after_a_colon_header_is_one_window():
    # «Боевой континент 2», глава 16: справочник рангов в конце главы.
    ranks = [
        "[Справочник рангов:]",
        "Младший боец (Сила удара 900 кг, 25 м/с)",
        "Средний боец (Сила удара 2000 кг, 40 м/с)",
        "Старший бог войны (Сила удара 256 000 кг)",
    ]
    note = "Уровни ментального телекинеза соответствуют уровням воинов."
    html = _chapter("Седоволосый старик благоговейно распростерся на земле.", *ranks, note, "(Конец главы)")

    # Примечание без цифр остаётся текстом: после «[Динь! Получено:]» так же
    # осталась бы и короткая проза вроде «Он замер.».
    assert [window.lines for window in find_windows(html)] == [ranks]


def test_colon_header_lists_stop_at_prose():
    # «Боевой континент 2», глава 8.
    stars = ["«Пожиратель Звёзд»:", "Младший боец (сила удара 900 кг, скорость 25 м/с)", "Средний боец (сила удара 2000 кг, скорость 40 м/с)"]
    dou = ["«Расколотая Битвой Небесная Высь»:", "Сила Доу.", "Практик Доу.", "Мастер Доу."]
    html = _chapter(
        "[Классификация уровней миров]",
        *stars,
        *dou,
        "Хо Юйхао долго смотрел на эти строки. Потом закрыл глаза и начал медитировать, как учил наставник.",
        "[Динь! Получено:]",
        "— Что это? — спросил он.",
    )

    windows = find_windows(html)

    assert [window.lines for window in windows] == [["[Классификация уровней миров]", *stars, *dou], ["[Динь! Получено:]"]]


def test_list_mode_takes_short_enumerations_but_not_prose_with_commas():
    skill = ["«Принцесса Мести»:", "* Свободная активация;", "* Значительное усиление атаки против монстров;"]
    stages = ["«Путь Смертного к Бессмертию»:", "Закалка Ци, заложение основ, формирование ядра, зарождение души, истинный бессмертный."]
    html = _chapter(
        *skill,
        "Этот навык, который Локи называла «сильнейшим атакующим навыком Нижнего Мира», был вовсе не таким простым, как многие привыкли считать.",
        *stages,
        "…— Вы поразительны, господин Шарль, — восхищенно прошептала Фильвис, — обладать такими познаниями, это же мастерство!",
    )

    assert [window.lines for window in find_windows(html)] == [skill, stages]
