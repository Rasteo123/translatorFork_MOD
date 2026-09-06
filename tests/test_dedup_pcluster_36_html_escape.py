# -*- coding: utf-8 -*-
"""pcluster-36: ручное HTML-экранирование вместо html.escape в 4 местах.

Было: qa/service.py и translation_quality_controller.py содержали побайтово
идентичную функцию ``_escape(value)`` (html.escape(str(value or ""))),
document_importer.py -- тот же паттерн (html.escape(..., quote=True)). Все
три поведенчески совпадали. validation.py вместо функции использовал
россыпь ad hoc ``.replace('<', '&lt;')`` -- часть мест (StructureErrorsDialog,
_jump_to_tag_in_code) экранировала ТОЛЬКО '<', оставляя '&' и '>' сырыми;
html_safe() экранировал '&', '<', '>', но не кавычки.

Канонической стала ``gemini_translator.utils.text.escape_html`` --
``html.escape(str(value or ""), quote=True)``. Пять копий из ПЯТИ файлов,
разрешённых для этой волны (qa/service.py, translation_quality_controller.py,
document_importer.py, validation.py x2 сайта) переведены на неё.

Известные ОСТАВШИЕСЯ копии кластера вне разрешённого списка файлов для этой
волны (сознательно отложены, а не устранены -- изменение этих файлов
требует отдельного разрешения):
  - gemini_translator/ui/dialogs/consistency_checker.py:2101 -- собственный
    ``_escape_html()`` (только '&','<','>', без кавычек), 4 сайта вызова
    (1945, 1946, 1973, 1974).
  - gemini_translator/utils/epub_tools.py:536 -- ``_xml_escape()``,
    побайтово совпадает с canonical (``html.escape(..., quote=True)``), но
    отдельная копия.

(а) Характеризационные тесты фиксируют ИМЕННО то поведение, которое
    различало копии: полное экранирование '&', '<', '>' и кавычек, и
    falsy-значения (None/0) -> "".
(б) Тесты-маршрутизация monkeypatch'ят ``escape_html`` в каждом из пяти
    модулей и проверяют, что вызов идёт именно через неё. До рефакторинга
    эти тесты ПАДАЮТ (validation.py вообще не знает такого имени; qa/
    service.py, translation_quality_controller.py и document_importer.py
    используют собственные локальные ``_escape``, не связанные с
    ``utils.text.escape_html``).
(в) Отдельный сквозной тест (без monkeypatch escape_html) строит
    LargeTextInputDialog с НАСТОЯЩЕЙ escape_html и проверяет сам рендер
    QLabel -- то, что маршрутизационные тесты с sentinel-подменой не могут
    поймать в принципе (see test_example_title_label_renders_as_rich_text_
    not_literal_entities).
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.utils.text import escape_html


DANGEROUS = "Tom & Jerry <b>\"quoted\"</b> 'it's' <script>"
DANGEROUS_ESCAPED = (
    "Tom &amp; Jerry &lt;b&gt;&quot;quoted&quot;&lt;/b&gt; "
    "&#x27;it&#x27;s&#x27; &lt;script&gt;"
)


class EscapeHtmlCharacterizationTests(unittest.TestCase):
    """Поведение канонической escape_html -- то, что различало копии."""

    def test_escapes_ampersand_angle_brackets_and_quotes(self):
        # html_safe() в validation.py не экранировал кавычки; строки
        # 1439/1455/1476/4562 не экранировали ни '&', ни кавычки, ни (в
        # части случаев) '>'. Каноническая версия обязана закрыть все эти
        # пробелы одновременно.
        self.assertEqual(escape_html(DANGEROUS), DANGEROUS_ESCAPED)

    def test_none_and_falsy_become_empty_string(self):
        # `str(value or "")` -- поведение, общее для всех трёх исходно
        # идентичных _escape (qa/service.py, translation_quality_controller.py).
        self.assertEqual(escape_html(None), "")
        self.assertEqual(escape_html(0), "")
        self.assertEqual(escape_html(""), "")

    def test_plain_text_is_unchanged(self):
        self.assertEqual(escape_html("обычный текст"), "обычный текст")

    def test_already_escaped_entity_is_not_double_broken(self):
        # Ключевой сценарий divergence: '<'-only частичное экранирование
        # оставляло голый '&' в выводе, что ломало разметку. Полное
        # экранирование хотя бы не плодит недоэкранированных '&'.
        raw = "5 & <7"
        out = escape_html(raw)
        self.assertNotIn(" & ", out)
        self.assertIn("&amp;", out)
        self.assertIn("&lt;", out)


class ValidateHtmlStructureFundamentalTagMessageTests(unittest.TestCase):
    """utils/text.py:validate_html_structure -- остаточная partial-escape.

    Строка 3594 делала ``display_name.replace('<', '&lt;')`` -- только '<',
    оставляя '>' сырым (то самое "часть версий неполная" из формулировки
    кластера). Сообщение уходит в чисто текстовые приёмники (log_message в
    core/chunk_assembler.py, RuntimeError) -- НЕ rich text, поэтому честный
    фикс здесь не "доэкранировать до escape_html", а вообще не экранировать:
    показать тег как есть, как показывают остальные сообщения этой функции
    (например "не вернуло ожидаемую обертку <body>...</body>").
    """

    def test_lost_fundamental_tag_message_is_not_partially_escaped(self):
        from gemini_translator.utils.text import validate_html_structure

        orig = "<html><p>hi</p></html>"
        trans = "<p>hi</p>"

        is_valid, reason, _ = validate_html_structure(orig, trans)

        self.assertFalse(is_valid)
        self.assertNotIn("&lt;", reason)
        self.assertIn("<html>", reason)


class QaServiceRoutingTests(unittest.TestCase):
    """qa/service.py: ChapterQaResult.change_details_html() -> escape_html."""

    def test_change_details_html_routes_through_escape_html(self):
        from gemini_translator.qa import service as qa_service
        from gemini_translator.qa.models import RiskLevel

        result = qa_service.ChapterQaResult(
            chapter_id="глава & <1>",
            risk_level=RiskLevel.LOW,
            may_continue_translation=True,
            coverage_mode="full",
        )

        sentinel_calls = []

        def fake_escape_html(value):
            sentinel_calls.append(value)
            return f"ESCAPED[{value}]"

        with mock.patch.object(qa_service, "escape_html", fake_escape_html):
            html = result.change_details_html()

        self.assertIn("ESCAPED[глава & <1>]", html)
        self.assertIn("глава & <1>", sentinel_calls)


class TranslationQualityControllerRoutingTests(unittest.TestCase):
    """translation_quality_controller.py: _resume_header/_log_chapter -> escape_html."""

    def test_resume_header_routes_through_escape_html(self):
        from gemini_translator.ui.dialogs.validation_dialogs import (
            translation_quality_controller as controller_module,
        )

        class _Item:
            def __init__(self, reason):
                self.reason = reason

        selected = [_Item("deferred"), _Item("deferred")]

        def fake_escape_html(value):
            return f"ESCAPED[{value}]"

        with mock.patch.object(controller_module, "escape_html", fake_escape_html):
            header = controller_module.TranslationQualityController._resume_header(
                selected, total=5
            )

        self.assertIn("ESCAPED[", header)

    def test_log_chapter_unchanged_routes_through_escape_html(self):
        from gemini_translator.ui.dialogs.validation_dialogs import (
            translation_quality_controller as controller_module,
        )

        class _FakeController:
            chapter_logged = mock.Mock()

            def _log_chapter(self, result):
                return controller_module.TranslationQualityController._log_chapter(
                    self, result
                )

        class _Result:
            chapter_id = "1 & 2"
            changed_anything = False
            language = None
            may_continue_translation = True

        fake = _FakeController()

        def fake_escape_html(value):
            return f"ESCAPED[{value}]"

        with mock.patch.object(controller_module, "escape_html", fake_escape_html):
            fake._log_chapter(_Result())

        emitted = fake.chapter_logged.emit.call_args[0][0]
        self.assertIn("ESCAPED[1 & 2]", emitted)


class DocumentImporterRoutingTests(unittest.TestCase):
    """document_importer.py: _paragraphs_from_plain_text -> escape_html."""

    def test_paragraphs_from_plain_text_routes_through_escape_html(self):
        from gemini_translator.utils import document_importer

        def fake_escape_html(value):
            return f"ESCAPED[{value}]"

        with mock.patch.object(document_importer, "escape_html", fake_escape_html):
            html = document_importer._paragraphs_from_plain_text("Tom & Jerry")

        self.assertIn("ESCAPED[Tom & Jerry]", html)


class ValidationStructureErrorsDialogRoutingTests(unittest.TestCase):
    """validation.py: StructureErrorsDialog._populate_details -> escape_html.

    Это самое опасное место divergence: html_safe() и несколько
    '<'-only replace() цепочек сосуществовали в одном методе.
    """

    @classmethod
    def setUpClass(cls):
        from PyQt6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def _make_dialog(self, errors, escape_impl):
        from gemini_translator.ui.dialogs import validation as validation_module

        with mock.patch.object(validation_module, "escape_html", escape_impl):
            dialog = validation_module.StructureErrorsDialog(errors)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def _all_label_text(self, dialog):
        from PyQt6.QtWidgets import QLabel

        return "\n".join(label.text() for label in dialog.findChildren(QLabel))

    def test_malformed_xml_message_routes_through_escape_html(self):
        calls = []

        def fake_escape_html(value):
            calls.append(value)
            return f"ESCAPED[{value}]"

        dialog = self._make_dialog(
            {"malformed_xml": ("описание", "parser said & <boom>")},
            fake_escape_html,
        )
        self.assertIn("parser said & <boom>", calls)
        self.assertIn("ESCAPED[parser said & <boom>]", self._all_label_text(dialog))

    def test_custom_tag_tooltip_routes_through_escape_html(self):
        calls = []

        def fake_escape_html(value):
            calls.append(value)
            return f"ESCAPED[{value}]"

        dialog = self._make_dialog(
            {"custom_tags": ["<unknown-marker & thing>"]},
            fake_escape_html,
        )
        self.assertIn("<unknown-marker & thing>", calls)
        from PyQt6.QtWidgets import QPushButton

        tooltips = [
            button.toolTip()
            for button in dialog.findChildren(QPushButton)
            if button.toolTip()
        ]
        self.assertTrue(any("ESCAPED[" in tip for tip in tooltips))

    def test_body_root_text_preview_routes_through_escape_html_not_html_safe(self):
        # Раньше здесь был локальный html_safe(), не escape_html.
        calls = []

        def fake_escape_html(value):
            calls.append(value)
            return f"ESCAPED[{value}]"

        dialog = self._make_dialog(
            {"body_root_text": ["stray & text <here>"]},
            fake_escape_html,
        )
        self.assertTrue(any("stray & text <here>" in call for call in calls))
        self.assertIn("ESCAPED[", self._all_label_text(dialog))

    def test_fundamental_tags_safe_tag_routes_through_escape_html(self):
        calls = []

        def fake_escape_html(value):
            calls.append(value)
            return f"ESCAPED[{value}]"

        dialog = self._make_dialog(
            {"fundamental_tags": {"<img>": (True, False)}},
            fake_escape_html,
        )
        self.assertIn("<img>", calls)
        self.assertIn("ESCAPED[<img>]", self._all_label_text(dialog))


class ValidationLargeTextInputDialogRoutingTests(unittest.TestCase):
    """validation.py: LargeTextInputDialog._populate_examples_list -> escape_html."""

    @classmethod
    def setUpClass(cls):
        from PyQt6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_example_title_and_code_route_through_escape_html(self):
        from gemini_translator.ui.dialogs import validation as validation_module

        calls = []

        def fake_escape_html(value):
            calls.append(value)
            return f"ESCAPED[{value}]"

        with mock.patch.object(validation_module, "escape_html", fake_escape_html):
            dialog = validation_module.LargeTextInputDialog(initial_text="")
        self.addCleanup(dialog.deleteLater)

        # LargeTextInputDialog.examples содержит фиксированные образцы --
        # достаточно убедиться, что title/code из НИХ прошли через escape_html.
        self.assertTrue(calls)
        from PyQt6.QtWidgets import QLabel

        texts = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertIn("ESCAPED[", texts)

    def test_example_title_label_renders_as_rich_text_not_literal_entities(self):
        # Регрессия: escape_html(quote=True) превращает апострофы в
        # '&#x27;', но title_label -- голый QLabel(escaped_title) без
        # setTextFormat. QLabel.AutoText считает строку rich text только
        # по '<' / '&lt;' (Qt::mightBeRichText), значит для заголовков без
        # '<' (например, "слова 'Хакер' или 'Вирус'") AutoText рендерит
        # ИДЕНТИЧНО PlainText, и пользователь видит буквальный '&#x27;'.
        # Настоящей escape_html (без sentinel-подмены), т.к. дело именно в
        # том, что реальный html.escape производит '&#x27;' без '<'.
        from gemini_translator.ui.dialogs import validation as validation_module
        from PyQt6.QtWidgets import QLabel
        from PyQt6.QtCore import Qt

        dialog = validation_module.LargeTextInputDialog(initial_text="")
        self.addCleanup(lambda: (dialog.deleteLater(), self.app.processEvents()))

        matching_labels = [
            label
            for label in dialog.findChildren(QLabel)
            if "Хакер" in label.text() or "Вирус" in label.text()
        ]
        self.assertTrue(matching_labels, "не нашли QLabel с заголовком примера про Хакер/Вирус")
        title_label = matching_labels[0]

        # Либо метка явно помечена как rich text, либо (что эквивалентно
        # для пользователя) в её тексте нет буквальных числовых сущностей.
        is_rich_text = title_label.textFormat() == Qt.TextFormat.RichText
        has_literal_entity = "&#x27;" in title_label.text()
        self.assertFalse(
            has_literal_entity and not is_rich_text,
            f"заголовок отрендерится как сырой текст с '&#x27;': {title_label.text()!r}",
        )


class ValidationTooltipsRoutingTests(unittest.TestCase):
    """validation.py: TranslationValidatorPage._set_tooltips -> escape_html."""

    @classmethod
    def setUpClass(cls):
        from PyQt6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_set_tooltips_routes_through_escape_html(self):
        from gemini_translator.ui.dialogs import validation as validation_module

        self.app.global_version = ""
        with mock.patch.object(
            validation_module.TranslationValidatorPage,
            "_perform_initial_cjk_scan",
        ):
            page = validation_module.TranslationValidatorPage(
                "/tmp/nonexistent-translations",
                "/tmp/nonexistent-book.epub",
                project_manager=None,
            )
        self.addCleanup(lambda: (page.deleteLater(), self.app.processEvents()))

        calls = []

        def fake_escape_html(value):
            calls.append(value)
            return f"ESCAPED[{value}]"

        with mock.patch.object(validation_module, "escape_html", fake_escape_html):
            page._set_tooltips()

        self.assertTrue(calls)
        self.assertTrue(page.check_structure.toolTip().startswith("ESCAPED["))
        self.assertTrue(page.btn_fix_ai_artifacts.toolTip().startswith("ESCAPED["))
        self.assertTrue(page.check_simplification.toolTip().startswith("ESCAPED["))


class ValidationJumpToTagRoutingTests(unittest.TestCase):
    """validation.py: TranslationValidatorPage._jump_to_tag_in_code -> escape_html."""

    @classmethod
    def setUpClass(cls):
        from PyQt6 import QtWidgets

        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_not_found_message_routes_through_escape_html(self):
        from gemini_translator.ui.dialogs import validation as validation_module
        from PyQt6.QtWidgets import QMessageBox

        self.app.global_version = ""
        with mock.patch.object(
            validation_module.TranslationValidatorPage,
            "_perform_initial_cjk_scan",
        ):
            page = validation_module.TranslationValidatorPage(
                "/tmp/nonexistent-translations",
                "/tmp/nonexistent-book.epub",
                project_manager=None,
            )
        self.addCleanup(lambda: (page.deleteLater(), self.app.processEvents()))
        page.is_code_view = True  # skip toggle_code_view()

        captured = {}

        def fake_information(parent, title, text):
            captured["text"] = text

        with mock.patch.object(validation_module, "escape_html", lambda v: f"ESCAPED[{v}]"), \
                mock.patch.object(QMessageBox, "information", staticmethod(fake_information)):
            page._jump_to_tag_in_code("<tag-not-in-empty-document>")

        self.assertIn("ESCAPED[<tag-not-in-empty-document>]", captured.get("text", ""))


if __name__ == "__main__":
    unittest.main()
