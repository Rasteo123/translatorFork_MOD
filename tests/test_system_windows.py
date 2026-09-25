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


def test_render_status_puts_short_key_values_one_per_row():
    # Колонки через «|» по центру рамки выглядели криво («Щит небосвода»).
    block = render_window(
        ["◆ СТАТУС ◆", "Имя: Ёдыре", "Раса: Человек", "Уровень: 14", "Очки здоровья: 100/100"],
        "status",
    )

    assert "\u00a0|\u00a0" not in block
    assert block.count("<br />") == 4


def test_render_columns_still_follow_the_template_setting():
    block = render_window(
        ["◆ СТАТУС ◆", "Имя: Ёдыре", "Раса: Человек", "Уровень: 14", "Очки здоровья: 100/100"],
        "status",
        templates={"status": {"columns": 3}},
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
    assert set(SAMPLE_WINDOWS) == {"status", "skill", "notice", "levelup", "achievement", "chat", "forum"}
    for kind, lines in SAMPLE_WINDOWS.items():
        if kind in ("chat", "forum"):
            continue
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


def test_rank_values_render_one_per_row_even_with_columns_template():
    columns = {"status": {"columns": 3}}
    ranks = render_window(["«Мудрец» (имя изменено), lv1.", "Сила: F358.", "Выносливость: S999.", "Ловкость: G297."], "status", templates=columns)
    slashes = render_window(["Фильвис Шалия, уровень 1.", "Сила: I 0 / Выносливость: I 0 / Магия: I 0."], "status", templates=columns)
    ordinary = render_window(["◆ СТАТУС ◆", "Имя: Ёдыре", "Раса: Человек", "Уровень: 14"], "status", templates=columns)

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


# --- карточки снаряжения и монстров одним абзацем («Щит небосвода») --------------


def _rows(block: str) -> list[str]:
    inner = re.sub(r"^<div[^>]*>|</div>$", "", block)
    return [re.sub(r"<[^>]+>", "", row) for row in inner.split("<br />")]


def test_item_card_in_one_paragraph_renders_one_field_per_row():
    card = (
        "[Хрустальный Свет Роэльсы]: Уровень: 38, Качество: Легендарное. Одноручный меч, Атака: 180~220. "
        "Защита: +800 единиц. Сила +60. Телосложение +70. Удача +3. Особое свойство: атаки наделены силой "
        "адского пламени, наносят 500 единиц урона огнем, игнорирующего защиту."
    )

    rows = _rows(render_window([card], "skill"))

    assert rows[0] == "◆ Хрустальный Свет Роэльсы ◆"
    assert rows[1:9] == [
        "Уровень: 38", "Качество: Легендарное", "Одноручный меч", "Атака: 180~220",
        "Защита: +800 единиц", "Сила +60", "Телосложение +70", "Удача +3",
    ]
    assert rows[9].startswith("Особое свойство: атаки наделены")


def test_several_cards_in_one_window_get_equal_name_rows():
    cards = [
        "[Стена Демонической Розы]: Уровень: 38, Качество: Легендарное. Щит, Защита: 3600. Блокирование: 125.",
        "[Демон Черного Пламени]: Уровень: 38, Качество: Легендарное. Посох, Атака: 200~250. Интеллект +70.",
    ]

    block = render_window(cards, "skill")
    rows = _rows(block)

    assert "◆" not in block
    assert rows[0] == "Стена Демонической Розы" and "Демон Черного Пламени" in rows
    assert rows.index("Демон Черного Пламени") == 6


def test_notice_in_prose_stays_one_row():
    notice = (
        "[Система: Группа игрока Цзо Сансары приняла задание «Возрождение Семени Жизни: Пробуждение друидов. "
        "Часть 3». Мать-Земля Эленмья просит Цзо Сансару отправиться к Престолу Элементов. Опыт за задание: 200 000.]"
    )

    assert len(_rows(render_window([notice], "notice"))) == 1


def test_signed_numbers_luck_and_set_bonuses_are_card_data():
    html = _chapter(
        "[Наручи Стража Гнева]: Класс: воин-защитник, Уровень: 40, Качество: легендарное. Броня: 1200.",
        "Сила: +90.",
        "Удача: +2.",
        "Особое свойство: повышает шанс блокирования щитом на 3%.",
        "2 вещи: Повышает уровень угрозы от всех соответствующих навыков на 5%. Добавляет 400 единиц чистого урона, игнорирующего броню.",
        "4 вещи: Урон по демоническим существам повышен на 5%. Облик медведя: генерация угрозы повышена на 5%. Облик кошки: скорость атаки повышена на 5%.",
        "Глубокий фиолетовый навсегда останется самым желанным цветом для игроков, ведь он означает Легенду.",
    )

    windows = find_windows(html)

    assert len(windows) == 1
    assert windows[0].lines[-1].startswith("4 вещи:")
    # Бонус комплекта остаётся одной строкой, как и соседний «2 вещи».
    rows = _rows(render_window(windows[0].lines, windows[0].kind))
    assert rows[-1].startswith("4 вещи: Урон") and rows[-1].endswith("скорость атаки повышена на 5%.")
    assert is_key_value("Скорость: -10%")
    assert not is_key_value("Он сказал: -Нет.")



# --- переписка: окна «Чат» (Ace in the Hole, «Бизнес с карточками») -----------

from gemini_translator.utils import system_windows as sw  # noqa: E402

ACE_CHAT = [
    "Сообщение от: Макото Ниидзима.",
    "[Макото Ниидзима]: Кен, я просто… хотела узнать, как ты.",
    "[Макото Ниидзима]: Я видела, что при ребятах ты держался молодцом, но…",
    "[Кен Амада]: Спасибо, что беспокоишься обо мне.",
    "[Кен]: Я… в порядке.",
    "[Макото Ниидзима]: Ох!",
]


def test_chat_line_reads_every_chat_format():
    assert sw.chat_line("[Кен Амада]: Я буду у тебя завтра в 9:30") == sw.ChatLine("Кен Амада", "Я буду у тебя завтра в 9:30", "bracket")
    assert sw.chat_line("Менеджер Сюй: «Секунду».") == sw.ChatLine("Менеджер Сюй", "Секунду.", "quoted")
    assert sw.chat_line("«Хаоюгэн: Ха-ха-ха! Ну и позорище.»") == sw.ChatLine("Хаоюгэн", "Ха-ха-ха! Ну и позорище.", "forum")
    assert sw.chat_line("Сье: Ты тут? Чем занята?").style == "bare"
    assert sw.chat_line("Сообщение от: Макото Ниидзима.") is None
    assert sw.is_chat_header("Групповой чат: Бывшие SEES (Минако Санада, +7)")
    assert not sw.is_chat_header("Чат мгновенно затих.")
    assert not sw.is_chat_header("Сообщение от Бэй Жу напомнило ему о главном.")


def test_chat_needs_a_header_or_a_repeated_speaker():
    assert sw.is_chat(ACE_CHAT)
    assert sw.is_chat(["[Неизвестная]: Кен Амада, верно?", "[Неизвестная]: Скажи мне, кто ты?"])
    assert sw.is_chat(["Юй Фан: «Что делать, Ци-цзы?»", "Юй Фан: «Отец меня убьёт».", "Цзян Ци: «Не преувеличивай»."])
    # Описания навыков в той же форме, что и чат, — системный текст.
    assert not sw.is_chat(["[Пожирание Тени]: Вы можете черпать очки тени.", "[Теневое Око]: Ваши глаза изменились."])
    assert not sw.is_chat(["[Скоростное производство]: создание огненных шаров.", "[Зловонный залп]: залп сзади."])
    assert not sw.is_chat(["Вопрос: как им помочь?", "Ответ: дать работу.", "Вопрос: зачем?", "Ответ: чтобы жили."])
    assert not sw.is_chat(["«Эффект первый: телосложение +5.»", "«Эффект второй: ловкость +5.»"])
    assert not sw.is_chat(["«Кто скажет: „Нет одежд?“»", "«Кто скажет: „Нет одежд?“»"])
    # Два собеседника по одной реплике — только если они уже известны по книге.
    pair = ["[Кен]: Кто это?", "[Неизвестная]: Хех… не притворяйся дурачком."]
    assert not sw.is_chat(pair)
    assert sw.is_chat(pair, participants=frozenset({"Кен Амада", "Неизвестная"}))


def test_find_windows_marks_chat_and_keeps_system_cards_apart():
    html = _chapter(
        "Телефон завибрировал.",
        *ACE_CHAT,
        "[Статус: Кен Амада]",
        "[Уровень: 30]",
        "Он убрал телефон.",
    )

    windows = find_windows(html)

    assert [(window.kind, window.origin) for window in windows] == [("chat", "chat"), ("status", "brackets")]
    assert windows[0].lines == ACE_CHAT


def test_scan_chapters_accepts_short_chat_of_known_participants():
    first = _chapter("Он открыл чат.", "[Кен]: Где вы?", "[Рен]: Уже едем.", "[Кен]: Жду.")
    second = _chapter("Позже пришло сообщение.", "[Рен]: Мы на месте.", "[Кен]: Иду.", "Он вышел.")

    scans = sw.scan_chapters([("a.html", "a.html", first, None), ("b.html", "b.html", second, None)])

    assert [window.kind for window in scans[1].candidates] == ["chat"]


def test_chat_renders_bubbles_left_and_right_and_strips_back():
    templates = {kind: dict(template) for kind, template in DEFAULT_TEMPLATES.items()}
    templates["chat"]["readers"] = "Кен"
    html = _chapter("Телефон завибрировал.", *ACE_CHAT, "Он улыбнулся.")
    windows = find_windows(html)

    result, count = sw.apply_windows(html, windows, templates=templates)
    block = sw._BLOCK_RE.search(result).group(0)

    assert count == 1
    assert block.count("float:left") == 3 and block.count("float:right") == 2
    # Имя — над первой репликой серии и только у собеседников.
    assert block.count(">Макото Ниидзима</b>") == 2 and "Кен Амада</b>" not in block
    assert ">Сообщение от: Макото Ниидзима.</span>" in block
    assert "<div" not in block[5:]
    assert sw.strip_windows(result) == (html, 1)


def test_chat_reader_is_who_takes_part_in_most_chats():
    chats = [
        ["[Кен]: Привет.", "[Анн]: Привет!", "[Анн]: Как ты?"],
        ["[Кен Амада]: Спасибо.", "[Макото]: Ох!", "[Макото]: Ну и славно."],
        ["[Рюдзи]: ЧЁРТ.", "[Рюдзи]: Опять?"],
    ]

    assert sw.chat_reader(chats) == "Кен"
    assert sw.chat_reader(chats[2:]) == ""
    assert sw.is_reader("Кен Амада", ["Кен"]) and not sw.is_reader("Дядя Ван", ["Дядя Ли"])


# --- чей аккаунт в переписке ---------------------------------------------------


def test_chat_owner_is_whose_phone_it_is_not_who_holds_it():
    rena = ["[Кен]: Нам нужно встретиться.", "[Анн]: Срочно?", "[Кен]: Да."]
    assert sw.chat_account_owner(["Вдруг телефон Рена завибрировал, и он принялся читать."], rena) == "Рена"
    assert sw.chat_account_owner(
        ["Рен достал телефон, чтобы написать Кену."],
        ["Сообщение отправлено: Кен.", "[Рен]: Можно к тебе?", "[Кен]: Серьёзно, Рен?"],
    ) == "Рен"
    assert sw.chat_account_owner(
        ["Вторник, 11 октября."],
        ["Сообщение от: Кен Амада.", "[Кен Амада]: Мицуру-сан, есть новости.", "[Мицуру Киридзё]: Поняла, Амада."],
    ) == "Мицуру Киридзё"
    assert sw.chat_account_owner(["Пришло сообщение от Анн.", "Рен замер."], ["[Анн]: Ты занят?", "[Рен]: Расскажешь?"]) == "Рен"
    assert sw.chat_account_owner(["Он шёл по улице."], ["[Анн]: Привет.", "[Рен]: Привет."]) == ""


def test_owner_goes_right_even_when_the_book_reader_is_someone_else():
    templates = {kind: dict(template) for kind, template in DEFAULT_TEMPLATES.items()}
    templates["chat"]["readers"] = ["Кен"]
    html = _chapter(
        "Макото достала телефон, чтобы написать Кену.",
        "[Кен]: Ой, прости, Макото.",
        "[Макото]: Ничего страшного.",
        "[Макото]: Надеюсь, ты не скучаешь.",
        "Она улыбнулась.",
    )
    windows = find_windows(html)
    assert windows[0].chat_owner == "Макото"

    block = sw._BLOCK_RE.search(sw.apply_windows(html, windows, templates=templates)[0]).group(0)

    assert block.count("float:right") == 2 and ">Кен</b>" in block
    # Выбор на странице главнее владельца: «никто» — все слева.
    windows[0].readers = ()
    block = sw._BLOCK_RE.search(sw.apply_windows(html, windows, templates=templates)[0]).group(0)
    assert "float:right" not in block


def test_next_chats_of_a_chapter_keep_the_owner_while_he_writes():
    html = _chapter(
        "Пришло сообщение от Анн.",
        "[Анн]: Ты занят?",
        "[Анн]: Ответь.",
        "[Рен]: Нет.",
        "Рен задумался.",
        "[Рен]: Расскажешь, что случилось?",
        "[Рен]: Я волнуюсь.",
        "[Анн]: Потом.",
        "Позже написала Макото.",
        "[Макото]: Встретимся?",
        "[Макото]: В шесть.",
    )

    owners = [window.chat_owner for window in find_windows(html)]

    assert owners == ["Рен", "Рен", ""]



# --- форум: ПЛО в фанфиках по «Червю» -------------------------------------------

PHO_THREAD = [
    "Добро пожаловать на форумы «Паралюди Онлайн»",
    "Вы вошли в систему как CatsPaw_8",
    "Вы просматриваете:",
    "• Темы, в которых вы отвечали.",
    "Десять постов на странице.",
    "■",
    "♦Тема: Стражи СКП ЕНЕ",
    "Раздел: Форумы.",
    "XxVoid_CowboyxX (Автор темы)",
    "Опубликовано 6 марта 2011 г.:",
    "Призрачного Сталкера переводят? Но кто теперь будет гонять Барыг?",
    "(Показана страница 2 из 2)",
    "► Мистер Фабу",
    "Ответил 6 марта 2011 г.:",
    "Не то чтобы по ней кто-то сильно скучал.",
    "… ► Баграт (Ветеран форума) (Посвященный)",
    "Ответил 7 марта 2011 г.:",
    "А кто-нибудь допускал мысль, что её могут _никуда_ не переводить?",
    "Конец страницы. 1 , 2",
]


def test_forum_thread_is_found_from_header_to_end_of_page():
    html = _chapter("Просмотр ПЛО ничем не помог.", *PHO_THREAD, "Полдень, а новостей всё нет.", "— Ты идёшь? — спросила мама.")

    windows = find_windows(html)

    assert [(window.kind, window.origin) for window in windows] == [("forum", "forum")]
    assert windows[0].lines == PHO_THREAD


def test_forum_structure_reads_topic_posts_tags_and_pages():
    parts = sw.forum_structure(PHO_THREAD)

    assert parts[0][0] == "welcome" and len(parts[0][1]) == 5
    assert parts[1] == ("topic", "Стражи СКП ЕНЕ", "Форумы.")
    assert parts[2] == ("post", "XxVoid_CowboyxX", ["Автор темы"], "Опубликовано 6 марта 2011 г.:",
                        ["Призрачного Сталкера переводят? Но кто теперь будет гонять Барыг?"])
    assert parts[3] == ("page", "(Показана страница 2 из 2)")
    assert parts[5][1:3] == ("Баграт", ["Ветеран форума", "Посвященный"])
    assert parts[-1] == ("page", "Конец страницы. 1 , 2")


def test_bbcode_leftovers_and_reply_from_form():
    lines = [
        "[b] Тема: Официальный тред Медузы[/b]",
        "[b]Раздел: Форумы ► США ► Броктон-Бей[/b]",
        "[b]Баграт [/b] (Автор темы) (Ветеран форума)",
        "Опубликовано 6 апреля 2011 г.:",
        "Всем привет!",
        "[/indent] [b](Показана страница 45 из 53)[/b] [indent]",
        "[b]►Smoothmoves [/b]",
        "Ответ от 12 апреля 2011 г.:",
        "Короче, работаю я дома.",
        "[/indent] [b]Конец страницы. [u]1[/u], [u]2[/u][/b]",
        "[CENTER]■[/CENTER]",
    ]
    html = _chapter(*lines, "Верити уставилась в телефон.", "Приходилось признать: выглядела она круто.")

    windows = find_windows(html)
    parts = sw.forum_structure(windows[0].lines)

    # BB-код снимается уже при разборе абзацев.
    assert windows[0].lines == [
        "Тема: Официальный тред Медузы", "Раздел: Форумы ► США ► Броктон-Бей",
        "Баграт (Автор темы) (Ветеран форума)", "Опубликовано 6 апреля 2011 г.:", "Всем привет!",
        "(Показана страница 45 из 53)", "►Smoothmoves", "Ответ от 12 апреля 2011 г.:", "Короче, работаю я дома.",
        "Конец страницы. 1, 2", "■",
    ]
    assert parts[0] == ("topic", "Официальный тред Медузы", "Форумы ► США ► Броктон-Бей")
    assert [part[1] for part in parts if part[0] == "post"] == ["Баграт", "Smoothmoves"]
    assert "[b]" not in sw.render_window(lines, "forum")


def test_last_post_body_stops_at_dialogue_and_without_posts_there_is_no_forum():
    html = _chapter("♦Тема: Сибирь", "От: PathToVictory", "— Ну вот и предсказатель, — сказала Верити.")
    assert all(window.kind != "forum" for window in find_windows(html))

    thread = ["♦Тема: Новые Боги.", "В: Форумы ► Религия.", "MignonMan (Автор темы)", "Опубликовано 2 мая 2011 года:", "Обсуждаем."]
    html = _chapter(*thread, "— Алло? — ответила я.", "Спустя двадцать минут меня провели в спальню.")
    assert find_windows(html)[0].lines == thread


def test_forum_in_calibre_wrappers_is_cut_by_whole_elements_and_strips_back():
    html = (
        "<html><body>\n<p>Текст до.</p>\n"
        "<p>♦Тема: Тема нового Кейпа.</p>\n<p>В: Доски.</p>\n"
        "<p>Sunlit_Worship (Автор темы)</p>\n<p>Опубликовано 16 февраля 2011:</p>\n<p>Новый кейп!</p>\n"
        '<div class="calibre1">\n<p>► ManualOverdrive.</p>\n</div>\n'
        '<div class="calibre1">\n<p>Ответил 16 февраля 2011:</p>\n</div>\n'
        '<div class="calibre1">\n<p>Да ладно.</p>\n</div>\n'
        "<p>Конец страницы. 1, 2</p>\n<p>Текст после.</p>\n</body></html>\n"
    )

    windows = find_windows(html)
    result, count = sw.apply_windows(html, windows)

    assert count == 1 and windows[0].kind == "forum"
    assert result.count("<div") == result.count("</div>")
    assert "Текст до." in result and "Текст после." in result
    assert sw.strip_windows(result) == (html, 1)


def test_nested_forum_blocks_are_found_whole():
    block = sw.render_window(PHO_THREAD, "forum", source_html="<p>x</p>")
    html = f"<p>До.</p>\n{block}\n<p>После.</p>"

    spans = list(sw.iter_block_spans(html))

    assert len(spans) == 1 and html[spans[0][0]:spans[0][1]] == block
    assert block.count("<div") > 1
    assert sw.strip_windows(html) == ("<p>До.</p>\n<p>x</p>\n<p>После.</p>", 1)



def test_prose_about_messages_is_not_a_private_message_header():
    assert sw.forum_role("Новых сообщений так и не появилось.") == "body"
    assert sw.forum_role("♦ Личное сообщение от ПСтранница2011 (Подтверждённый кейп):") == "pm"
    assert sw.forum_role("Личное сообщение для Баграт:") == "pm"
    html = _chapter("Новых сообщений так и не появилось.", "К счастью, как раз в этот момент Колин зашевелился.")
    assert find_windows(html) == []



def test_list_line_with_two_ranks_keeps_the_list_going():
    # «Боевой континент 2», глава 8: две ступени в одной строке списка.
    dou = [
        "«Расколотая Битвой Небесная Высь»:",
        "Сила Доу.",
        "Почтенный Доу.",
        "Пик почтенного Доу (с первого по десятый ранги), выход за грани смертного. Полусвятой (младший, средний, старший уровни).",
        "Святой Доу (от одной до девяти звезд, способность создавать пространства).",
        "Бог Доу (высший ранг Континента Доуци).",
    ]
    html = _chapter("[Классификация уровней миров]", *dou, "Хо Юйхао долго смотрел на эти строки. Потом закрыл глаза.")

    assert [window.lines for window in find_windows(html)] == [["[Классификация уровней миров]", *dou]]


# --- что значат скобки в книге: заголовки сцен и мысленная речь ------------------


def test_book_conventions_tell_scene_headers_and_speech_from_system():
    headers = [_chapter(f"[Магнус Берк {n:02d}]", "[Мастерская Арканиста, Доки, Броктон-Бэй]", "Текст главы.") for n in range(10)]
    speech = [_chapter("[Мне нужно знать, где ты.]", "[Я здесь, – ответила она.]", "Текст.") for _ in range(10)]
    system = [_chapter("[Динь! Навык получен]", "[Уровень: 3]", "Текст.") for _ in range(10)]

    assert sw.book_bracket_conventions(headers) == {"chapter_headers": True, "speech_brackets": False}
    assert sw.book_bracket_conventions(speech)["speech_brackets"] is True
    assert sw.book_bracket_conventions(system) == {"chapter_headers": False, "speech_brackets": False}


def test_scene_headers_are_not_windows_but_alerts_are():
    settings = sw.DetectorSettings(chapter_headers=True)
    html = _chapter(
        "[Интерлюдия 02]",
        "[Тэмми Херрен]",
        "Руна шла за Ренессансом.",
        "[Он видит вас. Немедленно отступайте!]",
        "И путь появился.",
        "— ​",
        "[Мисси Бирон, героиня Виста]",
        "Это был патруль.",
        "— ​",
        "[Тревога! Тактическая сеть атакована!]",
        "Он вскочил.",
    )

    lines = [window.lines for window in find_windows(html, settings)]

    assert lines == [["[Он видит вас. Немедленно отступайте!]"], ["[Тревога! Тактическая сеть атакована!]"]]


def test_speech_in_brackets_is_not_a_window_but_chat_and_system_are():
    settings = sw.DetectorSettings(speech_brackets=True)
    html = _chapter(
        "[Есть. Тебе… Не больно ли тебе отвечать на вопросы?]",
        "— [Я чувствую твою усталость. Ты уверена?]",
        "Текст.",
        "[Данные повреждены]",
        "Текст.",
        "[Кен]: Ты где?",
        "[Кен]: Отзовись.",
        "[Анн]: Уже иду.",
    )

    windows = find_windows(html, settings)

    assert [window.kind for window in windows] == ["notice", "chat"]
    assert windows[0].lines == ["[Данные повреждены]"]


def test_bbcode_tags_arc_markers_and_story_metadata_are_not_windows():
    html = _chapter(
        "[/spoiler]", "Текст.",
        "Конец арки 01: Осколок, окутанный тенями", "Следующая арка 02: Избранный Бога Войны.", "Текст.",
        "Опубликовано: 2025–12–30.", "Завершено: 2026–05–12.", "Слов: 319,462.", "Текст.",
        "Следующая Улика: клочок бумаги с номером?", "Далее: досье СКП!", "Далее: пазл, ну и что?",
    )

    assert find_windows(html) == []


# --- абзацы в обёртках: страницы веб-новелл и границы разделов --------------------


def _wrapped(*paragraphs):
    # «Dimensional Traveler», «Данмачи Белл взрослый»: каждый абзац в своих div.
    body = "\n\n".join(
        f'<div class="db cha-paragraph"><div class="dib pr">\n<p>{text}</p>\n</div></div>' for text in paragraphs
    )
    return f"<html><body>\n{body}\n</body></html>\n"


def test_paragraphs_in_their_own_wrappers_form_one_window():
    html = _wrapped(
        "В этот миг перед глазами соткался экран Системы:",
        "[Поздравляем! Вы уничтожили 8 демонов.]",
        "[Дзинь. Текущий уровень повышен: 49 → 50!]",
        "[Разблокирована новая функция: «Мини-карта».]",
        "— Наконец-то! — усмехнулся Рэн.",
    )

    windows = find_windows(html)
    result, count = sw.apply_windows(html, windows)

    assert [window.lines for window in windows] == [[
        "[Поздравляем! Вы уничтожили 8 демонов.]",
        "[Дзинь. Текущий уровень повышен: 49 → 50!]",
        "[Разблокирована новая функция: «Мини-карта».]",
    ]]
    assert count == 1 and result.count("<div") == result.count("</div>")
    assert "экран Системы:" in result and "Наконец-то!" in result
    assert sw.strip_windows(result) == (html, 1)


def test_forum_across_containers_does_not_swallow_the_prose_around_it():
    # «Ангел»: ветка начинается посреди общей обёртки главы, а продолжается уже
    # после неё. Расширить замену до целых элементов значило бы проглотить главу.
    head, tail = PHO_THREAD[:12], PHO_THREAD[12:]
    html = (
        '<html><body>\n<div class="chapter">\n<h2>Глава 15</h2>\n<p>Я вздохнула и открыла ноутбук.</p>\n'
        + "".join(f"<p>{text}</p>\n" for text in head)
        + "</div>\n"
        + "".join(f"<p>{text}</p>\n" for text in tail)
        + "<p>Я закрыла ноутбук.</p>\n</body></html>\n"
    )

    windows = find_windows(html)
    result, count = sw.apply_windows(html, windows)

    assert {window.kind for window in windows} == {"forum"}
    assert [line for window in windows for line in window.lines] == PHO_THREAD
    assert "Я вздохнула и открыла ноутбук." in result and "<h2>Глава 15</h2>" in result
    assert result.count("<div") == result.count("</div>")
    assert sw.strip_windows(result) == (html, count)


def test_system_lines_in_two_sections_are_not_joined_through_the_prose_wrappers():
    html = (
        '<html><body>\n<div class="s1">\n<p>Он открыл панель.</p>\n<p>[Имя: Лин]</p>\n</div>\n'
        '<div class="s2">\n<p>[Уровень: 3]</p>\n<p>Он закрыл панель.</p>\n</div>\n</body></html>\n'
    )

    windows = find_windows(html)
    result, count = sw.apply_windows(html, windows)

    assert [line for window in windows for line in window.lines] == ["[Имя: Лин]", "[Уровень: 3]"]
    assert "Он открыл панель." in result and "Он закрыл панель." in result
    assert result.count("<div") == result.count("</div>")
    assert sw.strip_windows(result) == (html, count)


# --- вёрстка: списки групп и длинные значения -------------------------------------


def test_group_list_without_commas_is_rendered_with_separators_not_italic():
    # «So I'm an Earth»: «[земляной дракон ур. 1] [Скоростная регенерация ОЗ ур. 8] …»
    lines = ["Навыки:", "[земляной дракон ур. 1] [Скоростная регенерация ОЗ ур. 8] [Небесная сила ур. 2]"]

    block = sw.render_window(lines, "status")

    assert "земляной дракон ур. 1 · Скоростная регенерация ОЗ ур. 8 · Небесная сила ур. 2" in block
    assert "<i>" not in block


def test_long_values_keep_bold_keys_when_the_window_is_a_card():
    # «Kumo desu ka», симулятор: «День первый: …» жирным, а длинный день — нет.
    lines = [
        "День первый: Клуб Героев отбивает атаку Vertex.",
        "День сорок пятый: Миёси Карин вступает в Клуб Героев.",
        "День пятьдесят четвертый: Vertex атакуют. Ты тайно нападаешь на Инубодзаки Ицуки и выводишь её из строя."
        " Инубодзаки Фу остается охранять сестру и не участвует в битве.",
    ]

    block = sw.render_window(lines, "status")

    assert block.count("<b ") == 3
    assert "День пятьдесят четвертый:</b>" in block
    assert "<i>" not in block


def _contrast(first, second):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_forum_muted_text_is_readable_on_header_and_body():
    # Строка раздела под темой лежит на фоне шапки: 4,29:1 было ниже AA.
    for template in (sw.DEFAULT_TEMPLATES["forum"], {**sw.DEFAULT_TEMPLATES["forum"], "text": "#b0b6c4"}):
        colors = sw._forum_colors(template)
        assert _contrast(colors["muted"], colors["header"]) >= 4.5
        assert _contrast(colors["muted"], template["background"]) >= 4.5


def test_dash_list_inside_a_post_does_not_end_the_thread():
    # «Ангел»: пост со сводкой слухов пунктами через тире, дальше снова посты.
    thread = [
        "♦ Тема: Девушка Симург",
        "В: Доски ► Слухи ► Губители.",
        "► Winged",
        "Ответил 15 июня 2011 года:",
        "Да! Всем привет! Я отслеживаю каждый слух. Скину список:",
        "— В «Ящике Игрушек» есть участница, которой нравится Симург (Правда – «Ящик Игрушек» подтвердил).",
        "— По какой-то причине Йип сбежала из больницы (Возможно – свидетели видели её в Тампе).",
        "► Nakyak",
        "Ответил 15 июня 2011 года:",
        "Спасибо!",
        "Конец страницы. 1, 2",
    ]
    html = _chapter("Луиза ткнула планшетом мне в лицо.", *thread, "— И как мне это исправить? — спросила я.")

    windows = find_windows(html)

    assert [window.lines for window in windows] == [thread]


def test_forum_lines_with_a_leading_ellipsis_keep_their_roles():
    # «Ангел», «Predatory»: переводчик поставил «…» перед строками ветки.
    assert sw.forum_role("…Конец страницы. 1 , 2 , 3 , 4.") == "end"
    assert sw.forum_role("…В: Доски ► Слухи ► Губители.") == "board"
    assert sw.forum_role("…") == "decor"
    parts = sw.forum_structure(["♦ Тема: Девушка Симург", "…В: Доски ► Слухи.", "► Judge", "Ответил 15 июня:",
                                "… Не-а, беру свои слова обратно.", "…Конец страницы. 1, 2"])
    assert parts[0] == ("topic", "Девушка Симург", "Доски ► Слухи.")
    assert parts[1][4] == ["… Не-а, беру свои слова обратно."]
    assert parts[-1] == ("page", "Конец страницы. 1, 2")


def test_digit_groups_do_not_break_across_lines():
    # На телефоне «10 000» рвалось на «10» и «000».
    block = sw.render_window(["[Очки богатства: 2 618 757]", "[Оружейный камень: 10 000 лет]"], "status")

    assert "2 618 757" in block and "10 000" in block


def test_set_bonus_line_is_not_taken_for_the_window_title():
    # «Щит небосвода»: «2 предмета: …» — строка бонуса комплекта, а не «Предмет: Меч».
    lines = ["2 предмета: физическая защита +150%", "4 предмета: основные характеристики +150%"]

    block = sw.render_window(lines, "status")

    assert "2 ПРЕДМЕТА" not in block and block.count("<b ") == 2
    assert sw._looks_like_title("Предмет: Меч Рассвета")


def test_angle_bracketed_lines_are_system_messages():
    # «So I'm an Earth»: уведомления системы в угловых скобках, как в «Кумо».
    lines = [
        "< Повышен уровень мастерства. Навык [Память] достиг уровня 2>",
        "< Получена компетенция. Вы получили навык [Продвинутый слух Ур. 1]>",
    ]
    escaped = [line.replace("<", "&lt;").replace(">", "&gt;") for line in lines]
    html = _chapter("Я продолжила тренировку.", *escaped, "Отлично!", "&lt;Хр-р-р&gt;", "Кот спал.")

    windows = find_windows(html)
    block = sw.render_window(windows[0].lines, windows[0].kind)
    result, count = sw.apply_windows(html, windows)

    assert [window.lines for window in windows] == [lines]
    assert "Повышен уровень мастерства. Навык [Память] достиг уровня 2" in block and "&lt;" not in block
    assert sw.strip_windows(result) == (html, count)


def test_bullet_line_is_never_the_window_title():
    # «Dimensional Traveler»: список требований задания начинался пунктом.
    block = sw.render_window(["• 50 отжиманий (0/50)", "• 50 приседаний (0/50)", "Награда:", "+1 к Силе"], "notice")

    assert "ОТЖИМАНИЙ" not in block and "✦" not in block


def test_keyed_line_inside_outer_brackets_gets_a_bold_term():
    # «So I'm an Earth»: «<[Месть]: Если пользователь…>».
    block = sw.render_window(["<Гнев: при активации растёт атака.>", "<[Месть]: Если союзник погиб, растёт урон.>"], "status")

    assert "Месть:</b>" in block


def test_bbcode_inside_lines_is_dropped_for_detection_and_display():
    # «Данмачи Белл взрослый»: «[Вельф Кроццо] [spoiler] [/spoiler]».
    html = _chapter("Он открыл лист.", "[b]Имя:[/b] Вельф Кроццо", "[b]Уровень:[/b] 2", "Он закрыл лист.")

    windows = find_windows(html)

    assert [window.lines for window in windows] == [["Имя: Вельф Кроццо", "Уровень: 2"]]


def test_open_bracket_span_continues_the_current_window():
    # «Реинкарнация в злого дракона», гл. 78; «Пробудив Белого Жнеца», гл. 594.
    html = _chapter(
        "Дракон зарычал.",
        "[Поздравляем! Получено 5000 очков системы]",
        "[Высшее поглощение: поедание добычи ускоряет рост тела.",
        "Дополнительно: поглощение различных веществ дает соответствующие усиления.]",
        "[Текущие очки: 5000]",
        "Он довольно облизнулся.",
    )

    windows = find_windows(html)

    assert [len(window.lines) for window in windows] == [4]


def test_brackets_without_system_words_or_data_are_speech_in_the_book():
    # «Сукуна слишком добрый» — телепатия, «Годжо Бог» — кадр фильма,
    # «Моя соседка знаменитость» — комментарии стрима: в скобках нет ни слов
    # системы, ни чисел, ни пар «ключ: значение».
    telepathy = [
        _chapter("[Сверху!]", "Итадори пригнулся.", "[Эй… может, сделаешь перерыв?]", "[Это всего лишь первая тренировка.]", "Текст.")
        for _ in range(8)
    ]
    system = [_chapter("[Динь! Навык получен]", "[Сила: 12 → 15]", "[Молодец, носитель!]", "Текст.") for _ in range(8)]

    assert sw.book_bracket_conventions(telepathy)["speech_brackets"] is True
    assert sw.book_bracket_conventions(system)["speech_brackets"] is False


# --- поля карточек: одиночные термины, кавычки, длинные значения ------------------


def test_single_keyed_card_is_a_window():
    # «Star Rail»: карточка способности одним абзацем; «Полоска здоровья»: класс.
    html = _chapter(
        "Он открыл описание.",
        "[Фиолетовая способность: «Стопроцентный прорыв»]: с каким бы врагом вы ни столкнулись, шанс прорыва – 100%.",
        "Он хмыкнул.",
        "[Охотник]: Ты способен видеть полоску здоровья жертвы.",
        "Текст.",
        "[Дыхание] Зриода взрывается внутри его собственного тела, и меня отбрасывает прочь.",
        "Текст.",
    )

    lines = [window.lines for window in find_windows(html)]

    assert lines == [
        ["[Фиолетовая способность: «Стопроцентный прорыв»]: с каким бы врагом вы ни столкнулись, шанс прорыва – 100%."],
        ["[Охотник]: Ты способен видеть полоску здоровья жертвы."],
    ]


def test_key_value_line_fully_in_quotes_is_data():
    # «Носитель Венома», «Годжо в Убийце Богов», «Хвост Феи. Симуляция».
    html = _chapter(
        "Экран мигнул.",
        "«Токсичность крови: 43%».",
        "«Эффект: очистка организма от токсинов, ускорение метаболизма для их выведения».",
        "Он выдохнул.",
        "«Слушай: я не пойду».",
    )

    lines = [window.lines for window in find_windows(html)]

    assert lines == [["«Токсичность крови: 43%».", "«Эффект: очистка организма от токсинов, ускорение метаболизма для их выведения»."]]


def test_card_fields_with_long_or_lowercase_values_continue_the_card():
    # «Красный дракон», «Аномальный коллекционер», «Лавовый дракон», «Арканный поход».
    card = [
        "[Посох Бури]",
        "Качество: легендарное.",
        "Материал: адамантин.",
        "Описание: Ваши яростные атаки заставят трепетать всех мелких тварей. Но прежде чем взять его,"
        " убедитесь, что ваша сила не меньше 40, иначе отдача сломает вам руки!",
        "Форма первая: расход 1 заряда, эффект заклинания «Зеркальное отражение».",
        "Проявления заражения: за короткое время происходят резкие изменения скелетных мышц, кожа"
        " покрывается чешуёй, а зрачки вытягиваются в вертикальные щели.",
    ]
    html = _chapter("Он взял посох.", *card, "Он подумал: неплохо.", "Текст.")

    assert [window.lines for window in find_windows(html)] == [card]


# --- служебные строки, благодарности, реплики «Поздравляю», время -----------------


def test_placeholders_novel_end_and_bonus_marks_are_not_windows():
    html = _chapter(
        "[Изображение]", "Текст.", "[Картинка].", "Текст.", "[Конец романа]", "Текст.", "[Короткий бонус]", "Текст.",
        "[Благодарность пользователям 20210724204602184 за пожертвование; 20210724204602185 за поддержку]", "Текст.",
    )

    assert find_windows(html) == []


def test_site_info_page_has_no_windows():
    # Служебная страница книги с сайта: «ID книги», просмотры, аннотация.
    html = _chapter("ID книги: 7586325203580357694.", "Количество просмотров: 780.", "[Описание]",
                    "[Обыватель × Скрытный манипулятор Наруто × Исключительное обожание Хинаты.]")

    assert find_windows(html) == []


def test_speech_congratulations_and_clock_times_are_not_windows():
    html = _chapter(
        "«Поздравляю! Ты снова побил рекорд по количеству ежемесячных голосов на „Цидяне“, котик».",
        "Текст.",
        "«Поздравляем носителя с получением навыка „Кулинария“!»",
        "Текст.",
        "9:57. До начала – три минуты. Участников попросили перевести телефоны в беззвучный режим.",
        "10:00 ровно.",
    )

    assert [window.lines for window in find_windows(html)] == [["«Поздравляем носителя с получением навыка „Кулинария“!»"]]


def test_forum_welcome_after_end_of_page_starts_a_new_thread():
    # «Девять жизней Калико»: второй визит на форум сразу после «Конца страницы».
    second = ["Добро пожаловать на форумы «Паралюди Онлайн.»", "Вы вошли в систему как Тик-Так (Подтвержденный кейп)",
              "♦ Тема: Новости", "Баграт (Автор темы)", "Опубликовано 3 мая 2011:", "Кто-нибудь видел?", "Конец страницы. 1"]
    html = _chapter("Я открыла ноутбук.", *PHO_THREAD, *second, "Я закрыла ноутбук.")

    assert [window.lines for window in find_windows(html)] == [PHO_THREAD, second]


def test_empty_bracket_line_does_not_start_a_window_but_continues_one():
    # «So I'm an Earth»: «[…]» — разделитель между абзацами, 243 окна из 391.
    html = _chapter("Я огляделась.", "[…]", "Никого.", "[Динь! Начать слияние?]", "[…]", "Он молчал.")

    assert [window.lines for window in find_windows(html)] == [["[Динь! Начать слияние?]", "[…]"]]


def test_topic_with_arrow_marker_starts_a_forum_thread():
    # «Atonement»: «►Тема: …» вместо «♦ Тема: …».
    assert sw.forum_role("►Тема: Взрывы в заливе.") == "topic"
    assert sw.forum_role("►Topic: Привязь.") == "topic"


def test_leading_ellipsis_before_bracket_and_chat_line():
    # «I Am NOT Going Through»: «…[ОШИБКА: …]»; «Our Wild Love»: «… [Акира]: Обещаю.»
    assert sw.bracket_shape("…[ОШИБКА: ЗНАЧЕНИЕ НЕИЗВЕСТНО]") == "full"
    assert sw.chat_line("… [Акира]: Обещаю.") == sw.ChatLine("Акира", "Обещаю.", "bracket")


def test_chapter_titles_site_notes_photo_captions_and_author_notes_are_not_windows():
    html = _chapter(
        "[Глава 99: Начало]", "Текст.",
        "【Глава сорок четвертая: Эволюция】", "Текст.",
        "[Добавьте закладку, чтобы облегчить чтение]", "Текст.",
        "[Фотография: ночное звёздное небо. На земле возвышается гигантский купол.]", "Текст.",
        "От автора: Благодарю вас за терпение и понимание.", "Предупреждение: Сцены насилия и смерть персонажа.", "Текст.",
        "【Еще раз спасибо всем за поддержку, люблю вас всех!】", "Текст.",
        "Прим. 1: В этой книге классификация уровней выше божественного ранга своя.", "Текст.",
        "Омаке от Orian D'Cate: О големах.", "Текст.",
    )

    assert find_windows(html) == []


def test_phrase_before_colon_is_not_a_stat_key():
    # «Сорвать луну»: «Как гласит мудрость: «…»», «Дух Сяи изумился: «…»».
    assert not sw.is_key_value("Как гласит мудрость: «Знай врага своего и знай себя»")
    assert not sw.is_key_value("Дух Сяи изумился: «Но ведь она мертва!»")
    assert sw.is_key_value("Мудрость: 12")


def test_private_messages_view_after_welcome_is_a_forum_visit():
    # «Predatory», гл. 23: третий заход — список новых личных сообщений.
    visit = [
        "Welcome to the Parahumans Online message boards.", "Вы вошли как Джистринг-Гёрл.", "Вы просматриваете:",
        "• Темы, в которых вы отвечали…", "Новые личные сообщения (1):",
        "mr10tickles: Я ходячий, говорящий монстр со щупальцами, если ты об этом.", "GstringGirl: пищит от восторга!",
        "mr10tickles: Хорошо, я поговорю с ней.", "GstringGirl: ДА!",
    ]
    html = _chapter("Она открыла ноутбук.", *visit, "— Ты чего так улыбаешься? — спросила мама.")

    assert sw.forum_role("Новые личные сообщения (1):") == "pm"
    assert [window.lines for window in find_windows(html)] == [visit]


# --- приписки автора, ответы на отзывы, пустые реплики ----------------------------


def test_review_replies_and_credits_are_not_windows():
    # «The Demon Eyes of Fairy Tail», «Watch», «The Rise of Warrior Fairy Tail».
    html = _chapter(
        "(Ответы на старые отзывы)",
        "matiasl151: Спасибо, и это интересный вариант для пейринга.",
        "nickclause: Спасибо, я и сам всегда был фанатом Итачи.",
        "Guest: Спасибо.",
        "izica1: Спасибо.",
        "Итачи открыл глаза.",
        "Редакторская правка: dogbertcarroll, laros_deejay.",
        "Помощь с латынью: Эндрю Вулф.",
        "Ошибки исправлены: AlyssonR, Эндрю Чапмен.",
    )

    assert find_windows(html) == []


def test_exchange_of_bare_ellipses_is_not_a_chat():
    # «Пробудив Белого Жнеца»: «Линь Най: «…»», «Дракончик: «…»».
    assert sw.chat_verdict(["Линь Най: «…»", "Дракончик: «…»", "Линь Най: «…»"]) is None
    assert sw.chat_verdict(["Тан Сяо: «…»", "Тан Сань: «…»", "Тан Юань: «!!»"]) is None
    assert sw.chat_verdict(["Цзян Ци: «?»", "Юй Фан: «Если бы я сжег дом, всё было бы проще».", "Цзян Ци: «Ты где?»"]) == "chat"


def test_danmachi_status_sheet_keeps_magic_and_skills():
    # «Danmachi попаданец в Белла», гл. 36.
    sheet = [
        "Уровень 3.",
        "Прозвище: weiẞes Häschen.",
        "Сила: S: 998 – SS: 1166.",
        "Магия: SS: 1099 – SSS: 1383.",
        "Магия:",
        "(Огненный Болт)",
        "• Магия быстрого применения.",
        "(Хирайшин)",
        "• Магия зачарования.",
        "Навыки:",
        "Лиарис Фриз – Стремительный рост. Ускоряет развитие, пока чувства остаются неизменными.",
        "Аргонавт – Автоматический заряд при активном действии.",
    ]
    html = _chapter("Элпис без труда выбила из меня дух.", "Белл Кранел.", "Семья Гестии.", *sheet, "Я со вздохом отложил лист.")

    assert [window.lines for window in find_windows(html)] == [["Белл Кранел.", "Семья Гестии.", *sheet]]


def test_german_quoted_skill_names_continue_the_list():
    # «Kumo desu ka»: навыки в „лапках“.
    lines = ["# Навыки:", "„Стремительность, ур. 4.“", "„Лазанье по деревьям, ур. 5.“", "„Замедление расхода SP, ур. 2.“"]
    html = _chapter("Я проверила статус.", "HP: 1000/1000", "MP: 500/500", *lines, "Неплохо.")

    assert [window.lines for window in find_windows(html)] == [["HP: 1000/1000", "MP: 500/500", *lines]]


def test_whole_line_bracket_chat_is_a_chat_but_status_is_not():
    # «Our Wild Love», «Ace», «Пробудив Белого Жнеца»: «[Футаба: Подождите!]».
    lines = ["[Футаба: Подождите! Я еще морально не готова!]", "[Акира: Ты должна сделать это, Футаба.]",
             "[Футаба: Ладно, ладно…]"]
    html = _chapter("Телефон завибрировал.", *lines, "Он усмехнулся.")
    status = _chapter("Он взглянул на неё.", "[Имя: Анна Хэтэуэй]", "[Ранг: SSS]", "[Характер: робкая, добрая]", "Текст.")

    assert [(window.kind, window.lines) for window in find_windows(html)] == [("chat", lines)]
    assert [window.kind for window in find_windows(status)] == ["status"]


def test_polite_quote_without_system_hints_is_not_confirmed_by_source():
    # LOL: реплики зрителей в «кавычках» с обращением на «вы».
    assert not sw._source_confirms("«Мать вашу, Миллер, вы можете не тянуть кота за хвост?»")
    assert sw._source_confirms("«Ваша защита снижена на 10%»")
    assert sw._source_confirms("«Носитель, вы получили навык „Рывок“»")


def test_bracketed_speech_with_remark_and_social_posts_are_not_single_windows():
    # «Наруто»: реплики ИИ-спутницы с ремаркой; «Состояние…»: посты «Вэйбо».
    html = _chapter(
        "[Ну, ладно,] – светящийся шар Сяоянь описал пару кругов.", "Текст.",
        "[Угу…] – пискнула Сяоянь тише комара.", "Текст.",
        "[Сюй Литао V]: Для меня большая честь представлять родной университет!", "Текст.",
        "[Фильм «Ветер крепчает» V]: Фильм по сценарию @[Сюй Литао V] вышел в прокат.", "Текст.",
        "[Пилюля Вечной Юности]: Как следует из названия, пилюля останавливает старение.", "Текст.",
    )

    assert [window.lines for window in find_windows(html)] == [
        ["[Пилюля Вечной Юности]: Как следует из названия, пилюля останавливает старение."]
    ]


def test_dash_terms_in_prose_and_viewer_quotes_are_not_single_windows():
    # «Лицемерная серая жизнь», «Реинкарнация», LOL.
    html = _chapter(
        "[Доверие] – единственное оружие, которым Ичиносе могла бы разрушить замысел Сакаянаги.", "Текст.",
        "[Чистая правда (尺v尺)] – торжественно заверила система.", "Текст.",
        "«Принимаю ставки: ставлю сотку, что Олаф возьмет 3-й уровень против 1-го».", "Текст.",
        "«Я же говорил: чем страннее пик, тем быстрее победа. Думаю, управятся за 25 минут».", "Текст.",
        "«Возраст: 15 лет».", "Текст.",
    )

    assert [window.lines for window in find_windows(html)] == [["«Возраст: 15 лет»."]]


# --- речь в скобках, стикеры в чате, карточка в кавычках на несколько абзацев ------


def test_bracketed_speech_line_is_not_a_window_but_system_address_is():
    # «Kumo», «Caterpillar», «A New World»: реплики и телепатия в скобках.
    html = _chapter(
        "[Память у меня не настолько плохая. Ее ведь можно назвать моей гордостью.]", "Текст.",
        "[Наруто, твоя несносная девчонка только что пробралась в твою комнату.]", "Текст.",
        "[Человек, ты слышишь меня?]", "Текст.",
        "[Молодец, носитель! Ты справился.]", "Текст.",
        "[Ты получил навык «Рывок».]", "Текст.",
    )

    assert [window.lines for window in find_windows(html)] == [
        ["[Молодец, носитель! Ты справился.]"], ["[Ты получил навык «Рывок».]"],
    ]


def test_stickers_and_photos_inside_a_chat_keep_it_whole():
    # «Да вы издеваетесь!»: стикер посреди переписки в WeChat.
    lines = ["Кун Лю: «Ты её фанатка?»", "[Подозрение]", "Лу Си: «Нет, просто спросила».", "[Фото]",
             "Кун Лю: «Ага, конечно».", "Лу Си: «Ладно, фанатка».",]
    html = _chapter("Она открыла WeChat.", *lines, "Лу Си отложила телефон.")

    assert [(window.kind, window.lines) for window in find_windows(html)] == [("chat", lines)]


def test_quoted_card_across_paragraphs_is_one_window():
    # «Марвел»: плакат розыска в одних кавычках на три абзаца.
    card = ["«Цель: Эсдес.", "Требование: Живым! (В случае трудностей допускается ликвидация).",
            "Заказчики: Йонду Удонта, Ронан!", "Награда: 10 000 000 юнитов».",]
    html = _chapter("На стене висел плакат.", *card, "Питер присвистнул.")

    assert [window.lines for window in find_windows(html)] == [card]


def test_polite_system_messages_stay_and_viewer_comments_go():
    # «Полоска здоровья»: система на «вы»; LOL: комментарии зрителей.
    assert sw._source_confirms("«У вас недостаточно прав для доступа к этой информации».")
    assert sw._source_confirms("«Блогер „Ю“, на которого вы подписаны, загрузил новое видео».")
    assert sw._source_confirms("«Охота началась. Очевидно, его сила оказалась не так велика, как вы ожидали».")
    assert not sw._source_confirms("«Да ладно, вы серьезно радуетесь успехам этого балласта?»")
    assert not sw._source_confirms("«Ха-ха-ха, а вы видели, как его Кеннен сделал квадрокилл?»")
    assert not sw._source_confirms("«Стой линию, не подставляйся? Вы что тут, сказки рассказываете?»")


def test_skill_named_recall_is_not_an_author_note_header():
    # «Рефреш», гл. 315: навык «Отзыв» — не отзыв читателя.
    card = ["Бочи, потенциальный уровень 3.", "Врожденные способности: «Слоты снаряжения», «Отзыв», «Опутывание»."]
    html = _chapter("Что касается Бочи…", *card, "Он почесал затылок.")

    assert [window.lines for window in find_windows(html)] == [card]


def test_skill_names_are_bold_in_status_sheets():
    lines = ["Магия:", "(Огненный Болт)", "• Магия быстрого применения.", "Навыки:",
             "Лиарис Фриз – Стремительный рост. Ускоряет развитие.", "«Казан Души Клинка.»"]

    block = sw.render_window(lines, "status")

    assert "(Огненный Болт)</b>" in block
    assert "Лиарис Фриз</b> – Стремительный рост." in block
    assert "Казан Души Клинка.</b>" in block


def test_group_chat_replies_in_brackets_from_different_people_are_a_chat():
    # «Возрождение духовной энергии», гл. 211: ответы в группе без повторов.
    lines = ["[Шэнь Чжихао: Я участвую!]", "[Цзыи: Я тоже еду!]", "[Чжэнь Тяньюань: И я с вами!]"]
    html = _chapter("Затем он снова перевёл взгляд на чат.", *lines, "Ниже шло ещё несколько подобных ответов.")

    assert [(window.kind, window.lines) for window in find_windows(html)] == [("chat", lines)]


def test_system_prompts_and_second_person_system_voice_are_not_speech():
    # «Бизнес с карточками», «Реинкарнация», «Полоска здоровья», «Белый Жнец», «Король Демонов».
    for line in (
        "[Пожалуйста, выберите тип карты]",
        "[Пожалуйста, выберите место для размещения Темного бестиария.]",
        "[Ты осознаешь, что силы Казни и Вердикта, вероятно, связаны с подобными Правилами.]",
        "[Репутация – это мера твоего авторитета среди избранных.]",
        "[Цзян Вэньхао, Университет Минцин – выбыл!]",
        "[Да / Нет]",
        "[Гордыня: абсолютное эго, где собственное «я» – единственная и высшая ценность.]",
    ):
        assert not sw._speech_in_brackets(line), line
    for line in ("[Ты что, книг не читал?]", "[Твою ж мать!]", "[Господин Цзян, Вы и правда демон?]", "[Ого, а в голосе-то что – разочарование?]"):
        assert sw._speech_in_brackets(line), line


def test_ellipsis_spacers_inside_a_chat_keep_it_whole():
    # «Our Wild Love», гл. 82: реплики разделены абзацами «…».
    lines = ["[Сихо: Погодите… у Саэ завтра суд?]", "…", "… [Акэти: Работа прокурора заставляет вести несколько дел.]",
             "…", "…", "… [Хисато: Боже мой. Без обид, Макото, но это звучит как горы работы.]"]
    html = _chapter("После вылазки все разошлись по домам.", *lines, "Акира отложил телефон.")

    windows = find_windows(html)
    block = sw.render_window(windows[0].lines, "chat")

    assert [(window.kind, window.lines) for window in windows] == [("chat", lines)]
    assert block.count("float:") == 3 and ">…<" not in block


# --- мелочи по снимкам: звёзды, «Удача +10.», «Внимание:», ключи-местоимения -------


def test_card_with_stars_deltas_and_warning_stays_whole():
    # «Бизнес с карточками»: «Звездность: ★★»; «Щит»: «Удача +10.»; «Красный дракон»: «Внимание: …».
    card = [
        "Карта персонажа: Ван Эръя",
        "Звездность: ★★",
        "Удача +10.",
        "Особая характеристика: увеличивает шанс уклонения на 6%.",
        "Внимание: этот эффект может привести к дестабилизации арканной энергии в области.",
    ]
    html = _chapter("Описание карты изменилось.", *card, "Цзян Ци хмыкнул.")

    assert [window.lines for window in find_windows(html)] == [card]


def test_pronoun_before_colon_is_not_a_key():
    assert not sw.is_key_value("Я из будущего: FNC победят KZ в финале со счетом 3:1")
    assert not sw.is_key_value("Он подумал: неплохо бы поесть")
    assert sw.is_key_value("Навык: Контроль температуры")
    # Система говорит на «вы»: «Вы получили особую способность: Вечная Тьма.» («Красный дракон»).
    assert sw.is_key_value("Вы получили особую способность: Вечная Тьма.")


def test_colon_inside_parentheses_is_not_a_key_value_pair():
    # «Abaddon Borne»: заголовок «Развитие 2.x (Интерлюдия: Чак)» и авторское предупреждение под ним.
    html = _chapter("Развитие 2.x (Интерлюдия: Чак)", "Предупреждение: экстремальный расизм. Здесь нет хороших парней.",
                    "Чарли не понимал, то ли это сон, то ли его просто неслабо приложили.")
    assert find_windows(html) == []


def test_item_with_colon_in_parentheses_continues_a_card():
    # «The Limits of Power»: «Звёздная Мантия (Экипирована: Тейлор Эберт)» в списке артефактов.
    card = ["Артефакты:", "Звёздная Мантия (Экипирована: Тейлор Эберт)", "Золотой Лоток (Экипирован: Тейлор Эберт)"]
    html = _chapter("Она открыла инвентарь.", "Очки Жизни: 4", *card, "Тейлор вздохнула.")

    assert [window.lines for window in find_windows(html)] == [["Очки Жизни: 4", *card]]


def test_pronoun_phrase_does_not_continue_a_card():
    # «Пробуждение»: «Он открыл инвентарь: действительно, в первой ячейке…» — проза.
    html = _chapter("[Получен подарок]", "Он открыл инвентарь: действительно, в первой ячейке лежал подарок.", "Текст.")

    assert [window.lines for window in find_windows(html)] == [["[Получен подарок]"]]


def test_two_items_with_colons_in_parentheses_start_a_window():
    # «The Limits of Power»: предметы с владельцем в скобках идут подряд.
    items = ["Золотой Лоток (Экипирован: Тейлор Эберт)", "Звёздная Мантия (Экипирована: Тейлор Эберт)"]
    html = _chapter("Мокс Янтарь", *items, "Посох Небесного Ослепления")

    assert [window.lines for window in find_windows(html)] == [items]


# --- «The Game Begins»: одиночные сообщения, служебные строки чата, Арканы ------------


def test_typing_and_rename_lines_inside_a_chat_keep_it_whole():
    # Гл. 13: «… Содзиро печатает.» между сообщениями; гл. 63: «Футаба изменила имя на ДосВагина.».
    lines = [
        "Акира: «Здравствуйте, дядя Содзиро. Ничего, если я вернусь попозже?»",
        "… Содзиро печатает.",
        "Содзиро: «Возвращайся до закрытия кафе, тогда ладно».",
        "Акира: «Поняла. Спасибо вам огромное».",
    ]
    html = _chapter("Акира достала телефон и быстро набрала сообщение.", *lines, "Акира убрала телефон в карман.")
    windows = find_windows(html)

    assert [(window.kind, window.lines) for window in windows] == [("chat", lines)]
    block = sw.render_window(windows[0].lines, "chat")
    assert block.count("float:") == 3 and "… Содзиро печатает.</span>" in block

    renamed = [
        "Ниа: Но тебе действительно не стоит использовать это имя. Смени его.",
        "Футаба: Ладно, так лучше?",
        "Футаба изменила имя на ДосВагина.",
        "Ниа: Зачем?!",
        "ДосВагина: Был еще вариант ДосПингас.",
    ]
    html = _chapter("Ниа хмыкнул.", *renamed, "Он ведь наверняка пожалеет об этом?")
    assert [(window.kind, window.lines) for window in find_windows(html)] == [("chat", renamed)]


def test_typing_line_alone_or_rename_in_prose_is_not_a_window():
    html = _chapter(
        "— Покороче, пожалуйста, — попросил Ниа.",
        "ДосВагина печатает…",
        "Сожаление нахлынуло мгновенно.",
        "Став королевой, Алассра сменила имя на Симбул.",
    )

    assert find_windows(html) == []
    assert not sw._is_chat_service("Она снова сменила никнейм на один-единственный иероглиф: «И».")
    assert not sw._is_chat_service("В то же время она заметила, что Майлз снова печатает.")


def test_nickname_starting_with_a_digit_is_a_chat_speaker():
    # Гл. 82: «2-тян» — ник Ниа в общем чате.
    lines = [
        "2-тян: Рюдзи, а каково это – иметь маму?",
        "DreadPirateSakamoto: Ну это типа когда рядом очень добрый человек, который постоянно пилит тебя.",
        "2-тян: Кажется, меня только что усыновили.",
    ]
    html = _chapter("Ниа вытащил телефон.", *lines, "— Чудно, — буркнул Ниа, пряча телефон.")

    assert [(window.kind, window.lines) for window in find_windows(html)] == [("chat", lines)]
    # Порядковые «2-й», «1-е место» — не ники.
    assert not sw._looks_like_name("2-й")
    assert not sw._looks_like_name("1-е место")


def test_single_message_after_a_phone_line_is_a_chat_and_a_reply_is_the_owners():
    # Гл. 81: сообщение Акиры на телефоне Ниа и ответ Ниа под ником Near.
    red = "RedJoker: АКЭТИ НАБЛЮДАЕТ. ОН НЕ ПОДПУСТИТ НИКОГО К НИА, ПОКА МЫ НЕ ОБНАРУЖИМ СЕБЯ."
    near = "Near: Назад, пока нет нужды ввязываться в бой. Я собираюсь сбежать."
    html = _chapter(
        "На ходу она отправила сообщение остальным.",
        "У Ниа пискнул телефон – к всеобщему удивлению, мобильник до сих пор не разбился.",
        red,
        "— …Дерьмо, — Ниа поднялся на ноги и быстро напечатал ответ.",
        near,
        "Спрятав телефон, Ниа приготовился к худшему.",
    )
    windows = find_windows(html)

    assert [(window.kind, window.lines) for window in windows] == [("chat", [red]), ("chat", [near])]
    assert windows[0].chat_owner == "" and windows[1].chat_owner == "Near"


def test_single_message_between_chats_of_the_same_people_is_a_chat():
    # Гл. 63: одна реплика Футабы между двумя кусками переписки.
    first = ["Ниа: Кровавый что? Поясни?", "Футаба: Ну, кровавый дождь… ладно, проехали.", "Футаба: Забудь, это неважно."]
    lone = "Футаба: И вообще, почему у тебя до сих пор нет имени посмешнее?"
    second = ["Ниа: Посмешнее? Хочешь сказать, мое имя забавное?", "Футаба: Ниа – твое настоящее имя?", "Ниа: … Тут все сложно."]
    html = _chapter(
        "Ниа прищурился.", *first, "… Ниа был уверен, что Футаба видит искажения.", lone,
        "Ниа удивленно приподнял бровь.", *second, "Подросток хмыкнул.",
    )

    assert [(window.kind, window.lines) for window in find_windows(html)] == [
        ("chat", first), ("chat", [lone]), ("chat", second),
    ]


def test_single_colon_line_without_messaging_is_not_a_chat():
    for lines in (
        ["Он шёл домой.", "Сье: Ты тут? Чем занята?", "Он вздохнул."],
        # Приписка автора и заголовок — не собеседники.
        ["Пишите в чат читателей~", "PS2: Если вы не видите комментарии к главе – это нормально, у нас сбой.", "Текст."],
        ["Она вытащила мобильный телефон.", "Воспоминание: несколько месяцев назад…", "Текст."],
        # Новость в кавычках после слов о смартфоне — не переписка.
        ["Компания выпустила смартфон.", "«Невероятно: сегодня еще одна компания начала продажи!»", "Текст."],
    ):
        assert all(window.kind != "chat" for window in find_windows(_chapter(*lines))), lines
    # Сообщение ИИ в скобках на телефоне — системное окно, а не чат.
    html = _chapter("Линь Цие поспешно открыл телефон.", "[Предупреждение Управляющему: человек проявляет сильную враждебность.]", "Текст.")
    assert [window.kind for window in find_windows(html)] == ["notice"]


def test_persona_card_with_unknown_value_and_level_colon_stays_whole():
    # Гл. 90: карточка Персоны Футабы теряла шапку.
    card = [
        "Владелица Персоны: Футаба.",
        "Персона:?? — Отшельник.",
        "Ур. 35:",
        "Сопротивляемость: Слабость к льду, устойчивость к тьме/огню.",
        "Навыки: Агилао, Мегидо, Эйха, Контрудар, Масакунда",
        "HP: 266/266.",
        "SP: 220/220.",
    ]
    html = _chapter("— Интересно, — пробормотала Футаба, когда перед ее глазами всплыли параметры.", *card, "— Эх, — Футаба опустила голову.")

    assert [window.lines for window in find_windows(html)] == [card]


def test_numbered_dash_lists_under_labels_join_the_tally():
    # Гл. 89: списки Арканов над итогом «Старшие Арканы: 18.».
    lines = [
        "Старшие Арканы:", "0 – Шут.", "I – Маг.", "II – Верховная Жрица.", "XIV – Умеренность.",
        "Обратные Арканы:", "0 – Шут.", "i – Консультант.", "xvii – Комета.",
        "Старшие Арканы: 18.", "Обратные Арканы: 11.",
    ]
    html = _chapter("Величественные карты окружили её.", *lines, "— Кроме того, я советую вам вернуться ко сну, — сказал L.")

    assert [window.lines for window in find_windows(html)] == [lines]
    # Одна карта в прозе — не окно.
    assert find_windows(_chapter("Она поднесла карту к глазам.", "xi – Похоть.", "Акиру передернуло.")) == []


def test_shout_drawn_in_angle_brackets_is_not_a_notice():
    # Гл. 83: облачко крика Мисы «^^^ / < А НУ СТОЙТЕ! > / vvv — закричала Миса».
    html = _chapter("— Как я могу называть их героями?", "^^^^^^^^^", "< А НУ СТОЙТЕ! >", "vvvvvvvvv … — закричала Миса.")

    assert find_windows(html) == []
    # Уведомления в угловых скобках остаются («So I'm an Earth»).
    assert [window.lines for window in find_windows(_chapter("Текст.", "< УРОВЕНЬ ПОВЫШЕН! >", "Текст."))] == [["< УРОВЕНЬ ПОВЫШЕН! >"]]
    assert [window.lines for window in find_windows(_chapter("Текст.", "< Вы согласны?>", "Текст."))] == [["< Вы согласны?>"]]


def test_script_reply_after_a_phone_in_speech_is_not_a_chat():
    # «Сукуна слишком добрый», гл. 5: Годжо машет телефоном, а реплики записаны сценарием.
    html = _chapter(
        "— Однако… — он достал телефон и помахал им в воздухе. — Утренние лекции вам прочитает Фушигуро.",
        "Фушигуро: «Так я и знал».",
        "Итадори:",
        "— Ого! Фушигуро будет вести у нас уроки?",
    )

    assert [window.kind for window in find_windows(html)] == []


def test_verbs_and_emoticons_are_not_lone_messages():
    for lines in (
        ["Она открыла чат.", "Набрала: «Ты тут?», но так и не решилась нажать «отправить».", "Текст."],
        ["И вот вам приходит сообщение.", "Представьте: вы гуляете по парку, и тут снова сообщение?", "Текст."],
        ["— Не забудь выключить телефон, — пришло сообщение от учителя.", "Чэнь Фань: «∑(O_O;)»", "Текст."],
    ):
        assert [window.kind for window in find_windows(_chapter(*lines))] == [], lines


def test_paragraph_with_line_breaks_is_not_one_message():
    # «Our Wild Love»: вся переписка в одном абзаце через <br/> — одним пузырём её не показать.
    merged = "Акира: Привет всем. Простите, что не отвечал.<br/>Анн: О, я-то знаю.<br/>Рюдзи: Че? Ты знаешь?"
    html = _chapter("Телефон Акиры завибрировал от нового сообщения.", merged, "Он улыбнулся.")

    assert find_windows(html) == []


def test_lone_message_next_to_a_chat_in_another_form_is_not_a_chat():
    # «Белый Жнец»: «Обезьяна: «… Верю…»» сказано вслух, а «[Обезьяна: …]» — переписка.
    chat = ["[Обезьяна: Как ты это сделал?]", "[Линь Най: Тебя это не касается.]", "[Обезьяна: Ладно, молчу.]"]
    html = _chapter("— Теперь веришь?", "Обезьяна: «… Верю…»", "Обезьяна смотрел на него странно.", *chat, "Текст.")

    assert [(window.kind, window.lines) for window in find_windows(html)] == [("chat", chat)]


def test_lone_messages_do_not_introduce_chat_participants():
    # Одиночное сообщение не делает собеседников «известными» для коротких пар в других главах.
    first = _chapter(
        "Пришло сообщение от Фушигуро.", "Фушигуро: «Так я и знал».", "Текст.",
        "Пришло сообщение от Сукуны.", "Сукуна: «Где мой шоколад?»", "Текст.",
    )
    second = _chapter("— И шоколад еще!", "Сукуна: «… Как он догадался, что это я?!»", "Фушигуро: «Вы что, младшеклассники?»", "Текст.")

    scans = sw.scan_chapters([("a.html", "a.html", first, None), ("b.html", "b.html", second, None)])

    assert [window.kind for window in scans[0].candidates] == ["chat", "chat"]
    assert scans[1].candidates == []


def test_notice_followed_by_a_question_about_it_stays_a_window():
    # «So I'm an Earth»: «— Кто это сказал?» после уведомления — не ремарка к нему.
    notice = "< Опыт получен. Вы получили навык [Сопротивление огню ур. 1]>"
    html = _chapter("— Жжёт! — вскрикнула я.", notice, "— Кто это сказал? — Голос прозвучал прямо в моей голове.")
    assert [window.lines for window in find_windows(html)] == [[notice]]
    card = "[«Секретная карта. Наруто Узумаки: Преемник Хокаге»]"
    html = _chapter("На карте красовался юноша.", card, "— В магазине сказали, что эта карта самая редкая, — добавил Канкуро.")
    assert [window.lines for window in find_windows(html)] == [[card]]


def test_two_messages_glued_into_one_line_are_not_one_bubble():
    # «Predatory», гл. 12: перевод склеил два сообщения; под одним ником их не показать.
    html = _chapter(
        "Она быстро напечатала ответ.",
        "FlippinMad: какого хрена тебе от меня надо mr10tickles: Будь на месте, Мэдисон, не будь занудой.",
        "На последующие сообщения никто не ответил.",
    )
    assert find_windows(html) == []
    # Двоеточие внутри сообщения само по себе не мешает: «Еще один вопрос: как…».
    html = _chapter("Он отправил сообщение.", "Цзян Ци: «Еще один вопрос: как повышать звездный ранг?»", "Текст.")
    assert [window.kind for window in find_windows(html)] == ["chat"]


def test_lone_message_between_chats_keeps_the_phone_owner():
    # «The Game Begins», гл. 63: телефон Ниа; одинокая реплика Футабы — тоже слева.
    html = _chapter(
        "Телефон Ниа издал короткий сигнал.",
        "Футаба: Э-ге-гей! Ты в порядке?!", "Футаба: Прошло уже 24 часа!",
        "Ниа ахнул.",
        "Ниа: Я в порядке, не о чем беспокоиться.", "Футаба: А дождь ты видел?", "Футаба: Забудь.",
        "Ниа был уверен, что Футаба что-то видит.",
        "Футаба: И вообще, почему у тебя нет имени посмешнее?",
        "Ниа приподнял бровь.",
    )

    assert [window.chat_owner for window in find_windows(html)] == ["Ниа", "Ниа", "Ниа"]


def test_arcana_lists_render_numerals_and_both_labels_alike():
    # «The Game Begins», гл. 89: номера карт выделены одинаково, обе подписи — одинаково.
    lines = ["Старшие Арканы:", "0 – Шут.", "I – Маг.", "Обратные Арканы:", "0 – Шут.", "xvii – Комета.",
             "Старшие Арканы: 18.", "Обратные Арканы: 11."]

    block = sw.render_window(lines, "status")

    assert block.count("Арканы:</b>") == 4
    for numeral in ("0", "I", "xvii"):
        assert f">{numeral}</b> – " in block
    assert "СТАРШИЕ АРКАНЫ" not in block


def test_level_line_with_colon_is_a_bold_row_without_the_colon():
    # «The Game Begins», гл. 90: «Ур. 35:» в карточке Футабы.
    block = sw.render_window(["Владелица Персоны: Футаба.", "Ур. 35:", "HP: 266/266."], "status")

    assert ">Ур. 35</b>" in block and "Ур. 35:" not in block


# --- голос приложения в диалоге: Мета-Навигатор «The Game Begins» ---------------------


def test_navigator_voice_in_dialogue_is_a_notice_with_the_remark_kept_small():
    # Глава 37: «Совпадений не найдено», – отозвался Мета-Навигатор.
    lines = [
        "«Совпадений не найдено», – отозвался Мета-Навигатор.",
        "«Найдено совпадение», – ответил Мета-Навигатор. «Пожалуйста, введите место назначения и ключевое слово».",
        "Мета-Навигатор отозвался громким резким сигналом: «ДОСТУП ОГРАНИЧЕН».",
        "— Локация найдена, — отозвался голос навигационного приложения.",
        "«Тень находится в: Дворец Мадарамэ», – отозвался голос Мета-Навигатора.",
    ]
    for line in lines:
        html = _chapter("— Ититаро Мадарамэ, — произнёс он вслух.", line, "— Ну вот, — вздохнул Ниа.")
        windows = find_windows(html)
        assert [(window.kind, window.lines) for window in windows] == [("notice", [line])], line

    block = sw.render_window([lines[1]], "notice")
    assert "Найдено совпадение" in block and "Пожалуйста, введите место назначения и ключевое слово" in block
    assert "«" not in block.split(">", 1)[1]
    # Ремарка не пропадает, но набрана мелко.
    assert "font-size:0.85em" in block and "ответил Мета-Навигатор" in block


def test_short_quotes_right_after_the_navigator_are_its_answers():
    # «Ключевое слово принято».  «Неверное место назначения».
    html = _chapter(
        "«Найдено совпадение», – ответил Мета-Навигатор. «Введите место назначения».",
        "— Музей, — произнёс Ниа.",
        "«Ключевое слово принято».",
        "— Правда, я понятия не имею, где его Дворец, — пробормотал Ниа.",
        "«Неверное место назначения».",
        "— Ну вот, эксперимент провалился.",
    )

    lines = [window.lines for window in find_windows(html)]
    assert lines == [
        ["«Найдено совпадение», – ответил Мета-Навигатор. «Введите место назначения»."],
        ["«Ключевое слово принято»."],
        ["«Неверное место назначения»."],
    ]


def test_people_and_quotes_without_a_device_stay_text():
    for lines in (
        # Штурман корабля — человек, а не приложение.
        ["Корабль дрогнул.", "«Курс проложен», – ответил навигатор.", "Текст."],
        ["Он кивнул.", "«Я знаю», – ответил Ниа.", "Текст."],
        # Мысль в кавычках без навигатора рядом — не ответ приложения.
        ["Он задумался.", "«Какая глупость».", "Текст."],
        ["— Мета-Навигатор запущен! — Мисима нажал на кнопку.", "Текст."],
    ):
        assert find_windows(_chapter(*lines)) == [], lines


def test_a_character_speaking_through_a_mechanical_voice_is_not_a_notice():
    # «Less Than Zero»: злодей перехватил канал; «Бизнес с карточками»: игрушка говорит о себе.
    for line in (
        "— Нет, — произнес механический голос.",
        "— Приветствую, мой дражайший хозяин. Я доблестный кавалерист и буду охранять вас, — раздался электронный голос.",
    ):
        assert find_windows(_chapter("Экран внезапно переключился.", line, "— Брат.")) == [], line
    # Объявление тем же голосом — уведомление («Марвел»).
    line = "— Приношу извинения, на данном участке дороги сигнал заблокирован, — ответил механический голос."
    assert [window.kind for window in find_windows(_chapter("Он набрал номер.", line, "Текст."))] == ["notice"]


# --- смена ника в переписке: «Имя пользователя Ниа … изменено на 2-тян» -------------


def test_rename_forms_are_service_lines_of_a_chat():
    for line in (
        "ДосВагина меняет ник на ДосБадзина.",
        "Имя пользователя Ниа принудительно изменено на 2-тян.",
        "Футаба изменила имя на ДосВагина.",
    ):
        assert sw._is_chat_service(line), line
    assert sw._chat_rename("Имя пользователя Ниа принудительно изменено на 2-тян.") == ("Ниа", "2-тян")
    assert sw._chat_rename("ДосВагина меняет ник на ДосБадзина.") == ("ДосВагина", "ДосБадзина")
    assert sw._chat_rename("Став королевой, Алассра сменила имя на Симбул.") is None


def test_typing_line_after_the_last_message_stays_in_the_chat():
    lines = ["Ниа: Тебе длинную версию?", "Футаба: Да!", "Ниа: Подлиннее, пожалуйста.", "ДосВагина печатает…"]
    html = _chapter("Ниа хмыкнул.", *lines, "Сожаление нахлынуло мгновенно.")

    assert [(window.kind, window.lines) for window in find_windows(html)] == [("chat", lines)]


def test_renamed_owner_stays_on_the_right():
    # «The Game Begins», гл. 61: Футаба переименовала Ниа в «2-тян» посреди переписки.
    first = [
        "Футаба: Привет! Ты тут?", "Ниа: Тут.", "Футаба: Смотри, что я умею!",
        "Имя пользователя Ниа принудительно изменено на 2-тян.", "2-тян: Как?", "Футаба: Секрет!",
    ]
    second = ["2-тян: Ладно, мне пора.", "Футаба: Пока!", "2-тян: Пока."]
    html = _chapter("Телефон Ниа издал короткий сигнал.", *first, "Ниа закатил глаза.", *second, "Он убрал телефон.")
    windows = find_windows(html)
    templates = {kind: dict(template) for kind, template in DEFAULT_TEMPLATES.items()}

    assert [window.lines for window in windows] == [first, second]
    assert sw.window_readers(windows[0], templates["chat"]) == ["Ниа", "2-тян"]
    assert sw.window_readers(windows[1], templates["chat"]) == ["2-тян"]
    block = sw.render_window(windows[1].lines, "chat", templates={"chat": {**templates["chat"], "readers": ["2-тян"]}})
    assert block.count("float:right") == 2


def test_polite_request_is_live_speech_for_a_lone_message():
    # Гл. 61: «Ниа: Подлиннее, пожалуйста.» между кусками переписки.
    chat = ["Ниа: Зачем?!", "ДосВагина: Был еще вариант ДосПингас.", "ДосВагина: Тебе длинную версию или короткую?"]
    lone = ["Ниа: Подлиннее, пожалуйста.", "ДосВагина печатает…"]
    html = _chapter("Ниа хмыкнул.", *chat, "Он ведь наверняка пожалеет об этом?", *lone, "Сожаление нахлынуло мгновенно.")

    assert [window.lines for window in find_windows(html)] == [chat, lone]


def test_note_words_deep_inside_a_chat_message_do_not_make_an_author_note():
    # Гл. 137 «The Game Begins»: послесловие переписано строками чата; «бета-ридер» — в середине реплики.
    lines = [
        "Sushion: Всем привет! Я автор этого фанфика. А DD – мой бета-ридер и очень важный человек в творческом процессе.",
        "DD: Приветик, я его девушка.",
        "Sushion: Так вот… Этот финал. Просто вынос мозга, верно?",
        "DD: Ну, для начала, Гавайи взлетели на воздух…",
    ]
    html = _chapter(*lines)

    assert [(window.kind, window.lines) for window in find_windows(html)] == [("chat", lines)]


def test_reply_list_with_a_note_word_stays_an_author_note():
    # «The Demon Eyes of Fairy Tail», гл. 14: ответы на отзывы без заголовка, ники не повторяются.
    html = _chapter(
        "Mr. Haziq: Спасибо, что сообщил, к счастью, мне удалось исправить эту мелкую опечатку.",
        "Guest: Да, это было действительно мило, правда же?",
        "Hamza Shinwari: Мы уже пообщались в личных сообщениях.",
        "Amethyst Lavender: Всё в порядке, никаких обид.",
        "MayanPanther: Рад, что понравилось.",
        "Глава 12: «Орасьон Сейс!»",
    )
    assert find_windows(html) == []


def test_system_panel_with_a_polite_request_is_not_a_chat():
    # «Dimensional Traveler», гл. 12: «Пожалуйста» в системном предупреждении — не живая речь.
    panel = [
        "[Цель обнаружена: Четвёртая Низшая Луна.]",
        "[Местоположение: Заброшенная усадьба на окраине города Кояма.]",
        "[Предупреждение Системы: Цель представляет высокий уровень угрозы. Пожалуйста, тщательно подготовьтесь.]",
    ]
    assert [window.kind for window in find_windows(_chapter("Текст.", *panel, "Текст."))] == ["notice"]


def test_forum_replies_signed_answer_with_a_date_keep_the_thread_whole():
    # «Сын Симург», «19. Метаморфоза 8»: «Replied on April 21, 2011» → «Ответ 21 апреля 2011.».
    thread = [
        "Добро пожаловать на форумы «Паралюди Онлайн».",
        "Вы вошли в систему как: Антигона",
        "Тема: Новая ветка.",
        "В разделе: Форумы ► События ► Америка.",
        "Bagrat (Автор темы) (Проверенный пользователь)",
        "Опубликовано 20 апреля 2011:",
        "Видео смотреть здесь. Стражи ВСВ вынесены за пять минут.",
        "11 апреля: Лунг захвачен Оружейником.",
        "14 апреля: Неформалы грабят банк.",
        "15 апреля: Бакуда устраивает серию терактов.",
        "15 апреля: Маг, Рой и Сплетница схвачены СКП.",
        "15–16 апреля: Спешный суд вечером в выходной день.",
        "17 апреля: Во время транспортировки троица совершает побег.",
        "20 апреля: Рой и Маг появляются у здания Ассоциации докеров.",
        "Кто-нибудь, скажите мне, что за херня вообще происходит?",
        "xxvoidcowboyxx.",
        "Ответ 21 апреля 2011.",
        "Точно знаю, что Рой пытали в школе.",
        "Vista (подтвержденный кейп) (Стражи ВСВ)",
        "Ответ 21 апреля 2011.",
        "Мне нельзя отвечать. [вышла из сети]",
        "Конец страницы. 1, 2, 3, 4, 5 … 138, 139, 140, 141.",
    ]
    # Личная переписка сразу после «Конец страницы» — тот же экран форума: шапка
    # обещает «темы… ИЛИ личные сообщения».
    messages = ["Личное сообщение от AllSeeingEye:", "AllSeeingEye: У нас будет полно места.", "Antigone: Мне больше нечего терять."]
    html = _chapter("— Не наглей, Бэйли.", "~~Сын Симург~~", *thread, *messages, "— Вставай, — сказал Гарри.")

    windows = find_windows(html)

    assert [(window.kind, window.lines) for window in windows] == [("forum", thread + messages)]
    assert sw.forum_role("Ответ 21 апреля 2011.") == "time"
    assert sw.forum_role("Ответ был прост: нет.") != "time"
