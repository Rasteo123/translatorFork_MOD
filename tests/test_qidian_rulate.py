import json

from qidian_rulate.models import QidianBookMetadata
from qidian_rulate.models import PreparedRulateMetadata, RulateBookDraft
from qidian_rulate import workers
from gemini_translator.ui.dialogs import qidian_rulate_creator as creator_module
from gemini_translator.ui.dialogs.qidian_rulate_creator import QidianRulateCreatorWindow
from qidian_rulate.workers import (
    _is_browser_missing_error,
    _clean_qidian_description,
    _clean_qidian_chapter_text,
    _extract_qidian_description_from_body,
    _QIDIAN_CHAPTER_LINKS_SCRIPT,
    _select_qidian_description,
    _tag_file_candidates,
    RULATE_BOOK_TYPE_DESCRIPTION,
    RULATE_BOOK_TYPE_SELECTOR,
    RULATE_BOOK_TYPE_TITLE,
    RULATE_CATEGORY_URL,
    RULATE_CHINESE_CATEGORY_TITLE,
    RULATE_INFO_URL,
    RULATE_PROFILE_DIR,
    RulateFillWorker,
    build_ai_prompt,
    build_catalog_prompt,
    build_cover_prompt_request,
    clean_cover_prompt_response,
    normalize_rulate_tags,
    parse_catalog_metadata,
    parse_prepared_metadata,
    parse_translation_metadata,
    validate_qidian_url,
)


FANTASY = "\u0444\u044d\u043d\u0442\u0435\u0437\u0438"
MYSTIC = "\u043c\u0438\u0441\u0442\u0438\u043a\u0430"
ADVENTURE = "\u043f\u0440\u0438\u043a\u043b\u044e\u0447\u0435\u043d\u0438\u044f"


class _QidianCreatorHarness:
    _return_to_menu = QidianRulateCreatorWindow._return_to_menu

    def __init__(self, handler=None):
        self._return_to_menu_handler = handler
        self.calls = []

    def hide(self):
        self.calls.append("hide")

    def close(self):
        self.calls.append("close")


class _FillDescriptionHarness:
    _fill_description = RulateFillWorker._fill_description

    def __init__(self):
        self.logs = []
        self.draft = RulateBookDraft(
            qidian=QidianBookMetadata(
                source_url="https://www.qidian.com/book/1041604040/",
                title_original="\u5f02\u5ea6\u65c5\u793e",
                author_name="\u8fdc\u77b3",
                description="\u63cf\u8ff0",
                cover_url="https://example.com/cover.webp",
            ),
            prepared=PreparedRulateMetadata(
                translated_description="\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435",
                genres=[],
                tags=[],
            ),
        )

    def log(self, level, message):
        self.logs.append((level, message))


class _DescriptionPage:
    def __init__(self):
        self.filled_selectors = []
        self.selected_options = []
        self.evaluated = []

    def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))

    def locator(self, selector):
        class _Locator:
            def wait_for(self, **kwargs):
                return None

        return _Locator()

    def wait_for_timeout(self, timeout):
        return None

    def select_option(self, selector, value):
        self.selected_options.append((selector, value))


def test_validate_qidian_url_accepts_book_links_only():
    assert validate_qidian_url("https://www.qidian.com/book/1041604040/")
    assert validate_qidian_url("http://qidian.com/book/1041604040")
    assert validate_qidian_url("https://www.qidian.com/book/1041604040/?source=m")
    assert not validate_qidian_url("https://www.qidian.com/author/4362948/")
    assert not validate_qidian_url("https://www.qidian.com/book/1041604040/catalog/")
    assert not validate_qidian_url("https://example.com/book/1041604040/")


def test_qidian_rulate_profile_is_separate_from_ranobelib_uploader():
    assert ".qidian_rulate_creator" in str(RULATE_PROFILE_DIR)
    assert ".ranobelib_uploader" not in str(RULATE_PROFILE_DIR)


def test_tag_file_candidates_use_program_area(monkeypatch):
    monkeypatch.delenv("RULATE_TAGS_FILE", raising=False)

    candidates = list(_tag_file_candidates())
    candidate_strings = [str(path).lower() for path in candidates]

    assert any("qidian_rulate" in path and path.endswith("tags.txt") for path in candidate_strings)
    assert not any(
        path.name.lower() == "tags.txt" and path.parent.name.lower() == "downloads"
        for path in candidates
    )


def test_rulate_fill_uses_category_page_before_info_page():
    assert RULATE_CATEGORY_URL == "https://tl.rulate.ru/book/0/edit/cat"
    assert RULATE_BOOK_TYPE_TITLE == "Книга"
    assert RULATE_BOOK_TYPE_DESCRIPTION == "Публикуйте свои произведения"
    assert RULATE_BOOK_TYPE_SELECTOR == 'a.create-card.card-book[href*="typ=A"]'
    assert RULATE_CHINESE_CATEGORY_TITLE == "Китайские"
    assert RULATE_INFO_URL == "https://tl.rulate.ru/book/0/edit/info#general"


def test_qidian_creator_return_to_menu_closes_before_handler():
    handler_calls = []
    harness = _QidianCreatorHarness(handler=lambda: handler_calls.append("handler"))

    harness._return_to_menu()

    assert harness.calls == ["hide", "close"]
    assert handler_calls == ["handler"]


def test_qidian_creator_return_to_menu_without_handler_closes_then_reboots(monkeypatch):
    reboot_calls = []
    monkeypatch.setattr(creator_module, "return_to_main_menu", lambda: reboot_calls.append("menu"))
    harness = _QidianCreatorHarness()

    harness._return_to_menu()

    assert harness.calls == ["close"]
    assert reboot_calls == ["menu"]


def test_rulate_description_fill_does_not_insert_cover_url(monkeypatch):
    filled = []
    monkeypatch.setattr(workers, "_fill", lambda page, selector, value: filled.append((selector, value)))

    harness = _FillDescriptionHarness()
    page = _DescriptionPage()

    harness._fill_description(page)

    assert "#Book_new_img_url" not in [selector for selector, _value in filled]
    assert ('select[name="Book[status]"]', "1") in page.selected_options


def test_parse_prepared_metadata_strips_json_fence_and_normalizes_lists(monkeypatch):
    allowed_tags = [
        "sci-fi",
        "\u0442\u0430\u0439\u043d\u044b",
        "\u043c\u0438\u0441\u0442\u0438\u043a\u0430",
        "\u043f\u0443\u0442\u0435\u0448\u0435\u0441\u0442\u0432\u0438\u0435 \u0432 \u0434\u0440\u0443\u0433\u043e\u0439 \u043c\u0438\u0440",
    ]
    monkeypatch.setattr(workers, "load_rulate_tags", lambda: allowed_tags)
    payload = {
        "english_title": "Otherworldly Inn",
        "translated_title": "\u0418\u043d\u043e\u043c\u0435\u0440\u043d\u0430\u044f \u0433\u043e\u0441\u0442\u0438\u043d\u0438\u0446\u0430",
        "translated_description": "\u0422\u0435\u043a\u0441\u0442\n\n\n\u043e\u043f\u0438\u0441\u0430\u043d\u0438\u044f",
        "genres": [FANTASY.upper(), MYSTIC, "unknown"],
        "tags": [
            "SCI-FI",
            "\u0422\u0430\u0439\u043d\u044b",
            "\u043d\u0435\u0441\u0443\u0449\u0435\u0441\u0442\u0432\u0443\u044e\u0449\u0438\u0439 \u0442\u0435\u0433",
        ],
        "cover_prompt": "```text\nA cinematic cover. Typography: The text \"\u0418\u043d\u043e\u043c\u0435\u0440\u043d\u0430\u044f \u0433\u043e\u0441\u0442\u0438\u043d\u0438\u0446\u0430\" written in glowing serif letters. --ar 2:3\n```",
    }
    prepared = parse_prepared_metadata(f"```json\n{json.dumps(payload, ensure_ascii=False)}\n```")

    assert prepared.english_title == "Otherworldly Inn"
    assert prepared.translated_title
    assert prepared.translated_description == "\u0422\u0435\u043a\u0441\u0442\n\n\u043e\u043f\u0438\u0441\u0430\u043d\u0438\u044f"
    assert prepared.genres[:3] == [FANTASY, MYSTIC, ADVENTURE]
    assert prepared.tags[:3] == [
        "sci-fi",
        "\u0442\u0430\u0439\u043d\u044b",
        "\u043c\u0438\u0441\u0442\u0438\u043a\u0430",
    ]
    assert prepared.cover_prompt == (
        "A cinematic cover. Typography: The text \"\u0418\u043d\u043e\u043c\u0435\u0440\u043d\u0430\u044f \u0433\u043e\u0441\u0442\u0438\u043d\u0438\u0446\u0430\" written in glowing serif letters. --ar 2:3"
    )


def test_parse_translation_metadata_ignores_catalog_fields():
    payload = {
        "english_title": "Otherworldly Inn",
        "translated_title": "\u0418\u043d\u043e\u043c\u0438\u0440\u043d\u0430\u044f \u0433\u043e\u0441\u0442\u0438\u043d\u0438\u0446\u0430",
        "translated_description": "\u0422\u0435\u043a\u0441\u0442\n\n\n\u043e\u043f\u0438\u0441\u0430\u043d\u0438\u044f",
        "genres": [FANTASY],
        "tags": ["sci-fi"],
    }

    prepared = parse_translation_metadata(json.dumps(payload, ensure_ascii=False))

    assert prepared.english_title == "Otherworldly Inn"
    assert prepared.translated_title == "\u0418\u043d\u043e\u043c\u0438\u0440\u043d\u0430\u044f \u0433\u043e\u0441\u0442\u0438\u043d\u0438\u0446\u0430"
    assert prepared.translated_description == "\u0422\u0435\u043a\u0441\u0442\n\n\u043e\u043f\u0438\u0441\u0430\u043d\u0438\u044f"
    assert prepared.genres == []
    assert prepared.tags == []


def test_parse_catalog_metadata_returns_only_catalog_fields(monkeypatch):
    allowed_tags = ["sci-fi", "\u0442\u0430\u0439\u043d\u044b", "\u043c\u0438\u0441\u0442\u0438\u043a\u0430"]
    monkeypatch.setattr(workers, "load_rulate_tags", lambda: allowed_tags)
    payload = {
        "genres": [FANTASY.upper(), MYSTIC],
        "tags": ["SCI-FI"],
        "cover_prompt": "A cover. Typography: The text \"\u041d\u0430\u0437\u0432\u0430\u043d\u0438\u0435\" written in gold. --ar 2:3",
        "translated_description": "\u041d\u0435 \u0434\u043e\u043b\u0436\u043d\u043e \u043f\u043e\u043f\u0430\u0441\u0442\u044c \u0432 \u043f\u0435\u0440\u0435\u0432\u043e\u0434",
    }

    prepared = parse_catalog_metadata(json.dumps(payload, ensure_ascii=False))

    assert prepared.translated_description == ""
    assert prepared.genres[:3] == [FANTASY, MYSTIC, ADVENTURE]
    assert prepared.tags == ["sci-fi", "\u0442\u0430\u0439\u043d\u044b", "\u043c\u0438\u0441\u0442\u0438\u043a\u0430"]
    assert prepared.cover_prompt.startswith("A cover.")


def test_normalize_rulate_tags_requires_tags_from_allowed_file(monkeypatch):
    allowed_tags = ["sci-fi", "\u0442\u0430\u0439\u043d\u044b", "\u043c\u0438\u0441\u0442\u0438\u043a\u0430"]
    monkeypatch.setattr(workers, "load_rulate_tags", lambda: allowed_tags)

    tags = normalize_rulate_tags(["SCI-FI", "\u0447\u0443\u0436\u043e\u0439 \u0442\u0435\u0433"])

    assert tags == ["sci-fi", "\u0442\u0430\u0439\u043d\u044b", "\u043c\u0438\u0441\u0442\u0438\u043a\u0430"]


def test_clean_qidian_description_strips_seo_metadata():
    raw_description = (
        "盲候创作的奇幻小说《冒牌领主》，已更新227章，"
        "最新章节：第226章 瑟银要塞陷落。"
        "罗南穿越而来，成了贵族大少的背锅替身。"
        "此刻他正替那位刚凌辱了帝国名将夫人的本尊，被皇帝发配去往南境边陲的途中。"
        "旧神、尸鬼、灵能、义体，蒸汽与火枪...这是一个超凡世界。"
        "罗南从冒牌领主开始，一点点开拓荒地，发掘遗迹，航海探索。"
        "直到有一天，他登…本书的主要角色有罗南"
    )

    cleaned = _clean_qidian_description(raw_description, title="冒牌领主", author="盲候")

    assert cleaned.startswith("罗南穿越而来")
    assert "最新章节" not in cleaned
    assert "本书的主要角色" not in cleaned


def test_extract_qidian_description_from_body_removes_trailing_book_tag():
    body_text = (
        "作品简介\n\n"
        "罗南穿越而来，成了贵族大少的背锅替身。\n"
        "此刻他正替那位刚凌辱了帝国名将夫人的本尊，被皇帝发配去往南境边陲的途中。\n"
        "旧神、尸鬼、灵能、义体，蒸汽与火枪...\n"
        "这是一个超凡世界。\n"
        "罗南从冒牌领主开始，一点点开拓荒地，发掘遗迹，航海探索。\n"
        "直到有一天，他登通天塔而上。\n"
        "那些隐藏黑雾中的旧日主宰，尽皆匍匐，颤栗低语：“天灾之王”。\n"
        "我叫罗南，我即天灾。\n"
        "PS.《灾变卡皇》《机械炼金术士》相近题材，书荒可以看看两本300W+万定老书。\n\n"
        "龙\n\n"
        "月票\n推荐票"
    )

    description = _extract_qidian_description_from_body(body_text)

    assert "登通天塔而上" in description
    assert "龙" not in description.splitlines()[-1]
    assert "月票" not in description


def test_extract_qidian_description_accepts_alternate_headers_and_rank_stop():
    body_text = (
        "内容简介\n\n"
        "在日常之下，在理性尽头，在你所熟悉的世界之外——是你从未想象过的风景。\n"
        "当于生第一次打开那扇门的时候，他所熟悉的世界便轰然倒塌。\n\n"
        "男生月票榜No.10\n\n"
        "月票\n推荐票"
    )

    description = _extract_qidian_description_from_body(body_text)

    assert description == (
        "在日常之下，在理性尽头，在你所熟悉的世界之外——是你从未想象过的风景。\n"
        "当于生第一次打开那扇门的时候，他所熟悉的世界便轰然倒塌。"
    )


def test_select_qidian_description_prefers_full_body_over_truncated_meta():
    payload = {
        "body_text": (
            "作品简介\n\n"
            "罗南穿越而来，成了贵族大少的背锅替身。\n"
            "直到有一天，他登通天塔而上。\n"
            "我叫罗南，我即天灾。\n\n"
            "月票"
        ),
        "description": (
            "盲候创作的奇幻小说《冒牌领主》，已更新227章，"
            "最新章节：第226章 瑟银要塞陷落。罗南穿越而来，直到有一天，他登…"
        ),
        "meta_description": (
            "盲候创作的奇幻小说《冒牌领主》，已更新227章，"
            "最新章节：第226章 瑟银要塞陷落。罗南穿越而来，直到有一天，他登…"
        ),
    }

    description = _select_qidian_description(payload, title="冒牌领主", author="盲候")

    assert description == "罗南穿越而来，成了贵族大少的背锅替身。\n直到有一天，他登通天塔而上。\n我叫罗南，我即天灾。"


def test_select_qidian_description_uses_clean_partial_when_only_truncated_exists():
    payload = {
        "body_text": "",
        "description": "",
        "meta_description": (
            "盲候创作的奇幻小说《冒牌领主》，已更新227章，"
            "最新章节：第226章 瑟银要塞陷落。罗南穿越而来，直到有一天，他登…"
        ),
    }

    description = _select_qidian_description(payload, title="冒牌领主", author="盲候")

    assert description == "罗南穿越而来，直到有一天，他登…"


def test_build_ai_prompt_contains_only_translation_fields():
    metadata = QidianBookMetadata(
        source_url="https://www.qidian.com/book/1041604040/",
        title_original="\u5f02\u5ea6\u65c5\u793e",
        author_name="\u8fdc\u77b3",
        description="\u63cf\u8ff0",
    )

    prompt = build_ai_prompt(metadata, "Otherworldly Inn")

    assert "\u5f02\u5ea6\u65c5\u793e" in prompt
    assert "\u8fdc\u77b3" in prompt
    assert "Otherworldly Inn" in prompt
    assert "\u043d\u0435 \u0432\u0441\u0442\u0430\u0432\u043b\u044f\u0439 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435" in prompt
    assert "translated_description" in prompt
    assert "cover_prompt" not in prompt
    assert "\u0422\u0435\u043a\u0441\u0442 \u043f\u0435\u0440\u0432\u044b\u0445 \u0433\u043b\u0430\u0432" not in prompt


def test_build_catalog_prompt_contains_cover_context_and_no_translation_fields():
    metadata = QidianBookMetadata(
        source_url="https://www.qidian.com/book/1041604040/",
        title_original="\u5f02\u5ea6\u65c5\u793e",
        author_name="\u8fdc\u77b3",
        description="\u63cf\u8ff0",
    )
    prepared = PreparedRulateMetadata(
        english_title="Otherworldly Inn",
        translated_title="\u0418\u043d\u043e\u043c\u0438\u0440\u043d\u0430\u044f \u0433\u043e\u0441\u0442\u0438\u043d\u0438\u0446\u0430",
        translated_description="\u0420\u0443\u0441\u0441\u043a\u043e\u0435 \u043e\u043f\u0438\u0441\u0430\u043d\u0438\u0435.",
    )

    prompt = build_catalog_prompt(
        metadata,
        prepared,
        "\u7b2c1\u7ae0 \u96e8\n\u5947\u602a\u7684\u65c5\u793e\u5728\u96e8\u4e2d\u51fa\u73b0\u3002",
    )

    assert "cover_prompt" in prompt
    assert "genres" in prompt
    assert "tags" in prompt
    assert "translated_description:" not in prompt
    assert "\u0422\u0435\u043a\u0441\u0442 \u043f\u0435\u0440\u0432\u044b\u0445 \u0433\u043b\u0430\u0432" in prompt
    assert "\u5947\u602a\u7684\u65c5\u793e\u5728\u96e8\u4e2d\u51fa\u73b0" in prompt
    assert 'The text "\u0418\u043d\u043e\u043c\u0438\u0440\u043d\u0430\u044f \u0433\u043e\u0441\u0442\u0438\u043d\u0438\u0446\u0430"' in prompt


def test_build_ai_prompt_does_not_include_hardcoded_tag_examples():
    metadata = QidianBookMetadata(
        source_url="https://www.qidian.com/book/1041604040/",
        title_original="\u5f02\u5ea6\u65c5\u793e",
        author_name="\u8fdc\u77b3",
        description="\u63cf\u8ff0",
    )

    prompt = build_ai_prompt(metadata, "Otherworldly Inn")

    assert "sci-fi, \u043c\u0438\u0441\u0442\u0438\u043a\u0430" not in prompt
    assert "\u043f\u0443\u0442\u0435\u0448\u0435\u0441\u0442\u0432\u0438\u0435 \u043c\u0435\u0436\u0434\u0443 \u043c\u0438\u0440\u0430\u043c\u0438" not in prompt
    assert "\u0441\u043e\u0432\u0440\u0435\u043c\u0435\u043d\u043d\u044b\u0439 \u043c\u0438\u0440, \u043a\u0438\u0442\u0430\u0439" not in prompt


def test_clean_qidian_chapter_text_removes_comment_counters():
    raw_text = "Первый абзац\n806\n\n\u3000\u3000Второй абзац\n109\n本章完"

    assert _clean_qidian_chapter_text(raw_text) == "Первый абзац\nВторой абзац"


def test_qidian_chapter_link_script_supports_chinese_chapter_numbers():
    assert "chineseNumber" in _QIDIAN_CHAPTER_LINKS_SCRIPT
    assert "[0-9零〇一二两三四五六七八九十百千万]+" in _QIDIAN_CHAPTER_LINKS_SCRIPT
    assert r"^第\s*\d+\s*章" not in _QIDIAN_CHAPTER_LINKS_SCRIPT


def test_build_cover_prompt_request_includes_ru_title_and_chapters():
    prompt = build_cover_prompt_request(
        "Иномирная гостиница",
        "第1章 雨\nГерой видит странную тень под фонарем.",
        original_description="Оригинальное описание про странный отель между мирами.",
    )

    assert "Название (RU): Иномирная гостиница" in prompt
    assert "Оригинальное описание Qidian:" in prompt
    assert "Оригинальное описание про странный отель между мирами." in prompt
    assert 'The text "Иномирная гостиница"' in prompt
    assert "Герой видит странную тень под фонарем." in prompt
    assert "--ar 2:3" in prompt


def test_clean_cover_prompt_response_strips_markdown_fence():
    response = "```text\nA hero in rain. Typography: The text \"Название\" written in neon font. --ar 2:3\n```"

    assert clean_cover_prompt_response(response) == (
        'A hero in rain. Typography: The text "Название" written in neon font. --ar 2:3'
    )


def test_browser_missing_error_is_detected_for_playwright_install_message():
    error = RuntimeError(
        "BrowserType.launch: Executable doesn't exist at "
        "C:\\Users\\test\\AppData\\Local\\ms-playwright\\chromium_headless_shell-1223\\chrome.exe\n"
        "Looks like Playwright was just installed or updated. Please run: playwright install"
    )

    assert _is_browser_missing_error(error)
