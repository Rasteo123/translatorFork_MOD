# -*- coding: utf-8 -*-
"""
Аудит libs-recognizers-shim (recognizers-text / recognizers-text-number, emoji).

Баг: recognizers_text.utilities делает `from emoji import UNICODE_EMOJI` на
верхнем уровне; в установленной версии пакета `emoji` такого атрибута нет,
поэтому голый `import recognizers_text` падает ImportError. Патч
`emoji.UNICODE_EMOJI = {}` чинит это, но раньше стоял только в
numbers_master.py (без try/except). epub_analyzer.py и txt_importer.py
импортировали recognizers_text/recognizers_number в try/except БЕЗ патча —
если они успевали импортироваться раньше numbers_master.py (обычный случай
при `import main`), их ImportError ловился молча и RECOGNIZERS_AVAILABLE /
HAS_RECOGNIZERS навсегда становились False на весь процесс (recognizers_text
кешируется как неудачно импортированный).

Модуль gemini_translator/utils/recognizers_shim.py собирает патч + импорт
Culture/recognize_number в одном месте, так что доступность recognizers не
зависит от того, какой модуль-потребитель импортируется первым.
"""
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def _run(code: str) -> str:
    """Запускает code в чистом интерпретаторе (без загрязнения sys.modules
    текущего процесса pytest, где recognizers_text мог уже импортироваться
    другим тестом) и возвращает stdout."""
    result = subprocess.run(
        [PYTHON, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"subprocess завершился с ошибкой (code={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""


def test_shim_module_exposes_availability_and_symbols():
    """Сам модуль-шим после импорта даёт Culture/recognize_number и флаг True."""
    code = (
        "import gemini_translator.utils.recognizers_shim as shim\n"
        "print(shim.RECOGNIZERS_AVAILABLE, shim.Culture is not None, "
        "shim.recognize_number is not None)\n"
    )
    assert _run(code) == "True True True"


def test_recognizers_available_regardless_of_which_consumer_imports_first():
    """Ключевая характеризация бага: импортируем epub_analyzer и txt_importer
    БЕЗ предварительного импорта numbers_master.py (единственного места, где
    раньше стоял патч). До фикса это давало False/False — эффект зависел от
    порядка импорта модулей приложения; после фикса каждый потребитель сам
    тянет recognizers_shim и получает патч независимо от порядка."""
    code = (
        "import gemini_translator.utils.epub_analyzer as ea\n"
        "import gemini_translator.utils.txt_importer as ti\n"
        "print(ea.RECOGNIZERS_AVAILABLE, ti.HAS_RECOGNIZERS)\n"
    )
    assert _run(code) == "True True"


def test_numbers_master_recognizers_available_standalone():
    """numbers_master.py импортируется без try/except — при разрыве патча
    сам факт импорта main/этого модуля упал бы ImportError. Проверяем, что
    он загружается и Culture/recognize_number реально рабочие объекты."""
    code = (
        "import gemini_translator.ui.dialogs.glossary_dialogs.numbers_master as nm\n"
        "print(nm.Culture is not None, nm.recognize_number is not None)\n"
    )
    assert _run(code) == "True True"


CONSUMER_FILES = [
    REPO_ROOT / "gemini_translator" / "ui" / "dialogs" / "glossary_dialogs" / "numbers_master.py",
    REPO_ROOT / "gemini_translator" / "utils" / "epub_analyzer.py",
    REPO_ROOT / "gemini_translator" / "utils" / "txt_importer.py",
]


def test_local_emoji_patch_copies_removed_in_favor_of_shim():
    """Четыре локальные копии `if not hasattr(emoji, 'UNICODE_EMOJI'): ...`
    должны быть вынесены в recognizers_shim.py; потребители должны
    импортировать шим, а не патчить emoji сами."""
    for path in CONSUMER_FILES:
        text = path.read_text(encoding="utf-8")
        assert "UNICODE_EMOJI" not in text, (
            f"{path} всё ещё содержит локальный патч emoji.UNICODE_EMOJI, "
            "должен остаться только в recognizers_shim.py"
        )
        assert "recognizers_shim" in text, (
            f"{path} не импортирует общий gemini_translator.utils.recognizers_shim"
        )


def test_shim_module_is_the_only_place_patching_emoji():
    shim_path = REPO_ROOT / "gemini_translator" / "utils" / "recognizers_shim.py"
    assert shim_path.exists(), "gemini_translator/utils/recognizers_shim.py должен существовать"
    text = shim_path.read_text(encoding="utf-8")
    assert "UNICODE_EMOJI" in text
    assert "recognize_number" in text
    assert "RECOGNIZERS_AVAILABLE" in text


def test_shim_survives_non_import_errors_during_setup():
    """Ревью-замечание (minor): except ImportError слишком узкий — единственная
    и теперь общая для всех потребителей точка патча должна деградировать
    мягко при ЛЮБОЙ ошибке (например FileNotFoundError на emoji.json в
    урезанной сборке), а не только при ImportError."""
    shim_path = REPO_ROOT / "gemini_translator" / "utils" / "recognizers_shim.py"
    text = shim_path.read_text(encoding="utf-8")
    assert "except ImportError" not in text, (
        "recognizers_shim.py должен ловить `except Exception`, а не только "
        "ImportError, в обоих блоках try (emoji-патч и импорт recognizers_text)"
    )
    assert text.count("except Exception") >= 2


# ---------------------------------------------------------------------------
# Поведенческие регрессии, включённые тем, что recognizers теперь реально
# доступны в приложении (RECOGNIZERS_AVAILABLE=True вместо False на HEAD).
# Обе ветки были мертвы на HEAD и не были проверены на корректность при
# консолидации шима — ревью нашло в них баги.
# ---------------------------------------------------------------------------


def test_smart_replace_number_in_title_microsoft_recognizers_branch():
    """Ветка «2. MICROSOFT RECOGNIZERS» smart_replace_number_in_title:
    ModelResult.end у recognizers-text-number — индекс ПОСЛЕДНЕГО символа
    числа (включительно), а не exclusive-конец среза. Код `title[end:]`
    оставлял этот символ в результате (off-by-one), давая 'Глава 95' вместо
    'Глава 9' и т.п. Ловит только случаи БЕЗ CJK-иероглифов (ветка 1 имеет
    приоритет и здесь не участвует)."""
    code = (
        "from gemini_translator.utils.txt_importer import smart_replace_number_in_title\n"
        "cases = [\n"
        "    ('Глава 5', 9, 'Глава 9'),\n"
        "    ('Chapter 5', 9, 'Chapter 9'),\n"
        "    ('Chapter 5: Dawn', 9, 'Chapter 9: Dawn'),\n"
        "    ('Chapter Twenty-One', 9, 'Chapter 9'),\n"
        "]\n"
        "for title, new_num, expected in cases:\n"
        "    got = smart_replace_number_in_title(title, new_num)\n"
        "    assert got == expected, (title, got, expected)\n"
        "print('OK')\n"
    )
    assert _run(code) == "OK"


def test_check_numbering_no_false_positive_when_number_matches():
    """EpubAnalyzer._check_numbering сравнивал res.resolution['value'] (СТРОКУ,
    например '5') с target_number (int), поэтому `val != target_number` было
    истинно ВСЕГДА и корректно пронумерованная глава ложно попадала в
    «Рассинхрон нумерации»."""
    code = (
        "from gemini_translator.utils.epub_analyzer import EpubAnalyzer\n"
        "ea = EpubAnalyzer(epub_path='/nonexistent.epub')\n"
        "cases = [\n"
        "    ('Chapter 5', 5),\n"
        "    ('Глава 5', 5),\n"
        "    ('Chapter 5: The 3 Musketeers', 5),\n"
        "]\n"
        "for header, target in cases:\n"
        "    mismatches = ea._check_numbering(header, target, 'chapter.xhtml')\n"
        "    assert mismatches == [], (header, target, mismatches)\n"
        "print('OK')\n"
    )
    assert _run(code) == "OK"


def test_check_numbering_flags_real_mismatch():
    """Обратная сторона фикса: реальное расхождение номера всё ещё должно
    ловиться (не превратить проверку в вечное no-op)."""
    code = (
        "from gemini_translator.utils.epub_analyzer import EpubAnalyzer\n"
        "ea = EpubAnalyzer(epub_path='/nonexistent.epub')\n"
        "mismatches = ea._check_numbering('Chapter Seven', 5, 'chapter.xhtml')\n"
        "assert len(mismatches) == 1, mismatches\n"
        "assert mismatches[0]['new_number'] == 5, mismatches\n"
        "print('OK')\n"
    )
    assert _run(code) == "OK"
