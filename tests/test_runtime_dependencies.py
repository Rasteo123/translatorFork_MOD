from pathlib import Path

import build_master


ROOT = Path(__file__).resolve().parents[1]


def test_python_and_numeric_dependency_contract_is_consistent():
    assert build_master.FORCED_VERSIONS["numpy"] == ">=2.0,<3"
    assert build_master.FORCED_VERSIONS["pandas"] == ">=3.0,<4"
    assert {"numpy", "pandas"} <= set(build_master.ESSENTIAL_PACKAGES)

    for path in ("requirements.txt", "requirements-translator-only.txt"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "numpy>=2.0,<3" in text
        assert "pandas>=3.0,<4" in text

    assert 'target-version = "py311"' in (ROOT / "pyproject.toml").read_text()
    assert 'python-version: "3.11"' in (ROOT / ".github/workflows/tests.yml").read_text()
    assert 'python-version: "3.11"' in (ROOT / ".github/workflows/release.yml").read_text()
