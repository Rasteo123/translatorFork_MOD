import zipfile

import pytest
from PyQt6 import QtWidgets

from gemini_translator.ui.pages.validation_page import TranslationValidatorPage


class SavedFilters:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})

    def get_last_validation_filter_settings(self):
        return self.settings.copy()

    def save_last_validation_filter_settings(self, settings):
        self.settings = settings.copy()


@pytest.fixture
def make_page(qapp, monkeypatch, tmp_path):
    qapp.global_version = ""
    pages = []

    def create(settings=None, epub_path=None):
        store = settings if settings is not None else SavedFilters()
        monkeypatch.setattr(qapp, "settings_manager", store, raising=False)
        monkeypatch.setattr(qapp, "get_settings_manager", lambda: store, raising=False)
        page = TranslationValidatorPage(
            str(tmp_path), str(epub_path or tmp_path / "missing.epub"), project_manager=None,
        )
        page._populate_initial_table_timer.stop()
        pages.append(page)
        return page

    yield create
    for page in pages:
        page.deleteLater()


def select_manual(page):
    index = page.ratio_presets_combo.findText("Ручной")
    assert index >= 0, "В валидации должен быть ручной режим коэффициентов"
    page.ratio_presets_combo.setCurrentIndex(index)


def test_manual_bounds_reclassify_cached_rows_without_reanalysis(make_page):
    page = make_page()
    select_manual(page)
    for name in page.VALIDATION_FILTER_DEFAULTS:
        getattr(page, name).setChecked(name == "check_length_ratio")
    page.ratio_min_spinbox.setValue(1.0)
    page.ratio_max_spinbox.setValue(2.01)
    ratios = [0.99, 1.0, 2.0, 2.01]
    page.table_results.setRowCount(len(ratios))
    for row, ratio in enumerate(ratios):
        path = f"chapter{row}.xhtml"
        data = {
            "internal_html_path": path, "full_path": path,
            "has_cached_analysis": True, "len_orig": 1000,
            "len_trans": int(ratio * 1000), "ratio_value": ratio, "status": "neutral",
        }
        page._append_result_row(row, path, path, False, data, False, placeholder_text="")
    page.reapply_filters()
    assert [page.table_results.isRowHidden(row) for row in range(4)] == [False, True, True, False]

    page.ratio_min_spinbox.setValue(0.5)
    page.ratio_max_spinbox.setValue(3.0)
    assert all(page.table_results.isRowHidden(row) for row in range(4))
    assert page.dirty_files == set()


def test_manual_settings_survive_reopening_and_cjk_detection(make_page, tmp_path):
    epub_path = tmp_path / "cjk.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("chapter.xhtml", "<p>" + "中" * 150 + "</p>")
    store = SavedFilters()
    page = make_page(store)
    select_manual(page)
    page.ratio_max_spinbox.setValue(4.5)
    page.ratio_min_spinbox.setValue(2.5)
    page.check_repeating_chars.click()

    reopened = make_page(store, epub_path)
    assert reopened.ratio_presets_combo.currentText() == "Ручной"
    assert reopened._get_current_ratio_bounds() == (2.5, 4.5)
    assert reopened.check_repeating_chars.isChecked()


def test_explicit_preset_survives_cjk_detection(make_page, tmp_path):
    epub_path = tmp_path / "cjk.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("chapter.xhtml", "中" * 150)
    store = SavedFilters({"ratio_preset": "Медиана ±25%"})
    page = make_page(store, epub_path)
    assert page.ratio_presets_combo.currentText() == "Медиана ±25%"


def test_first_open_still_detects_cjk_preset(make_page, tmp_path):
    epub_path = tmp_path / "cjk.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("chapter.xhtml", "中" * 150)
    page = make_page(epub_path=epub_path)
    assert page.ratio_presets_combo.currentText() == "Иероглифический (象 -> A)"


def test_saving_other_filters_does_not_disable_language_detection(make_page, tmp_path):
    epub_path = tmp_path / "cjk.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("chapter.xhtml", "中" * 150)
    store = SavedFilters()
    page = make_page(store)
    page.check_repeating_chars.click()
    reopened = make_page(store, epub_path)
    assert reopened.ratio_presets_combo.currentText() == "Иероглифический (象 -> A)"


def test_automatically_detected_cjk_mode_does_not_override_next_alphabetic_book(make_page, tmp_path):
    epub_path = tmp_path / "cjk.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("chapter.xhtml", "中" * 150)
    store = SavedFilters()
    page = make_page(store, epub_path)
    assert page.ratio_presets_combo.currentText() == "Иероглифический (象 -> A)"
    reopened = make_page(store)
    assert reopened.ratio_presets_combo.currentText() == "Алфавитный (A -> A)"


@pytest.mark.parametrize("first_is_cjk", [False, True])
def test_explicitly_choosing_current_preset_persists_it(make_page, tmp_path, first_is_cjk):
    epub_path = tmp_path / "cjk.epub"
    with zipfile.ZipFile(epub_path, "w") as archive:
        archive.writestr("chapter.xhtml", "中" * 150)
    store = SavedFilters()
    page = make_page(store, epub_path if first_is_cjk else None)
    page.ratio_presets_combo.activated.emit(page.ratio_presets_combo.currentIndex())
    reopened = make_page(store, None if first_is_cjk else epub_path)
    expected = "Иероглифический (象 -> A)" if first_is_cjk else "Алфавитный (A -> A)"
    assert reopened.ratio_presets_combo.currentText() == expected


def test_manual_controls_follow_check_and_mode(make_page):
    page = make_page()
    select_manual(page)
    assert page.ratio_min_spinbox.isEnabled()
    assert page.ratio_max_spinbox.isEnabled()
    page.check_length_ratio.click()
    assert not page.ratio_min_spinbox.isEnabled()
    assert not page.ratio_max_spinbox.isEnabled()
    page.check_length_ratio.click()
    page.ratio_presets_combo.setCurrentIndex(0)
    assert not page.ratio_min_spinbox.isEnabled()
    assert not page.ratio_max_spinbox.isEnabled()
    assert page.ratio_min_spinbox.accessibleName()
    assert page.ratio_max_spinbox.accessibleName()
    assert isinstance(page.ratio_min_spinbox, QtWidgets.QDoubleSpinBox)


def test_crossed_bounds_are_corrected_and_saved_consistently(make_page):
    store = SavedFilters()
    page = make_page(store)
    select_manual(page)
    page.ratio_min_spinbox.setValue(5.0)
    assert page._get_current_ratio_bounds() == (5.0, 5.01)
    page.ratio_max_spinbox.setValue(2.0)
    assert page._get_current_ratio_bounds() == (1.99, 2.0)
    reopened = make_page(store)
    assert reopened._get_current_ratio_bounds() == (1.99, 2.0)


@pytest.mark.parametrize("minimum,maximum", [
    ("broken", 2), (3, 2), (-1, 2), (float("nan"), 2), (1, float("inf")),
])
def test_invalid_saved_manual_bounds_use_safe_defaults(make_page, minimum, maximum):
    page = make_page(SavedFilters({
        "ratio_preset": "Ручной", "ratio_min": minimum, "ratio_max": maximum,
    }))
    assert page.ratio_presets_combo.currentText() == "Ручной"
    assert page._get_current_ratio_bounds() == (0.92, 1.20)
