"""Routing: importing the QA hot-path modules must not pull pandas into memory.

foreign_text_filter, glossary_context, language_validation, repair_validator
and structural_repair only need the pure glossary-matching helpers
(match_glossary_policies / contains_term_forms / glossary_violation_reason).
Those helpers now live in gemini_translator.qa.glossary_terms, which does not
touch pandas.

glossary_audit.py itself keeps `import pandas as pd` out of module scope too:
GlossaryAuditor's DataFrame-based conflict analysis needs pandas, but only
each method that actually builds/inspects a DataFrame imports it locally, so
merely importing glossary_audit.py (which the five hot consumers above still
do, to re-export the three helpers for backward compatibility) no longer
drags pandas in either.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def _pandas_free_after_importing(project_root: Path, modules: str) -> subprocess.CompletedProcess:
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(project_root)!r})\n"
        f"import {modules}\n"
        "assert 'pandas' not in sys.modules, "
        "'pandas must not be imported by qa hot-path consumers'\n"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )


def test_foreign_text_filter_and_structural_repair_do_not_import_pandas_at_startup():
    """The two hot consumers with no `.llm` dependency are fully pandas-free."""
    project_root = Path(__file__).parents[1]
    modules = (
        "gemini_translator.qa.foreign_text_filter, "
        "gemini_translator.qa.glossary_context, "
        "gemini_translator.qa.structural_repair"
    )
    completed = _pandas_free_after_importing(project_root, modules)
    assert completed.returncode == 0, completed.stderr


def test_qa_hot_consumers_do_not_import_pandas_at_startup():
    """The exact acceptance check named in the libs-pandas-startup audit ticket."""
    project_root = Path(__file__).parents[1]
    modules = (
        "gemini_translator.qa.foreign_text_filter, "
        "gemini_translator.qa.repair_validator, "
        "gemini_translator.qa.structural_repair"
    )
    completed = _pandas_free_after_importing(project_root, modules)
    assert completed.returncode == 0, completed.stderr


def test_main_does_not_import_pandas_at_startup():
    """The end-to-end check the reviewer ran by hand: `import main` stays pandas-free."""
    project_root = Path(__file__).parents[1]
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(project_root)!r})\n"
        "import os\n"
        "os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')\n"
        "import main\n"
        "assert 'pandas' not in sys.modules, "
        "'pandas must not be imported merely by starting the application'\n"
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
