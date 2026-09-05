import shutil
from pathlib import Path

from gemini_translator.utils.translation_versions import select_target_translation_version


def _fresh_tmp_dir(name):
    path = Path("tests") / ".tmp_fix_g32" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def test_select_target_falls_back_when_validated_file_missing_from_disk():
    """
    Дефект utils-infra/bugs/4-missing-validated-file-hidden-:
    если запись _validated.html есть в карте версий, но сам файл удалён
    с диска, select_target_translation_version не должен молча возвращать
    этот путь как "готовый" — иначе глава без реального перевода
    помечается [Готов] и прячется в окне валидации. Функция должна
    откатиться к другой существующей версии, как это уже делает
    sort_translation_versions_for_epub_build.
    """
    tmp_path = _fresh_tmp_dir("missing_validated_with_fallback")
    translated_path = tmp_path / "Text" / "ch1_translated_dp.html"
    translated_path.parent.mkdir(parents=True)
    try:
        translated_path.write_text("<html><body><p>fallback</p></body></html>", encoding="utf-8")
        # ch1_validated.html значится в карте версий, но на диске отсутствует.

        rel_path, is_validated = select_target_translation_version(
            {
                "_validated.html": "Text/ch1_validated.html",
                "_translated_dp.html": "Text/ch1_translated_dp.html",
            },
            str(tmp_path),
        )

        assert rel_path == "Text/ch1_translated_dp.html"
        assert is_validated is False
    finally:
        shutil.rmtree(tmp_path.parent, ignore_errors=True)


def test_select_target_returns_none_when_only_validated_entry_missing():
    """
    Если единственная запись в карте версий — это удалённый validated-файл
    и других кандидатов нет, функция должна вернуть (None, False), а не
    выдавать путь к несуществующему файлу с флагом is_validated=True.
    """
    tmp_path = _fresh_tmp_dir("missing_validated_no_fallback")
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        rel_path, is_validated = select_target_translation_version(
            {
                "_validated.html": "Text/ch1_validated.html",
            },
            str(tmp_path),
        )

        assert rel_path is None
        assert is_validated is False
    finally:
        shutil.rmtree(tmp_path.parent, ignore_errors=True)
