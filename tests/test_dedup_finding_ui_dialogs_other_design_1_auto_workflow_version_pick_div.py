"""dedup finding-ui-dialogs-other/design/1 (auto_workflow_version_pick_div):
choose_preferred_translation_rel_path в auto_workflow.py переизобретал
канонический select_target_translation_version (utils/translation_versions.py)
со своей логикой выбора версии перевода главы.

Разошедшееся поведение (verify_evidence): при одновременном наличии версии ""
(без суффикса) и "_validated.html" старая локальная функция безусловно
предпочитала "" (первая же проверка в теле функции), тогда как канонический
хелпер, которым пользуется остальной пайплайн (validation.py, qa/assembly.py),
предпочитает validated-версию, если файл реально существует на диске.

(a) Характеризационные тесты фиксируют поведение канонической реализации на
    краевых случаях, которые как раз расходились между копиями:
    - validated побеждает версию "" (пустой суффикс), если файл validated
      существует на диске;
    - при отсутствии validated-файла на диске версия "" используется как
      обычный кандидат наравне с другими (не привилегированно, но и не
      игнорируется);
    - единственная версия "" выбирается сама собой;
    - суффикс "filtered" по-прежнему игнорируется.

(b) Тесты-маршрутизаторы патчат select_target_translation_version в модуле
    auto_workflow и проверяют, что load_project_chapters_for_consistency
    реально вызывает канонический хелпер (а не свою локальную копию) и что
    результат выбора канонического хелпера учитывается, даже когда он
    расходится со старой локальной логикой. До рефакторинга (локальная
    choose_preferred_translation_rel_path) эти тесты ПАДАЮТ — после
    рефакторинга проходят.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs import auto_workflow as auto_workflow_module
from gemini_translator.ui.dialogs.auto_workflow import load_project_chapters_for_consistency
from gemini_translator.utils.translation_versions import select_target_translation_version


def _fresh_tmp_dir(name):
    path = Path("tests") / ".tmp_dedup_auto_workflow_version_pick" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


class SelectTargetTranslationVersionEmptySuffixCharacterizationTests(unittest.TestCase):
    """(a) Канонический выбор версии перевода — краевые случаи, которые
    расходились между choose_preferred_translation_rel_path и
    select_target_translation_version."""

    def test_validated_on_disk_wins_over_empty_suffix_version(self):
        tmp_path = _fresh_tmp_dir("validated_wins_over_empty_suffix")
        try:
            validated_path = tmp_path / "Text" / "ch1_validated.html"
            plain_path = tmp_path / "Text" / "ch1.html"
            validated_path.parent.mkdir(parents=True)
            validated_path.write_text("<p>validated</p>", encoding="utf-8")
            plain_path.write_text("<p>plain</p>", encoding="utf-8")

            rel_path, is_validated = select_target_translation_version(
                {
                    "": "Text/ch1.html",
                    "_validated.html": "Text/ch1_validated.html",
                },
                str(tmp_path),
            )

            self.assertEqual(rel_path, "Text/ch1_validated.html")
            self.assertTrue(is_validated)
        finally:
            shutil.rmtree(tmp_path.parent, ignore_errors=True)

    def test_empty_suffix_used_as_plain_candidate_when_validated_missing_from_disk(self):
        tmp_path = _fresh_tmp_dir("empty_suffix_fallback")
        try:
            plain_path = tmp_path / "Text" / "ch1.html"
            plain_path.parent.mkdir(parents=True)
            plain_path.write_text("<p>plain</p>", encoding="utf-8")
            # "_validated.html" числится в карте версий, но файла на диске нет.

            rel_path, is_validated = select_target_translation_version(
                {
                    "": "Text/ch1.html",
                    "_validated.html": "Text/ch1_validated.html",
                },
                str(tmp_path),
            )

            self.assertEqual(rel_path, "Text/ch1.html")
            self.assertFalse(is_validated)
        finally:
            shutil.rmtree(tmp_path.parent, ignore_errors=True)

    def test_only_empty_suffix_version_present(self):
        tmp_path = _fresh_tmp_dir("only_empty_suffix")
        try:
            plain_path = tmp_path / "Text" / "ch1.html"
            plain_path.parent.mkdir(parents=True)
            plain_path.write_text("<p>plain</p>", encoding="utf-8")

            rel_path, is_validated = select_target_translation_version(
                {"": "Text/ch1.html"},
                str(tmp_path),
            )

            self.assertEqual(rel_path, "Text/ch1.html")
            self.assertFalse(is_validated)
        finally:
            shutil.rmtree(tmp_path.parent, ignore_errors=True)

    def test_filtered_suffix_ignored_even_with_no_other_candidates(self):
        tmp_path = _fresh_tmp_dir("filtered_ignored")
        try:
            filtered_path = tmp_path / "Text" / "ch1_filtered.html"
            filtered_path.parent.mkdir(parents=True)
            filtered_path.write_text("<p>filtered</p>", encoding="utf-8")

            rel_path, is_validated = select_target_translation_version(
                {"filtered": "Text/ch1_filtered.html"},
                str(tmp_path),
            )

            self.assertIsNone(rel_path)
            self.assertFalse(is_validated)
        finally:
            shutil.rmtree(tmp_path.parent, ignore_errors=True)


class _ConsistencyProjectManagerStub:
    def __init__(self, project_folder, originals, versions_map):
        self.project_folder = project_folder
        self._originals = list(originals)
        self._versions_map = {
            original: dict(versions) for original, versions in versions_map.items()
        }

    def get_all_originals(self):
        return list(self._originals)

    def get_versions_for_original(self, original_path):
        return dict(self._versions_map.get(original_path, {}))


class LoadProjectChaptersRoutesThroughCanonicalSelectorTests(unittest.TestCase):
    """(b) Маршрутизация: load_project_chapters_for_consistency должна выбирать
    версию перевода главы через канонический select_target_translation_version,
    а не через свою локальную copy-логику."""

    def test_canonical_selector_is_called_with_versions_and_project_folder(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            internal_path = "Text/chapter1.xhtml"
            translated_rel_path = os.path.join("Text", "chapter1.html")
            translated_full_path = os.path.join(tmp_dir, translated_rel_path)
            os.makedirs(os.path.dirname(translated_full_path), exist_ok=True)
            with open(translated_full_path, "w", encoding="utf-8") as handle:
                handle.write("<p>translated</p>")

            versions = {"": translated_rel_path}
            project_manager = _ConsistencyProjectManagerStub(
                tmp_dir, [internal_path], {internal_path: versions}
            )

            with patch.object(
                auto_workflow_module,
                "select_target_translation_version",
                wraps=select_target_translation_version,
            ) as mock_selector:
                chapters = load_project_chapters_for_consistency(project_manager)

            mock_selector.assert_any_call(versions, tmp_dir)
            self.assertEqual(len(chapters), 1)
            self.assertEqual(chapters[0]["name"], "chapter1.xhtml")

    def test_canonical_selector_choice_is_honored_even_when_it_diverges_from_old_local_logic(self):
        # Старая локальная choose_preferred_translation_rel_path безусловно
        # предпочитала версию "" (без суффикса) версии "_validated.html".
        # Канонический select_target_translation_version предпочитает
        # validated, если файл реально существует на диске, — это и есть
        # исправленное поведение (behavior_choice).
        with tempfile.TemporaryDirectory() as tmp_dir:
            internal_path = "Text/chapter1.xhtml"
            plain_rel = os.path.join("Text", "chapter1.html")
            validated_rel = os.path.join("Text", "chapter1_validated.html")
            os.makedirs(os.path.join(tmp_dir, "Text"), exist_ok=True)
            with open(os.path.join(tmp_dir, plain_rel), "w", encoding="utf-8") as handle:
                handle.write("<p>raw draft text</p>")
            with open(os.path.join(tmp_dir, validated_rel), "w", encoding="utf-8") as handle:
                handle.write("<p>proofread text</p>")

            versions = {"": plain_rel, "_validated.html": validated_rel}
            project_manager = _ConsistencyProjectManagerStub(
                tmp_dir, [internal_path], {internal_path: versions}
            )

            chapters = load_project_chapters_for_consistency(project_manager)

            self.assertEqual(len(chapters), 1)
            self.assertIn("proofread text", chapters[0]["content"])
            self.assertNotIn("raw draft text", chapters[0]["content"])


if __name__ == "__main__":
    unittest.main()
