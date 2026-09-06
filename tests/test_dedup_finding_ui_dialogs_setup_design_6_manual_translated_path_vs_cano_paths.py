"""
Тесты для finding-ui-dialogs-setup_design_6-manual-translated-path-vs-cano.

_is_chapter_validated и filter_logic (внутри _filter_all_translated_tasks) в
gemini_translator/ui/dialogs/setup.py вычисляли путь к уже переведённому/
готовому файлу вручную (os.path.join(project_folder, dirname(chapter), stem+suffix)),
вместо канонического build_translated_output_path из
gemini_translator/utils/translated_paths.py. Ручной расчёт расходится с тем,
что реально пишут воркеры, в двух случаях:
  1) для глав без внутренней EPUB-папки (internal_dir == '') канонический
     хелпер кладёт файл в OEBPS/Text, а не в корень project_folder;
  2) для слишком длинных имён канонический хелпер укорачивает стем хэшем.

Тесты ниже:
  (а) характеризуют правильное поведение (обнаружение уже переведённой/
      готовой root-главы по каноническому пути) — падают на старом ручном
      коде и проходят после перехода на build_translated_output_path;
  (б) проверяют маршрутизацию: оба бывших места ручного расчёта должны
      вызывать build_translated_output_path (шпион поверх реальной функции).

Тестируемые методы дергаются на "голом" экземпляре InitialSetupPage
(создан через __new__, без QDialog.__init__/QApplication) — им нужен только
self.project_manager, поэтому создавать полноценный Qt-виджет не требуется.
"""
import os

from gemini_translator.ui.dialogs import setup as dialogs_setup
from gemini_translator.ui.dialogs.setup import InitialSetupPage
from gemini_translator.utils.translated_paths import build_translated_output_path


class _FakeProjectManager:
    def __init__(self, project_folder):
        self.project_folder = project_folder

    def get_versions_for_original(self, original_internal_path):
        return {}

    def register_multiple_translations(self, entries_to_add):
        pass


def _make_page(project_folder):
    page = InitialSetupPage.__new__(InitialSetupPage)
    page.project_manager = _FakeProjectManager(project_folder)
    return page


def _write(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("<html></html>")


# ---------------------------------------------------------------------------
# (а) Характеризационные тесты поведения
# ---------------------------------------------------------------------------

def test_is_chapter_validated_finds_root_chapter_at_canonical_path(tmp_path):
    page = _make_page(str(tmp_path))
    chapter_path = "chapter_01.xhtml"
    suffix = "_validated.html"

    canonical_path = build_translated_output_path(str(tmp_path), chapter_path, suffix)
    _write(canonical_path)

    untracked = []
    assert page._is_chapter_validated(chapter_path, suffix, untracked) is True
    assert untracked == [
        (chapter_path, suffix, os.path.relpath(canonical_path, str(tmp_path)))
    ]


def test_is_chapter_validated_does_not_find_root_chapter_at_project_root(tmp_path):
    """Файл, ошибочно положенный в корень проекта (старое поведение), не должен
    считаться каноническим местом хранения — валидность определяется только
    каноническим путём."""
    page = _make_page(str(tmp_path))
    chapter_path = "chapter_05.xhtml"
    suffix = "_validated.html"

    wrong_path = os.path.join(str(tmp_path), "chapter_05_validated.html")
    _write(wrong_path)

    assert page._is_chapter_validated(chapter_path, suffix, []) is False


def test_filter_all_translated_tasks_detects_root_chapter_via_canonical_path(tmp_path, monkeypatch):
    page = _make_page(str(tmp_path))
    chapter_path = "chapter_02.xhtml"
    suffix = "_gemini.html"

    canonical_path = build_translated_output_path(str(tmp_path), chapter_path, suffix)
    _write(canonical_path)

    monkeypatch.setattr(dialogs_setup.api_config, "all_translated_suffixes", lambda: [suffix])
    monkeypatch.setattr(dialogs_setup.QMessageBox, "information", staticmethod(lambda *a, **k: None))

    captured = {}

    def fake_flatten(filter_function):
        chapters_to_filter = [chapter_path]
        filtered, untracked = filter_function(chapters_to_filter)
        captured["filtered"] = filtered
        captured["untracked"] = untracked
        return filtered, len(chapters_to_filter)

    page._flatten_and_filter_tasks = fake_flatten

    page._filter_all_translated_tasks()

    assert captured["filtered"] == []
    assert captured["untracked"] == [
        (chapter_path, suffix, os.path.relpath(canonical_path, str(tmp_path)))
    ]


# ---------------------------------------------------------------------------
# (б) Тест-маршрутизация: каждое бывшее место ручного расчёта обязано
# вызывать build_translated_output_path.
# ---------------------------------------------------------------------------

def test_is_chapter_validated_routes_through_canonical_builder(tmp_path, monkeypatch):
    page = _make_page(str(tmp_path))
    chapter_path = "chapter_03.xhtml"
    suffix = "_validated.html"

    calls = []
    real_builder = dialogs_setup.build_translated_output_path

    def spy(output_folder, original_internal_path, file_suffix, **kwargs):
        calls.append((output_folder, original_internal_path, file_suffix))
        return real_builder(output_folder, original_internal_path, file_suffix, **kwargs)

    monkeypatch.setattr(dialogs_setup, "build_translated_output_path", spy)

    page._is_chapter_validated(chapter_path, suffix, [])

    assert calls == [(str(tmp_path), chapter_path, suffix)]


def test_filter_all_translated_tasks_routes_through_canonical_builder(tmp_path, monkeypatch):
    page = _make_page(str(tmp_path))
    chapter_path = "chapter_04.xhtml"
    suffix = "_gemini.html"

    canonical_path = build_translated_output_path(str(tmp_path), chapter_path, suffix)
    _write(canonical_path)

    monkeypatch.setattr(dialogs_setup.api_config, "all_translated_suffixes", lambda: [suffix])
    monkeypatch.setattr(dialogs_setup.QMessageBox, "information", staticmethod(lambda *a, **k: None))

    calls = []
    real_builder = dialogs_setup.build_translated_output_path

    def spy(output_folder, original_internal_path, file_suffix, **kwargs):
        calls.append((output_folder, original_internal_path, file_suffix))
        return real_builder(output_folder, original_internal_path, file_suffix, **kwargs)

    monkeypatch.setattr(dialogs_setup, "build_translated_output_path", spy)

    def fake_flatten(filter_function):
        chapters = [chapter_path]
        filtered, untracked = filter_function(chapters)
        return filtered, len(chapters)

    page._flatten_and_filter_tasks = fake_flatten

    page._filter_all_translated_tasks()

    assert calls == [(str(tmp_path), chapter_path, suffix)]
