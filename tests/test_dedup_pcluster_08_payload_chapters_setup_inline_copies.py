"""
Регресс для pcluster-08 (Извлечение списка глав из payload задачи).

Предыдущая волна (finding-mcp-bench-cli_design_4-payload-chapters-quad-duplicat,
см. test_dedup_finding_mcp_bench_cli_design_4_payload_chapters_quad_duplicat.py)
уже свела 4 копии (cli.py, package_filter_tasks.py, auto_workflow_helpers.py,
task_manager.py) к одной канонической core.auto_workflow_helpers.extract_chapters_from_payload,
на которую gemini_translator/ui/dialogs/setup.py уже опирается через собственный
метод-делегат InitialSetupPage._extract_chapters_from_payload (см. строку ~5465).

Но внутри самого setup.py остались МИНИМУМ 4 инлайн-копии той же логики
"if task_type in ('epub','epub_chunk'): ...[2]; elif task_type=='epub_batch':
...extend([2])", которые НЕ вызывают этот уже существующий метод-делегат:
  - _flatten_and_filter_tasks (цикл "расплющивания" задач в главы)
  - _copy_original_chapters (дважды: сбор chapters_to_process и повторная
    проверка chapters_in_task перед task_done)
  - _open_filter_packaging_dialog (сбор chapters_in_task из get_ui_state_list)

Часть 1 (routing/RED-GREEN) проверяет, что каждое из этих мест реально зовёт
InitialSetupPage._extract_chapters_from_payload (шпион поверх реальной
реализации) — до рефакторинга падает, после рефакторинга проходит.

Часть 2 (характеризация divergence) фиксирует ДВА места, которые дедуплицировать
НЕЛЬЗЯ, так как они содержат осознанно другое поведение:
  - _process_filter_dialog_result уважает metadata['save_chapters'] (иначе
    'context'-главы filter-repack пакета ошибочно попадут в список "сохранённых");
  - _unpack_tasks_to_chapters схлопывает подряд идущие epub_chunk одной и той
    же главы, сохраняя дубликаты для epub_batch.
Эти тесты должны проходить и до, и после рефакторинга — они защищают
divergence от случайной "оптимизации" в общий хелпер.
"""
import os
import zipfile
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.ui.dialogs.setup import InitialSetupPage


def _spy_on_extract_chapters(monkeypatch):
    calls = []
    real = InitialSetupPage._extract_chapters_from_payload

    def spy(self, payload):
        calls.append(payload)
        return real(self, payload)

    monkeypatch.setattr(InitialSetupPage, "_extract_chapters_from_payload", spy)
    return calls


# ---------------------------------------------------------------------------
# 1. Тесты-маршрутизация
# ---------------------------------------------------------------------------

def test_flatten_and_filter_tasks_routes_through_canonical_extraction(monkeypatch):
    page = InitialSetupPage.__new__(InitialSetupPage)
    payload_a = ("epub", "book.epub", "ch1.xhtml")
    payload_b = ("epub_batch", "book.epub", ["ch2.xhtml", "ch3.xhtml"])
    tasks = [("t1", payload_a), ("t2", payload_b)]

    page.project_manager = SimpleNamespace(
        reload_data_from_disk=lambda: None,
        register_multiple_translations=lambda *_a, **_k: None,
    )
    page.engine = SimpleNamespace(
        task_manager=SimpleNamespace(get_all_tasks_for_rebuild=lambda: tasks)
    )
    page._on_project_data_changed = lambda **_kwargs: None

    calls = _spy_on_extract_chapters(monkeypatch)

    filtered, original_count = page._flatten_and_filter_tasks(lambda chapters: (chapters, []))

    assert calls == [payload_a, payload_b]
    assert filtered == ["ch1.xhtml", "ch2.xhtml", "ch3.xhtml"]
    assert original_count == 3


def test_open_filter_packaging_dialog_routes_through_canonical_extraction(monkeypatch):
    import gemini_translator.ui.dialogs.setup as dialogs_setup

    page = InitialSetupPage.__new__(InitialSetupPage)
    payload = ("epub_batch", "book.epub", ["bad.xhtml", "good.xhtml"])
    task_info = ("task-1", payload)
    all_tasks_state = [(task_info, "error", {"errors": {"CONTENT_FILTER": 1}})]

    page.engine = SimpleNamespace(
        task_manager=SimpleNamespace(get_ui_state_list=lambda: all_tasks_state)
    )
    page.project_manager = None  # skip successful_map branch entirely

    # Замечание рецензента (minor): вместо широкого except вокруг всего
    # вызова доводим метод до предсказуемой остановки — минимальный
    # translation_options_widget с ПУСТЫМ chapter_sizes_for_current_unit()
    # заставляет метод сработать на штатной ветке "Не удалось получить
    # данные о размерах глав" и вернуться, так и не дойдя до создания
    # FilterPackagingDialog. QMessageBox подменяем, чтобы не всплывало
    # реальное модальное окно.
    warnings = []
    monkeypatch.setattr(
        dialogs_setup,
        "QMessageBox",
        SimpleNamespace(
            information=lambda *_a, **_k: None,
            warning=lambda *_a, **_k: warnings.append(_a),
        ),
    )
    page.translation_options_widget = SimpleNamespace(
        task_size_spin=SimpleNamespace(value=lambda: 1000),
        task_size_unit=lambda: "chars",
        chapter_sizes_for_current_unit=lambda: {},
    )

    calls = _spy_on_extract_chapters(monkeypatch)

    page._open_filter_packaging_dialog()

    assert calls == [payload]
    assert len(warnings) == 1


def test_copy_original_chapters_routes_through_canonical_extraction_in_both_loops(monkeypatch, tmp_path):
    epub_path = tmp_path / "book.epub"
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("chapter1.xhtml", "<html><body>original</body></html>")

    output_folder = tmp_path / "out"

    payload = ("epub_batch", str(epub_path), ["chapter1.xhtml"])
    task_tuple = ("task-1", payload)

    class _FakeButton:
        def __init__(self, text, role):
            self.text = text
            self.role = role

        def setEnabled(self, *_a, **_k):
            pass

        def setToolTip(self, *_a, **_k):
            pass

    class _FakeMessageBox:
        class Icon:
            Question = "Question"
            Warning = "Warning"
            Critical = "Critical"
            Information = "Information"

        class ButtonRole:
            ActionRole = "ActionRole"
            AcceptRole = "AcceptRole"
            RejectRole = "RejectRole"

        def __init__(self, *_a, **_k):
            self._buttons = []
            self._clicked = None

        def setWindowTitle(self, *_a, **_k):
            pass

        def setIcon(self, *_a, **_k):
            pass

        def setText(self, *_a, **_k):
            pass

        def setInformativeText(self, *_a, **_k):
            pass

        def addButton(self, text, role):
            button = _FakeButton(text, role)
            self._buttons.append(button)
            return button

        def exec(self):
            # Симулируем клик по первой добавленной кнопке
            # ("Скопировать как есть" -> process_with_glossary=False).
            self._clicked = self._buttons[0]
            return 0

        def clickedButton(self):
            return self._clicked

    import gemini_translator.ui.dialogs.setup as dialogs_setup

    monkeypatch.setattr(dialogs_setup, "QMessageBox", _FakeMessageBox)
    monkeypatch.setattr(
        dialogs_setup.api_config,
        "api_providers",
        lambda: {"prov1": {"file_suffix": "_ru.html"}},
    )

    class _FakeTableItem:
        def row(self):
            return 0

    class _FakeTable:
        def selectedItems(self):
            return [_FakeTableItem()]

        def item(self, row, col):
            return SimpleNamespace(data=lambda role: task_tuple)

    page = InitialSetupPage.__new__(InitialSetupPage)
    page.task_management_widget = SimpleNamespace(
        chapter_list_widget=SimpleNamespace(table=_FakeTable())
    )
    page.selected_file = str(epub_path)
    page.output_folder = str(output_folder)
    page.project_manager = SimpleNamespace(register_translation=lambda *_a, **_k: None)
    page.glossary_widget = SimpleNamespace(get_glossary=lambda: [])
    page.key_management_widget = SimpleNamespace(get_selected_provider=lambda: "prov1")
    page.task_manager = SimpleNamespace(task_done=lambda *_a, **_k: None)
    page._show_custom_message = lambda *_a, **_k: None

    calls = _spy_on_extract_chapters(monkeypatch)

    page._copy_original_chapters()

    # Один раз при сборе chapters_to_process, второй раз при проверке
    # chapters_in_task перед task_done — оба раза с одним и тем же payload.
    assert calls == [payload, payload]


# ---------------------------------------------------------------------------
# 2. Характеризация divergence — эти места трогать нельзя.
# ---------------------------------------------------------------------------

def test_process_filter_dialog_result_prefers_save_chapters_metadata_over_raw_payload():
    """_process_filter_dialog_result — единственное место, которое обязано
    уважать metadata['save_chapters'] вместо сырого payload[2]. Замена на
    каноническую extract_chapters_from_payload сломала бы filter-repack:
    'context.xhtml' попала бы в html_files наравне с реально сохраняемой
    главой."""
    page = InitialSetupPage.__new__(InitialSetupPage)
    page._post_event = lambda *_a, **_k: None
    page.task_management_widget = SimpleNamespace(
        set_retry_filtered_button_visible=lambda *_a, **_k: None
    )
    page.translation_options_widget = SimpleNamespace(_update_info_text=lambda: None)
    page.paths_widget = SimpleNamespace(update_chapters_info=lambda *_a, **_k: None)

    captured = {}
    page.task_manager = SimpleNamespace(
        set_pending_tasks=lambda payloads, initial_history=None: captured.update(
            payloads=payloads, initial_history=initial_history
        )
    )

    payload = (
        "epub_batch",
        "book.epub",
        ["real.xhtml", "context.xhtml"],
        {"save_chapters": ["real.xhtml"]},
    )
    result = {"type": "payloads", "data": [payload]}

    page._process_filter_dialog_result(result)

    assert page.html_files == ["real.xhtml"]
    assert captured["payloads"] == [payload]


def test_process_filter_dialog_result_delegates_to_canonical_extraction_without_save_chapters(monkeypatch):
    """Минор-фикс рецензента (setup.py:3962): когда metadata['save_chapters']
    отсутствует, _process_filter_dialog_result обязан делегировать сбор глав
    той же канонической extract_chapters_from_payload (через self._extract_
    chapters_from_payload), а не держать собственную копию веток
    epub_batch/epub/epub_chunk. save_chapters по-прежнему в приоритете —
    см. test_process_filter_dialog_result_prefers_save_chapters_metadata_over_raw_payload."""
    page = InitialSetupPage.__new__(InitialSetupPage)
    page._post_event = lambda *_a, **_k: None
    page.task_management_widget = SimpleNamespace(
        set_retry_filtered_button_visible=lambda *_a, **_k: None
    )
    page.translation_options_widget = SimpleNamespace(_update_info_text=lambda: None)
    page.paths_widget = SimpleNamespace(update_chapters_info=lambda *_a, **_k: None)

    captured = {}
    page.task_manager = SimpleNamespace(
        set_pending_tasks=lambda payloads, initial_history=None: captured.update(
            payloads=payloads, initial_history=initial_history
        )
    )

    # Числа в именах нужны, чтобы extract_number_from_path давал
    # детерминированный порядок сортировки html_files (без чисел все ключи
    # сортировки равны float('inf'), и порядок зависел бы от произвольного
    # порядка обхода set(), который рандомизирован PYTHONHASHSEED).
    payload_batch = ("epub_batch", "book.epub", ["1.xhtml", "2.xhtml"])
    payload_single = ("epub", "book.epub", "3.xhtml")
    result = {"type": "payloads", "data": [payload_batch, payload_single]}

    calls = _spy_on_extract_chapters(monkeypatch)

    page._process_filter_dialog_result(result)

    assert calls == [payload_batch, payload_single]
    assert page.html_files == ["1.xhtml", "2.xhtml", "3.xhtml"]


def test_update_summary_routes_through_canonical_extraction_for_lonely_count(monkeypatch):
    """Замечание рецензента (major, package_filter_tasks.py:201-211):
    FilterPackagingDialog._update_summary держал ещё одну инлайн-копию
    разбора payload ('epub_batch' -> payload[2], 'epub' -> [payload[2]]),
    минуя уже импортированную в этом модуле каноническую
    extract_chapters_from_payload. Проверяем, что список глав задачи для
    подсчёта total/avg/lonely берётся из возвращаемого каноном значения."""
    import gemini_translator.scripts.package_filter_tasks as package_filter_tasks_module
    from gemini_translator.scripts.package_filter_tasks import FilterPackagingDialog

    calls = []
    real = package_filter_tasks_module.extract_chapters_from_payload

    def spy(payload):
        calls.append(payload)
        return real(payload)

    monkeypatch.setattr(package_filter_tasks_module, "extract_chapters_from_payload", spy)

    dialog = FilterPackagingDialog.__new__(FilterPackagingDialog)
    dialog.filtered_chapters = ["bad.xhtml"]

    payload_batch = ("epub_batch", "book.epub", ["bad.xhtml", "good1.xhtml", "good2.xhtml"])
    payload_lonely = ("epub", "book.epub", "bad.xhtml")
    final_payloads = [payload_batch, payload_lonely]

    dialog._calculate_new_chapter_list = lambda: {"type": "payloads", "data": final_payloads}

    labels = {}

    class _FakeLabel:
        def __init__(self, key):
            self._key = key

        def setText(self, text):
            labels[self._key] = text

    dialog.total_tasks_label = _FakeLabel("total")
    dialog.avg_chapters_label = _FakeLabel("avg")
    dialog.lonely_chapters_label = _FakeLabel("lonely")

    dialog._update_summary()

    assert calls == [payload_batch, payload_lonely]
    assert labels["total"] == "<b>2</b>"
    # 3 главы в единственном батче -> средний размер батча 3.0.
    assert labels["avg"] == "~3.0"
    # 'bad.xhtml' одинока в payload_lonely (epub-задача с ровно одной главой).
    assert labels["lonely"] == "<b style='color: red;'>1</b>"


def test_unpack_tasks_to_chapters_collapses_consecutive_chunks_but_keeps_batch_duplicates():
    """_unpack_tasks_to_chapters — единственное место, которое схлопывает
    подряд идущие epub_chunk одной главы (это не 'извлечение списка глав из
    payload', а отдельная политика 'разворачивания' очереди) и намеренно
    сохраняет дубликаты внутри epub_batch. Это поведение не покрывается
    канонической extract_chapters_from_payload и не должно быть заменено ей."""
    page = InitialSetupPage.__new__(InitialSetupPage)
    tasks = [
        ("t1", ("epub_chunk", "book.epub", "ch1.xhtml", 0)),
        ("t2", ("epub_chunk", "book.epub", "ch1.xhtml", 1)),
        ("t3", ("epub", "book.epub", "ch2.xhtml")),
        ("t4", ("epub_batch", "book.epub", ["ch3.xhtml", "ch3.xhtml"])),
    ]
    page.engine = SimpleNamespace(
        task_manager=SimpleNamespace(get_all_pending_tasks=lambda: tasks)
    )

    result = page._unpack_tasks_to_chapters()

    assert result == ["ch1.xhtml", "ch2.xhtml", "ch3.xhtml", "ch3.xhtml"]
