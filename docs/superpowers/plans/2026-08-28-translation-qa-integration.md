# Translation QA Session and UI Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Подключить TranslationQualityService к переводческой очереди и
интерфейсу: автоматическая проверка после каждой главы и в конце книги,
межглавный шлюз для сильных аномалий, ручные действия для главы/книги, четыре
независимые карточки анализаторов с понятной нагрузкой, полный отчёт и откат,
необязательный локальный ONNX embedding provider и отдельный COMETKiwi runner.

**Architecture:** Queue manager получает явные QA-состояния и persisted gate.
После сохранения перевода глава переходит в `qa_pending`; coordinator запускает
единый сервис, затем завершает задачу, откладывает QA при инфраструктурном сбое
либо ставит `qa_blocked` при подтверждённом сильном риске. UI и автоматический
процесс вызывают тот же coordinator/service API. Четыре независимые capability
cards управляют Razdel, LanguageTool, Slovnet/Navec и COMETKiwi, но не содержат
аналитической логики. Qt-модели отображают immutable snapshots pandas-отчёта и
не выполняют сеть или model loading в GUI thread. Финальный проход повторно
проверяет deferred и ранние главы с уже сформированной книжной статистикой;
COMETKiwi запускается отдельным процессом только для выбранных спорных окон.

**Tech Stack:** Python 3.11, PySide6, SQLite queue, asyncio/threads
существующего worker runtime, pandas 3.x, NumPy 2.x, Razdel, direct
LanguageTool HTTP, optional Slovnet/Navec, optional ONNX Runtime/tokenizers,
optional separate PyTorch/COMET environment, pytest/pytest-qt, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-08-28-translation-completeness-qa-design.md`

## Global Constraints

- Предварительно полностью выполнить три предыдущих плана в порядке: foundation → semantic alignment → verification and repair.
- Автоматический и ручной paths используют один `TranslationQualityService`; дублирующая логика QA в UI запрещена.
- По умолчанию включены: проверка полноты после главы, auto-fix подтверждённых пропусков, языковой QA после главы, auto-fix объективных языковых дефектов.
- Отдельные capability defaults: Razdel включён; LanguageTool, Slovnet/Navec и
  COMETKiwi выключены. Изменение одного флага не изменяет остальные.
- Каждая capability card показывает назначение, CPU/RAM/GPU/network нагрузку,
  качественное влияние на скорость, пользу, ограничения, статус и длительность
  последнего запуска.
- Никакая галочка не устанавливает модель, не запускает Java и не отправляет
  текст наружу без отдельного явного подтверждения.
- Correction model по умолчанию — текущая translation model; пользователь может выбрать отдельную.
- Сильный подтверждённый нерешённый риск блокирует выдачу следующей pending главы. Слабый статистический сигнал, style suggestion или временная недоступность QA не блокируют основную сессию.
- Уже запущенные параллельные worker tasks не отменяются; после появления gate новые задачи не выдаются.
- Ранние главы повторно оцениваются после появления не менее пяти подходящих глав книжной статистики.
- Абсолютные профили: алфавитный → русский `0.92–1.20`, CJK → русский `2.80–3.30`; UI показывает их из общего реестра, а не из новых констант.
- Пользовательские действия: проверить/исправить главу, проверить/исправить все главы, отменить исправления главы, отменить все автоматические исправления.
- Любая фоновая операция поддерживает progress, cancellation и безопасную точку остановки.
- Локальная ONNX-модель необязательна, не входит в базовые requirements/build и не включена по умолчанию.
- Slovnet/Navec и COMETKiwi необязательны и не входят в базовые
  requirements/PyInstaller. COMETKiwi работает только через отдельный runner.
- NumPy-миграция многоминутного аудио не входит в этот план.
- Не изменять и не включать в QA-коммиты несвязанные пользовательские изменения.

---

## Task 1: Добавить настройки QA и выбор моделей

**Files:**

- Modify: `gemini_translator/utils/settings.py`
- Modify: `gemini_translator/ui/widgets/translation_options_widget.py`
- Create: `gemini_translator/ui/widgets/qa_capability_card.py`
- Test: `tests/qa/test_qa_settings.py`
- Test: `tests/qa/test_qa_capability_card.py`
- Modify: `tests/test_translation_options_widget.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class QaSettings:
    check_completeness_after_chapter: bool = True
    auto_repair_confirmed_omissions: bool = True
    check_language_after_chapter: bool = True
    auto_repair_objective_language_issues: bool = True
    embedding_provider: str = "auto"
    embedding_model: str = ""
    correction_model_mode: str = "translation_model"
    correction_provider: str = ""
    correction_model: str = ""
    final_book_pass: bool = True
    capabilities: QaCapabilitySettings = field(
        default_factory=QaCapabilitySettings
    )
    language_tool_endpoint: str = ""
    language_tool_mode: str = "remote"
    language_tool_disabled_rules: tuple[str, ...] = ()
    slovnet_cpu_threads: int = 2
    slovnet_batch_size: int = 16
    cometkiwi_runner_path: str = ""
    cometkiwi_model: str = ""
    cometkiwi_device: str = "cpu"
    cometkiwi_license_accepted: bool = False

class QaCapabilityCard(QWidget):
    enabled_changed = Signal(str, bool)
    setup_requested = Signal(str)

    def set_description(
        self, description: QaCapabilityDescription
    ) -> None:
        raise NotImplementedError

    def set_runtime_status(
        self, status: QaCapabilityRuntimeStatus
    ) -> None:
        raise NotImplementedError
```

- [ ] **Step 1: Написать settings round-trip/migration tests**

```python
# tests/qa/test_qa_settings.py
def test_missing_legacy_settings_migrate_to_safe_enabled_defaults(tmp_settings):
    settings = SettingsManager(tmp_settings)
    qa = settings.get_qa_settings()
    assert qa.check_completeness_after_chapter is True
    assert qa.auto_repair_confirmed_omissions is True
    assert qa.check_language_after_chapter is True
    assert qa.auto_repair_objective_language_issues is True
    assert qa.embedding_provider == "auto"
    assert qa.correction_model_mode == "translation_model"
    assert qa.capabilities == QaCapabilitySettings(
        razdel_enabled=True,
        language_tool_enabled=False,
        slovnet_enabled=False,
        cometkiwi_enabled=False,
    )


def test_separate_correction_model_round_trips(tmp_settings):
    expected = QaSettings(
        correction_model_mode="separate",
        correction_provider="gemini",
        correction_model="gemini-3-flash",
    )
    tmp_settings.set_qa_settings(expected)
    assert SettingsManager(tmp_settings.path).get_qa_settings() == expected


def test_four_capability_flags_round_trip_independently(tmp_settings):
    expected = replace(
        QaSettings(),
        capabilities=QaCapabilitySettings(
            razdel_enabled=False,
            language_tool_enabled=True,
            slovnet_enabled=False,
            cometkiwi_enabled=True,
        ),
        language_tool_endpoint="http://127.0.0.1:8081/v2",
        cometkiwi_license_accepted=True,
    )
    tmp_settings.set_qa_settings(expected)
    restored = SettingsManager(tmp_settings.path).get_qa_settings()
    assert restored.capabilities == expected.capabilities
    assert restored.language_tool_endpoint == expected.language_tool_endpoint
    assert restored.cometkiwi_license_accepted is True
```

- [ ] **Step 2: Написать widget test**

Проверить четыре общие checkbox автоматизации, четыре независимые capability
cards, embedding provider combo и correction model mode. При
`translation_model` отдельные provider/model controls disabled; при
`separate` enabled. `get_settings()/set_settings()` обязаны round-trip все
поля без запуска API, установки моделей или вызова endpoint.

`test_qa_capability_card.py` проверяет, что каждая карточка берёт данные из
`CAPABILITY_DESCRIPTIONS` и показывает title, summary, load level,
CPU/RAM/GPU/network resources, speed impact, quality benefit, quality risk,
network policy, availability reason и last-run duration. Галочки меняются
независимо.

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие настроек**

Run: `python -m pytest tests/qa/test_qa_settings.py tests/qa/test_qa_capability_card.py tests/test_translation_options_widget.py -q`

Expected: FAIL, потому что `QaSettings`/controls ещё отсутствуют.

- [ ] **Step 4: Реализовать versioned settings mapping**

Хранить под одним ключом `translation_qa` с `schema_version: 2`. Миграция
версии 1 добавляет capability defaults без изменения прежних полей. Это
глобальные настройки программы для всех книг. Не разбрасывать отдельные magic
keys. Не сохранять API keys в QA settings: adapters используют существующее
хранилище provider credentials.

- [ ] **Step 5: Добавить компактную UI-группу**

В `TranslationOptionsWidget` добавить collapsible group «Контроль качества»
после основных параметров перевода. Под общими настройками разместить четыре
`QaCapabilityCard` в порядке Razdel, LanguageTool, Slovnet/Navec, COMETKiwi.
Краткий summary виден всегда; details раскрывают нагрузку, ресурсы, скорость,
качество и риски. Advanced endpoint/model controls показывать только для
включённой карточки. При отсутствии настройки статус меняется на
«Требует настройки», но галочка не сбрасывается. Не загружать модели и не
делать сеть при построении widget.

LanguageTool setup требует явного подтверждения передачи проверяемого текста
для удалённого endpoint и умеет проверить соединение только по отдельной
кнопке. Slovnet/Navec setup показывает manifest, размер и кнопки
«Установить»/«Удалить» с progress/cancel. COMETKiwi setup до Task 8 показывает
статус «Требует настройки», не пытаясь импортировать или установить PyTorch.

- [ ] **Step 6: Проверить настройки**

Run: `python -m pytest tests/qa/test_qa_settings.py tests/qa/test_qa_capability_card.py tests/test_translation_options_widget.py -q`

Expected: PASS.

- [ ] **Step 7: Зафиксировать этап**

```bash
git add gemini_translator/utils/settings.py gemini_translator/ui/widgets/translation_options_widget.py gemini_translator/ui/widgets/qa_capability_card.py tests/qa/test_qa_settings.py tests/qa/test_qa_capability_card.py tests/test_translation_options_widget.py
git commit -m "feat: configure automatic translation QA"
```

## Task 2: Расширить очередь состояниями QA и persisted interchapter gate

**Files:**

- Modify: `gemini_translator/core/task_manager.py`
- Test: `tests/qa/test_task_manager_qa_gate.py`
- Modify: `tests/test_task_db_worker_lifecycle.py`

**Interfaces:**

```python
class ChapterQueueManager:
    def mark_task_qa_pending(self, task_id: int, chapter_ids: Sequence[str]) -> None:
        raise NotImplementedError
    def resolve_task_qa(self, task_id: int, outcome: QaQueueOutcome) -> None:
        raise NotImplementedError
    def open_qa_gate(self, task_id: int, chapter_id: str, reason: str) -> None:
        raise NotImplementedError
    def close_qa_gate(self, task_id: int, chapter_id: str) -> None:
        raise NotImplementedError
    def get_open_qa_gates(self) -> list[QaGateRecord]:
        raise NotImplementedError
```

- [ ] **Step 1: Написать migration и lifecycle tests**

```python
def test_existing_database_is_migrated_with_qa_gates(task_db):
    manager = ChapterQueueManager(task_db)
    assert manager.schema_has_table("qa_gates")
    assert manager.schema_version >= QA_GATE_SCHEMA_VERSION


def test_qa_pending_predecessor_blocks_next_chained_task(manager, chained_tasks):
    manager.mark_task_qa_pending(chained_tasks[0].id, ["chapter-1"])
    assert manager.get_next_task("worker-2") is None
    manager.resolve_task_qa(chained_tasks[0].id, QaQueueOutcome.completed())
    assert manager.get_next_task("worker-2").id == chained_tasks[1].id
```

- [ ] **Step 2: Написать global gate tests**

Открытый high-risk gate запрещает выдачу любой новой pending translation task в проекте, но не меняет `in_progress`. Weak/deferred outcome не создаёт gate. Закрытие gate после успешного repair или ручного решения снова разрешает выдачу.

- [ ] **Step 3: Запустить тест и подтвердить падение**

Run: `python -m pytest tests/qa/test_task_manager_qa_gate.py tests/test_task_db_worker_lifecycle.py -q`

Expected: FAIL, потому что schema/methods/statuses отсутствуют.

- [ ] **Step 4: Добавить idempotent SQLite migration**

```sql
CREATE TABLE IF NOT EXISTS qa_gates (
    task_id INTEGER NOT NULL,
    chapter_id TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    reason TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution TEXT,
    PRIMARY KEY (task_id, chapter_id)
);
```

Миграция не переписывает существующие task rows. Добавить индексы для unresolved gates и task_id.

- [ ] **Step 5: Обновить eligibility query**

Добавить `qa_pending` и `qa_blocked` к predecessor blocker statuses. Перед выбором pending task проверить отсутствие unresolved `risk_level='high'` gate. SQL должен оставаться одной транзакционной операцией выбора/claim, чтобы два workers не обошли gate.

- [ ] **Step 6: Реализовать исходы**

- `completed`: task → `completed`, gates closed.
- `high_unresolved`: task → `qa_blocked`, high gate open.
- `deferred`: task → `completed`, journal/final-pass marker сохраняется, gate не открывается.
- `cancelled`: task остаётся `qa_pending` для resume, если сессию отменил пользователь до safe point.

Все методы идемпотентны и публикуют существующие task update events после commit транзакции.

- [ ] **Step 7: Проверить очередь**

Run: `python -m pytest tests/qa/test_task_manager_qa_gate.py tests/test_task_db_worker_lifecycle.py tests/test_worker_dispatch.py -q`

Expected: PASS.

- [ ] **Step 8: Зафиксировать этап**

```bash
git add gemini_translator/core/task_manager.py tests/qa/test_task_manager_qa_gate.py tests/test_task_db_worker_lifecycle.py
git commit -m "feat: gate translation queue on unresolved QA risk"
```

## Task 3: Подключить ChapterQaCoordinator после сохранения перевода

**Files:**

- Create: `gemini_translator/core/chapter_qa_coordinator.py`
- Modify: `gemini_translator/core/worker.py`
- Modify: `gemini_translator/core/translation_engine.py`
- Modify: `gemini_translator/core/worker_helpers/response_parser.py`
- Modify: `gemini_translator/core/worker_helpers/taskers/epub_batch_processor.py`
- Test: `tests/qa/test_chapter_qa_coordinator.py`
- Modify: `tests/test_worker_dispatch.py`
- Modify: `tests/test_worker_run_async.py`

**Interfaces:**

```python
class ChapterQaCoordinator:
    async def inspect_completed_task(self, event: TranslationReadyEvent) -> TaskQaOutcome:
        raise NotImplementedError
    async def check_chapter_now(self, chapter_id: str, options: QaOptions) -> ChapterQaResult:
        raise NotImplementedError
    async def check_all_now(self, options: QaOptions) -> BookQaResult:
        raise NotImplementedError
    def cancel(self) -> None:
        raise NotImplementedError
```

- [ ] **Step 1: Написать exact lifecycle test**

```python
@pytest.mark.asyncio
async def test_saved_chapter_is_qa_pending_before_next_task_can_launch(harness):
    await harness.finish_translation("chapter-1")
    assert harness.task_status("chapter-1") == "qa_pending"
    assert harness.next_dispatch() is None

    harness.qa_service.complete_with(clean_result())
    await harness.coordinator.drain()
    assert harness.task_status("chapter-1") == "completed"
    assert harness.next_dispatch().chapter_id == "chapter-2"
```

- [ ] **Step 2: Написать high/weak/failure tests**

- high repaired successfully → completed, next allowed;
- high unresolved → `qa_blocked`, gate open, next denied;
- weak ratio/glossary signal → completed, next allowed;
- embedding/LLM outage → deferred, next allowed, final pass scheduled;
- cancellation → safe `qa_pending`, no corrupt output;
- batch translation task → task remains pending until every emitted chapter result resolved.
- изменение capability checkbox применяется к следующему check и не ставит уже
  завершённые главы в очередь; их повторная проверка начинается только ручной
  кнопкой или явно выбранным final pass.

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие coordinator**

Run: `python -m pytest tests/qa/test_chapter_qa_coordinator.py -q`

Expected: FAIL with import error.

- [ ] **Step 4: Эмитить структурированное TranslationReadyEvent после записи**

`ResponseParser.process_and_save_single_file()` и `_save_successful_chapters()` возвращают records с `task_id`, `chapter_id`, source path, translated path, source/target language, token/retry/duration metadata и fingerprint. Не читать GUI widgets. Worker до `task_finished` переводит task в `qa_pending`, если live QA включён.

- [ ] **Step 5: Реализовать coordinator на engine runtime**

Coordinator владеет одним bounded queue и semaphore. `TranslationEngine` передаёт ему successful ready events и не вызывает `_try_launch_replacement()` для task в `qa_pending`; запуск возобновляется только после outcome callback. Qt/main event thread не блокируется: coroutine запускается в существующем async worker runtime или выделенном QA runtime с тем же safe shutdown.

- [ ] **Step 6: Собрать ChapterQaRequest из project state**

Coordinator читает исходный/переведённый EPUB payload, текущий glossary, model settings и journal. Для batch task запускает chapters последовательно в книжном порядке, чтобы pandas baseline обновлялся детерминированно. На каждый result вызывает `resolve_task_qa()` только после завершения всех chapters task.

Из `QaSettings` coordinator строит один `QaOptions` с неизменяемым
`QaCapabilitySettings` и provider configs. Выключенный provider не
создаётся. Для каждого включённого provider измерять monotonic duration,
публиковать `QaCapabilityRuntimeStatus` и сохранять секунды в
`ChapterMetrics.capability_durations` без полного текста или секретов.
Карточка читает последнее значение по главам текущего журнала.

- [ ] **Step 7: Обработать strong anomaly до следующего dispatch**

Cheap metrics выполняются первыми. При strong signal coordinator углубляет semantic/LLM path. Только `ChapterQaResult.high_unresolved` открывает gate; successful auto-repair пересчитывает метрики до закрытия. Infrastructure failure записывает deferred и не открывает gate.

- [ ] **Step 8: Проверить lifecycle**

Run: `python -m pytest tests/qa/test_chapter_qa_coordinator.py tests/test_worker_dispatch.py tests/test_worker_run_async.py tests/test_task_db_worker_lifecycle.py -q`

Expected: PASS.

- [ ] **Step 9: Зафиксировать этап**

```bash
git add gemini_translator/core/chapter_qa_coordinator.py gemini_translator/core/worker.py gemini_translator/core/translation_engine.py gemini_translator/core/worker_helpers/response_parser.py gemini_translator/core/worker_helpers/taskers/epub_batch_processor.py tests/qa/test_chapter_qa_coordinator.py tests/test_worker_dispatch.py tests/test_worker_run_async.py
git commit -m "feat: run translation QA between chapters"
```

## Task 4: Добавить финальный проход книги и восстановление после перезапуска

**Files:**

- Modify: `gemini_translator/core/chapter_qa_coordinator.py`
- Modify: `gemini_translator/core/translation_engine.py`
- Modify: `gemini_translator/qa/journal.py`
- Test: `tests/qa/test_final_book_qa_pass.py`

**Interfaces:**

```python
class ChapterQaCoordinator:
    async def run_final_book_pass(self, session_id: str) -> BookQaResult:
        raise NotImplementedError
    async def resume_pending_qa(self) -> ResumeResult:
        raise NotImplementedError
```

- [ ] **Step 1: Написать final-pass selection tests**

Финальный проход выбирает: deferred chapters, chapters без current schema/preprocessing version, ранние главы до формирования baseline, главы с unresolved medium/high candidates. Он пропускает unchanged fully checked chapters и уже applied idempotent repairs.

- [ ] **Step 2: Написать restart test**

Создать persisted task DB с `qa_pending`, journal и backup; пересоздать manager/coordinator; `resume_pending_qa()` обязан продолжить с безопасного этапа и не повторить применённую repair. Повреждённый embedding cache разрешается пересоздать; повреждённый journal показывает ошибку и не перезаписывается.

- [ ] **Step 3: Запустить тест и подтвердить падение**

Run: `python -m pytest tests/qa/test_final_book_qa_pass.py -q`

Expected: FAIL, методы отсутствуют.

- [ ] **Step 4: Запустить final pass до session finished event**

В `_check_if_session_finished()` при отсутствии translation workers/pending translation tasks и включённом `final_book_pass` запустить coordinator один раз на session ID. Только после завершения/отмены/фиксированного deferred результата публиковать окончательный session summary. Сбой QA не меняет успешный статус перевода, но отображается отдельным warning count.

- [ ] **Step 5: Пересчитать статистику и glossary ledger**

Сначала загрузить все ChapterMetrics в DataFrame, построить baseline по каждой language pair, затем выбрать anomalies. Ранние главы повторно классифицируются относительно baseline, но relative anomaly внутри абсолютного профиля не auto-fixable без semantic confirmation.

- [ ] **Step 6: Проверить final pass**

Run: `python -m pytest tests/qa/test_final_book_qa_pass.py tests/qa/test_book_metrics.py tests/qa/test_glossary_audit.py -q`

Expected: PASS.

- [ ] **Step 7: Зафиксировать этап**

```bash
git add gemini_translator/core/chapter_qa_coordinator.py gemini_translator/core/translation_engine.py gemini_translator/qa/journal.py tests/qa/test_final_book_qa_pass.py
git commit -m "feat: run resumable final book QA pass"
```

## Task 5: Добавить раздел «Качество перевода» и четыре действия

**Files:**

- Create: `gemini_translator/ui/dialogs/validation_dialogs/translation_quality_dialog.py`
- Create: `gemini_translator/ui/dialogs/validation_dialogs/translation_quality_models.py`
- Modify: `gemini_translator/ui/dialogs/validation_dialogs/__init__.py`
- Modify: `gemini_translator/ui/dialogs/validation.py`
- Test: `tests/qa/test_translation_quality_dialog.py`
- Modify: `tests/test_validation_page.py`

**Interfaces:**

```python
class TranslationQualityDialog(QDialog):
    check_chapter_requested = Signal(str)
    check_all_requested = Signal()
    undo_chapter_requested = Signal(str)
    undo_all_requested = Signal()
    cancel_requested = Signal()

class ChapterQaTableModel(QAbstractTableModel):
    def set_snapshot(self, snapshot: BookQaReportSnapshot) -> None:
        raise NotImplementedError
```

- [ ] **Step 1: Написать GUI contract tests**

```python
def test_dialog_exposes_four_actions_and_disables_conflicting_actions(qtbot, report):
    dialog = TranslationQualityDialog()
    qtbot.addWidget(dialog)
    dialog.set_report(report)

    assert dialog.check_chapter_button.text() == "Проверить и исправить главу"
    assert dialog.check_all_button.text() == "Проверить и исправить все главы"
    assert dialog.undo_chapter_button.text() == "Отменить исправления главы"
    assert dialog.undo_all_button.text() == "Отменить все автоматические исправления"

    dialog.set_busy(True)
    assert not dialog.check_all_button.isEnabled()
    assert dialog.cancel_button.isEnabled()
```

- [ ] **Step 2: Написать report navigation tests**

Выбор агрегированной ratio/glossary anomaly переводит chapter table на соответствующий chapter ID; выбор candidate показывает source, old translation, proposed/applied text, anchors, terms, explanation и decision. Language issue показывает category и validation result. Таблица использует snapshot, а не мутируемый DataFrame из background thread.

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие диалога**

Run: `python -m pytest tests/qa/test_translation_quality_dialog.py tests/test_validation_page.py -q`

Expected: FAIL with import error/UI control missing.

- [ ] **Step 4: Реализовать read-only Qt models**

Колонки chapters: глава, языковая пара, коэффициент, профиль, книжная медиана/robust z, glossary conflicts, untranslated script, possible/confirmed gaps, language issues, actions, risk, duration/tokens. Форматировать значения в `data()`; не копировать DataFrame при каждом cell call. Background coordinator передаёт один immutable `BookQaReportSnapshot` сигналом.

- [ ] **Step 5: Реализовать карточку кандидата и summary**

Группы: «Полнота», «Языковые дефекты», «Термины», «Статистика книги». Показать причину исключения бренда/диалога, limited mode, failure/deferred и undo status. Цвет — дополнительный сигнал, все риски имеют текст/иконку для accessibility.

- [ ] **Step 6: Связать четыре действия с coordinator**

`TranslationValidatorPage` создаёт dialog и connects signals к public coordinator methods через существующий event/runtime bridge. UI не вызывает service напрямую. Для all chapters показать progress `checked/total`, текущую главу и Cancel. После каждого result обновлять snapshot, не закрывать dialog.

- [ ] **Step 7: Реализовать безопасный undo UX**

Undo chapter/all сначала показывает список изменяемых глав. При `ManualEditConflict` открыть comparison и не перезаписывать вручную изменённую главу. Successful undo обновляет journal, table и снимает gate, если high risk был разрешён пользовательским действием.

- [ ] **Step 8: Проверить UI**

Run: `python -m pytest tests/qa/test_translation_quality_dialog.py tests/test_validation_page.py tests/test_validation_reanalysis.py -q`

Expected: PASS.

- [ ] **Step 9: Зафиксировать этап**

```bash
git add gemini_translator/ui/dialogs/validation_dialogs/translation_quality_dialog.py gemini_translator/ui/dialogs/validation_dialogs/translation_quality_models.py gemini_translator/ui/dialogs/validation_dialogs/__init__.py gemini_translator/ui/dialogs/validation.py tests/qa/test_translation_quality_dialog.py tests/test_validation_page.py
git commit -m "feat: add translation quality report and controls"
```

## Task 6: Добавить экспорт pandas-отчёта без новой постоянной зависимости

**Files:**

- Create: `gemini_translator/qa/reporting.py`
- Modify: `gemini_translator/ui/dialogs/validation_dialogs/translation_quality_dialog.py`
- Test: `tests/qa/test_qa_reporting.py`

**Interfaces:**

```python
class QaReportBuilder:
    def chapter_frame(self, journal: QaJournal) -> pd.DataFrame:
        raise NotImplementedError
    def glossary_frame(self, journal: QaJournal) -> pd.DataFrame:
        raise NotImplementedError
    def summary(self, journal: QaJournal) -> BookQaReportSnapshot:
        raise NotImplementedError
    def export_csv_bundle(self, directory: Path, journal: QaJournal) -> tuple[Path, ...]:
        raise NotImplementedError
```

- [ ] **Step 1: Написать schema/export tests**

Проверить точные поля из spec:
`chapter/source_chars/translated_chars/ratio/glossary_hits/glossary_conflicts/untranslated_cn/language_tool_issues/protected_entities/syntax_candidates/quality_estimator/quality_score/quality_score_status/retries/input_tokens/output_tokens/duration/risk/actions`.
Для реально вызванных возможностей добавить
`duration_razdel/duration_language_tool/duration_slovnet/duration_cometkiwi`.
CSV открывается pandas с теми же row count/IDs; JSON journal остаётся источником
истины.

- [ ] **Step 2: Запустить тест и подтвердить отсутствие builder**

Run: `python -m pytest tests/qa/test_qa_reporting.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать vectorized frames and summary**

Использовать `DataFrame.from_records`, `groupby`, `agg`, `merge`,
categorical columns и nullable dtypes. Не хранить DataFrame в journal и не
сериализовать pandas-specific JSON. `untranslated_by_script` разворачивать в
стабильные столбцы (`untranslated_han`, `untranslated_kana`,
`untranslated_hangul`, `untranslated_latin`), а
`capability_durations` — в четыре nullable duration-столбца.

- [ ] **Step 4: Добавить CSV bundle action**

Экспорт создаёт `chapters.csv`, `glossary.csv`, `candidates.csv`, `repairs.csv` в выбранной директории атомарно. Excel/XLSX не добавлять: это потребовало бы отдельной зависимости; пользователь получает универсальный UTF-8-SIG CSV.

- [ ] **Step 5: Проверить reporting**

Run: `python -m pytest tests/qa/test_qa_reporting.py tests/qa/test_qa_journal.py tests/qa/test_book_metrics.py -q`

Expected: PASS.

- [ ] **Step 6: Зафиксировать этап**

```bash
git add gemini_translator/qa/reporting.py gemini_translator/ui/dialogs/validation_dialogs/translation_quality_dialog.py tests/qa/test_qa_reporting.py
git commit -m "feat: export book translation QA reports"
```

## Task 7: Добавить необязательный локальный multilingual E5 через ONNX

**Files:**

- Create: `gemini_translator/qa/embeddings/local_onnx.py`
- Create: `gemini_translator/qa/embeddings/local_model_manager.py`
- Modify: `gemini_translator/qa/embeddings/factory.py`
- Modify: `gemini_translator/utils/settings.py`
- Modify: `gemini_translator/ui/widgets/translation_options_widget.py`
- Test: `tests/qa/test_local_onnx_embedding_provider.py`
- Test: `tests/qa/test_local_embedding_model_manager.py`

**Interfaces:**

```python
class LocalOnnxEmbeddingProvider:
    def __init__(self, model_dir: Path, runtime_loader: Callable[[], OnnxRuntimeFacade]):
        raise NotImplementedError

@dataclass(frozen=True, slots=True)
class LocalEmbeddingModelStatus:
    state: Literal["missing", "installing", "ready", "invalid"]
    version: str | None
    installed_size_bytes: int | None
    reason: str = ""

class LocalEmbeddingModelManager:
    def status(self) -> LocalEmbeddingModelStatus:
        raise NotImplementedError
    async def install(
        self, progress, cancellation
    ) -> LocalEmbeddingModelStatus:
        raise NotImplementedError
    def uninstall(self) -> LocalEmbeddingModelStatus:
        raise NotImplementedError
```

- [ ] **Step 1: Написать optional-import tests**

При отсутствии `onnxruntime`/`tokenizers` импорт `gemini_translator.qa.embeddings` и запуск приложения проходят; выбор local возвращает понятный `OptionalEmbeddingDependencyMissing`, auto fallback идёт онлайн. Базовые requirements не содержат этих packages.

- [ ] **Step 2: Написать provider contract test с fake runtime**

Проверить E5 prefixes `query:`/`passage:` согласно выбранному режиму, attention-mask mean pooling, `float32`, L2 normalization, batch order и truncation metadata. Реальный model/network в unit test не использовать.

- [ ] **Step 3: Написать model manager tests**

Install пишет во временную директорию, проверяет manifest с SHA-256 каждого файла и только затем atomic rename. Cancellation/неверный hash удаляет только temp dir. Uninstall удаляет точную model dir после проверки manifest, не embedding cache/journal.

- [ ] **Step 4: Запустить тесты и подтвердить отсутствие local provider**

Run: `python -m pytest tests/qa/test_local_onnx_embedding_provider.py tests/qa/test_local_embedding_model_manager.py -q`

Expected: FAIL with import errors.

- [ ] **Step 5: Реализовать lazy runtime loader**

Импорты `onnxruntime` и tokenizer находятся только внутри loader. Provider валидирует manifest/model version и использует CPUExecutionProvider. Число intra-op threads ограничить настройкой; не занимать все CPU cores рядом с переводом/озвучкой.

- [ ] **Step 6: Реализовать install/remove UI**

В advanced QA settings показывать размер загрузки, путь, version/hash, progress и кнопки «Установить локальную модель»/«Удалить». Никакой автоматической загрузки. После удаления provider factory немедленно возвращается к configured fallback.

- [ ] **Step 7: Проверить optional path**

Run: `python -m pytest tests/qa/test_local_onnx_embedding_provider.py tests/qa/test_local_embedding_model_manager.py tests/qa/test_embedding_contract.py -q`

Expected: PASS both with optional modules monkeypatched present and absent.

- [ ] **Step 8: Зафиксировать этап**

```bash
git add gemini_translator/qa/embeddings/local_onnx.py gemini_translator/qa/embeddings/local_model_manager.py gemini_translator/qa/embeddings/factory.py gemini_translator/utils/settings.py gemini_translator/ui/widgets/translation_options_widget.py tests/qa/test_local_onnx_embedding_provider.py tests/qa/test_local_embedding_model_manager.py
git commit -m "feat: support optional local ONNX embeddings"
```

## Task 8: Добавить отдельный необязательный COMETKiwi runner

**Files:**

- Create: `gemini_translator/qa/estimators/__init__.py`
- Create: `gemini_translator/qa/estimators/base.py`
- Create: `gemini_translator/qa/estimators/cometkiwi_client.py`
- Create: `gemini_translator/qa/estimators/cometkiwi_model_manager.py`
- Create: `tools/translation_qa_cometkiwi_runner.py`
- Modify: `gemini_translator/core/chapter_qa_coordinator.py`
- Modify: `gemini_translator/ui/widgets/qa_capability_card.py`
- Test: `tests/qa/test_cometkiwi_estimator.py`
- Test: `tests/qa/test_cometkiwi_runner_protocol.py`
- Test: `tests/qa/test_cometkiwi_model_manager.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class SourceTranslationWindow:
    window_id: str
    source: str
    translation: str
    visible_chars: int

@dataclass(frozen=True, slots=True)
class QualityEstimateRequest:
    chapter_id: str
    windows: tuple[SourceTranslationWindow, ...]
    source_language: str
    target_language: str

@dataclass(frozen=True, slots=True)
class QualityEstimate:
    estimator: str
    model: str
    window_scores: tuple[float, ...]
    chapter_score: float | None
    minimum_score: float | None
    p10_score: float | None
    status: Literal["completed", "disabled", "unavailable"]
    metadata: Mapping[str, str]

class TranslationQualityEstimator(Protocol):
    async def estimate(
        self,
        request: QualityEstimateRequest,
        cancellation: CancellationToken,
    ) -> QualityEstimate:
        raise NotImplementedError

@dataclass(frozen=True, slots=True)
class CometKiwiModelStatus:
    state: Literal["missing", "installing", "ready", "invalid"]
    model: str
    installed_size_bytes: int | None
    license_name: str
    reason: str = ""

@dataclass(frozen=True, slots=True)
class CometKiwiModelManifest:
    model: str
    source_url: str
    size_bytes: int
    sha256_by_file: Mapping[str, str]
    license_name: str
    license_url: str
    minimum_ram_bytes: int

class CometKiwiModelManager:
    def status(self) -> CometKiwiModelStatus:
        raise NotImplementedError
    async def install(
        self,
        manifest: CometKiwiModelManifest,
        *,
        license_accepted: bool,
        progress,
        cancellation,
    ) -> CometKiwiModelStatus:
        raise NotImplementedError
    def uninstall(self) -> CometKiwiModelStatus:
        raise NotImplementedError
```

- [ ] **Step 1: Написать reference-free contract и aggregation tests**

```python
@pytest.mark.asyncio
async def test_cometkiwi_sends_only_source_and_translation(fake_runner):
    result = await estimator(fake_runner).estimate(
        estimate_request([
            ("原文一", "Первый перевод"),
            ("原文二", "Второй перевод"),
        ]),
        token(),
    )
    payload = fake_runner.requests[0]
    assert set(payload["segments"][0]) == {"source", "translation"}
    assert "reference" not in payload["segments"][0]
    assert result.window_scores == (0.81, 0.63)
    assert result.chapter_score == pytest.approx(
        length_weighted_mean((0.81, 0.63), payload["segments"])
    )
    assert not hasattr(result, "auto_fix_allowed")
```

Добавить тест, что одинаковые window scores агрегируются детерминированно,
невалидные NaN/Inf/неправильное число scores отклоняются, а обычный
reference-based COMET не создаётся без отдельного эталонного текста.

- [ ] **Step 2: Написать тест запрета запуска для чистой главы**

```python
@pytest.mark.asyncio
async def test_clean_interchapter_check_never_starts_comet_runner(harness):
    harness.settings.capabilities = QaCapabilitySettings(
        cometkiwi_enabled=True
    )
    await harness.coordinator.inspect_completed_task(clean_chapter_event())
    assert harness.comet_runner.starts == 0
```

Отдельно проверить: medium/high disputed candidate в final pass запускает один
пакет окон; выключенная галочка, неподтверждённая лицензия или пустой model
возвращают status без старта процесса.

- [ ] **Step 3: Написать protocol failure tests**

JSON Lines protocol имеет request ID, schema version, model/device и segments.
Тесты моделируют timeout, cancellation, process exit, OOM marker, stderr без
JSON, неверную schema/version и частичный score batch. Во всех случаях
coordinator получает `status="unavailable"`, продолжает сессию и записывает
причину без полного текста главы.

- [ ] **Step 4: Написать model/license manager tests**

Манифест хранит model ID, source URL, размер, hashes, license name/text URL и
минимальные RAM/device требования. `install()` запрещён до
`license_accepted=True`, использует temp directory + hash verification +
atomic rename. Никакой модели по умолчанию не скачивать. Uninstall удаляет
только проверенную model directory и отдельное runner environment.

- [ ] **Step 5: Запустить тесты и подтвердить отсутствие estimator**

Run: `python -m pytest tests/qa/test_cometkiwi_estimator.py tests/qa/test_cometkiwi_runner_protocol.py tests/qa/test_cometkiwi_model_manager.py -q`

Expected: FAIL with import errors.

- [ ] **Step 6: Реализовать безопасный client и отдельный runner**

Основной пакет не импортирует `torch` или `comet`. Client запускает явно
настроенный interpreter/runner через существующую безопасную process
абстракцию, пишет один JSON request и читает один bounded JSON response.
`tools/translation_qa_cometkiwi_runner.py` импортирует PyTorch/COMET только
после validation request, загружает указанную локальную model directory,
оценивает source/translation segments и возвращает scores/metadata. Не
принимать URL модели от каждого запроса и не скачивать веса во время проверки.

- [ ] **Step 7: Реализовать выбор окон и калибруемый сигнал**

Coordinator передаёт только окна medium/high disputed candidates либо окна
явно выбранного итогового аудита. `chapter_score` — взвешенное числом видимых
символов среднее валидных window scores; min и p10 хранить в типизированных
полях `minimum_score` и `p10_score`. Estimator не содержит универсального
порога и не меняет risk/repair:
его score только добавляется к evidence, после чего решение остаётся за
семантической и LLM-проверкой.
`ChapterMetrics.quality_estimator/quality_score/quality_score_status` и
journal metadata обновляются после результата; disabled/unavailable сохраняют
`quality_score=None`.

- [ ] **Step 8: Подключить status и last-run duration к карточке**

Карточка показывает runner/model/device, лицензию, установленный размер,
оценочную нагрузку и фактическую длительность последнего запуска. Включение
галочки без runner/model/license переводит статус в «Требует настройки» и
открывает setup UI, но не меняет флаг и не начинает установку.

- [ ] **Step 9: Проверить отсутствие тяжёлых импортов в базовом режиме**

Run: `python -m pytest tests/qa/test_cometkiwi_estimator.py tests/qa/test_cometkiwi_runner_protocol.py tests/qa/test_cometkiwi_model_manager.py tests/qa/test_final_book_qa_pass.py -q`

Expected: PASS; с monkeypatched import blocker основной application imports без
`torch`/`comet`, clean chapter не запускает процесс.

- [ ] **Step 10: Зафиксировать этап**

```bash
git add gemini_translator/qa/estimators tools/translation_qa_cometkiwi_runner.py gemini_translator/core/chapter_qa_coordinator.py gemini_translator/ui/widgets/qa_capability_card.py tests/qa/test_cometkiwi_estimator.py tests/qa/test_cometkiwi_runner_protocol.py tests/qa/test_cometkiwi_model_manager.py
git commit -m "feat: add optional COMETKiwi quality estimator"
```

## Task 9: Провести интеграционные, качественные, производительные и сборочные проверки

**Files:**

- Create: `tests/qa/test_translation_qa_end_to_end.py`
- Create: `tests/qa/test_translation_qa_quality_corpus.py`
- Create: `tests/qa/test_translation_qa_performance.py`
- Create: `docs/translation-quality-qa.md`
- Modify only if required by packaging test: `translatorFork_MOD.spec`
- Modify only if required by packaging test: `translatorFork-translator-only.spec`

- [ ] **Step 1: Собрать end-to-end fixture harness**

Использовать локальные EPUB fixtures, fake embeddings, fake
LanguageTool/Slovnet/COMET providers и scripted completion responses. Покрыть:
после главы, final pass, две check buttons, две undo buttons, четыре независимые
capability flags, restart, cancellation, provider outage, batch task, manual
edit conflict и совместимость с EPUB build.

- [ ] **Step 2: Зафиксировать quality corpus acceptance**

Corpus из spec содержит все значимые пропуски и negative controls. Тест должен утверждать:

```python
assert detected_significant_omissions == expected_significant_omissions
assert auto_modified_negative_controls == set()
assert duplicate_repairs == set()
assert all(repair.has_two_anchors for repair in applied_repairs)
assert all(repair.post_validation_passed for repair in applied_repairs)
```

Отдельно проверить CJK ratios `2.80`, `3.05`, `3.30` как внутри профиля и `2.79`, `3.31` как абсолютные сигналы; alphabetic `0.92`, `1.00`, `1.20` внутри, `0.91`, `1.21` снаружи.

- [ ] **Step 3: Добавить performance budgets**

На synthetic chapter 1 000 source/1 100 target units:

- peak similarity/DP memory не превышает заданный config budget;
- vectors `float32`;
- повторный запуск даёт ноль network embedding calls;
- pandas aggregation 10 000 chapter metrics не использует Python row loop и завершается в согласованный тестовый budget с запасом для CI;
- UI model snapshot обновляется одним reset/layout event, не сигналом на каждую cell.
- выключенные LanguageTool, Slovnet и COMETKiwi дают ноль provider/process
  calls;
- включённые providers записывают отдельную duration, которую capability card
  показывает как фактическое время последнего запуска;
- COMETKiwi memory/process budget проверяется только в отдельном optional CI
  job с подготовленным runner; обычный correctness suite использует fake
  process.

Performance test маркировать `@pytest.mark.performance`; correctness/memory assertions должны оставаться в обычном наборе, wall-clock budget запускать отдельным CI job, чтобы исключить flaky baseline.

- [ ] **Step 4: Написать пользовательскую документацию**

`docs/translation-quality-qa.md` объясняет четыре кнопки, автоматический
режим, high/medium/low, почему длина не является доказательством, новые профили
`0.92–1.20`/`2.80–3.30`, расходы сетевых запросов, limited mode, local
model, журнал, экспорт и undo. Отдельная таблица описывает четыре галочки:
значения по умолчанию, CPU/RAM/GPU/network нагрузку, влияние на скорость и
качество, ложные срабатывания, приватность LanguageTool, установку
Slovnet/Navec и лицензию/ресурсы COMETKiwi.

- [ ] **Step 5: Запустить целевые интеграционные тесты**

Run: `python -m pytest tests/qa/test_translation_qa_end_to_end.py tests/qa/test_translation_qa_quality_corpus.py -q`

Expected: PASS with all acceptance assertions.

- [ ] **Step 6: Запустить весь QA-набор**

Run: `python -m pytest tests/qa -q`

Expected: PASS.

- [ ] **Step 7: Запустить полный проектный gate**

Run: `python tools/run_checks.py`

Expected: PASS with no Ruff/test failures.

- [ ] **Step 8: Проверить packaged applications**

Собрать normal и translator-only варианты документированными командами
`build_master.py`. Smoke test обоих приложений: открыть проект, построить
report из fixture journal, открыть четыре capability cards, запустить
fake-provider check и undo. Отсутствие optional ONNX, Slovnet/Navec, Java,
PyTorch и COMET packages не ломает startup. Проверить, что PyInstaller не
включил COMET runner environment или model weights. Если PyInstaller требует
hooks, добавить только точечные базовые imports/data к соответствующему
`.spec` и повторить обе сборки.

- [ ] **Step 9: Зафиксировать финальный этап**

```bash
git add tests/qa/test_translation_qa_end_to_end.py tests/qa/test_translation_qa_quality_corpus.py tests/qa/test_translation_qa_performance.py docs/translation-quality-qa.md translatorFork_MOD.spec translatorFork-translator-only.spec
git commit -m "test: verify translation QA end to end"
```

Не добавлять `.spec` в commit, если они не изменились.

## Completion Gate

- [ ] После каждой главы QA запускается до выдачи следующей chained task.
- [ ] Только high unresolved risk открывает persisted gate; weak signal и QA outage не блокируют перевод.
- [ ] Финальный проход пересматривает deferred и ранние главы с полной статистикой книги.
- [ ] Четыре действия главы/книги вызывают тот же сервис, что автоматический режим.
- [ ] UI показывает source/old/new/anchors/glossary/reason и все auto actions.
- [ ] Откат одной главы и всей сессии работает после перезапуска и не вызывает LLM.
- [ ] pandas-отчёт показывает объём, профили, glossary, scripts, retries, tokens, duration и QA decisions.
- [ ] Профили `0.92–1.20` и `2.80–3.30` берутся из общего реестра.
- [ ] Local ONNX остаётся необязательным и не утяжеляет базовую сборку.
- [ ] Четыре независимые галочки сохраняются между запусками; каждая карточка
  показывает назначение, ресурсы, скорость, пользу, риск, status и last-run
  duration.
- [ ] Базовое приложение запускается без Java, Slovnet/Navec, PyTorch и COMET;
  отсутствующая возможность не выключает другие.
- [ ] COMETKiwi работает отдельным runner, не получает reference, не запускается
  для чистой главы и не разрешает исправление по одному score.
- [ ] Quality corpus не меняет brands/names/codes/foreign dialogue и находит все внесённые значимые пропуски.
- [ ] Обычная и translator-only сборки проходят smoke test.
- [ ] Полный `python tools/run_checks.py` проходит.
