"""Скрипт замены перевода на Rulate (tools/rulate_replace_chapters.js).

Скрипт работает в браузере на странице книги, его чистые функции проверяются
тестами Node: tests/js/rulate_replace_chapters.test.js.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE_TESTS = ROOT / "tests" / "js" / "rulate_replace_chapters.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js не установлен")
def test_rulate_replace_script_pure_functions():
    result = subprocess.run(
        ["node", "--test", str(NODE_TESTS)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )

    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
