from gemini_translator.utils.txt_importer import (
    BASIC_CJK_CHAPTER_REGEX,
    BRACKETED_TXT_FILENAME_CHAPTER_REGEX,
    KOREAN_EPISODE_CHAPTER_REGEX,
    TxtChapterAnalyzer,
    collapse_adjacent_duplicate_txt_chapters,
    find_cjk_chapter_number_gaps,
)


def test_basic_cjk_regex_matches_markdown_and_plain_chapter_titles():
    text = "\n".join(
        [
            "# 第1章 这就是美利坚，这就是纽约",
            "第一段正文",
            "第181章 移动手术室",
            "第一百八十一次手术开始了",
        ]
    )

    boundaries = TxtChapterAnalyzer(text).scan_chapter_boundaries(
        custom_regex=BASIC_CJK_CHAPTER_REGEX
    )

    assert [item["title"] for item in boundaries] == [
        "# 第1章 这就是美利坚，这就是纽约",
        "第181章 移动手术室",
    ]


def test_auto_analysis_offers_one_regex_for_mixed_cjk_headings():
    text = "\n".join(
        [
            "# 第1章 一",
            "正文内容 " * 100,
            "第2章 二",
            "正文内容 " * 100,
            "#第三章 三",
            "正文内容 " * 100,
        ]
    )

    results = TxtChapterAnalyzer(text).analyze_potential_markers()
    patterns = [marker[1] for marker, _count in results]

    assert BASIC_CJK_CHAPTER_REGEX in patterns


def test_cjk_duplicate_heading_lines_are_collapsed_and_strict_regex_wins():
    text = "\n".join(
        [
            "第1章修仙",
            "正文内容 " * 100,
            "第2章摇光圣地到来",
            "第2章摇光圣地来临",
            "正文内容 " * 100,
            "第3章态度的变化",
            "",
            "第3章态度的变化",
            "正文内容 " * 100,
            "第4章启程",
            "第4章启程",
            "正文内容 " * 100,
        ]
    )

    analyzer = TxtChapterAnalyzer(text)
    boundaries = analyzer.scan_chapter_boundaries(
        custom_regex=BASIC_CJK_CHAPTER_REGEX
    )
    results = analyzer.analyze_potential_markers()

    assert [item["title"] for item in boundaries] == [
        "第1章修仙",
        "第2章摇光圣地到来",
        "第3章态度的变化",
        "第4章启程",
    ]
    assert results[0][0][1] == BASIC_CJK_CHAPTER_REGEX
    assert results[0][1] == 4


def test_bracketed_txt_filename_regex_matches_dumped_chapter_titles():
    text = "\n".join(
        [
            "---------------------------",
            " [ 0001_프롤로그..txt ] ",
            "---------------------------",
            "첫 번째 본문",
            "---------------------------",
            " [ 0002_Hello. World!.txt ] ",
            "---------------------------",
            "두 번째 본문",
        ]
    )

    boundaries = TxtChapterAnalyzer(text).scan_chapter_boundaries(
        custom_regex=BRACKETED_TXT_FILENAME_CHAPTER_REGEX
    )

    assert [item["title"] for item in boundaries] == [
        "[ 0001_프롤로그..txt ]",
        "[ 0002_Hello. World!.txt ]",
    ]


def test_auto_analysis_prioritizes_bracketed_txt_filename_headings():
    text = "\n".join(
        f" [ {number:04d}_제목 {number}.txt ] \n" + ("나는 반복되는 본문입니다.\n" * 40)
        for number in range(1, 5)
    )

    results = TxtChapterAnalyzer(text).analyze_potential_markers()

    assert results[0][0][1] == BRACKETED_TXT_FILENAME_CHAPTER_REGEX
    assert results[0][1] == 4


def test_korean_episode_regex_matches_plain_and_hwa_titles_only():
    text = "\n".join(
        [
            "Название книги",
            "1",
            "첫 번째 본문",
            "1.",
            "Это число внутри текста",
            "2",
            "두 번째 본문",
            "199화",
            "50층",
            "세 번째 본문",
            "200화",
            "연을 엮다 (1)",
            "네 번째 본문",
        ]
    )

    boundaries = TxtChapterAnalyzer(text).scan_chapter_boundaries(
        custom_regex=KOREAN_EPISODE_CHAPTER_REGEX
    )

    assert [item["title"] for item in boundaries] == ["1", "2", "199화", "200화"]


def test_auto_analysis_prioritizes_mixed_korean_episode_headings():
    text = "\n".join(
        f"{number}{'화' if number >= 199 else ''}\n"
        + ("한유성은 반복되는 본문입니다.\n" * 40)
        for number in (1, 2, 199, 200, 201)
    )

    results = TxtChapterAnalyzer(text).analyze_potential_markers()

    assert results[0][0][1] == KOREAN_EPISODE_CHAPTER_REGEX
    assert results[0][1] == 5


def test_cjk_sequence_filter_keeps_prose_that_looks_like_chapter_headers():
    text = "\n".join(
        [
            "# 第268章 正常章节",
            "正文开始。",
            "第1928章 年，量子隧穿解释了衰变。",
            "正文继续。",
            "# 第269章 下一章",
            "正文。",
            "# 第287章 正常章节",
            "第一章引言，第三页倒数第二段。",
            "第1963章 年，方法被正式发表。",
            "# 第288章 下一章",
            "正文。",
            "# 第289章 正常章节",
            "第六章，倒数第三页。",
            "正文中间。",
            "第六章倒数第三页？",
            "# 第290章 下一章",
            "正文。",
        ]
    )

    analyzer = TxtChapterAnalyzer(text)
    boundaries = analyzer.scan_chapter_boundaries(
        custom_regex=BASIC_CJK_CHAPTER_REGEX
    )
    chapters, titles = analyzer._split_by_marker(
        custom_regex=BASIC_CJK_CHAPTER_REGEX
    )

    assert [item["title"] for item in boundaries] == [
        "# 第268章 正常章节",
        "# 第269章 下一章",
        "# 第287章 正常章节",
        "# 第288章 下一章",
        "# 第289章 正常章节",
        "# 第290章 下一章",
    ]
    assert titles == [item["title"] for item in boundaries]
    assert "第1928章 年" in "".join(chapters[0])
    assert "第一章引言" in "".join(chapters[2])
    assert "第六章倒数第三页" in "".join(chapters[4])


def test_full_adjacent_cjk_chapter_duplicate_is_removed_but_parts_are_kept():
    repeated = ("李东继续研究这个问题。" * 100) + "【逻辑 +0.1（永久）】"
    repeated_variant = ("李东继续研究这个问题。" * 100) + "【逻辑+0.1(永久)】"
    distinct_part = "另一部分拥有完全不同的正文。" * 100

    filtered, removed = collapse_adjacent_duplicate_txt_chapters(
        [
            ("# 第294章 回应", repeated),
            ("第294章回应", repeated_variant),
            ("第295章 新章节", distinct_part),
            ("第295章 新章节（下）", "下半部分。" * 100),
        ]
    )

    assert [title for title, _content in filtered] == [
        "# 第294章 回应",
        "第295章 新章节",
        "第295章 新章节（下）",
    ]
    assert removed == [{
        "title": "第294章回应",
        "duplicate_of": "# 第294章 回应",
    }]


def test_cjk_gap_detection_reports_missing_source_chapter_without_renumbering():
    chapters = [
        ("# 第299章 比赛", "正文"),
        ("# 第301章 太阳事件", "正文"),
    ]

    assert find_cjk_chapter_number_gaps(chapters) == [(299, 301)]
