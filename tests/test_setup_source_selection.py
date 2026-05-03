import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.setup import InitialSetupDialog


class _PathsWidget:
    def __init__(self):
        self.file_paths = []
        self.folder_paths = []
        self.chapter_counts = []

    def set_file_path(self, path):
        self.file_paths.append(path)

    def set_folder_path(self, path):
        self.folder_paths.append(path)

    def update_chapters_info(self, count):
        self.chapter_counts.append(count)


class _TaskManager:
    def __init__(self):
        self.clear_count = 0

    def clear_all_queues(self):
        self.clear_count += 1


class _SetupHarness:
    on_file_selected = InitialSetupDialog.on_file_selected

    def __init__(self, selected_file=None, output_folder=None, html_files=None):
        self.selected_file = selected_file
        self.output_folder = output_folder
        self.html_files = list(html_files or [])
        self.project_manager = object()
        self.task_manager = _TaskManager()
        self.paths_widget = _PathsWidget()
        self._pending_old_project_cleanup_offer = False
        self.process_calls = []
        self.initialization_calls = 0
        self.ready_checks = 0

    def _process_selected_file(self, pre_selected_chapters=None):
        self.process_calls.append(pre_selected_chapters)

    def _handle_project_initialization(self):
        self.initialization_calls += 1

    def check_ready(self):
        self.ready_checks += 1


class _SettingsManager:
    def __init__(self, history):
        self.history = list(history)
        self.added_projects = []

    def load_project_history(self):
        return list(self.history)

    def add_to_project_history(self, epub_path, output_folder):
        self.added_projects.append((epub_path, output_folder))


class _InitializationHarness:
    _handle_project_initialization = InitialSetupDialog._handle_project_initialization

    def __init__(self, selected_file, output_folder, history):
        self.selected_file = selected_file
        self.output_folder = output_folder
        self.html_files = ["Text/new.xhtml"]
        self.settings_manager = _SettingsManager(history)
        self.paths_widget = _PathsWidget()
        self.project_manager = None
        self._pending_old_project_cleanup_offer = True
        self.cleanup_calls = []
        self.filter_calls = 0
        self.data_changed_calls = 0

    def _maybe_offer_old_project_chapter_cleanup(self, folder_path, file_path):
        self.cleanup_calls.append((folder_path, file_path))
        return True

    def _ask_and_filter_chapters(self):
        self.filter_calls += 1

    def _on_project_data_changed(self):
        self.data_changed_calls += 1


def test_selecting_file_after_project_folder_opens_chapter_selection(tmp_path):
    project_folder = tmp_path / "project"
    project_folder.mkdir()
    new_file = tmp_path / "new.epub"

    harness = _SetupHarness(
        selected_file=None,
        output_folder=str(project_folder),
        html_files=["Text/old.xhtml"],
    )

    harness.on_file_selected(str(new_file))

    assert harness.selected_file == str(new_file)
    assert harness.html_files == []
    assert harness.paths_widget.chapter_counts == [0]
    assert len(harness.process_calls) == 1
    assert harness.initialization_calls == 0
    assert harness.ready_checks == 1


def test_switching_project_source_opens_chapter_selection_before_initialization(tmp_path):
    project_folder = tmp_path / "project"
    project_folder.mkdir()
    old_file = tmp_path / "old.epub"
    new_file = tmp_path / "new.epub"

    harness = _SetupHarness(
        selected_file=str(old_file),
        output_folder=str(project_folder),
        html_files=["Text/old.xhtml"],
    )

    harness.on_file_selected(str(new_file))

    assert harness.selected_file == str(new_file)
    assert harness.html_files == []
    assert harness.paths_widget.chapter_counts == [0]
    assert harness.task_manager.clear_count == 1
    assert harness.project_manager is None
    assert harness._pending_old_project_cleanup_offer is True
    assert len(harness.process_calls) == 1
    assert harness.initialization_calls == 0


def test_reselecting_same_file_with_existing_chapters_keeps_initialization_path(tmp_path):
    project_folder = tmp_path / "project"
    project_folder.mkdir()
    current_file = tmp_path / "book.epub"

    harness = _SetupHarness(
        selected_file=str(current_file),
        output_folder=str(project_folder),
        html_files=["Text/chapter.xhtml"],
    )

    harness.on_file_selected(str(current_file))

    assert harness.html_files == ["Text/chapter.xhtml"]
    assert harness.paths_widget.chapter_counts == []
    assert harness.process_calls == []
    assert harness.initialization_calls == 1
    assert harness.ready_checks == 1


def test_pending_cleanup_offer_runs_even_when_new_source_is_already_in_history(tmp_path):
    project_folder = tmp_path / "project"
    project_folder.mkdir()
    new_file = tmp_path / "new.epub"
    history = [{
        "epub_path": str(new_file).replace(os.sep, "/"),
        "output_folder": str(project_folder).replace(os.sep, "/"),
    }]

    harness = _InitializationHarness(
        selected_file=str(new_file),
        output_folder=str(project_folder),
        history=history,
    )

    harness._handle_project_initialization()

    assert [(os.path.normpath(folder), os.path.normpath(file_path))
            for folder, file_path in harness.cleanup_calls] == [
        (os.path.normpath(str(project_folder)), os.path.normpath(str(new_file)))
    ]
    assert harness.settings_manager.added_projects
    assert harness.filter_calls == 1
    assert harness.data_changed_calls == 1
