# tests/test_no_hardcoded_colors.py
import re

# Files whose setStyleSheet() colors have been tokenized. Each sweep task
# appends its file here; the test then guards it against regressions.
TOKENIZED_FILES: list[str] = [
    "gemini_translator/ui/dialogs/validation_dialogs/untranslated_fixer_dialog.py",
    "gemini_translator/ui/dialogs/validation.py",
    "gemini_translator/ui/widgets/model_settings_widget.py",
    "gemini_translator/ui/widgets/translation_options_widget.py",
    "gemini_translator/ui/dialogs/glossary_dialogs/ai_correction.py",
    "gemini_translator/ui/dialogs/glossary_dialogs/versioning.py",
    "gemini_translator/ui/dialogs/glossary_dialogs/term_frequency_analyzer.py",
    "gemini_translator/ui/dialogs/setup.py",
    "gemini_translator/ui/dialogs/consistency_checker.py",
    "gemini_translator/ui/widgets/auto_translate_widget.py",
    "gemini_translator/ui/widgets/status_bar_widget.py",
    "gemini_translator/ui/widgets/key_management_widget.py",
    "gemini_translator/ui/dialogs/glossary.py",
    "gemini_translator/ui/dialogs/epub.py",
    "gemini_translator/ui/dialogs/misc.py",
    "gemini_translator/ui/dialogs/chapter_editor.py",
    "gemini_translator/ui/pages/rulate_export_page.py",
    "gemini_translator/utils/markdown_viewer.py",
]

_COLOR = re.compile(
    r"#[0-9a-fA-F]{3,6}\b|\b(?:white|black|red|green|grey|gray|orange)\b"
)


def _setstylesheet_segments(src: str):
    i = 0
    while True:
        m = src.find("setStyleSheet(", i)
        if m < 0:
            return
        k = src.index("(", m)
        depth = 0
        e = k
        while e < len(src):
            if src[e] == "(":
                depth += 1
            elif src[e] == ")":
                depth -= 1
                if depth == 0:
                    break
            e += 1
        i = e + 1
        yield src[k : e + 1]


def test_tokenized_files_have_no_raw_colors_in_setstylesheet():
    offenders = {}
    for path in TOKENIZED_FILES:
        src = open(path, encoding="utf-8").read()
        hits = []
        for seg in _setstylesheet_segments(src):
            hits += _COLOR.findall(seg)
        if hits:
            offenders[path] = sorted(set(hits))
    assert not offenders, f"raw colors remain in setStyleSheet: {offenders}"
