from gemini_translator.core.auto_workflow_helpers import (
    auto_result_uses_cjk_ratio,
    build_sequential_chapter_chains,
    compose_auto_details,
    describe_auto_payload,
    effective_auto_short_ratio_limit,
    estimate_auto_task_size_limit,
    extract_chapters_from_payload,
    format_auto_chapter_list,
    make_auto_chapter_signature,
    merge_auto_details,
    normalize_auto_chapters,
    short_auto_name,
    text_has_cjk,
    truncate_auto_trace_text,
)


def test_extract_chapters_from_payload_handles_supported_task_types():
    assert extract_chapters_from_payload(("epub", "book.epub", "chapter.xhtml")) == ["chapter.xhtml"]
    assert extract_chapters_from_payload(("epub_chunk", "book.epub", "chapter.xhtml", 1)) == ["chapter.xhtml"]
    assert extract_chapters_from_payload(("epub_batch", "book.epub", ["a.xhtml", "b.xhtml"])) == [
        "a.xhtml",
        "b.xhtml",
    ]
    assert extract_chapters_from_payload(("unknown",)) == []


def test_auto_short_ratio_uses_cjk_sources_and_callback():
    assert text_has_cjk("她抬头看向窗外。")
    assert not text_has_cjk("She looked out the window.")
    assert auto_result_uses_cjk_ratio({"is_cjk_original": True})
    assert auto_result_uses_cjk_ratio({"original_html": "<p>彼は笑った。</p>"})
    assert auto_result_uses_cjk_ratio(
        {"internal_html_path": "Text/chapter.xhtml"},
        chapter_has_cjk=lambda path: path == "Text/chapter.xhtml",
    )

    ratio_limit, profile = effective_auto_short_ratio_limit(
        {"retry_short_ratio": 0.70},
        {"original_text": "她抬头看向窗外。"},
    )

    assert ratio_limit == 1.80
    assert profile == "CJK"


def test_auto_short_ratio_keeps_alphabetic_user_limit():
    ratio_limit, profile = effective_auto_short_ratio_limit(
        {"retry_short_ratio": 0.82},
        {"original_text": "She looked out the window."},
    )

    assert ratio_limit == 0.82
    assert profile == "alphabetic"


def test_estimate_auto_task_size_limit_clamps_to_supported_range():
    assert estimate_auto_task_size_limit(0) == (None, None)
    assert estimate_auto_task_size_limit(120) == (500, "Gemini-токены")
    assert estimate_auto_task_size_limit(2000) == (2000, "Gemini-токены")
    assert estimate_auto_task_size_limit(999999) == (350000, "Gemini-токены")


def test_build_sequential_chapter_chains_splits_evenly_and_safely():
    chapters = ["ch1", "ch2", "ch3", "ch4", "ch5"]

    assert build_sequential_chapter_chains(chapters, 2) == [["ch1", "ch2"], ["ch3", "ch4", "ch5"]]
    assert build_sequential_chapter_chains(chapters, 99) == [["ch1"], ["ch2"], ["ch3"], ["ch4"], ["ch5"]]
    assert build_sequential_chapter_chains(chapters, "bad") == [chapters]
    assert build_sequential_chapter_chains([], 3) == []


def test_normalize_auto_chapters_deduplicates_and_sorts_naturally():
    chapters = ["chapter_10.xhtml", "chapter_2.xhtml", "", "chapter_2.xhtml", None]

    assert normalize_auto_chapters(chapters) == ["chapter_2.xhtml", "chapter_10.xhtml"]
    assert normalize_auto_chapters(chapters, preserve_order=True) == ["chapter_10.xhtml", "chapter_2.xhtml"]
    assert make_auto_chapter_signature(chapters) == ("chapter_2.xhtml", "chapter_10.xhtml")


def test_auto_chapter_formatting_helpers():
    assert short_auto_name("OEBPS/Text/chapter.xhtml") == "chapter.xhtml"
    assert short_auto_name("x" * 10, max_length=5) == "xxxx…"
    assert format_auto_chapter_list([]) == "нет глав"
    assert format_auto_chapter_list(["a.xhtml", "b.xhtml", "c.xhtml"], limit=2, preserve_order=True) == (
        "a.xhtml, b.xhtml, … +1"
    )


def test_auto_detail_text_helpers():
    details = compose_auto_details([
        ("Главы", ["chapter_2.xhtml", "chapter_1.xhtml"]),
        ("Пусто", []),
        (None, "plain text"),
    ])

    assert "Главы:\n- chapter_2.xhtml\n- chapter_1.xhtml" in details
    assert "Пусто" not in details
    assert "plain text" in details
    assert merge_auto_details(" first ", "", "second") == "first\n\nsecond"
    assert truncate_auto_trace_text("abcdefghijklmnopqrstuvwxyz", limit=20) == "abcd\n...[truncated]..."


def test_describe_auto_payload_uses_task_type_labels():
    assert describe_auto_payload(("epub", "book.epub", "chapter.xhtml")) == "глава: chapter.xhtml"
    assert describe_auto_payload(("epub_chunk", "book.epub", "chapter.xhtml")) == "чанк: chapter.xhtml"
    assert describe_auto_payload(("epub_batch", "book.epub", ["a.xhtml", "b.xhtml"])) == (
        "пакет 2 глав: a.xhtml, b.xhtml"
    )
