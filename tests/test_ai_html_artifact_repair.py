from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from gemini_translator.ui.dialogs.validation import (
    AIRepairReviewPage,
    LINE_REVIEW_RISK_ROLE,
    ValidationThread,
    ai_repair_candidate_warning,
    ai_repair_protected_terms_from_glossary,
    apply_line_review_selection,
    build_line_review_segments,
    line_review_change_risk,
    should_auto_accept_line_review_change,
    _replace_all_literal_text,
)
from gemini_translator.utils.text import (
    escape_stray_angle_brackets,
    find_stray_angle_bracket_snippets,
    find_unwrapped_body_text_snippets,
    is_well_formed_xml,
    normalize_xhtml_tag_case,
    repair_ai_html_artifacts,
    validate_html_structure,
)


def _worker():
    return ValidationThread(
        translated_folder="",
        original_epub_path="",
        checks_config={},
        word_exceptions_set=set(),
        project_manager=None,
    )


def test_escape_stray_angle_brackets_preserves_real_tags():
    html = '<body><p>2 < 3 and 5 > 4</p><a href="notes.xhtml#n1">note</a></body>'

    repaired = escape_stray_angle_brackets(html)

    assert '<body><p>' in repaired
    assert '<a href="notes.xhtml#n1">note</a>' in repaired
    assert '2 &lt; 3 and 5 &gt; 4' in repaired


def test_repair_ai_html_artifacts_wraps_body_text_and_escapes_angles():
    original = '<html><body><p>One.</p><p>Two.</p></body></html>'
    translated = '<html><body><p>One < stray ></p>Loose text<p>Two ></p></body></html>'

    repaired = repair_ai_html_artifacts(original, translated)

    assert is_well_formed_xml(repaired)
    assert '<p>One &lt; stray &gt;</p>' in repaired
    assert '<p>Loose text</p>' in repaired
    assert '<p>Two &gt;</p>' in repaired
    assert find_unwrapped_body_text_snippets(repaired) == []
    assert find_stray_angle_bracket_snippets(repaired) == []


def test_repair_ai_html_artifacts_normalizes_xhtml_tag_case():
    original = '<html><body><p>Один.</p></body></html>'
    translated = '<html><body><p>Один.</P></body></html>'

    repaired = repair_ai_html_artifacts(original, translated)

    assert '<p>Один.</p>' in repaired
    assert '</P>' not in repaired
    assert is_well_formed_xml(repaired)


def test_repair_ai_html_artifacts_removes_duplicated_closing_tag_prefix():
    original = '<body><p>Ся Ваньци покачала головой.</p></body>'

    for broken_prefix in (
        '</</p>',
        '<</p>',
        '</  </p>',
        '</p.</p>',
        '</p</p>',
    ):
        translated = f'<body><p>Ся Ваньци покачала головой.{broken_prefix}</body>'

        repaired = repair_ai_html_artifacts(original, translated)

        assert '<p>Ся Ваньци покачала головой.</p>' in repaired
        assert '&lt;/' not in repaired
        assert is_well_formed_xml(repaired)


def test_repair_ai_html_artifacts_removes_orphan_close_prefix_before_dialogue_dash():
    original = '<body><p>Dialogue.</p></body>'
    translated = (
        "<body><p>— Исторические узлы, — отчетливо произнесла Вэнь Ци."
        "</— То есть так называемая концепция.</p></body>"
    )

    repaired = repair_ai_html_artifacts(original, translated)

    assert 'Вэнь Ци. — То есть' in repaired
    assert '&lt;/' not in repaired
    assert is_well_formed_xml(repaired)


def test_repair_ai_html_artifacts_preserves_complete_xhtml_shell_in_review():
    prefix = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        '<head>\n<title>第十六章 她不喝，我喝</title>\n</head>\n'
    )
    suffix = '\n</html>'
    original = prefix + '<body><p>她摇了摇头。</p></body>' + suffix
    translated = (
        prefix
        + '<body>\n<p>Ся Ваньци покачала головой.</</p>\n</body>'
        + suffix
    )

    repaired = repair_ai_html_artifacts(original, translated)
    _segments, changes = build_line_review_segments(translated, repaired)

    assert repaired.startswith(prefix)
    assert repaired.endswith(suffix)
    assert '<p>Ся Ваньци покачала головой.</p>' in repaired
    assert '&lt;/' not in repaired
    assert is_well_formed_xml(repaired)
    assert not ai_repair_candidate_warning(translated, repaired)
    assert len(changes) == 1
    assert changes[0]['kind'] == 'replace'
    assert not any(
        change['kind'] == 'delete'
        and any(
            marker in (change.get('old_text') or '')
            for marker in ('<?xml', '<!DOCTYPE', '<html', '<head', '<title', '</html>')
        )
        for change in changes
    )


def test_validate_html_structure_repairs_uppercase_closing_tag():
    original = '<html><body><p>Один.</p></body></html>'
    translated = '<html><body><p>Один.</P></body></html>'

    valid, reason, final_html = validate_html_structure(original, translated)

    assert valid, reason
    assert '</P>' not in final_html
    assert is_well_formed_xml(final_html)


def test_normalize_xhtml_tag_case_preserves_unknown_tags_and_attributes():
    html = '<BODY class="main"><CustomTag Data-ID="1">x</CustomTag><P>y</P></BODY>'

    normalized = normalize_xhtml_tag_case(html)

    assert '<body class="main">' in normalized
    assert '<p>y</p>' in normalized
    assert '<CustomTag Data-ID="1">x</CustomTag>' in normalized


def test_validation_analysis_flags_stray_angle_brackets():
    result = {
        "path": "Text/chapter.xhtml",
        "internal_html_path": "Text/chapter.xhtml",
    }
    original = '<html><body><p>One.</p></body></html>'
    translated = '<html><body><p>One > Two.</p></body></html>'

    analyzed = _worker()._analyze_html_content(original, translated, result)

    assert "stray_angle_brackets" in analyzed["structural_errors"]


def test_line_review_selection_can_accept_single_changed_line():
    old_html = "<body>\n<p>2 < 3</p>\nLoose text\n</body>\n"
    new_html = "<body>\n<p>2 &lt; 3</p>\n<p>Loose text</p>\n</body>\n"

    segments, changes = build_line_review_segments(old_html, new_html)

    assert len(changes) == 2
    accepted_ids = {changes[0]["id"]}
    selected_html = apply_line_review_selection(segments, accepted_ids)

    assert "<p>2 &lt; 3</p>" in selected_html
    assert "Loose text\n" in selected_html
    assert "<p>Loose text</p>" not in selected_html


def test_line_review_selection_accepts_insert_and_rejects_delete():
    old_html = "<body>\n<p>Keep.</p>\n<p>Remove.</p>\n</body>\n"
    new_html = "<body>\n<p>Keep.</p>\n<p>Added.</p>\n</body>\n"

    segments, changes = build_line_review_segments(old_html, new_html)
    accepted_ids = {change["id"] for change in changes if change["new_text"] and "Added" in change["new_text"]}
    selected_html = apply_line_review_selection(segments, accepted_ids)

    assert "<p>Added.</p>" in selected_html
    assert "<p>Remove.</p>" not in selected_html


def test_line_review_splits_minified_body_without_deleting_unchanged_paragraphs():
    old_html = "<body>\n<h1>Title</h1>\n<p>Keep.</p>\n<p>Also keep.</p>\n</body>\n"
    new_html = "<body><h1>Title</h1><p>Keep.</p><p>Also keep.</p><p>Added.</p></body>"

    _segments, changes = build_line_review_segments(old_html, new_html)

    assert any(change["kind"] == "insert" and "Added" in (change["new_text"] or "") for change in changes)
    assert not any(change["kind"] == "delete" and "Keep" in (change["old_text"] or "") for change in changes)


def test_line_review_does_not_auto_accept_deletions():
    change = {
        "kind": "delete",
        "old_text": "<p>Normal paragraph.</p>\n",
        "new_text": None,
    }

    assert not should_auto_accept_line_review_change(change)


def test_line_review_rejects_a_closing_tag_escaped_as_visible_text():
    change = {
        "kind": "replace",
        "old_text": "<p>Text.</</p>\n",
        "new_text": "<p>Text.&lt;/</p>\n",
    }

    assert "видимый текст" in line_review_change_risk(change)
    assert not should_auto_accept_line_review_change(change)


def test_ai_repair_candidate_warning_flags_removed_xhtml_shell():
    original = (
        '<?xml version="1.0"?><html><head><title>Title</title></head>'
        '<body><p>Text.</p></body></html>'
    )
    repaired = '<body><p>Text.</p></body>'

    warning = ai_repair_candidate_warning(original, repaired)

    assert "<html>" in warning
    assert "<head>" in warning


def test_ai_repair_extracts_protected_russian_glossary_terms():
    protected = ai_repair_protected_terms_from_glossary([
        {"original": "SeaGuard", "rus": "Стражморя", "aliases": ["Морской страж"]},
        {"original": "Небесный Путь", "rus": ""},
    ])

    assert protected == {"Стражморя", "Морской страж", "Небесный Путь"}


def test_ai_repair_review_updates_selection_summary_and_manual_edit_risk():
    app = QApplication.instance() or QApplication([])
    old_html = "<body>\n<p>Bad.</p>\n</body>\n"
    repaired_html = "<body>\n<p>Good.</p>\n</body>\n"
    segments, changes = build_line_review_segments(old_html, repaired_html)
    page = AIRepairReviewPage(
        [{
            "row": 1,
            "chapter": "chapter.xhtml",
            "original_html": old_html,
            "repaired_html": repaired_html,
            "segments": segments,
            "changes": changes,
            "warning": "",
        }]
    )

    try:
        assert "Выбрано: 1/1" in page.selection_summary_label.text()
        assert page.apply_button.isEnabled()

        page._set_all_checked(False)
        assert "Выбрано: 0/1" in page.selection_summary_label.text()
        assert not page.apply_button.isEnabled()

        page.table.item(0, 5).setText("<p>Good.&lt;/</p>")
        app.processEvents()

        check_item = page.table.item(0, 0)
        assert check_item.checkState() == Qt.CheckState.Checked
        assert "видимый текст" in check_item.data(LINE_REVIEW_RISK_ROLE)
        assert "Рискованных: 1" in page.selection_summary_label.text()
        assert page.apply_button.isEnabled()
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_line_review_selection_uses_manual_edit_text():
    old_html = "<body>\n<p>Bad.</p>\n</body>\n"
    new_html = "<body>\n<p>Good.</p>\n</body>\n"

    segments, changes = build_line_review_segments(old_html, new_html)
    replace_change = next(change for change in changes if change["kind"] == "replace")
    selected_html = apply_line_review_selection(
        segments,
        {replace_change["id"]},
        {replace_change["id"]: "<p>Manual.</p>\n"},
    )

    assert "<p>Manual.</p>" in selected_html
    assert "<p>Good.</p>" not in selected_html


def test_manual_replace_all_can_ignore_case():
    text, count = _replace_all_literal_text("Alpha alpha ALPHA", "alpha", "beta", match_case=False)

    assert text == "beta beta beta"
    assert count == 3


def test_ai_repair_candidate_warning_flags_large_text_loss():
    original = "<body>" + "".join(f"<p>Visible paragraph {idx} with text.</p>" for idx in range(8)) + "</body>"
    repaired = "<body><p>Visible paragraph 1 with text.</p></body>"

    assert ai_repair_candidate_warning(original, repaired)
