"""pcluster-30: ручные обходы parent() -> канонический find_ancestor_by_class_name /
новый find_ancestor_by_predicate (ui/widgets/ancestor_utils.py).

(а) Характеризационные тесты на find_ancestor_by_predicate (и на то, что
    find_ancestor_by_class_name продолжает вести себя как раньше поверх него).
(б) Тест-маршрутизация: подменяем каноническую функцию и проверяем, что
    каждое бывшее место ручного обхода .parent()/.parentWidget() идёт через
    неё. Эти тесты обязаны падать на дореформенном коде (см. отчёт агента).
"""
import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets

from gemini_translator.ui.widgets.ancestor_utils import (
    find_ancestor_by_class_name,
    find_ancestor_by_predicate,
)
from gemini_translator.ui.dialogs.glossary_dialogs import ai_correction
from gemini_translator.ui.dialogs.validation_dialogs import untranslated_fixer_dialog as ufd
from gemini_translator.ui.dialogs import validation
from gemini_translator.ui.dialogs import glossary
from gemini_translator.ui.dialogs import menu_utils

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _Node:
    """Обычный (не-Qt) объект с .parent() — этого достаточно для функций,
    которые ходят по цепочке только через .parent()/getattr()."""

    def __init__(self, parent=None, **attrs):
        self._parent = parent
        for key, value in attrs.items():
            setattr(self, key, value)

    def parent(self):
        return self._parent


# --- (а) характеризационные тесты канонической find_ancestor_by_predicate ---

class FindAncestorByPredicateTests(unittest.TestCase):
    def test_checks_start_node_itself_first(self):
        root = _Node(marker=True)
        self.assertIs(find_ancestor_by_predicate(root, lambda n: getattr(n, 'marker', False)), root)

    def test_walks_up_to_first_match(self):
        root = _Node(marker=True)
        mid = _Node(parent=root)
        leaf = _Node(parent=mid)
        self.assertIs(find_ancestor_by_predicate(leaf, lambda n: getattr(n, 'marker', False)), root)

    def test_returns_none_when_absent(self):
        leaf = _Node()
        self.assertIsNone(find_ancestor_by_predicate(leaf, lambda n: getattr(n, 'marker', False)))

    def test_returns_none_for_none_start(self):
        self.assertIsNone(find_ancestor_by_predicate(None, lambda n: True))

    def test_max_depth_limits_search(self):
        root = _Node(marker=True)
        mid = _Node(parent=root)
        leaf = _Node(parent=mid)
        self.assertIsNone(
            find_ancestor_by_predicate(leaf, lambda n: getattr(n, 'marker', False), max_depth=2)
        )
        self.assertIs(
            find_ancestor_by_predicate(leaf, lambda n: getattr(n, 'marker', False), max_depth=3),
            root,
        )

    def test_guards_against_parent_cycles(self):
        a = _Node()
        b = _Node(parent=a)
        a._parent = b  # a <-> b цикл
        self.assertIsNone(find_ancestor_by_predicate(a, lambda n: False))

    def test_stops_gracefully_when_a_candidate_has_no_parent_method(self):
        # Объединение защит четырёх снятых копий: одна из них (было в
        # validation._get_ai_repair_protected_terms) проверяла
        # callable(getattr(current, "parent", None)) перед вызовом, чтобы не
        # упасть на не-QObject узле (например, ручной заглушке в тестах).
        class _NoParentAttr:
            pass

        leaf = _Node(parent=_NoParentAttr())
        self.assertIsNone(find_ancestor_by_predicate(leaf, lambda n: False))

    def test_find_ancestor_by_class_name_still_skips_the_widget_itself(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        class InitialSetupPage(QtWidgets.QWidget):
            pass

        root = InitialSetupPage()
        self.addCleanup(root.close)
        mid = QtWidgets.QWidget(root)
        leaf = QtWidgets.QWidget(mid)
        self.assertIs(find_ancestor_by_class_name(leaf, "InitialSetupPage"), root)
        self.assertIsNone(find_ancestor_by_class_name(root, "InitialSetupPage"))


# --- (б) маршрутизация: ai_correction.CorrectionSessionPage._locate_glossary_owner ---

class AiCorrectionLocateOwnerRoutingTests(unittest.TestCase):
    def test_delegates_to_canonical_even_when_direct_parent_already_matches(self):
        # До рефактора здесь был ручной "if parent.__class__.__name__ in (...)":
        # при совпадении прямого родителя canonical вообще не вызывался.
        MainWindow = type("MainWindow", (_Node,), {})
        direct_parent = MainWindow()
        leaf = _Node(parent=direct_parent)
        sentinel = object()
        with mock.patch.object(
            ai_correction, "find_ancestor_by_class_name", return_value=sentinel
        ) as canon:
            result = ai_correction.CorrectionSessionPage._locate_glossary_owner(leaf)
        canon.assert_called_once_with(leaf, 'MainWindow', 'GlossaryManagerPage')
        self.assertIs(result, sentinel)


# --- (б) маршрутизация: untranslated_fixer_dialog.py ---

class ProjectGlossaryControllerRoutingTests(unittest.TestCase):
    def test_discover_context_delegates_to_canonical(self):
        owner_parent = _Node()
        owner = _Node(parent=owner_parent)
        with mock.patch.object(
            ufd, "find_ancestor_by_predicate", return_value=None
        ) as canon:
            ufd.ProjectGlossaryController(owner)
        # один вызов на поиск project_folder, один — на glossary_widget
        self.assertEqual(canon.call_count, 2)
        for call in canon.call_args_list:
            self.assertIs(call.args[0], owner_parent)

    def test_discover_context_behaviour_matches_previous_manual_walk(self):
        top = _Node(project_manager=_Node(project_folder="/proj"))
        mid = _Node(parent=top, glossary_widget="GW")
        owner = _Node(parent=mid)
        controller = ufd.ProjectGlossaryController(owner)
        self.assertEqual(controller.project_folder, "/proj")
        self.assertEqual(controller.glossary_widget, "GW")
        self.assertIs(controller.glossary_owner, mid)


class GetProjectManagerRoutingTests(unittest.TestCase):
    def test_delegates_to_canonical_for_both_search_phases(self):
        validator_host = _Node()
        parent_node = _Node()
        stub_self = _Node(parent=parent_node)
        stub_self._validator_host = validator_host
        with mock.patch.object(ufd, "find_ancestor_by_predicate", return_value=None) as canon:
            result = ufd.UntranslatedFixerPage._get_project_manager(stub_self)
        self.assertIsNone(result)
        self.assertEqual(canon.call_count, 2)
        self.assertIs(canon.call_args_list[0].args[0], validator_host)
        self.assertIs(canon.call_args_list[1].args[0], parent_node)

    def test_finds_project_manager_via_validator_host_first(self):
        pm = object()
        host_ancestor = _Node(project_manager=pm)
        stub_self = _Node(_validator_host=_Node(parent=host_ancestor), parent=_Node())
        result = ufd.UntranslatedFixerPage._get_project_manager(stub_self)
        self.assertIs(result, pm)

    def test_falls_back_to_self_parent_chain(self):
        pm = object()
        parent_ancestor = _Node(project_manager=pm)
        stub_self = _Node(_validator_host=None, parent=_Node(parent=parent_ancestor))
        result = ufd.UntranslatedFixerPage._get_project_manager(stub_self)
        self.assertIs(result, pm)


class AdvancedTagFilterDialogRoutingTests(unittest.TestCase):
    def test_project_folder_search_delegates_to_canonical(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        parent = QtWidgets.QWidget()
        self.addCleanup(parent.close)
        with mock.patch.object(ufd, "find_ancestor_by_predicate", return_value=None) as canon:
            dlg = ufd.AdvancedTagFilterDialog(set(), set(), parent=parent)
        self.addCleanup(dlg.close)
        canon.assert_called_once()
        self.assertIs(canon.call_args.args[0], parent)
        self.assertIsNone(dlg.project_folder)

    def test_finds_real_project_folder_through_widget_ancestry(self):
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

        class FakeProjectManager:
            project_folder = "/some/project"

        class OwnerWindow(QtWidgets.QWidget):
            pass

        owner = OwnerWindow()
        owner.project_manager = FakeProjectManager()
        self.addCleanup(owner.close)
        mid = QtWidgets.QWidget(owner)
        dlg = ufd.AdvancedTagFilterDialog(set(), set(), parent=mid)
        self.addCleanup(dlg.close)
        self.assertEqual(dlg.project_folder, "/some/project")


# --- (б) маршрутизация: validation.TranslationValidatorPage._get_ai_repair_protected_terms ---

class GetAiRepairProtectedTermsRoutingTests(unittest.TestCase):
    def test_delegates_to_canonical_with_depth_limit(self):
        stub_self = _Node()
        with mock.patch.object(validation, "find_ancestor_by_predicate", return_value=None) as canon:
            terms = validation.TranslationValidatorPage._get_ai_repair_protected_terms(stub_self)
        self.assertEqual(terms, set())
        canon.assert_called_once()
        self.assertIs(canon.call_args.args[0], stub_self)
        self.assertEqual(canon.call_args.kwargs.get("max_depth"), 10)

    def test_extracts_terms_from_found_glossary_owner(self):
        glossary_widget = mock.Mock()
        glossary_widget.get_glossary.return_value = {"t1": {"rus": "Слово"}}
        owner = _Node(glossary_widget=glossary_widget)
        stub_self = _Node(parent=owner)
        terms = validation.TranslationValidatorPage._get_ai_repair_protected_terms(stub_self)
        self.assertIn("Слово", terms)
        glossary_widget.commit_active_editor.assert_called_once()


# --- (б) маршрутизация: glossary.MainWindow.closeEvent -> menu_utils.prompt_return_to_menu ---

class GlossaryCloseEventRoutingTests(unittest.TestCase):
    def _make_target(self, launch_mode='standalone', dialog_result_closing=False):
        page = mock.Mock()
        page.launch_mode = launch_mode
        page._dialog_result_closing = dialog_result_closing
        target = mock.Mock()
        target.page = page
        return target

    def test_cancel_ignores_close_without_asking_backup(self):
        target = self._make_target()
        event = mock.Mock()
        with mock.patch.object(glossary, "prompt_return_to_menu", return_value="cancel") as prompt:
            glossary.MainWindow.closeEvent(target, event)
        prompt.assert_called_once_with(target)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()
        target.page._ask_delete_backup.assert_not_called()

    def test_menu_asks_backup_and_exits_with_reboot_code(self):
        target = self._make_target()
        event = mock.Mock()
        with mock.patch.object(glossary, "prompt_return_to_menu", return_value="menu"), \
                mock.patch.object(glossary.QApplication, "exit") as app_exit:
            glossary.MainWindow.closeEvent(target, event)
        target.page._ask_delete_backup.assert_called_once()
        app_exit.assert_called_once_with(2000)
        event.accept.assert_called_once()

    def test_exit_asks_backup_without_reboot_code(self):
        target = self._make_target()
        event = mock.Mock()
        with mock.patch.object(glossary, "prompt_return_to_menu", return_value="exit"), \
                mock.patch.object(glossary.QApplication, "exit") as app_exit:
            glossary.MainWindow.closeEvent(target, event)
        target.page._ask_delete_backup.assert_called_once()
        app_exit.assert_not_called()
        event.accept.assert_called_once()

    def test_non_standalone_result_closing_skips_prompt_entirely(self):
        target = self._make_target(launch_mode='dialog', dialog_result_closing=True)
        event = mock.Mock()
        with mock.patch.object(glossary, "prompt_return_to_menu") as prompt:
            glossary.MainWindow.closeEvent(target, event)
        prompt.assert_not_called()
        target.page._ask_delete_backup.assert_called_once()
        event.accept.assert_called_once()


# --- (в) prompt_return_to_menu: закрытие мессаджбокса крестиком -> "cancel"
# (характеризация поведения, которое теперь используют оба closeEvent —
#  glossary.MainWindow и validation.TranslationValidatorDialog) ---

class PromptReturnToMenuClosedByXTests(unittest.TestCase):
    def test_clicked_button_none_is_treated_as_cancel(self):
        # QMessageBox.clickedButton() возвращает None, если диалог закрыт
        # крестиком/Esc, а не одной из добавленных кнопок.
        fake_box = mock.Mock()
        fake_box.addButton.side_effect = lambda *a, **k: mock.Mock()
        fake_box.clickedButton.return_value = None
        with mock.patch.object(menu_utils, "QMessageBox", return_value=fake_box):
            result = menu_utils.prompt_return_to_menu(None)
        self.assertEqual(result, "cancel")


# --- (б) маршрутизация: validation.TranslationValidatorDialog.closeEvent ->
#     menu_utils.prompt_return_to_menu (симметрично GlossaryCloseEventRoutingTests) ---

class TranslationValidatorCloseEventRoutingTests(unittest.TestCase):
    def _make_target(self, retry_is_available=False):
        page = mock.Mock()
        page._awaiting_analysis_thread_stop = False
        page.analysis_thread = None  # часть 1 (проверка потока) неактуальна здесь
        page.retry_is_available = retry_is_available
        target = mock.Mock()
        target.page = page
        return target

    def test_cancel_ignores_close(self):
        target = self._make_target()
        event = mock.Mock()
        with mock.patch.object(validation, "prompt_return_to_menu", return_value="cancel") as prompt:
            validation.TranslationValidatorDialog.closeEvent(target, event)
        prompt.assert_called_once_with(target)
        event.ignore.assert_called_once()
        event.accept.assert_not_called()

    def test_menu_exits_with_reboot_code(self):
        target = self._make_target()
        event = mock.Mock()
        with mock.patch.object(validation, "prompt_return_to_menu", return_value="menu"), \
                mock.patch.object(validation.QApplication, "exit") as app_exit:
            validation.TranslationValidatorDialog.closeEvent(target, event)
        app_exit.assert_called_once_with(2000)
        event.accept.assert_called_once()

    def test_exit_without_reboot_code(self):
        target = self._make_target()
        event = mock.Mock()
        with mock.patch.object(validation, "prompt_return_to_menu", return_value="exit"), \
                mock.patch.object(validation.QApplication, "exit") as app_exit:
            validation.TranslationValidatorDialog.closeEvent(target, event)
        app_exit.assert_not_called()
        event.accept.assert_called_once()

    def test_retry_available_skips_prompt_entirely(self):
        target = self._make_target(retry_is_available=True)
        event = mock.Mock()
        with mock.patch.object(validation, "prompt_return_to_menu") as prompt:
            validation.TranslationValidatorDialog.closeEvent(target, event)
        prompt.assert_not_called()
        event.accept.assert_called_once()


if __name__ == "__main__":
    unittest.main()
