"""
Регресс для finding-mcp-bench-cli_design_4-payload-chapters-quad-duplicat.

Логика извлечения списка глав из payload-кортежа задачи была продублирована
4 раза: cli.py:_payload_chapters, package_filter_tasks.py:_payload_chapters,
core/auto_workflow_helpers.py:extract_chapters_from_payload и
core/task_manager.py:_extract_chapters_from_payload.

Каноническая реализация — core.auto_workflow_helpers.extract_chapters_from_payload
(module-level, без self). По умолчанию она воспроизводит поведение ТРЁХ
дословных копий-дубликатов (task_manager.py, package_filter_tasks.py и
исходная версия самого auto_workflow_helpers.py до этой волны): без ветки
`glossary_batch_task` и без str()-коэрсии элементов. Расширенное поведение
cli.py (ветка glossary_batch_task) доступно только через явный
keyword-only флаг `include_glossary_batch=True`, который передаёт сам
cli.py в двух местах, где эта ветка была исторически.

Ревью (needs_work) указало, что предыдущая правка расширила контракт
канонической функции глобально:
  - major: ветка glossary_batch_task стала поведением по умолчанию и утекла
    в шесть мест gemini_translator/ui/dialogs/setup.py (файл вне зоны
    ответственности), не покрытых анализом или тестами;
  - major: task_manager.py:_restore_snapshot_payload (снапшот очереди)
    стал получать главы glossary-задач без проверки потребителя
    (restored_chapters уходит как список глав КНИГИ в setup.py);
  - minor: str()-коэрсия перенесена в общую функцию и незаметно меняет
    поведение normalize_auto_chapters (Path/др. типы перестают отбрасываться);
  - minor: package_filter_tasks.py:_payload_chapters — однострочный делегат
    без единственного вызывающего кроме себя самого (вызывается один раз),
    то есть alias «на всякий случай».

Часть 1 — характеризационные тесты канонической реализации (умолчание vs флаг).
Часть 2 — тесты-маршрутизация: подменяют каноническую функцию и проверяют,
что каждое бывшее место дублирования идёт именно через неё (и что cli.py
передаёт include_glossary_batch=True, а task_manager.py — нет).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections import Counter
from pathlib import Path

from gemini_translator.core.auto_workflow_helpers import extract_chapters_from_payload


# ---------------------------------------------------------------------------
# 1. Характеризационные тесты канонической реализации
# ---------------------------------------------------------------------------

def test_canonical_default_ignores_glossary_batch_task():
    """По умолчанию функция воспроизводит поведение трёх копий-дубликатов
    (task_manager.py / package_filter_tasks.py / доверфакторная версия
    auto_workflow_helpers.py), которые не знали о glossary_batch_task.
    Это устраняет major-замечание рецензента: без явного opt-in поведение
    для всех остальных вызывающих (включая setup.py и task_manager.py)
    не меняется."""
    payload = ("glossary_batch_task", "book.epub", ("a.xhtml", "b.xhtml"))
    assert extract_chapters_from_payload(payload) == []


def test_canonical_include_glossary_batch_flag_opts_in():
    """cli.py исторически поддерживал glossary_batch_task — поведение
    сохраняется, но только по явному флагу."""
    payload = ("glossary_batch_task", "book.epub", ("a.xhtml", "b.xhtml"))
    assert extract_chapters_from_payload(payload, include_glossary_batch=True) == ["a.xhtml", "b.xhtml"]


def test_canonical_handles_epub_and_epub_chunk():
    assert extract_chapters_from_payload(("epub", "book.epub", "chapter.xhtml")) == ["chapter.xhtml"]
    assert extract_chapters_from_payload(("epub_chunk", "book.epub", "chapter.xhtml", 1)) == ["chapter.xhtml"]


def test_canonical_handles_epub_batch():
    payload = ("epub_batch", "book.epub", ["a.xhtml", "b.xhtml"])
    assert extract_chapters_from_payload(payload) == ["a.xhtml", "b.xhtml"]


def test_canonical_epub_batch_unaffected_by_glossary_flag():
    """Флаг влияет только на распознавание task_type == glossary_batch_task,
    обычный epub_batch ведёт себя одинаково вне зависимости от флага."""
    payload = ("epub_batch", "book.epub", ["a.xhtml", "b.xhtml"])
    assert extract_chapters_from_payload(payload, include_glossary_batch=True) == ["a.xhtml", "b.xhtml"]


def test_canonical_returns_empty_for_falsy_or_unknown_payload():
    assert extract_chapters_from_payload(None) == []
    assert extract_chapters_from_payload(()) == []
    assert extract_chapters_from_payload(("unknown_type", "x")) == []


def test_canonical_defends_against_short_payload():
    """cli.py-версия не проверяла len(payload) > 2 и падала бы с IndexError —
    каноническая реализация обязана унаследовать защиту от трёх других копий."""
    assert extract_chapters_from_payload(("epub",)) == []
    assert extract_chapters_from_payload(("epub_batch", "book.epub")) == []
    assert extract_chapters_from_payload(("glossary_batch_task", "book.epub"), include_glossary_batch=True) == []


def test_canonical_does_not_coerce_chapter_values_to_str():
    """Минор-замечание рецензента: str()-коэрсия убрана из общей функции —
    она незаметно меняла поведение normalize_auto_chapters (Path и т.п.
    больше не отбрасывались бы). Единственный вызывающий, которому
    действительно требовалась строка (task_manager.py:2256), сам
    оборачивает результат в str(chapter)."""
    chapter_path = Path("chapter1.xhtml")
    assert extract_chapters_from_payload(("epub", "book.epub", chapter_path)) == [chapter_path]

    batch_payload = ("epub_batch", "book.epub", [Path("a.xhtml"), Path("b.xhtml")])
    assert extract_chapters_from_payload(batch_payload) == [Path("a.xhtml"), Path("b.xhtml")]


# ---------------------------------------------------------------------------
# 2. Тесты-маршрутизация: до рефакторинга FAIL (своя копия/своё поведение),
#    после — PASS.
# ---------------------------------------------------------------------------

def test_cli_summarize_payloads_routes_through_canonical_with_glossary_flag(monkeypatch):
    """cli.py обязан передавать include_glossary_batch=True — это его
    исторически расширенное поведение, которое не должно было утечь
    в остальные вызывающие."""
    from gemini_translator import cli

    sentinel = ["ROUTED-CLI"]
    monkeypatch.setattr(
        cli,
        "extract_chapters_from_payload",
        lambda payload, **kwargs: sentinel if kwargs.get("include_glossary_batch") is True else ["WRONG-FLAG"],
    )

    payload = ("epub_batch", "book.epub", ["a.xhtml"])
    result = cli.summarize_payloads([payload])
    assert result["chapters"] == sentinel


def test_cli_session_observer_on_event_routes_through_canonical_with_glossary_flag(monkeypatch):
    from gemini_translator import cli

    sentinel = ["ROUTED-CLI-OBSERVER"]
    monkeypatch.setattr(
        cli,
        "extract_chapters_from_payload",
        lambda payload, **kwargs: sentinel if kwargs.get("include_glossary_batch") is True else ["WRONG-FLAG"],
    )

    observer = object.__new__(cli.CliSessionObserver)
    observer.event_counts = Counter()
    observer.capture_results = False
    observer.task_events = []
    observer.task_results = []
    observer.verbose = False
    observer.logs = []

    task_payload = ("epub_batch", "book.epub", ["a.xhtml"])
    observer.on_event({
        "event": "task_finished",
        "data": {
            "success": True,
            "task_info": ("task-1", task_payload),
        },
    })

    assert observer.task_events[-1]["chapters"] == sentinel


def test_task_manager_extract_chapters_routes_through_canonical_without_glossary_flag(monkeypatch):
    """major-фикс: task_manager.py НЕ должен передавать include_glossary_batch —
    это как раз путь _restore_snapshot_payload, для которого рецензент указал
    непроверенное расширение поведения (glossary-главы в списке глав книги)."""
    import gemini_translator.core.task_manager as task_manager_module
    from gemini_translator.core.task_manager import ChapterQueueManager

    def fake_extract(payload, **kwargs):
        assert "include_glossary_batch" not in kwargs
        return ["ROUTED-TASK-MANAGER"]

    monkeypatch.setattr(task_manager_module, "extract_chapters_from_payload", fake_extract)

    payload = ("epub_batch", "book.epub", ["a.xhtml"])
    result = ChapterQueueManager._extract_chapters_from_payload(object(), payload)
    assert result == ["ROUTED-TASK-MANAGER"]


def test_task_manager_restore_snapshot_glossary_batch_task_chapters_are_not_extracted():
    """Прямая проверка потребителя из major-замечания №2: для
    glossary_batch_task (виртуальный payload вида
    ('glossary_batch_task', epub_path, ('correction_data.txt',)))
    список глав книги (restored_chapters/extracted_chapters в
    load_queue_snapshot) не должен пополняться псевдо-главой из
    payload задачи глоссария, раз это поведение не проверено по
    потребителю (setup.py:self.html_files)."""
    from gemini_translator.core.task_manager import ChapterQueueManager

    payload = ("glossary_batch_task", "book.epub", ("correction_data.txt",))
    result = ChapterQueueManager._extract_chapters_from_payload(object(), payload)
    assert result == []


def test_package_filter_tasks_with_filter_save_targets_routes_through_canonical(monkeypatch):
    """Минор-фикс: _payload_chapters был однострочным делегатом с ровно
    одним вызывающим (себя самого) — удалён; маршрутизация теперь
    проверяется через реальную точку входа _with_filter_save_targets."""
    import gemini_translator.scripts.package_filter_tasks as package_filter_tasks_module
    from gemini_translator.scripts.package_filter_tasks import FilterPackagingDialog

    assert not hasattr(FilterPackagingDialog, "_payload_chapters")

    monkeypatch.setattr(
        package_filter_tasks_module,
        "extract_chapters_from_payload",
        lambda payload: ["a.xhtml"],
    )

    dialog = FilterPackagingDialog.__new__(FilterPackagingDialog)
    payload = ("epub_batch", "book.epub", ["a.xhtml"])
    result = dialog._with_filter_save_targets(payload, filtered_set={"a.xhtml"})
    assert result is not None
