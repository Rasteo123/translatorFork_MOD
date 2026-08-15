import os
import importlib
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtCore, QtWidgets
from PyQt6.QtWidgets import QApplication

from gemini_translator.ui.notifications import NotificationManager
from gemini_translator.ui.overlay_host import (
    _MessageBoxPanel,
    exec_dialog,
    find_overlay_host,
)
from gemini_translator.ui.shell import MainShell, ShellPage
from gemini_translator.utils.glossary_review import (
    classify_translation_review_change,
    normalize_translation_review_key,
)


def _import_correction_preview_dialog():
    """Import ai_correction without tripping the widgets/glossary circular import."""
    module_name = "gemini_translator.ui.widgets.glossary_widget"
    previous_module = sys.modules.get(module_name)
    fake_module = types.ModuleType(module_name)
    fake_glossary_widget = type("_FakeGlossaryWidget", (), {})
    fake_module.GlossaryWidget = fake_glossary_widget
    sys.modules[module_name] = fake_module
    try:
        module = importlib.import_module(
            "gemini_translator.ui.dialogs.glossary_dialogs.ai_correction"
        )
    finally:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module

        widgets_package = sys.modules.get("gemini_translator.ui.widgets")
        if widgets_package and getattr(widgets_package, "GlossaryWidget", None) is fake_glossary_widget:
            if previous_module is not None:
                widgets_package.GlossaryWidget = previous_module.GlossaryWidget
            else:
                try:
                    real_module = importlib.import_module(module_name)
                    widgets_package.GlossaryWidget = real_module.GlossaryWidget
                except Exception:
                    delattr(widgets_package, "GlossaryWidget")

    return module.CorrectionPreviewDialog


CorrectionPreviewDialog = _import_correction_preview_dialog()
ai_correction_module = sys.modules[CorrectionPreviewDialog.__module__]
CorrectionSessionPage = ai_correction_module.CorrectionSessionPage


class GlossaryManagerPage:
    def __init__(self):
        self.direct_conflicts = {}
        self._glossary = [
            {"original": "Alpha", "rus": "A", "note": ""},
            {"original": "Beta", "rus": "B", "note": ""},
        ]

    def get_glossary(self):
        return list(self._glossary)


class _CorrectionPatchHarness(ShellPage):
    correction_accepted = QtCore.pyqtSignal(list)
    _handle_correction_patch = CorrectionSessionPage._handle_correction_patch

    def __init__(self, owner):
        super().__init__()
        self._owner = owner
        self.cb_notes = SimpleNamespace(isChecked=lambda: True)
        self.morph_analyzer = None

    def _get_glossary_owner(self):
        return self._owner


class CorrectionPreviewDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def _drain_events(self):
        for _ in range(5):
            self._app.processEvents()

    def _shell(self, page=None):
        shell = MainShell()
        shell.resize_controller.set_duration(0)
        shell.resize_controller.set_content_fade_duration(0)
        shell.overlay_host.set_animation_durations(0, 0, 0, 0)
        self.addCleanup(shell.close)
        self.addCleanup(shell.hide)
        shell.set_home(page or ShellPage())
        shell.show()
        self._drain_events()
        return shell

    def test_unsaved_term_switch_confirmation_stays_in_overlay_card(self):
        owner = GlossaryManagerPage()
        page = _CorrectionPatchHarness(owner)
        shell = self._shell(page)
        previews = []
        seen = {}

        class CapturingPreview(CorrectionPreviewDialog):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                previews.append(self)

        def resolve_confirmation():
            panels = [
                widget
                for widget in QApplication.allWidgets()
                if isinstance(widget, _MessageBoxPanel) and widget.isVisible()
            ]
            seen["confirmation_card_shown"] = bool(panels)
            if panels:
                confirmation = panels[-1]
                seen["confirmation_is_window"] = confirmation.isWindow()
                message_box = confirmation._box
            else:
                message_boxes = [
                    widget
                    for widget in QApplication.allWidgets()
                    if isinstance(widget, QtWidgets.QMessageBox) and widget.isVisible()
                ]
                message_box = message_boxes[-1]
                seen["confirmation_is_window"] = message_box.isWindow()

            discard_button = next(
                button
                for button in message_box.buttons()
                if button.text().replace("&", "") == "Не сохранять"
            )
            discard_button.click()

        def exercise_preview():
            preview = previews[0]
            seen["preview_is_window"] = preview.isWindow()
            seen["preview_uses_shell_host"] = (
                find_overlay_host(preview) is shell.overlay_host
            )

            preview._select_row(0)
            preview.translation_editor.setPlainText("A manually edited")
            seen["edit_became_dirty"] = preview._translation_edit_dirty

            QtCore.QTimer.singleShot(0, resolve_confirmation)
            preview.table.setCurrentCell(1, 1)
            seen["selected_term"] = preview._current_edit_data["original"]
            preview.reject()

        QtCore.QTimer.singleShot(0, exercise_preview)
        with (
            patch.object(ai_correction_module, "CorrectionPreviewDialog", CapturingPreview),
            patch.object(NotificationManager, "show", return_value=None),
        ):
            page._handle_correction_patch(
                {
                    "Alpha": {"rus": "A2", "note": ""},
                    "Beta": {"rus": "B2", "note": ""},
                }
            )

        self.assertTrue(seen["edit_became_dirty"])
        self.assertFalse(seen["preview_is_window"])
        self.assertTrue(seen["preview_uses_shell_host"])
        self.assertTrue(seen["confirmation_card_shown"])
        self.assertFalse(seen["confirmation_is_window"])
        self.assertEqual(seen["selected_term"], "Beta")

    def test_note_wipe_review_stays_in_overlay_card(self):
        shell = self._shell()
        page = shell.navigation.current_page()
        preview = CorrectionPreviewDialog(
            original_glossary_list=[
                {"original": "Alpha", "rus": "A", "note": "Old note"}
            ],
            patch_dict={
                "Alpha": {"rus": "A2", "note": "New note"}
            },
            direct_conflicts={},
            parent=page,
        )
        preview.review_data[0]["is_note_wiped"] = True
        seen = {}

        def close_note_review():
            note_reviews = [
                widget
                for widget in QApplication.allWidgets()
                if isinstance(widget, ai_correction_module.NoteWipeResolutionDialog)
                and widget.isVisible()
            ]
            note_review = note_reviews[-1]
            seen["is_window"] = note_review.isWindow()
            seen["uses_shell_host"] = (
                find_overlay_host(note_review) is shell.overlay_host
            )
            note_review.reject()

        def apply_and_close_preview():
            QtCore.QTimer.singleShot(0, close_note_review)
            preview._apply_and_accept()
            preview.reject()

        QtCore.QTimer.singleShot(0, apply_and_close_preview)
        result = exec_dialog(page, preview)

        self.assertEqual(result, int(QtWidgets.QDialog.DialogCode.Rejected))
        self.assertFalse(seen["is_window"])
        self.assertTrue(seen["uses_shell_host"])

    def test_normalized_equivalent_translation_key_ignores_wrappers_case_and_yo(self):
        normalized = normalize_translation_review_key(
            ' [\u00ab\u041f\u043e\u043b\u0443\u0434\u0401\u043c\u043e\u043d\u00bb] '
        )
        self.assertEqual(normalized, "\u043f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d")

    def test_classify_translation_change_treats_case_only_change_as_cosmetic(self):
        meaningful, cosmetic = classify_translation_review_change(
            ["\u041f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d"],
            "\u043f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d",
        )
        self.assertFalse(meaningful)
        self.assertTrue(cosmetic)

    def test_classify_translation_change_treats_wrappers_as_same_translation(self):
        meaningful, cosmetic = classify_translation_review_change(
            ["\u041f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d"],
            '["\u041f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d"]',
        )
        self.assertFalse(meaningful)
        self.assertTrue(cosmetic)

    def test_classify_translation_change_keeps_real_translation_change_visible(self):
        meaningful, cosmetic = classify_translation_review_change(
            ["\u041f\u043e\u043b\u0443\u0434\u0435\u043c\u043e\u043d"],
            "\u0412\u044b\u0441\u0448\u0438\u0439 \u0434\u0435\u043c\u043e\u043d",
        )
        self.assertTrue(meaningful)
        self.assertFalse(cosmetic)

    def test_manual_save_does_not_prompt_during_table_rebuild(self):
        dialog = CorrectionPreviewDialog(
            original_glossary_list=[
                {"original": "Wei", "rus": "Vey", "note": "Character"}
            ],
            patch_dict={
                "Wei": {"rus": "Senior Vey", "note": "Character"}
            },
            direct_conflicts={},
        )
        self.addCleanup(dialog.close)

        pending_prompts = []
        dialog._resolve_pending_translation_edit = (
            lambda *args, **kwargs: pending_prompts.append((args, kwargs)) or True
        )

        dialog._select_row(0)
        dialog.translation_editor.setPlainText("Vey the Elder")
        self.assertTrue(dialog._translation_edit_dirty)

        self.assertTrue(dialog._save_current_translation_edit())
        self.assertEqual(pending_prompts, [])

    def test_manual_note_save_preserves_user_edit_and_skips_determination(self):
        dialog = CorrectionPreviewDialog(
            original_glossary_list=[
                {"original": "Wei", "rus": "Vey", "note": "Character"}
            ],
            patch_dict={
                "Wei": {"rus": "Senior Vey", "note": "AI Note"}
            },
            direct_conflicts={},
        )
        self.addCleanup(dialog.close)

        dialog._select_row(0)
        dialog.note_editor.setPlainText("User Note")
        self.assertTrue(dialog._translation_edit_dirty)

        self.assertTrue(dialog._save_current_translation_edit())
        
        edited_data = dialog.review_data[0]
        self.assertEqual(edited_data["new_note"], "User Note")
        self.assertTrue(edited_data.get("_user_edited_note"))

if __name__ == "__main__":
    unittest.main()
