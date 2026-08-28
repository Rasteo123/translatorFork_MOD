# Translation QA Verification and Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить строгую LLM-проверку смысловых пропусков и языковых дефектов, независимые сигналы LanguageTool и Slovnet/Navec, локальный доперевод с релевантным глоссарием, безопасную структурную вставку/замену, повторную валидацию, полный откат и обратное выявление добавленного моделью содержания.

**Architecture:** `TranslationQualityService` управляет чистым каскадом:
read-only coverage → включённые LanguageTool/Slovnet signals → LLM verdict →
локальный repair proposal → временная структурная версия главы →
детерминированные и LLM post-checks → атомарное принятие либо отказ.
LanguageTool работает через прямой HTTP-контракт, Slovnet/Navec — через
необязательный ленивый runtime; ни один из них не разрешает правку. Все
LLM-ответы проходят явные JSON-схемы без эвристического разрешения правки.
Полнота и языковой QA используют один completion adapter, но разные схемы и
prompts. Несколько языковых дефектов главы исправляются одним пакетом, который
делится только по безопасному бюджету контекста.

**Tech Stack:** Python 3.11, существующие API handlers/retry policy, direct
LanguageTool HTTP API, optional Slovnet/Navec, dataclasses/JSON schema
validation, BeautifulSoup/`epub_json`, NumPy alignment, pandas journal,
pytest.

**Spec:** `docs/superpowers/specs/2026-08-28-translation-completeness-qa-design.md`

## Global Constraints

- Предварительно полностью выполнить планы foundation и semantic alignment.
- Статистика, длина и similarity только создают кандидата; автоматическая правка требует независимого LLM-подтверждения и успешной повторной проверки.
- Для пропуска высокая уверенность требует: согласованные независимые сигналы, LLM verdict `missing_content`, конкретные потерянные факты/действия, два якоря и успешный post-check.
- Один кандидат получает не более одной автоматической попытки доперевода.
- Repair request получает только потерянный фрагмент, соседний контекст, стиль и релевантные термины. Он не переписывает главу целиком.
- Глоссарий фильтруется по кандидату и контексту; полный глоссарий в prompt не отправлять.
- Бренды, имена и намеренные иностранные реплики не исправлять автоматически без явного `MUST_TRANSLATE` и подтверждения контекстом.
- Языковой QA исправляет объективные опечатки, грамматику, пунктуацию, повторы, подтверждённые кальки и мета-комментарии; субъективная стилистика остаётся предложением.
- LanguageTool и Slovnet запускаются только при включённых независимых флагах.
  Их результаты — кандидаты или защитные сигналы, но не разрешение на замену.
- LanguageTool использует только явно настроенный HTTP endpoint; скрытый
  публичный fallback, обязательная Python-обёртка и обязательная Java запрещены.
- Slovnet/Navec импортируются и загружаются лениво; отсутствие пакетов или
  моделей не блокирует QA и не меняет пользовательскую галочку.
- Несколько калек/дефектов одной главы: один diagnostic request, один batch correction request и один validation request; отдельные запросы на каждый дефект запрещены.
- Добавленное моделью содержание выявляется обратным alignment и не удаляется автоматически без LLM-подтверждения.
- Любая мутация проверяет fingerprint, anchors, HTML, IDs, отсутствие дубля, глоссарий и смысл; при сбое исходный файл остаётся неизменным.
- Откат не обращается к LLM.
- По умолчанию correction model совпадает с моделью перевода, но interface допускает отдельную настройку.
- Абсолютные профили длины остаются `0.92–1.20` для алфавитного → русский и `2.80–3.30` для CJK → русский; этот план их не дублирует в коде.

---

## Task 1: Добавить общий completion adapter и строгие JSON-схемы QA

**Files:**

- Create: `gemini_translator/qa/llm/__init__.py`
- Create: `gemini_translator/qa/llm/completion.py`
- Create: `gemini_translator/qa/llm/schemas.py`
- Create: `gemini_translator/qa/llm/json_response.py`
- Test: `tests/qa/test_qa_llm_schemas.py`
- Test: `tests/qa/test_qa_completion_adapter.py`

**Interfaces:**

```python
class QaCompletionClient(Protocol):
    async def complete_json(
        self,
        prompt: str,
        *,
        model: QaModelSelection,
        max_output_tokens: int,
        cancellation: CancellationToken,
    ) -> dict[str, object]:
        raise NotImplementedError

class ExistingHandlerCompletionClient:
    def __init__(self, handler_factory, retry_policy, event_sink):
        raise NotImplementedError
```

- [ ] **Step 1: Написать тесты строгого JSON parsing**

```python
# tests/qa/test_qa_llm_schemas.py
def test_json_fence_is_accepted_but_trailing_prose_is_rejected():
    assert parse_single_json_object('```json\n{"decision":"no_gap"}\n```') == {
        "decision": "no_gap"
    }
    with pytest.raises(QaResponseSchemaError):
        parse_single_json_object('{"decision":"no_gap"}\nЯ всё проверил.')


@pytest.mark.parametrize("payload", [
    {},
    {"decision": "missing_content", "confidence": 1.4},
    {"decision": "missing_content", "confidence": 0.99, "missing_facts": []},
    {"decision": "unknown", "confidence": 0.99, "missing_facts": ["fact"]},
])
def test_incomplete_or_invalid_verdict_never_authorizes_repair(payload):
    with pytest.raises(QaResponseSchemaError):
        OmissionVerdict.from_dict(payload)
```

- [ ] **Step 2: Написать adapter test с существующим handler**

Fake handler должен получить `use_stream=False`, `allow_incomplete=False`, выбранный model context и существующий cancellation/retry path. Тест проверяет, что API error сохраняет typed failure и не превращается в пустой JSON.

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие слоя**

Run: `python -m pytest tests/qa/test_qa_llm_schemas.py tests/qa/test_qa_completion_adapter.py -q`

Expected: FAIL with import errors.

- [ ] **Step 4: Реализовать single-object parser**

Допускается ровно один JSON object, возможно внутри одного markdown fence. После closing brace допустим только whitespace/fence. Не использовать поиск первого подходящего `{...}` среди произвольной прозы, потому что это может принять частичный ответ.

- [ ] **Step 5: Реализовать dataclass schemas**

Минимальные типы:

```python
@dataclass(frozen=True, slots=True)
class OmissionVerdict:
    decision: Literal["missing_content", "covered", "intentional_foreign", "ambiguous"]
    confidence: float
    source_unit_ids: tuple[str, ...]
    missing_facts: tuple[str, ...]
    explanation: str

@dataclass(frozen=True, slots=True)
class RepairProposal:
    candidate_id: str
    translated_fragment: str
    glossary_terms_used: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class LanguageIssue:
    issue_id: str
    category: Literal["typo", "grammar", "punctuation", "calque", "repetition", "meta_comment", "hallucinated_addition", "style_suggestion"]
    block_id: str
    original_text: str
    replacement_text: str | None
    objective: bool
    confidence: float
    explanation: str
```

`from_dict()` проверяет точный набор обязательных полей, enum, ranges, непустые IDs и соответствие candidate ID запросу. Неизвестные поля допускаются только в `metadata`, а не молча игнорируются.

- [ ] **Step 6: Реализовать adapter поверх API handler lifecycle**

Не копировать сетевую retry-логику. Adapter получает уже сконфигурированный handler factory и вызывает тот же `execute_api_call`/`call_api`, который использует перевод. Он подменяет только prompt и `model selection`, а события логирует с отдельным `qa_request_id`. Cancellation обязан закрыть текущий вызов тем же безопасным способом, что worker.

- [ ] **Step 7: Проверить слой**

Run: `python -m pytest tests/qa/test_qa_llm_schemas.py tests/qa/test_qa_completion_adapter.py tests/test_consistency_engine_handler_execution.py -q`

Expected: PASS; существующий consistency execution не ломается.

- [ ] **Step 8: Зафиксировать этап**

```bash
git add gemini_translator/qa/llm tests/qa/test_qa_llm_schemas.py tests/qa/test_qa_completion_adapter.py
git commit -m "feat: add strict QA completion contracts"
```

## Task 2: Реализовать LLM-подтверждение смысловых пропусков

**Files:**

- Create: `gemini_translator/qa/llm/omission_verifier.py`
- Create: `gemini_translator/config/translation_qa_prompts.json`
- Modify: `gemini_translator/qa/models.py`
- Test: `tests/qa/test_omission_verifier.py`
- Fixture: `tests/fixtures/qa/omission_verdicts.json`

**Interfaces:**

```python
class OmissionVerifier:
    async def verify(
        self,
        candidate: GapCandidate,
        context: CandidateContext,
        glossary: Sequence[RelevantGlossaryTerm],
        model: QaModelSelection,
        cancellation: CancellationToken,
    ) -> VerifiedCandidate:
        raise NotImplementedError
```

- [ ] **Step 1: Написать prompt boundary test**

```python
@pytest.mark.asyncio
async def test_verifier_sends_only_candidate_context_anchors_and_relevant_terms(fake_client):
    await verifier(fake_client).verify(candidate(), context(), glossary_fixture(), model(), token())
    prompt = fake_client.last_prompt
    assert "source-gap-text" in prompt
    assert "left-source-anchor" in prompt and "right-target-anchor" in prompt
    assert "武魂殿 → Зал Духов" in prompt
    assert "unrelated-glossary-secret" not in prompt
    assert "full-chapter-tail" not in prompt
```

- [ ] **Step 2: Написать decision tests**

Параметризовать fixture: потерянное отрицание, слово с изменением смысла, реплика, предложение, абзац, сцена, литературный paraphrase, объединённые абзацы, бренд, вывеска, иностранный диалог. Только конкретный `missing_content` с непустыми facts и confidence не ниже configured high threshold может дать `eligible_for_repair=True`.

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие verifier**

Run: `python -m pytest tests/qa/test_omission_verifier.py -q`

Expected: FAIL with import error.

- [ ] **Step 4: Создать версионированный prompt**

`translation_qa_prompts.json` содержит ключ `omission_verifier_v1`. Prompt явно запрещает оценивать длину как доказательство, требует различать covered/paraphrase/intentional foreign/missing, перечислить потерянные факты и вернуть только schema. Вставляемые пользовательские тексты оборачивать уникальными XML-like delimiters и объявлять данными, не инструкциями.

- [ ] **Step 5: Реализовать verifier и confidence rule**

`VerifiedCandidate.eligible_for_repair` истинно только при всех условиях:

```python
eligible = (
    candidate.repairable
    and verdict.decision == "missing_content"
    and verdict.confidence >= config.high_confidence
    and bool(verdict.missing_facts)
    and set(verdict.source_unit_ids) <= set(candidate.source_unit_ids)
    and candidate.has_independent_semantic_signal
    and not context.foreign_text_decision.blocks_auto_repair
)
```

- [ ] **Step 6: Проверить verifier**

Run: `python -m pytest tests/qa/test_omission_verifier.py tests/qa/test_foreign_text_filter.py -q`

Expected: PASS; invalid JSON/timeout/ambiguous остаются report-only.

- [ ] **Step 7: Зафиксировать этап**

```bash
git add gemini_translator/qa/llm/omission_verifier.py gemini_translator/config/translation_qa_prompts.json gemini_translator/qa/models.py tests/qa/test_omission_verifier.py tests/fixtures/qa/omission_verdicts.json
git commit -m "feat: verify semantic omissions with LLM"
```

## Task 3: Допереводить один подтверждённый фрагмент с релевантным глоссарием

**Files:**

- Create: `gemini_translator/qa/llm/omission_repairer.py`
- Create: `gemini_translator/qa/glossary_context.py`
- Modify: `gemini_translator/config/translation_qa_prompts.json`
- Test: `tests/qa/test_omission_repairer.py`
- Test: `tests/qa/test_glossary_context.py`

**Interfaces:**

```python
class GlossaryContextSelector:
    def select_for_candidate(
        self,
        glossary: Sequence[GlossaryTerm],
        source_text: str,
        source_context: str,
        max_terms: int,
    ) -> tuple[RelevantGlossaryTerm, ...]:
        raise NotImplementedError

class OmissionRepairer:
    async def propose(self, verified: VerifiedCandidate, request: RepairContext) -> RepairProposal:
        raise NotImplementedError
```

- [ ] **Step 1: Написать тест точного glossary subset**

Тестовый glossary содержит два термина из gap, один из соседнего контекста и 200 несвязанных. Selector обязан вернуть два термина gap первыми, затем контекстный в пределах `max_terms`; policies и canonical translation сохраняются.

- [ ] **Step 2: Написать тест запрета полной главы**

```python
@pytest.mark.asyncio
async def test_repair_prompt_requests_only_missing_fragment(fake_client):
    proposal = await repairer(fake_client).propose(verified_gap(), repair_context())
    prompt = fake_client.last_prompt
    assert "Переведи только потерянный фрагмент" in prompt
    assert "missing-source" in prompt
    assert "chapter-unrelated-tail" not in prompt
    assert proposal.candidate_id == verified_gap().candidate_id
```

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие repairer**

Run: `python -m pytest tests/qa/test_glossary_context.py tests/qa/test_omission_repairer.py -q`

Expected: FAIL with import errors.

- [ ] **Step 4: Реализовать glossary selector**

Совпадение искать по нормализованному source term в gap, затем окне контекста. Приоритет: `MUST_TRANSLATE`, точное совпадение, больше occurrences, длиннее term. `KEEP_ORIGINAL` тоже передавать, чтобы модель не переводила его по ошибке. Не выбирать термины только по статистической популярности вне контекста.

- [ ] **Step 5: Реализовать repair prompt и response validation**

Prompt содержит source gap, левый/правый контекст обеих сторон, anchors, список missing facts, стиль/POV и glossary subset. Response `RepairProposal` должен содержать только fragment; отклонять пустой ответ, HTML document, markdown fence, соседние anchor texts целиком, candidate mismatch и запрещённые glossary variants.

- [ ] **Step 6: Ограничить попытки**

`OmissionRepairer` сам не повторяет semantic attempt. Сетевые retries остаются в handler; после одного валидного, но не прошедшего post-check proposal candidate получает `attempted=True`, `decision="repair_rejected"`.

- [ ] **Step 7: Проверить repairer**

Run: `python -m pytest tests/qa/test_glossary_context.py tests/qa/test_omission_repairer.py -q`

Expected: PASS.

- [ ] **Step 8: Зафиксировать этап**

```bash
git add gemini_translator/qa/llm/omission_repairer.py gemini_translator/qa/glossary_context.py gemini_translator/config/translation_qa_prompts.json tests/qa/test_glossary_context.py tests/qa/test_omission_repairer.py
git commit -m "feat: propose glossary-aware omission repairs"
```

## Task 4: Реализовать структурную вставку, атомарное принятие и откат

**Files:**

- Create: `gemini_translator/qa/structural_repair.py`
- Create: `gemini_translator/qa/repair_store.py`
- Modify: `gemini_translator/utils/epub_json.py`
- Modify: `gemini_translator/utils/project_manager.py`
- Test: `tests/qa/test_structural_repair.py`
- Test: `tests/qa/test_repair_store.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class StructuralPatch:
    patch_id: str
    chapter_id: str
    expected_fingerprint: str
    left_anchor_unit_id: str
    right_anchor_unit_id: str
    parent_block_id: str
    translated_fragment: str

class StructuralRepairEngine:
    def preview(self, document_model: dict, patch: StructuralPatch) -> RepairPreview:
        raise NotImplementedError
    def validate(self, preview: RepairPreview, context: RepairValidationContext) -> RepairValidation:
        raise NotImplementedError
    def commit(self, preview: RepairPreview, chapter_path: Path, store: RepairStore) -> AppliedRepair:
        raise NotImplementedError
```

- [ ] **Step 1: Написать тесты fingerprint/anchors/idempotence**

```python
def test_stale_fingerprint_and_missing_anchor_never_modify_file(chapter_file, patch):
    before = chapter_file.read_bytes()
    with pytest.raises(StaleChapterError):
        engine().preview(load_model(chapter_file), replace(patch, expected_fingerprint="old"))
    assert chapter_file.read_bytes() == before


def test_same_patch_is_idempotent(chapter_file, patch, repair_store):
    first = apply_patch(engine(), chapter_file, patch, repair_store)
    second = apply_patch(engine(), chapter_file, patch, repair_store)
    assert second.status == "already_applied"
    assert chapter_file.read_text().count(patch.translated_fragment) == 1
```

- [ ] **Step 2: Написать тест rollback**

Применить patch, убедиться в изменении, вызвать `RepairStore.undo_chapter(chapter_id)`, проверить байтовое равенство исходному файлу. Повторный undo возвращает `nothing_to_undo`; он не вызывает LLM.

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие repair engine**

Run: `python -m pytest tests/qa/test_structural_repair.py tests/qa/test_repair_store.py -q`

Expected: FAIL with import errors.

- [ ] **Step 4: Добавить минимальную структурную операцию в `epub_json`**

Добавить функцию `insert_text_between_units(document_model, location, text) -> dict`, работающую на deep copy. Она находит parent block и inline text node по mapping сегментатора, разбивает только целевой text node при необходимости и вставляет новый text/element node в допустимой позиции. Существующие node IDs не менять; новому узлу присвоить детерминированный repair ID из patch ID. Не использовать `str.replace()` по HTML.

- [ ] **Step 5: Реализовать preview validations**

До commit проверить:

- SHA-256 текущего normalized document равен expected fingerprint;
- anchors существуют, упорядочены и принадлежат документу;
- fragment ещё не встречается между anchors и patch ID отсутствует в journal;
- `validate_html_structure()` проходит;
- `build_translation_payload()` сохраняет IDs неизменённых blocks/nodes;
- glossary validator не находит запрещённой замены;
- local overlap detector не находит дубля anchors/fragment.

- [ ] **Step 6: Реализовать атомарный commit и backup store**

Перед первым изменением главы сохранить исходные bytes под `translation_qa_backups/<chapter-id>/<session-id>.html` и metadata с hash. Записать preview во временный файл рядом с chapter, `fsync`, `os.replace`. Только после replace добавить journal action. Если journal save падает, восстановить backup и вернуть typed failure.

- [ ] **Step 7: Реализовать undo одной главы и всей сессии**

`undo_chapter()` восстанавливает последнюю непрерванную исходную версию, проверяет hash backup и записывает reversal entry. `undo_session()` обходит applied repairs в обратном порядке. При конфликте с ручной правкой не перезаписывать её: вернуть `ManualEditConflict` и предложить preview пользователю.

- [ ] **Step 8: Проверить structural path и текущую EPUB-валидацию**

Run: `python -m pytest tests/qa/test_structural_repair.py tests/qa/test_repair_store.py tests/test_epub_json_pipeline.py tests/test_validation_missing_paragraph_tags.py -q`

Expected: PASS.

- [ ] **Step 9: Зафиксировать этап**

```bash
git add gemini_translator/qa/structural_repair.py gemini_translator/qa/repair_store.py gemini_translator/utils/epub_json.py gemini_translator/utils/project_manager.py tests/qa/test_structural_repair.py tests/qa/test_repair_store.py
git commit -m "feat: apply and undo atomic structural repairs"
```

## Task 5: Добавить post-repair validation полного каскада

**Files:**

- Create: `gemini_translator/qa/repair_validator.py`
- Modify: `gemini_translator/qa/coverage_service.py`
- Modify: `gemini_translator/qa/structural_repair.py`
- Test: `tests/qa/test_repair_validator.py`

**Interfaces:**

```python
class RepairValidator:
    async def validate(
        self,
        before: ChapterSnapshot,
        preview: ChapterSnapshot,
        candidate: VerifiedCandidate,
        proposal: RepairProposal,
    ) -> RepairValidation:
        raise NotImplementedError
```

- [ ] **Step 1: Написать fail-closed tests**

Проверить отказы: gap сохранился; появился дубль; потерялся anchor; изменилась другая часть главы; нарушился glossary; HTML invalid; LLM post-check вернул invalid JSON; исходный файл успели вручную изменить.

- [ ] **Step 2: Написать success test**

Fixture с потерянным отрицанием: после preview повторный local alignment закрывает gap, diff ограничен одним patch region, glossary и HTML valid, LLM post-check подтверждает ровно перечисленные missing facts. Результат `accepted=True`.

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие validator**

Run: `python -m pytest tests/qa/test_repair_validator.py -q`

Expected: FAIL with import error.

- [ ] **Step 4: Реализовать последовательность без short-circuit сокрытия причин**

Выполнить все безопасные локальные checks и собрать причины. LLM post-check вызывать только если структурные checks прошли. Post-check prompt получает before/after только локального окна и обязан подтвердить: missing facts присутствуют, новый смысл не добавлен, anchors/context не переписаны.

- [ ] **Step 5: Проверить и зафиксировать**

Run: `python -m pytest tests/qa/test_repair_validator.py tests/qa/test_structural_repair.py -q`

Expected: PASS.

```bash
git add gemini_translator/qa/repair_validator.py gemini_translator/qa/coverage_service.py gemini_translator/qa/structural_repair.py tests/qa/test_repair_validator.py
git commit -m "feat: validate omission repairs before commit"
```

## Task 6: Добавить прямой HTTP-провайдер LanguageTool

**Files:**

- Create: `gemini_translator/qa/language_rules/__init__.py`
- Create: `gemini_translator/qa/language_rules/base.py`
- Create: `gemini_translator/qa/language_rules/language_tool.py`
- Create: `gemini_translator/qa/language_rules/cache.py`
- Test: `tests/qa/test_language_tool_provider.py`
- Test: `tests/qa/test_language_rule_cache.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class LanguageRuleRequest:
    units: tuple[SemanticUnit, ...]
    language: str
    disabled_rule_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class LanguageRuleIssue:
    rule_id: str
    category: str
    message: str
    replacements: tuple[str, ...]
    unit_id: str
    block_id: str
    unit_start: int
    unit_end: int

class LanguageRuleProvider(Protocol):
    async def check(
        self, request: LanguageRuleRequest
    ) -> tuple[LanguageRuleIssue, ...]:
        raise NotImplementedError

@dataclass(frozen=True, slots=True)
class LanguageRuleCacheKey:
    text_fingerprint: str
    language: str
    endpoint: str
    server_version: str
    disabled_rule_ids: tuple[str, ...]
    preprocessing_version: str

class LanguageRuleCache:
    def get(
        self, key: LanguageRuleCacheKey
    ) -> tuple[LanguageRuleIssue, ...] | None:
        raise NotImplementedError
    def put(
        self,
        key: LanguageRuleCacheKey,
        issues: Sequence[LanguageRuleIssue],
    ) -> None:
        raise NotImplementedError

@dataclass(frozen=True, slots=True)
class LanguageRuleResult:
    status: Literal["completed", "disabled", "unavailable"]
    issues: tuple[LanguageRuleIssue, ...]
    warnings: tuple[str, ...]

class LanguageRuleService:
    async def collect(
        self,
        units: Sequence[SemanticUnit],
        capabilities: QaCapabilitySettings,
    ) -> LanguageRuleResult:
        raise NotImplementedError
```

- [ ] **Step 1: Написать HTTP-контракт и тест отображения смещений**

```python
@pytest.mark.asyncio
async def test_language_tool_posts_sentences_and_maps_offsets_to_epub(fake_http):
    fake_http.respond_json({
        "software": {"version": "6.6"},
        "matches": [{
            "offset": 2,
            "length": 6,
            "message": "Возможная опечатка",
            "rule": {"id": "MORFOLOGIK_RULE_RU_RU", "category": {"id": "TYPOS"}},
            "replacements": [{"value": "привет"}],
        }],
    })
    provider = LanguageToolHttpProvider(
        endpoint="http://127.0.0.1:8081/v2",
        session_factory=fake_http.session_factory,
        timeout_seconds=10,
    )
    issues = await provider.check(rule_request("— Превет!", unit_id="u-1", block_id="b-1"))
    assert fake_http.last_request.url == "http://127.0.0.1:8081/v2/check"
    assert fake_http.last_request.form["language"] == "ru-RU"
    assert issues[0].unit_id == "u-1"
    assert issues[0].block_id == "b-1"
    assert (issues[0].unit_start, issues[0].unit_end) == (2, 8)
```

Добавить случаи: несколько предложений в одном пакете, offset на границе,
неизвестный rule, отключённое правило, невалидный JSON и offset вне текста.

- [ ] **Step 2: Написать тест отсутствия скрытого fallback**

```python
@pytest.mark.asyncio
async def test_timeout_never_sends_text_to_another_endpoint(fake_http):
    fake_http.raise_timeout()
    provider = LanguageToolHttpProvider(
        endpoint="https://configured.example/v2",
        session_factory=fake_http.session_factory,
        timeout_seconds=1,
    )
    with pytest.raises(LanguageRuleUnavailable):
        await provider.check(rule_request("Текст."))
    assert {request.host for request in fake_http.requests} == {
        "configured.example"
    }
```

- [ ] **Step 3: Написать cache и disabled-capability tests**

`LanguageRuleCache` должен повторно использовать неизменившийся результат по
отпечатку текста, языку, endpoint, версии сервера, disabled rules и версии
предобработки. Запись с истёкшим TTL или другой server version — miss.
`LanguageRuleService` при `language_tool_enabled=False` возвращает
`status="disabled"` и не создаёт HTTP provider.

- [ ] **Step 4: Запустить тесты и подтвердить отсутствие адаптера**

Run: `python -m pytest tests/qa/test_language_tool_provider.py tests/qa/test_language_rule_cache.py -q`

Expected: FAIL with import errors.

- [ ] **Step 5: Реализовать direct HTTP adapter и fail-closed parsing**

Нормализовать endpoint только добавлением `/v2/check` к явно заданному
пользователем адресу. Отправлять form fields `text`, `language=ru-RU` и
`disabledRules`; не использовать `language_tool_python` и не запускать Java.
Проверять HTTP status, типы полей, offsets и принадлежность каждого match одной
смысловой единице. Match, пересекающий две единицы или служебную разметку,
сохранять как report-only warning без replacement.

- [ ] **Step 6: Реализовать кэш и типизированную недоступность**

Кэш хранит только исходный ответ без пользовательских секретов, атомарно и с
ограниченным TTL. Timeout, DNS, 4xx/5xx и неверный JSON превращаются в
`LanguageRuleUnavailable` с безопасным сообщением. Сервис возвращает status,
warning и пустой tuple кандидатов, чтобы основная сессия продолжилась.

- [ ] **Step 7: Проверить адаптер**

Run: `python -m pytest tests/qa/test_language_tool_provider.py tests/qa/test_language_rule_cache.py tests/qa/test_semantic_units.py -q`

Expected: PASS, включая точное отображение Razdel offsets на EPUB.

- [ ] **Step 8: Зафиксировать этап**

```bash
git add gemini_translator/qa/language_rules tests/qa/test_language_tool_provider.py tests/qa/test_language_rule_cache.py
git commit -m "feat: add optional LanguageTool rule provider"
```

## Task 7: Добавить необязательный Slovnet/Navec-провайдер

**Files:**

- Create: `gemini_translator/qa/russian_nlp/__init__.py`
- Create: `gemini_translator/qa/russian_nlp/base.py`
- Create: `gemini_translator/qa/russian_nlp/slovnet_provider.py`
- Create: `gemini_translator/qa/russian_nlp/model_manager.py`
- Modify: `gemini_translator/qa/foreign_text_filter.py`
- Test: `tests/qa/test_slovnet_provider.py`
- Test: `tests/qa/test_slovnet_model_manager.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ProtectedEntity:
    unit_id: str
    block_id: str
    start: int
    end: int
    entity_type: Literal["PER", "ORG", "LOC"]
    text: str

@dataclass(frozen=True, slots=True)
class MorphologyCandidate:
    unit_id: str
    block_id: str
    start: int
    end: int
    category: str
    confidence: Literal["high", "medium", "ambiguous"]
    auto_fix_allowed: Literal[False] = False

@dataclass(frozen=True, slots=True)
class SyntaxCandidate:
    unit_id: str
    block_id: str
    token_ids: tuple[int, ...]
    category: str
    confidence: Literal["high", "medium", "ambiguous"]
    auto_fix_allowed: Literal[False] = False

@dataclass(frozen=True, slots=True)
class RussianNlpAnalysis:
    protected_entities: tuple[ProtectedEntity, ...]
    morphology_candidates: tuple[MorphologyCandidate, ...]
    syntax_candidates: tuple[SyntaxCandidate, ...]
    model_versions: Mapping[str, str]

class RussianNlpProvider(Protocol):
    def analyze(
        self, units: Sequence[SemanticUnit]
    ) -> RussianNlpAnalysis:
        raise NotImplementedError

@dataclass(frozen=True, slots=True)
class RussianNlpResult:
    status: Literal["completed", "disabled", "unavailable"]
    analysis: RussianNlpAnalysis | None
    warnings: tuple[str, ...]

class RussianNlpService:
    def analyze(
        self,
        units: Sequence[SemanticUnit],
        capabilities: QaCapabilitySettings,
    ) -> RussianNlpResult:
        raise NotImplementedError

@dataclass(frozen=True, slots=True)
class SlovnetModelStatus:
    state: Literal["missing", "installing", "ready", "invalid"]
    version: str | None
    installed_size_bytes: int | None
    reason: str = ""

class SlovnetModelManager:
    def status(self) -> SlovnetModelStatus:
        raise NotImplementedError
    async def install(
        self, progress, cancellation
    ) -> SlovnetModelStatus:
        raise NotImplementedError
    def uninstall(self) -> SlovnetModelStatus:
        raise NotImplementedError
```

- [ ] **Step 1: Написать optional-import и entity-protection tests**

```python
def test_import_and_disabled_mode_do_not_require_optional_packages(
    monkeypatch,
):
    block_imports(monkeypatch, {"slovnet", "navec"})
    import gemini_translator.qa.russian_nlp
    service = RussianNlpService(
        provider_factory=forbidden_provider_factory
    )
    result = service.analyze(
        units(),
        QaCapabilitySettings(slovnet_enabled=False),
    )
    assert result.status == "disabled"


def test_per_org_loc_entities_protect_exact_ranges(fake_slovnet_runtime):
    analysis = provider(fake_slovnet_runtime).analyze(
        units("Анна вошла в офис Apple в Москве.")
    )
    assert [(item.text, item.entity_type) for item in analysis.protected_entities] == [
        ("Анна", "PER"),
        ("Apple", "ORG"),
        ("Москве", "LOC"),
    ]
    assert ForeignTextFilter().classify(
        candidate_inside("Apple"), context_with(analysis)
    ).action == "report_only"
```

- [ ] **Step 2: Написать тесты морфологии, синтаксиса и доменных ограничений**

Fake runtime возвращает согласование, управление и dependency arcs. Провайдер
создаёт кандидатов с exact unit/block ranges, но каждый имеет
`auto_fix_allowed=False`. Необычная реплика художественного диалога и
неоднозначный разбор получают `confidence="ambiguous"` и никогда не
перекрывают защиту глоссария или решение LLM.

- [ ] **Step 3: Написать тесты менеджера моделей**

Манифест содержит version, URL, size и SHA-256 для Navec, NER, morphology и
syntax weights. Установка идёт во временную директорию, проверяет каждый hash и
делает atomic rename. Cancellation/неверный hash не затрагивает предыдущую
версию. Удаление разрешено только внутри точной model directory. Ни установка,
ни загрузка не происходят при импорте или простом включении галочки.

- [ ] **Step 4: Запустить тесты и подтвердить отсутствие провайдера**

Run: `python -m pytest tests/qa/test_slovnet_provider.py tests/qa/test_slovnet_model_manager.py -q`

Expected: FAIL with import errors.

- [ ] **Step 5: Реализовать низкоуровневый ленивый runtime**

Импортировать `navec.Navec`, `slovnet.NER`, `slovnet.Morph` и
`slovnet.Syntax` только внутри loader. Входные токены и offsets брать из
Razdel units; результаты переводить в доменные dataclass, не возвращать объекты
Natasha/Slovnet наружу. CPU batch size и число потоков брать из config и
ограничивать безопасным максимумом.

- [ ] **Step 6: Реализовать модельный manager и деградацию**

Если packages, manifest или weights отсутствуют, вернуть
`RussianNlpUnavailable` со status/reason. `RussianNlpService` преобразует
это в пустые дополнительные сигналы и warning, не выключая галочку в
настройках. NER protection передаётся в `ForeignTextFilter`; morphology и
syntax candidates передаются языковому reviewer только как evidence.

- [ ] **Step 7: Проверить optional path**

Run: `python -m pytest tests/qa/test_slovnet_provider.py tests/qa/test_slovnet_model_manager.py tests/qa/test_foreign_text_filter.py -q`

Expected: PASS при fake runtime present и absent.

- [ ] **Step 8: Зафиксировать этап**

```bash
git add gemini_translator/qa/russian_nlp gemini_translator/qa/foreign_text_filter.py tests/qa/test_slovnet_provider.py tests/qa/test_slovnet_model_manager.py
git commit -m "feat: add optional Slovnet Russian NLP signals"
```

## Task 8: Реализовать пакетный языковой QA для опечаток, калек и артефактов LLM

**Files:**

- Create: `gemini_translator/qa/llm/language_reviewer.py`
- Create: `gemini_translator/qa/llm/language_repairer.py`
- Create: `gemini_translator/qa/language_validation.py`
- Modify: `gemini_translator/config/translation_qa_prompts.json`
- Test: `tests/qa/test_language_qa.py`
- Fixture: `tests/fixtures/qa/language_quality_cases.json`

**Interfaces:**

```python
class LanguageQualityReviewer:
    async def diagnose_chapter(
        self,
        request: LanguageQaRequest,
        rule_candidates: Sequence[LanguageRuleIssue],
        nlp_analysis: RussianNlpAnalysis | None,
    ) -> tuple[LanguageIssue, ...]:
        raise NotImplementedError

class LanguageBatchRepairer:
    async def propose_batch(self, request: LanguageRepairRequest) -> LanguageRepairBatch:
        raise NotImplementedError

class LanguageRepairValidator:
    async def validate_batch(self, before: ChapterSnapshot, preview: ChapterSnapshot, batch: LanguageRepairBatch) -> LanguageBatchValidation:
        raise NotImplementedError
```

- [ ] **Step 1: Написать request-count test**

```python
@pytest.mark.asyncio
async def test_several_calques_use_three_chapter_level_requests(fake_client):
    result = await language_pipeline(fake_client).check_chapter(chapter_with_three_calques())
    assert [call.purpose for call in fake_client.calls] == [
        "language_diagnosis",
        "language_batch_correction",
        "language_batch_validation",
    ]
    assert len(result.issues) == 3
```

Отдельный тест: если diagnosis не находит auto-fixable issues, correction/validation не вызываются. Если глава превышает budget, deterministic chunker создаёт несколько трёхфазных пакетов с непересекающимися issue IDs; это единственное разрешённое деление.

Добавить тесты, что несколько LanguageTool matches и Slovnet candidates
включаются в тот же один `language_diagnosis`, но не создают собственных
LLM-запросов. Если LLM отклоняет правило LanguageTool либо считает синтаксис
Slovnet художественным, correction/validation не вызываются и текст не
изменяется.

- [ ] **Step 2: Добавить quality fixture**

Cases: опечатка, объективная грамматика, пунктуация, повтор слова, буквальная
калька, корректный необычный авторский оборот, мета-комментарий, несколько
калек, термин глоссария в падеже, сюжетный иностранный диалог, верное и ложное
правило LanguageTool, PER/ORG/LOC и необычный синтаксический кандидат Slovnet.
Для каждого задать expected category, auto-fix eligibility и неизменяемый
surrounding text.

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие language QA**

Run: `python -m pytest tests/qa/test_language_qa.py -q`

Expected: FAIL with import errors.

- [ ] **Step 4: Реализовать diagnosis schema и prompt**

Prompt просит локальные defects, запрещает свободную литературную редактуру и
требует `objective`, exact `block_id`, exact original span, optional
replacement, confidence, explanation. Отдельный раздел входа содержит
LanguageTool rule/message/replacements и Slovnet morphology/syntax evidence,
явно объявленные неподтверждёнными подсказками. Защищённые NER spans запрещено
менять без отдельного смыслового основания. `style_suggestion` всегда
`auto_fixable=False`. Калька auto-fixable только если модель объясняет
исходную конструкцию/смысл и предлагает локальную замену.

- [ ] **Step 5: Реализовать один batch correction**

Correction request содержит все eligible issues текущего chunk, соответствующие source fragments, target blocks, glossary subset и stable IDs. Ответ возвращает список replacements с теми же issue IDs; никаких полных глав. Все replacements сначала применяются к deep-copy payload и отклоняются при overlap или stale original span.

- [ ] **Step 6: Реализовать один batch validation**

Validation request получает source context и компактный before/after diff всех замен. Он должен подтвердить сохранение смысла, глоссария, авторского тона и разметки для каждого issue ID. Batch применяется атомарно: либо все replacements прошли, либо автоматически применяются только независимые подтверждённые non-overlapping subsets, явно записанные в journal. Не подтверждённые остаются suggestions.

- [ ] **Step 7: Проверить language pipeline**

Run: `python -m pytest tests/qa/test_language_qa.py tests/test_consistency_resilience.py tests/test_consistency_shared_glossary.py -q`

Expected: PASS; несколько калек не создают отдельный request на каждую.

- [ ] **Step 8: Зафиксировать этап**

```bash
git add gemini_translator/qa/llm/language_reviewer.py gemini_translator/qa/llm/language_repairer.py gemini_translator/qa/language_validation.py gemini_translator/config/translation_qa_prompts.json tests/qa/test_language_qa.py tests/fixtures/qa/language_quality_cases.json
git commit -m "feat: batch-check and repair translation language quality"
```

## Task 9: Выявлять добавленные моделью факты обратным выравниванием

**Files:**

- Create: `gemini_translator/qa/addition_detector.py`
- Modify: `gemini_translator/qa/llm/omission_verifier.py`
- Modify: `gemini_translator/config/translation_qa_prompts.json`
- Test: `tests/qa/test_addition_detector.py`

**Interfaces:**

```python
class AdditionDetector:
    async def detect(self, coverage: CoverageAnalysis, context: ChapterContext) -> tuple[AdditionCandidate, ...]:
        raise NotImplementedError
```

- [ ] **Step 1: Написать обратные alignment tests**

Fixture: добавленный факт, выдуманная реплика, допустимая поясняющая грамматическая частица, локальная перестановка, заголовок/служебный block. Только target-side gaps с двумя anchors и независимым сигналом идут LLM verifier.

- [ ] **Step 2: Запустить тест и подтвердить отсутствие detector**

Run: `python -m pytest tests/qa/test_addition_detector.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать reverse candidate mapping и отдельный verdict**

Добавить decision `hallucinated_addition/entailed/paraphrase/ambiguous`. Не переиспользовать `missing_content` с перевёрнутыми полями: schema должна явно называть target-only facts. Автоматическое удаление выключено в первой версии; high-confidence additions показываются и блокируют interchapter gate до повторной проверки или пользовательского решения.

- [ ] **Step 4: Проверить detector**

Run: `python -m pytest tests/qa/test_addition_detector.py tests/qa/test_semantic_alignment.py -q`

Expected: PASS.

- [ ] **Step 5: Зафиксировать этап**

```bash
git add gemini_translator/qa/addition_detector.py gemini_translator/qa/llm/omission_verifier.py gemini_translator/config/translation_qa_prompts.json tests/qa/test_addition_detector.py
git commit -m "feat: detect hallucinated translation additions"
```

## Task 10: Собрать единый TranslationQualityService

**Files:**

- Create: `gemini_translator/qa/service.py`
- Modify: `gemini_translator/qa/journal.py`
- Test: `tests/qa/test_translation_quality_service.py`
- Fixture: `tests/fixtures/qa/end_to_end_chapters.json`

**Interfaces:**

```python
class TranslationQualityService:
    async def check_chapter(
        self,
        request: ChapterQaRequest,
        options: QaOptions,
        cancellation: CancellationToken,
    ) -> ChapterQaResult:
        raise NotImplementedError

    async def undo_chapter(self, chapter_id: str) -> UndoResult:
        raise NotImplementedError
    async def undo_session(self, session_id: str) -> UndoResult:
        raise NotImplementedError
```

- [ ] **Step 1: Написать end-to-end service tests**

Проверить четыре потока: normal/no requests; confirmed omission/applied repair; ambiguous omission/report only; embedding failure/limited LLM mode. Отдельно проверить language QA после полноты, reverse additions, один auto attempt, journal entries и undo.

- [ ] **Step 2: Запустить тест и подтвердить отсутствие сервиса**

Run: `python -m pytest tests/qa/test_translation_quality_service.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать точный порядок каскада**

```python
coverage = await self.coverage.analyze(request.coverage_request)
rule_result = await self.language_rules.collect(
    request.target_units, options.capabilities
)
nlp_result = self.russian_nlp.analyze(
    request.target_units, options.capabilities
)
verified = await self._verify_candidates(coverage.accepted_candidates, request)
omission_repairs = await self._repair_verified_once(verified, request, options)
addition_candidates = await self.additions.detect(coverage, request.context)
language_result = await self.language.check_chapter(
    request,
    options,
    rule_candidates=rule_result.issues,
    nlp_analysis=nlp_result.analysis,
)
metrics = self.metrics.collect(request, coverage, omission_repairs, language_result)
self.journal.record_chapter_result(
    request=request,
    coverage=coverage,
    omission_repairs=omission_repairs,
    additions=addition_candidates,
    language_result=language_result,
    metrics=metrics,
)
return ChapterQaResult.from_pipeline(
    coverage=coverage,
    omission_repairs=omission_repairs,
    additions=addition_candidates,
    language_result=language_result,
    metrics=metrics,
)
```

Disabled providers не создавать и не вызывать. Недоступные включённые
провайдеры добавляют typed warning и status, но не блокируют остальные стадии.
`ChapterMetrics` получает counts LanguageTool issues, protected entities и
syntax candidates. LLM work ограничить semaphore и cancellation token. Journal
обновлять после каждого атомарного решения, чтобы перезапуск не повторил
применённую правку.

- [ ] **Step 4: Реализовать итоговый risk**

`high_unresolved` при подтверждённом пропуске без успешного repair, подтверждённом addition, structural failure после proposal или сильном glossary conflict. Weak statistical deviations и style suggestions не дают high risk. Результат содержит `may_continue_translation` для будущего gate, но сервис сам очередь не меняет.

- [ ] **Step 5: Проверить этап**

Run: `python -m pytest tests/qa -q`

Expected: PASS.

Run: `python tools/run_checks.py`

Expected: PASS.

- [ ] **Step 6: Зафиксировать этап**

```bash
git add gemini_translator/qa/service.py gemini_translator/qa/journal.py tests/qa/test_translation_quality_service.py tests/fixtures/qa/end_to_end_chapters.json
git commit -m "feat: orchestrate translation QA and safe repairs"
```

## Completion Gate

- [ ] Невалидный/неполный JSON никогда не разрешает изменение.
- [ ] Подтверждённый пропуск допереводится только локально и с релевантным глоссарием.
- [ ] Любая автоматическая вставка имеет два anchors, актуальный fingerprint и успешный post-check.
- [ ] Повторный запуск не дублирует вставку.
- [ ] Откат восстанавливает исходную главу без LLM.
- [ ] Опечатки, объективная грамматика, кальки и meta-comments диагностируются; субъективный стиль автоматически не правится.
- [ ] LanguageTool и Slovnet/Navec запускаются только своими галочками, не
  применяют правки самостоятельно и не блокируют QA при недоступности.
- [ ] PER/ORG/LOC от Slovnet защищены от ложной замены; новостной
  синтаксический сигнал без подтверждения остаётся предложением.
- [ ] Несколько дефектов главы используют пакет `diagnosis → correction → validation`, а не запрос на каждый дефект.
- [ ] Добавленные факты/реплики выявляются обратно и в первой версии не удаляются автоматически.
- [ ] Сбой QA не повреждает EPUB и возвращает typed result для продолжения основной сессии.
- [ ] Полный `python tools/run_checks.py` проходит.

Следующий план: `docs/superpowers/plans/2026-08-28-translation-qa-integration.md`.
