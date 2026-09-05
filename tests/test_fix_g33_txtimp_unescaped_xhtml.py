"""
Регресс-тест для utils-io/bugs/2-txtimp-unescaped-xhtml.

TxtImportWizardDialog.generate_epub собирает XHTML главы f-строками без
html.escape. Реальные TXT-дампы почти неизбежно содержат '&'/'<'/'>' (имена
вида "Tom & Jerry", "R&D", сравнения "a < b"), и такой символ делает
получившийся chapter_*.xhtml невалидным XML, хотя OPF объявляет его как
application/xhtml+xml.

Тест поднимает настоящий TxtImportWizardDialog (offscreen Qt), прогоняет
его так же, как это делает пользователь: задаёт RegEx, переходит к
редактированию оглавления (go_to_toc_editor) и жмёт "Создать EPUB"
(generate_epub) — то есть исполняется боевое тело диалога целиком, без
моков логики генерации. Результат проверяется напрямую по содержимому
получившегося .epub.
"""

import os
import xml.etree.ElementTree as ET
import zipfile

from gemini_translator.utils.txt_importer import TxtImportWizardDialog


def _build_epub(tmp_path, qtbot, txt_lines):
    txt_path = tmp_path / "novel & tales.txt"
    txt_path.write_text("\n".join(txt_lines), encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    dialog = TxtImportWizardDialog(str(txt_path), str(out_dir))
    qtbot.addWidget(dialog)

    dialog.regex_input.setText(r"^Глава\s*\d+")
    dialog.go_to_toc_editor()
    dialog.generate_epub()

    assert dialog.generated_epub_path
    assert os.path.exists(dialog.generated_epub_path)
    return dialog.generated_epub_path


def test_generate_epub_escapes_ampersand_and_angle_brackets_in_xhtml(tmp_path, qtbot):
    epub_path = _build_epub(
        tmp_path,
        qtbot,
        [
            "Глава 1. Отдел R&D",
            "Том сказал: «мы из Tom & Jerry Ltd.»",
            "Формула простая: a < b > c, и точка.",
            "Глава 2. Обычная",
            "Тут всё чисто, спецсимволов нет.",
        ],
    )

    with zipfile.ZipFile(epub_path) as epub:
        chapter_xhtml = epub.read("OEBPS/chapter_1.xhtml").decode("utf-8")

    # Заголовок и текст главы должны быть экранированы, а не вставлены сырыми.
    assert "R&amp;D" in chapter_xhtml
    assert "Tom &amp; Jerry" in chapter_xhtml
    assert "a &lt; b &gt; c" in chapter_xhtml
    # Сырых спецсимволов, ломающих XML, быть не должно.
    assert "R&D" not in chapter_xhtml
    assert "a < b > c" not in chapter_xhtml

    # Главное следствие бага: файл должен быть валидным XML, как и заявлено
    # в OPF-манифесте (media-type="application/xhtml+xml").
    ET.fromstring(chapter_xhtml)
