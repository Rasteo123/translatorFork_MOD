# -*- coding: utf-8 -*-
"""
Страж для аудита libs-fuzzywuzzy.

fuzzy_compat.py больше не умеет откатываться на fuzzywuzzy — rapidfuzz
обязателен как рантайм-зависимость в ОБОИХ рантайм-манифестах
(requirements.txt и requirements-translator-only.txt), а fuzzywuzzy остаётся
только эталоном для корпусного теста tests/test_fuzzy_compat.py и должен
жить исключительно в requirements-dev.txt.

Этот тест красный до применения соответствующего упаковочного элемента
(packaging_actions в отчёте элемента libs-fuzzywuzzy) — сам fuzzy_compat.py
и tests/test_fuzzy_compat.py правки requirements-файлов не делают:
requirements*.txt/*.spec/build_master.py — вне их разрешённого scope.
"""

from pathlib import Path

from packaging.requirements import Requirement

PROJECT_ROOT = Path(__file__).resolve().parents[1]

RUNTIME_REQUIREMENTS_FILES = ("requirements.txt", "requirements-translator-only.txt")


def _requirement_names(filename):
    names = set()
    for line in (PROJECT_ROOT / filename).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(Requirement(line).name.lower())
    return names


def test_runtime_requirements_have_rapidfuzz_not_fuzzywuzzy():
    for filename in RUNTIME_REQUIREMENTS_FILES:
        names = _requirement_names(filename)
        assert "rapidfuzz" in names, (
            f"{filename}: rapidfuzz отсутствует — fuzzy_compat.py без него "
            "поднимет RuntimeError на первом вызове ratio/token_set_ratio "
            "(или, там где вызов прикрыт FUZZ_AVAILABLE-гейтом, как в "
            "language_tools.py/consistency_checker.py, — тихо выключит "
            "fuzzy-поиск)."
        )
        assert "fuzzywuzzy" not in names, (
            f"{filename}: fuzzywuzzy — фолбэка на него больше нет в "
            "fuzzy_compat.py, пакет должен остаться только в "
            "requirements-dev.txt (эталон для корпусного теста)."
        )


def test_dev_requirements_have_fuzzywuzzy():
    names = _requirement_names("requirements-dev.txt")
    assert "fuzzywuzzy" in names, (
        "requirements-dev.txt: fuzzywuzzy отсутствует — корпусные тесты "
        "tests/test_fuzzy_compat.py (сравнение с эталоном) будут молча "
        "скипаться в dev/CI-окружении."
    )
