# -*- coding: utf-8 -*-
"""
Страж для упаковочного элемента libs-packaging.

Фиксирует итоговое состояние requirements*.txt / build_master.py / *.spec
после сведения независимых упаковочных решений и packaging_actions
отдельных code-элементов волны libs-*. Красный до применения правок,
зелёный после.
"""

from pathlib import Path

from packaging.requirements import Requirement

import build_master


PROJECT_ROOT = Path(__file__).resolve().parents[1]

FULL_SPECS = (
    "test.spec",
    "translatorFork 1.1.spec",
    "translatorFork-full.spec",
    "translatorFork-verify-project-runtime.spec",
    "translatorFork-verify.spec",
    "translatorFork_MOD.spec",
)


def _requirement_names(filename):
    names = set()
    for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(Requirement(line).name.lower())
    return names


def _requirement_lines(filename):
    return [
        line.strip()
        for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# --- (a) translator-only: rapidfuzz/selectolax/orjson/zstandard добавлены ---


def test_translator_only_gains_stage3_and_snapshot_backends():
    names = _requirement_names("requirements-translator-only.txt")
    for pkg in ("rapidfuzz", "selectolax", "orjson", "zstandard"):
        assert pkg in names, f"requirements-translator-only.txt: '{pkg}' отсутствует"
    # libs-fs-memfs завершился done -> 'fs' в translator-only не нужен
    assert "fs" not in names


# --- (b) google-genai только в полной сборке ---


def test_google_genai_dropped_from_translator_only_but_kept_in_full():
    assert "google-genai" not in _requirement_names("requirements-translator-only.txt")
    assert "google-genai" in _requirement_names("requirements.txt")

    translator_only_spec = (
        PROJECT_ROOT / "translatorFork-translator-only.spec"
    ).read_text(encoding="utf-8")
    assert "'google.genai'" not in translator_only_spec
    assert "'google.genai.types'" not in translator_only_spec

    for spec_name in FULL_SPECS:
        spec_text = (PROJECT_ROOT / spec_name).read_text(encoding="utf-8")
        assert "google.genai" in spec_text, f"{spec_name}: google.genai должен остаться"

    # cryptography — не трогать (свой security floor)
    assert "cryptography" in _requirement_names("requirements-translator-only.txt")
    assert "cryptography" in _requirement_names("requirements.txt")


# --- (c) emoji: пакет остаётся, collect_data_files('emoji') убран из всех спеков ---


def test_emoji_package_kept_but_manual_collect_data_files_removed():
    assert "emoji" in _requirement_names("requirements.txt")
    for spec_name in FULL_SPECS + ("translatorFork-translator-only.spec",):
        spec_text = (PROJECT_ROOT / spec_name).read_text(encoding="utf-8")
        assert "collect_data_files('emoji')" not in spec_text
        assert 'collect_data_files("emoji")' not in spec_text


# --- (d) werkzeug: явные строки убраны из requirements, collect_data_files не трогать ---


def test_werkzeug_only_transitive_via_flask():
    assert "werkzeug" not in _requirement_names("requirements.txt")
    assert "werkzeug" not in _requirement_names("requirements-translator-only.txt")
    assert "flask" in _requirement_names("requirements.txt")
    assert "flask" in _requirement_names("requirements-translator-only.txt")

    for spec_name in FULL_SPECS + ("translatorFork-translator-only.spec",):
        spec_text = (PROJECT_ROOT / spec_name).read_text(encoding="utf-8")
        assert "collect_data_files('werkzeug')" in spec_text or (
            "collect_data_files(\"werkzeug\")" in spec_text
        )


# --- (e) pytest: только dev; build_master.py исключает tests/tools из сканирования ---


def test_pytest_is_dev_only_and_build_master_excludes_test_dirs():
    assert "pytest" not in _requirement_names("requirements.txt")
    assert "pytest" in _requirement_names("requirements-dev.txt")

    assert {"tests", "tools", ".worktrees", ".claude"} <= build_master.EXCLUDE_DIRS
    assert {"PyInstaller", "pytest", "pytest-qt", "ruff"} <= build_master.DEV_MODULES

    ml_optional = getattr(build_master, "OPTIONAL_ML_PACKAGES", None)
    assert ml_optional is not None, (
        "build_master.py должен объявлять явный список опциональных ML-зависимостей "
        "(OPTIONAL_ML_PACKAGES), которые не должны попадать в runtime-requirements"
    )
    for pkg in ("onnxruntime", "tokenizers", "navec", "slovnet", "comet"):
        assert pkg in ml_optional
        assert pkg not in build_master.ESSENTIAL_PACKAGES
        assert pkg not in _requirement_names("requirements.txt")
        assert pkg not in _requirement_names("requirements-translator-only.txt")


# --- (f) websockets: без изменений ---


def test_websockets_untouched():
    assert "websockets" in _requirement_names("requirements.txt")
    assert "websockets" in build_master.ESSENTIAL_PACKAGES


# --- packaging_actions применённых code-элементов (status == done) ---


def test_levenshtein_and_fuzzywuzzy_moved_to_dev():
    runtime_names_full = _requirement_names("requirements.txt")
    runtime_names_translator = _requirement_names("requirements-translator-only.txt")
    dev_names = _requirement_names("requirements-dev.txt")

    for names in (runtime_names_full, runtime_names_translator):
        assert "python-levenshtein" not in names
        assert "fuzzywuzzy" not in names
        assert "rapidfuzz" in names

    assert "python-levenshtein" in dev_names
    assert "fuzzywuzzy" in dev_names

    assert "Levenshtein" not in build_master.IMPORT_TO_PACKAGE_MAP


def test_pytz_replaced_by_tzdata_in_both_runtime_manifests():
    for filename in ("requirements.txt", "requirements-translator-only.txt"):
        names = _requirement_names(filename)
        assert "pytz" not in names, f"{filename}: pytz должен быть удалён"
        assert "tzdata" in names, f"{filename}: tzdata должен быть добавлен"

    assert "tzdata" in build_master.ESSENTIAL_PACKAGES


def test_loguru_removed_from_runtime_and_essential_packages():
    assert "loguru" not in _requirement_names("requirements.txt")
    assert "loguru" not in build_master.ESSENTIAL_PACKAGES


def test_nltk_removed_runtime_dependency_razdel_kept():
    assert "nltk" not in _requirement_names("requirements.txt")
    assert "nltk" not in build_master.ESSENTIAL_PACKAGES
    assert "razdel" in _requirement_names("requirements.txt")
    assert "razdel" in build_master.ESSENTIAL_PACKAGES


def test_pymorphy2_mapping_removed():
    assert "pymorphy2" not in build_master.IMPORT_TO_PACKAGE_MAP
