"""
Регресс для элемента dups-gt_ui_dialogs_setup-00 (находки design/10, design/23,
design/26 из dups-gt_ui_dialogs_setup-00.json).

design/10: три копии сканирования CONTENT_FILTER по get_ui_state_list()/
get_full_map() (_open_filter_packaging_dialog, _try_auto_filter_recovery,
_try_auto_filter_redirect_followup) сведены к одному приватному хелперу
InitialSetupPage._collect_content_filter_state(need_successful_map=...).

design/23: _stop_translation содержал две дословно одинаковые ветки
(if self.engine and self.engine.session_id / elif self._check_and_sync_active_session())
— объединены в одно условие с одним телом.

design/26: _auto_original_chapter_has_cjk теперь сперва смотрит в уже
посчитанный TranslationOptionsWidget.chapter_compositions[...]['is_cjk'] и
только при отсутствии записи открывает EPUB и парсит его текстом
auto_workflow_helpers.text_has_cjk (как раньше).
"""
import os
import zipfile
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.setup import InitialSetupPage


# ---------------------------------------------------------------------------
# design/10 — сканирование CONTENT_FILTER
# ---------------------------------------------------------------------------

def _spy_on_collect_filter_state(monkeypatch):
    calls = []
    real = InitialSetupPage._collect_content_filter_state

    def spy(self, *args, **kwargs):
        calls.append((args, kwargs))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(InitialSetupPage, "_collect_content_filter_state", spy)
    return calls


def test_collect_content_filter_state_characterization_with_successful_map(tmp_path):
    """Каноническая логика: главы с status=='error' и 'CONTENT_FILTER' в
    details['errors'] считаются отфильтрованными; главы status=='success',
    у которых есть непустая версия (кроме suffix 'filtered') на диске —
    успешными."""
    ok_file = tmp_path / "ok.html"
    ok_file.write_text("done", encoding="utf-8")

    page = InitialSetupPage.__new__(InitialSetupPage)
    page.project_manager = SimpleNamespace(
        project_folder=str(tmp_path),
        get_full_map=lambda: {
            "ok.xhtml": {"filtered": "ignored.html", "": "ok.html"},
            "missing.xhtml": {"": "missing_on_disk.html"},
        },
    )
    tasks_state = [
        (("t1", ("epub", "book.epub", "bad.xhtml")), "error", {"errors": {"CONTENT_FILTER": 1}}),
        (("t2", ("epub", "book.epub", "ok.xhtml")), "success", {"errors": {}}),
        (("t3", ("epub", "book.epub", "missing.xhtml")), "success", {"errors": {}}),
        (("t4", ("epub", "book.epub", "net.xhtml")), "error", {"errors": {"NETWORK": 1}}),
    ]
    page.engine = SimpleNamespace(
        task_manager=SimpleNamespace(get_ui_state_list=lambda: tasks_state)
    )
    page._extract_chapters_from_payload = lambda payload: [payload[2]]

    filtered, successful = page._collect_content_filter_state(need_successful_map=True)

    assert filtered == {"bad.xhtml"}
    # 'ok.xhtml' has a version file on disk -> считается успешной;
    # 'missing.xhtml' указывает на несуществующий файл -> не считается.
    assert successful == {"ok.xhtml"}


def test_collect_content_filter_state_without_successful_map_skips_project_scan():
    """Когда successful_chapters не нужны (need_successful_map=False), метод
    не должен даже обращаться к project_manager.get_full_map()."""
    page = InitialSetupPage.__new__(InitialSetupPage)

    def _boom():
        raise AssertionError("get_full_map() не должен вызываться, когда need_successful_map=False")

    page.project_manager = SimpleNamespace(get_full_map=_boom)
    tasks_state = [
        (("t1", ("epub", "book.epub", "bad.xhtml")), "error", {"errors": {"CONTENT_FILTER": 1}}),
        (("t2", ("epub", "book.epub", "ok.xhtml")), "success", {"errors": {}}),
    ]
    page.engine = SimpleNamespace(
        task_manager=SimpleNamespace(get_ui_state_list=lambda: tasks_state)
    )
    page._extract_chapters_from_payload = lambda payload: [payload[2]]

    filtered, successful = page._collect_content_filter_state(need_successful_map=False)

    assert filtered == {"bad.xhtml"}
    assert successful == set()


def test_collect_content_filter_state_returns_empty_without_engine():
    page = InitialSetupPage.__new__(InitialSetupPage)
    page.engine = None
    page.project_manager = None

    filtered, successful = page._collect_content_filter_state(need_successful_map=True)

    assert filtered == set()
    assert successful == set()


def test_open_filter_packaging_dialog_routes_through_collect_content_filter_state(monkeypatch):
    import gemini_translator.ui.dialogs.setup as dialogs_setup

    page = InitialSetupPage.__new__(InitialSetupPage)
    page.engine = SimpleNamespace(
        task_manager=SimpleNamespace(get_ui_state_list=lambda: [])
    )
    page.project_manager = None

    infos = []
    monkeypatch.setattr(
        dialogs_setup,
        "QMessageBox",
        SimpleNamespace(
            information=lambda *_a, **_k: infos.append(_a),
            warning=lambda *_a, **_k: None,
        ),
    )

    calls = _spy_on_collect_filter_state(monkeypatch)

    page._open_filter_packaging_dialog()

    assert calls == [((), {"need_successful_map": True})]
    assert len(infos) == 1  # "нет отфильтрованных глав"


def test_try_auto_filter_recovery_routes_through_collect_content_filter_state(monkeypatch):
    page = InitialSetupPage.__new__(InitialSetupPage)
    page.engine = SimpleNamespace(
        task_manager=SimpleNamespace(get_ui_state_list=lambda: [])
    )
    page.project_manager = SimpleNamespace(get_full_map=lambda: {})

    calls = _spy_on_collect_filter_state(monkeypatch)

    result = page._try_auto_filter_recovery({}, deferred_retry_chapters=None)

    assert calls == [((), {"need_successful_map": True})]
    assert result is False  # нет отфильтрованных глав -> ранний return False


def test_try_auto_filter_redirect_followup_routes_through_collect_content_filter_state(monkeypatch):
    page = InitialSetupPage.__new__(InitialSetupPage)
    page.engine = SimpleNamespace(
        task_manager=SimpleNamespace(get_ui_state_list=lambda: [])
    )

    calls = _spy_on_collect_filter_state(monkeypatch)

    result = page._try_auto_filter_redirect_followup({}, deferred_retry_chapters=None)

    # Этому месту successful_chapters не нужны вовсе.
    assert calls == [((), {"need_successful_map": False})]
    assert result is False


# ---------------------------------------------------------------------------
# design/23 — _stop_translation: две одинаковые ветки объединены
# ---------------------------------------------------------------------------

def _make_stop_translation_page(*, engine_active: bool, sync_active: bool, hard_stop: bool):
    page = InitialSetupPage.__new__(InitialSetupPage)
    events = []
    page._post_event = lambda name, payload=None: events.append((name, payload))
    page._set_stop_button_mode = lambda flag: events.append(("_set_stop_button_mode", flag))
    page._hard_stop_enabled = hard_stop
    page.engine = SimpleNamespace(session_id="sess-1") if engine_active else None
    page._check_and_sync_active_session = lambda: sync_active
    return page, events


def test_stop_translation_hard_stop_identical_via_engine_or_sync_path():
    page_engine, events_engine = _make_stop_translation_page(
        engine_active=True, sync_active=False, hard_stop=True
    )
    page_engine._stop_translation()

    page_sync, events_sync = _make_stop_translation_page(
        engine_active=False, sync_active=True, hard_stop=True
    )
    page_sync._stop_translation()

    assert events_engine == events_sync
    assert ("manual_stop_requested", None) in events_engine


def test_stop_translation_soft_stop_identical_via_engine_or_sync_path():
    page_engine, events_engine = _make_stop_translation_page(
        engine_active=True, sync_active=False, hard_stop=False
    )
    page_engine._stop_translation()

    page_sync, events_sync = _make_stop_translation_page(
        engine_active=False, sync_active=True, hard_stop=False
    )
    page_sync._stop_translation()

    assert events_engine == events_sync
    assert ("soft_stop_requested", None) in events_engine
    assert ("_set_stop_button_mode", True) in events_engine


def test_stop_translation_noop_when_no_active_session():
    page, events = _make_stop_translation_page(
        engine_active=False, sync_active=False, hard_stop=True
    )
    page._stop_translation()
    assert events == []


# ---------------------------------------------------------------------------
# design/26 — _auto_original_chapter_has_cjk предпочитает chapter_compositions
# ---------------------------------------------------------------------------

def test_auto_original_chapter_has_cjk_prefers_cached_composition(monkeypatch):
    """Когда TranslationOptionsWidget уже посчитал is_cjk для этой главы,
    метод обязан вернуть закэшированное значение и НЕ трогать EPUB-файл
    вовсе (даже если selected_file не существует)."""
    page = InitialSetupPage.__new__(InitialSetupPage)
    page.selected_file = "/does/not/exist.epub"
    page._auto_cjk_original_cache = {}
    page.translation_options_widget = SimpleNamespace(
        chapter_compositions={"ch1.xhtml": {"is_cjk": True, "total_size": 100}}
    )

    def _boom(*_a, **_k):
        raise AssertionError("ZipFile не должен открываться, если composition уже есть")

    monkeypatch.setattr(zipfile, "ZipFile", _boom)

    assert page._auto_original_chapter_has_cjk("ch1.xhtml") is True


def test_auto_original_chapter_has_cjk_prefers_cached_composition_false():
    page = InitialSetupPage.__new__(InitialSetupPage)
    page.selected_file = "/does/not/exist.epub"
    page._auto_cjk_original_cache = {}
    page.translation_options_widget = SimpleNamespace(
        chapter_compositions={"ch1.xhtml": {"is_cjk": False}}
    )

    assert page._auto_original_chapter_has_cjk("ch1.xhtml") is False


def test_auto_original_chapter_has_cjk_falls_back_to_zip_when_not_cached(tmp_path):
    """Глава отсутствует в chapter_compositions (например, ещё не
    проанализирована) — старое поведение чтения EPUB и text_has_cjk
    сохраняется как фолбэк."""
    epub_path = tmp_path / "book.epub"
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("ch1.xhtml", "<html><body>中文</body></html>")

    page = InitialSetupPage.__new__(InitialSetupPage)
    page.selected_file = str(epub_path)
    page._auto_cjk_original_cache = {}
    page.translation_options_widget = SimpleNamespace(chapter_compositions={})

    assert page._auto_original_chapter_has_cjk("ch1.xhtml") is True
    # Результат кэшируется в _auto_cjk_original_cache для повторных вызовов.
    assert page._auto_cjk_original_cache[(os.path.abspath(str(epub_path)), "ch1.xhtml")] is True


def test_auto_original_chapter_has_cjk_falls_back_when_widget_missing():
    """translation_options_widget ещё не создан (ранняя стадия
    инициализации, атрибут есть, но None) — не должно ломать метод, просто
    идёт фолбэк на прежнее поведение (чтение EPUB, который тут не
    существует -> False)."""
    page = InitialSetupPage.__new__(InitialSetupPage)
    page.selected_file = "/does/not/exist.epub"
    page._auto_cjk_original_cache = {}
    page.translation_options_widget = None

    assert page._auto_original_chapter_has_cjk("ch1.xhtml") is False
