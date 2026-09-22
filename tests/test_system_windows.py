# -*- coding: utf-8 -*-
"""Системные окна: поиск серий системных строк и их оформление."""

import re

from gemini_translator.utils.system_windows import (
    DetectorSettings,
    find_windows,
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
