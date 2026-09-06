# -*- coding: utf-8 -*-
"""
pcluster-24: выбор версии перевода для предпросмотра в setup.py
(_resolve_translated_preview_path) расходился с канонической
select_target_translation_version — предпросмотр мог показать более
свежий НЕ-готовый файл вместо утверждённого (_validated.html), тогда как
канон всегда безусловно предпочитает validated (если он реально есть на
диске).

Эти тесты характеризуют канон на разошедшемся кейсе и проверяют, что
_resolve_translated_preview_path реально маршрутизируется через него
(до рефакторинга падает: локальная копия сортировала по mtime и отдавала
приоритет более свежему файлу; после рефакторинга — проходит).
"""
import os
import shutil
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.setup import InitialSetupDialog
from gemini_translator.utils.translation_versions import (
    VALIDATED_SUFFIX,
    select_target_translation_version,
)


def _fresh_tmp_dir(name):
    path = Path("tests") / ".tmp_pcluster_24" / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


class _ProjectManagerStub:
    def __init__(self, project_folder, versions):
        self.project_folder = str(project_folder)
        self._versions = dict(versions)
        self.reload_calls = 0

    def reload_data_from_disk(self):
        self.reload_calls += 1

    def get_versions_for_original(self, chapter_path):
        return dict(self._versions)


class _PreviewHarness:
    """Тонкий хост без тяжёлой инициализации InitialSetupDialog/QDialog —
    метод в проде обращается только к self.project_manager."""

    _resolve_translated_preview_path = InitialSetupDialog._resolve_translated_preview_path

    def __init__(self, project_manager):
        self.project_manager = project_manager


class CanonicalCharacterizationTests(unittest.TestCase):
    """(a) Канон: validated безусловно побеждает более свежий не-validated файл."""

    def test_validated_wins_even_when_older_than_other_existing_version(self):
        tmp_path = _fresh_tmp_dir("canon_validated_wins")
        try:
            text_dir = tmp_path / "Text"
            text_dir.mkdir(parents=True)
            validated_file = text_dir / "ch1_validated.html"
            other_file = text_dir / "ch1_translated_dp.html"

            validated_file.write_text("<p>готово</p>", encoding="utf-8")
            time.sleep(0.02)
            other_file.write_text("<p>новее, но не готово</p>", encoding="utf-8")

            self.assertGreater(other_file.stat().st_mtime, validated_file.stat().st_mtime)

            rel_path, is_validated = select_target_translation_version(
                {
                    VALIDATED_SUFFIX: "Text/ch1_validated.html",
                    "_translated_dp.html": "Text/ch1_translated_dp.html",
                },
                str(tmp_path),
            )

            self.assertEqual(rel_path, "Text/ch1_validated.html")
            self.assertTrue(is_validated)
        finally:
            shutil.rmtree(tmp_path.parent, ignore_errors=True)


class ResolveTranslatedPreviewPathRoutingTests(unittest.TestCase):
    """(b) Маршрутизация: _resolve_translated_preview_path обязан звать канон,
    а не переизобретать выбор версии локальной сортировкой по mtime."""

    def test_preview_prefers_validated_over_newer_unvalidated_file(self):
        tmp_path = _fresh_tmp_dir("preview_prefers_validated")
        try:
            text_dir = tmp_path / "Text"
            text_dir.mkdir(parents=True)
            validated_file = text_dir / "ch1_validated.html"
            newer_file = text_dir / "ch1_translated_dp.html"

            validated_file.write_text("<p>готово</p>", encoding="utf-8")
            time.sleep(0.02)
            newer_file.write_text("<p>новее, но не готово</p>", encoding="utf-8")
            self.assertGreater(newer_file.stat().st_mtime, validated_file.stat().st_mtime)

            project_manager = _ProjectManagerStub(
                tmp_path,
                {
                    VALIDATED_SUFFIX: "Text/ch1_validated.html",
                    "_translated_dp.html": "Text/ch1_translated_dp.html",
                },
            )
            harness = _PreviewHarness(project_manager)

            preview_path, preview_suffix = harness._resolve_translated_preview_path("Text/ch1.html")

            self.assertEqual(preview_path, str(validated_file))
            self.assertEqual(preview_suffix, VALIDATED_SUFFIX)
            self.assertEqual(project_manager.reload_calls, 1)
        finally:
            shutil.rmtree(tmp_path.parent, ignore_errors=True)

    def test_preview_routes_through_canonical_selector(self):
        """Монки-патч канона в модуле setup.py: подставленное решение канона
        обязано определить результат _resolve_translated_preview_path."""
        tmp_path = _fresh_tmp_dir("preview_routes_through_canon")
        try:
            text_dir = tmp_path / "Text"
            text_dir.mkdir(parents=True)
            chosen_file = text_dir / "ch1_some_suffix.html"
            chosen_file.write_text("<p>x</p>", encoding="utf-8")

            versions = {"_some_suffix.html": "Text/ch1_some_suffix.html"}
            project_manager = _ProjectManagerStub(tmp_path, versions)
            harness = _PreviewHarness(project_manager)

            calls = []

            def fake_select(passed_versions, translated_folder):
                calls.append((passed_versions, translated_folder))
                return "Text/ch1_some_suffix.html", False

            import gemini_translator.ui.dialogs.setup as setup_module

            original = setup_module.select_target_translation_version
            setup_module.select_target_translation_version = fake_select
            try:
                preview_path, preview_suffix = harness._resolve_translated_preview_path("Text/ch1.html")
            finally:
                setup_module.select_target_translation_version = original

            self.assertEqual(len(calls), 1, "_resolve_translated_preview_path обязан звать канон ровно один раз")
            self.assertEqual(calls[0], (versions, str(tmp_path)))
            self.assertEqual(preview_path, str(chosen_file))
            self.assertEqual(preview_suffix, "_some_suffix.html")
        finally:
            shutil.rmtree(tmp_path.parent, ignore_errors=True)

    def test_preview_returns_none_when_map_points_to_missing_file(self):
        """Файл значится в карте версий, но физически отсутствует на диске —
        предпросмотр не должен падать, должен вернуть (None, None)."""
        tmp_path = _fresh_tmp_dir("preview_missing_file_on_disk")
        try:
            project_manager = _ProjectManagerStub(
                tmp_path,
                {VALIDATED_SUFFIX: "Text/ch1_validated.html"},
            )
            harness = _PreviewHarness(project_manager)

            preview_path, preview_suffix = harness._resolve_translated_preview_path("Text/ch1.html")

            self.assertIsNone(preview_path)
            self.assertIsNone(preview_suffix)
        finally:
            shutil.rmtree(tmp_path.parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
