"""cluster-73: TermVersioningDialog читает/пишет glossary_versions.json
в обход TranslationProjectManager.

До рефакторинга ``TermVersioningDialog._load_all_versions``/``_save_all_versions``
открывали ``glossary_versions.json`` напрямую (os.path.join + open), в обход
``TranslationProjectManager.load_version_map``/``save_version_map`` и их
``self.lock`` — при параллельном чтении из воркера перевода (glossary_tools.py)
и записи из UI-диалога возможна гонка (частичное/повреждённое чтение JSON).

Канонические методы — ``TranslationProjectManager.load_version_map`` /
``TranslationProjectManager.save_version_map`` в
gemini_translator/utils/project_manager.py.

Часть A — характеризационные тесты канонической реализации (крайние случаи,
которые различали копии: отсутствующий файл, битый JSON, ошибка записи).
Часть B — тест-маршрутизация: должен падать до рефакторинга (диалог сам
открывал файл, минуя project_manager) и проходить после (диалог вызывает
project_manager.load_version_map()/save_version_map()).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GT_DISABLE_LOCAL_MODEL_DISCOVERY", "1")

import json
import tempfile
import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox

from gemini_translator.utils.project_manager import TranslationProjectManager
from gemini_translator.ui.dialogs.glossary_dialogs.versioning import TermVersioningDialog


_APP = None


def _ensure_app():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


class LoadSaveVersionMapCharacterizationTests(unittest.TestCase):
    """Часть A: поведение канонических load_version_map/save_version_map."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.pm = TranslationProjectManager(self.tmpdir.name)

    def test_load_returns_empty_dict_when_file_missing(self):
        self.assertEqual(self.pm.load_version_map(), {})

    def test_load_returns_empty_dict_on_corrupt_json(self):
        version_file = os.path.join(self.tmpdir.name, "glossary_versions.json")
        with open(version_file, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        self.assertEqual(self.pm.load_version_map(), {})

    def test_save_then_load_roundtrip(self):
        data = {"Term": [{"scope": ["ch1.xhtml"], "override": {"rus": "Термин"}}]}
        self.pm.save_version_map(data)
        self.assertEqual(self.pm.load_version_map(), data)

    def test_save_writes_to_project_folder_glossary_versions_json(self):
        data = {"Term": []}
        self.pm.save_version_map(data)
        version_file = os.path.join(self.tmpdir.name, "glossary_versions.json")
        self.assertTrue(os.path.exists(version_file))
        with open(version_file, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), data)

    def test_save_raises_on_write_failure_so_ui_can_report_it(self):
        """save_version_map должен пробрасывать ошибку записи наверх,
        чтобы вызывающий UI-код (TermVersioningDialog) мог показать её
        пользователю — как раньше делал самодельный _save_all_versions."""
        with patch(
            "gemini_translator.utils.project_manager.atomic_write_text",
            side_effect=OSError("disk full"),
            create=True,
        ):
            with self.assertRaises(OSError):
                self.pm.save_version_map({"Term": []})

    def test_update_term_versions_sets_and_removes_a_single_term(self):
        self.pm.save_version_map(
            {"Other": [{"scope": [], "override": {"rus": "X"}}]}
        )
        rules = [{"scope": ["ch1.xhtml"], "override": {"rus": "Термин"}}]

        result = self.pm.update_term_versions("Term", rules)

        self.assertEqual(result.get("Term"), rules)
        self.assertEqual(result.get("Other"), [{"scope": [], "override": {"rus": "X"}}])
        self.assertEqual(self.pm.load_version_map(), result)

        result2 = self.pm.update_term_versions("Term", [])

        self.assertNotIn("Term", result2)
        self.assertIn("Other", result2)
        self.assertEqual(self.pm.load_version_map(), result2)


class TermVersioningDialogRoutingTests(unittest.TestCase):
    """Часть B: диалог обязан ходить через project_manager, не открывать файл сам."""

    def setUp(self):
        _ensure_app()
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.pm = TranslationProjectManager(self.tmpdir.name)

    def _make_dialog(self):
        return TermVersioningDialog(
            term="Term",
            base_data={"rus": "Термин", "note": ""},
            project_manager=self.pm,
            epub_path=None,
        )

    def test_construction_routes_through_project_manager_load(self):
        """Должно ПАДАТЬ до рефакторинга: диалог сам открывал versions_file,
        не вызывая project_manager.load_version_map()."""
        sentinel_rule = {"scope": ["ch1.xhtml"], "override": {"rus": "sentinel"}}
        with patch.object(
            TranslationProjectManager,
            "load_version_map",
            return_value={"Term": [sentinel_rule]},
        ) as mocked_load:
            dlg = self._make_dialog()
            try:
                mocked_load.assert_called_once()
                self.assertEqual(dlg.term_rules, [sentinel_rule])
            finally:
                dlg.deleteLater()

    def test_add_rule_routes_through_project_manager_save(self):
        """Должно ПАДАТЬ до рефакторинга: _save_all_versions писал файл
        напрямую, не вызывая project_manager.update_term_versions()."""
        dlg = self._make_dialog()
        try:
            with patch.object(
                TranslationProjectManager, "update_term_versions"
            ) as mocked_save:
                dlg.term_rules.append(
                    {"scope": ["ch1.xhtml"], "override": {"rus": "Вариант"}}
                )
                dlg._save_all_versions()
                mocked_save.assert_called_once_with("Term", dlg.term_rules)
        finally:
            dlg.deleteLater()

    def test_dialog_does_not_open_files_directly(self):
        """Ни при построении, ни при сохранении диалог сам (его собственный
        модуль versioning.py) не должен звать builtins.open на
        glossary_versions.json (или его tmp-файл атомарной записи) — эта
        работа делегирована project_manager, у которого есть self.lock.

        Гвард подставляет ``_guarded_open`` НАПРЯМУЮ в builtins.open (а не
        через ``side_effect=`` на автосозданный MagicMock) — иначе
        ``open(...)`` вызывает ``Mock.__call__``, и непосредственным
        вызывающим в ``inspect.stack()[1]`` всегда оказывается
        unittest/mock.py, а не настоящий caller, и guard не может
        сработать ни при каком коде (см. cluster-73 review, issue major #1:
        scratchpad/guard_probe.py -> 'triggered: []' даже когда versioning.py
        открывал файл напрямую).

        До рефакторинга падает: у диалога был собственный versions_file, и
        он сам открывал файл (guard срабатывает и уходит в
        QMessageBox.critical, который мы держим замоканным, чтобы не
        поймать реальный модальный exec() и не зависнуть)."""
        import inspect

        versioning_module_file = os.path.abspath(
            inspect.getsourcefile(TermVersioningDialog)
        )

        dlg = self._make_dialog()
        try:
            version_file = os.path.join(self.tmpdir.name, "glossary_versions.json")
            version_file_candidates = {
                os.path.abspath(version_file),
                os.path.abspath(version_file + ".tmp"),
            }
            real_open = open
            triggered = []

            def _guarded_open(path, *args, **kwargs):
                caller_file = os.path.abspath(inspect.stack()[1].filename)
                if (
                    os.path.abspath(str(path)) in version_file_candidates
                    and caller_file == versioning_module_file
                ):
                    triggered.append(True)
                    raise AssertionError(
                        "TermVersioningDialog открыл glossary_versions.json "
                        "напрямую в обход project_manager"
                    )
                return real_open(path, *args, **kwargs)

            with patch("builtins.open", _guarded_open), \
                 patch.object(QMessageBox, "critical"):
                dlg.term_rules.append(
                    {"scope": ["ch1.xhtml"], "override": {"rus": "Вариант"}}
                )
                dlg._save_all_versions()

            self.assertFalse(
                triggered,
                "TermVersioningDialog открыл glossary_versions.json напрямую",
            )
            self.assertEqual(
                self.pm.load_version_map().get("Term"), dlg.term_rules
            )
        finally:
            dlg.deleteLater()

    def test_save_does_not_clobber_concurrent_edit_of_another_term(self):
        """Закрывает lost-update окно (review issue minor #3): диалог читает
        всю карту один раз в конструкторе; если за время, пока он открыт,
        карту меняет кто-то ещё (второй диалог версий, тот же
        project_manager из другого места), сохранение своего термина не
        должно затирать чужую правку устаревшим снимком all_versions_data."""
        self.pm.save_version_map(
            {"Other": [{"scope": [], "override": {"rus": "X"}}]}
        )
        dlg = self._make_dialog()
        try:
            # Пока dlg открыт (self.all_versions_data — снимок с "Other": X),
            # кто-то ещё меняет "Other" через тот же project_manager.
            self.pm.update_term_versions(
                "Other", [{"scope": ["ch2.xhtml"], "override": {"rus": "Y"}}]
            )

            dlg.term_rules.append(
                {"scope": ["ch1.xhtml"], "override": {"rus": "Вариант"}}
            )
            dlg._save_all_versions()

            result = self.pm.load_version_map()
            self.assertEqual(result.get("Term"), dlg.term_rules)
            self.assertEqual(
                result.get("Other"),
                [{"scope": ["ch2.xhtml"], "override": {"rus": "Y"}}],
            )
        finally:
            dlg.deleteLater()

    def test_save_failure_still_shown_to_user_via_message_box(self):
        """Поведение сохранено: при ошибке записи пользователь видит
        QMessageBox.critical, а не тихий сбой."""
        dlg = self._make_dialog()
        try:
            dlg.term_rules.append(
                {"scope": ["ch1.xhtml"], "override": {"rus": "Вариант"}}
            )
            with patch.object(
                TranslationProjectManager,
                "update_term_versions",
                side_effect=OSError("disk full"),
            ):
                with patch.object(QMessageBox, "critical") as mocked_critical:
                    dlg._save_all_versions()
                    mocked_critical.assert_called_once()
        finally:
            dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
