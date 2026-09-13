"""Служебные файлы книги, которые пишут окна: сбой записи не стирает прежний файл.

Каждое из этих мест открывало файл через open(path, "w"): файл усекается ещё
до записи, и сбой посередине (диск переполнен, антивирус, облачная
синхронизация, падение процесса) оставлял обрезанный JSON. Для глоссария это
потеря ручной работы.

Отказ os.replace имитирует сбой на последнем шаге атомарной записи. Прямая
запись этот шаг не проходит вовсе и успевает перезаписать файл, поэтому такой
тест ловит её на любой платформе.
"""

import os
import threading
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402

# validation импортируется первым: иначе циклический импорт
# untranslated_fixer_dialog <-> glossary_dialogs.ai_generation.
from gemini_translator.ui.dialogs import validation  # noqa: E402,F401
from gemini_translator.ui.dialogs import chapter_editor  # noqa: E402
from gemini_translator.ui.dialogs import glossary as glossary_dialog  # noqa: E402
from gemini_translator.ui.dialogs import setup as setup_dialog  # noqa: E402
from gemini_translator.ui.dialogs.glossary_dialogs import ai_generation  # noqa: E402
from gemini_translator.ui.dialogs.validation_dialogs import (  # noqa: E402
    untranslated_fixer_dialog as ufd,
)
from gemini_translator.ui.widgets import glossary_widget  # noqa: E402

_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

_PREVIOUS = '{"previous": "сохранённое раньше"}'
_GLOSSARY = [{"original": "Beta", "rus": "бета"}]


def _fail_replace(*_args):
    raise OSError("disk full")


class _SilentMessageBox:
    @staticmethod
    def information(*_args, **_kwargs):
        return None

    @staticmethod
    def warning(*_args, **_kwargs):
        return None

    @staticmethod
    def critical(*_args, **_kwargs):
        return None


class _Label:
    def __init__(self, text=""):
        self._text = text

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text


def _scroll_bar_table():
    return SimpleNamespace(
        setCurrentItem=lambda _item: None,
        verticalScrollBar=lambda: SimpleNamespace(value=lambda: 120),
    )


class _SetupHarness:
    _save_project_glossary_only = setup_dialog.InitialSetupDialog._save_project_glossary_only
    _save_base_glossary_prompt_state = (
        setup_dialog.InitialSetupDialog._save_base_glossary_prompt_state
    )
    _base_glossary_state_path = setup_dialog.InitialSetupDialog._base_glossary_state_path

    def __init__(self, folder):
        self.output_folder = folder
        self.glossary_widget = SimpleNamespace(get_glossary=lambda: list(_GLOSSARY))

    def mark_project_glossary_as_saved(self, _glossary):
        pass


class _GlossaryPageHarness:
    _save_project_glossary = glossary_dialog.GlossaryManagerPage._save_project_glossary
    _save_project_view_state = glossary_dialog.GlossaryManagerPage._save_project_view_state
    _glossary_state_path = glossary_dialog.GlossaryManagerPage._glossary_state_path

    def __init__(self, folder):
        self.launch_mode = "standalone"
        self.associated_project_path = folder
        self.table = _scroll_bar_table()
        self.status_label = _Label()

    def get_glossary(self):
        return list(_GLOSSARY)

    def mark_current_state_as_saved(self, saved_to_project=False):
        pass

    def _sync_saved_project_state_to_parent(self, _glossary):
        pass


class _GlossaryWidgetHarness:
    _save_project_view_state = glossary_widget.GlossaryWidget._save_project_view_state
    _project_glossary_state_path = glossary_widget.GlossaryWidget._project_glossary_state_path

    def __init__(self, folder):
        self.project_path = folder
        self.current_page = 2
        self.table = _scroll_bar_table()


class _FilterDialogHarness:
    _save_to_project = ufd.AdvancedTagFilterDialog._save_to_project

    def __init__(self, folder):
        self.project_folder = folder
        self.whitelist = {"Ли"}
        self.blacklist = {"Ван"}


class _ChapterEditorHarness:
    _save_draft = chapter_editor.ChapterEditorDialog._save_draft

    def __init__(self, folder):
        self.draft_path = os.path.join(folder, ".chapter_editor", "drafts", "chapter.json")
        self.translated_path = os.path.join(folder, "chapter.html")
        self._saved_text = "было"
        self.translated_document = SimpleNamespace(
            isModified=lambda: True, toPlainText=lambda: "стало"
        )
        self.meta_label = _Label("Глава 1")
        self.warning_label = _Label()


class _RecoveryHarness:
    _perform_safe_recovery_save = ai_generation.GenerationSessionPage._perform_safe_recovery_save
    _get_recovery_candidates = ai_generation.GenerationSessionPage._get_recovery_candidates

    def __init__(self, folder):
        self.project_manager = SimpleNamespace(project_folder=folder)
        self._recovery_lock = threading.RLock()

    def _create_recovery_snapshot(self):
        return {"glossary": list(_GLOSSARY)}


def _save_glossary_from_untranslated_fixer(folder):
    controller = ufd.ProjectGlossaryController.__new__(ufd.ProjectGlossaryController)
    controller.owner = None
    controller.project_folder = folder
    controller.glossary_widget = None
    controller.glossary_owner = None
    controller.save(list(_GLOSSARY))


@pytest.fixture
def failing_commit(monkeypatch):
    for module in (setup_dialog, glossary_dialog, ufd):
        monkeypatch.setattr(module, "QMessageBox", _SilentMessageBox)
    monkeypatch.setattr(os, "replace", _fail_replace)


@pytest.mark.parametrize(
    "relative_path, save",
    [
        pytest.param(
            "project_glossary.json",
            _save_glossary_from_untranslated_fixer,
            id="glossary-from-untranslated-fixer",
        ),
        pytest.param(
            "project_glossary.json",
            lambda folder: _SetupHarness(folder)._save_project_glossary_only(),
            id="glossary-from-setup",
        ),
        pytest.param(
            "project_glossary.json",
            lambda folder: _GlossaryPageHarness(folder)._save_project_glossary(notify=False),
            id="glossary-from-glossary-window",
        ),
        pytest.param(
            "project_glossary_state.json",
            lambda folder: _GlossaryPageHarness(folder)._save_project_view_state(),
            id="view-state-from-glossary-window",
        ),
        pytest.param(
            "project_glossary_state.json",
            lambda folder: _GlossaryWidgetHarness(folder)._save_project_view_state(),
            id="view-state-from-glossary-widget",
        ),
        pytest.param(
            "base_glossary_prompt_state.json",
            lambda folder: _SetupHarness(folder)._save_base_glossary_prompt_state(
                selected_ids=["base"]
            ),
            id="base-glossary-choice",
        ),
        pytest.param(
            "untranslated_filters.json",
            lambda folder: _FilterDialogHarness(folder)._save_to_project(),
            id="untranslated-filters",
        ),
        pytest.param(
            os.path.join(".chapter_editor", "drafts", "chapter.json"),
            lambda folder: _ChapterEditorHarness(folder)._save_draft(),
            id="chapter-editor-draft",
        ),
    ],
)
def test_failed_save_keeps_previous_book_file(tmp_path, failing_commit, relative_path, save):
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_PREVIOUS, encoding="utf-8")

    try:
        save(str(tmp_path))
    except OSError:
        pass

    assert target.read_text(encoding="utf-8") == _PREVIOUS
    assert list(tmp_path.rglob("*.tmp")) == []


def test_failed_recovery_save_keeps_the_previous_recovery_file(tmp_path, failing_commit):
    previous = tmp_path / "~glossary_session_recovery_1.json"
    previous.write_text(_PREVIOUS, encoding="utf-8")

    _RecoveryHarness(str(tmp_path))._perform_safe_recovery_save()

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "~glossary_session_recovery_1.json"
    ]
    assert previous.read_text(encoding="utf-8") == _PREVIOUS
