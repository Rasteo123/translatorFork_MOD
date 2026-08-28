# Translation Semantic Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Построить независимый от Qt семантический конвейер, который извлекает устойчивые смысловые единицы из EPUB с переключаемой русской сегментацией Razdel, пакетно получает и кэширует embeddings, выравнивает оригинал и перевод многие-ко-многим через NumPy и выдаёт локальные кандидаты на пропуск с двумя надёжными якорями.

**Architecture:** Структурные идентификаторы берутся из существующего
`epub_json`, а сегментация создаёт дочерние стабильные unit ID без изменения
EPUB. Для русского при включённом флаге используется точное смещение
`razdel.sentenize`, при выключенном — существующий упрощённый алгоритм; CJK
всегда использует отдельные правила. Провайдеры embeddings реализуют один
async-контракт и возвращают нормализованный `float32` массив. Кэш адресуется
хэшем текста, модели, размерности, выбранного сегментатора и версии
предобработки. Монотонный динамический алгоритм работает в ограниченной полосе
и возвращает typed alignment; фильтр допустимого иностранного текста
применяется до передачи кандидата LLM.

**Tech Stack:** Python 3.11, NumPy 2.x, Razdel `>=0.5,<1`,
aiohttp/requests из существующего проекта, hashlib, JSON/NPZ cache, pytest.

**Spec:** `docs/superpowers/specs/2026-08-28-translation-completeness-qa-design.md`

## Global Constraints

- Предварительно полностью выполнить `2026-08-28-translation-qa-foundation.md`.
- Сегментация не изменяет EPUB и не считает номер абзаца семантической идентичностью.
- Русская сегментация Razdel включена по умолчанию, но отключается независимо;
  при отключении используется резервный сегментатор. CJK-правила от галочки
  Razdel не зависят.
- Unit ID детерминированно выводится из `document_id`, стабильного `block_id`, диапазона и версии сегментации.
- Выравнивание обязано поддерживать `1→1`, `1→2`, `2→1`, `2→2`, ограниченные окна до трёх единиц и явный gap.
- Автоматически ремонтопригодный gap обязан иметь два надёжных упорядоченных якоря. Кандидаты у начала/конца главы без двух якорей остаются только в отчёте.
- Векторы имеют dtype `float32`, фиксированную размерность и единичную L2-норму; NaN/Inf и частичные пакеты отклоняются.
- Повторная проверка неизменившейся главы не выполняет повторный сетевой embedding-запрос.
- При недоступности embeddings возвращается явный ограниченный режим, а не скрытая эвристическая имитация.
- Бренд, имя, код, URL, вывеска и намеренная иностранная реплика сами по себе не являются пропуском.
- На этом этапе не выполняются LLM-подтверждение, перевод, вставка или изменение главы.
- Полная матрица всей книги запрещена; память ограничивается текущей главой.

---

## Task 1: Извлечь стабильные смысловые единицы из EPUB JSON

**Files:**

- Create: `gemini_translator/qa/semantic_units.py`
- Modify: `gemini_translator/qa/models.py`
- Test: `tests/qa/test_semantic_units.py`
- Fixture: `tests/fixtures/qa/segmentation_cases.json`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class SemanticUnit:
    unit_id: str
    document_id: str
    block_id: str
    ordinal: int
    text: str
    normalized_text: str
    source_start: int
    source_end: int
    kind: str

@dataclass(frozen=True, slots=True)
class SemanticWindow:
    unit_ids: tuple[str, ...]
    text: str

class SemanticUnitExtractor:
    PREPROCESSING_VERSION = "semantic-units-v1"
    def __init__(self, capabilities: QaCapabilitySettings):
        raise NotImplementedError
    def extract(self, payload: dict, language: str) -> tuple[SemanticUnit, ...]:
        raise NotImplementedError
    def windows(self, units: Sequence[SemanticUnit], max_size: int = 3) -> tuple[SemanticWindow, ...]:
        raise NotImplementedError
```

- [ ] **Step 1: Добавить fixture с художественными случаями**

`segmentation_cases.json` содержит реальные минимальные примеры: китайский текст с `。！？`, русский диалог с тире, инициалы/сокращения, многоточие, inline-теги внутри предложения, пустые/служебные блоки и два одинаковых предложения в разных блоках. Для каждого случая хранить ожидаемый текст единиц и parent `block_id`.

- [ ] **Step 2: Написать падающие тесты стабильности**

```python
# tests/qa/test_semantic_units.py
def test_units_keep_epub_block_identity_and_are_repeatable(epub_payload):
    extractor = SemanticUnitExtractor(QaCapabilitySettings())
    first = extractor.extract(epub_payload, language="zh")
    second = extractor.extract(epub_payload, language="zh-CN")

    assert first == second
    assert all(unit.block_id.startswith("b-") for unit in first)
    assert len({unit.unit_id for unit in first}) == len(first)


def test_inline_markup_does_not_split_one_sentence(inline_markup_payload):
    units = SemanticUnitExtractor(QaCapabilitySettings()).extract(
        inline_markup_payload, "ru"
    )
    assert [unit.text for unit in units] == ["Он сказал: «Я вернусь»." ]


def test_razdel_offsets_cover_dialogue_abbreviation_and_ellipsis(ru_payload):
    units = SemanticUnitExtractor(QaCapabilitySettings()).extract(
        ru_payload, "ru"
    )
    assert [unit.text for unit in units] == [
        "— Проф. Ли здесь?..",
        "— Да, — ответил он.",
    ]
    assert reconstruct_visible_ranges(ru_payload, units) == [
        unit.text for unit in units
    ]


def test_disabling_razdel_uses_legacy_segmenter_without_changing_epub(ru_payload):
    before = deepcopy(ru_payload)
    settings = QaCapabilitySettings(razdel_enabled=False)
    units = SemanticUnitExtractor(settings).extract(ru_payload, "ru")
    assert units
    assert ru_payload == before


def test_windows_are_local_and_never_cross_document_boundary(epub_payload):
    extractor = SemanticUnitExtractor(QaCapabilitySettings())
    units = extractor.extract(epub_payload, "zh")
    windows = extractor.windows(units, max_size=3)
    assert {len(window.unit_ids) for window in windows} <= {1, 2, 3}
    assert all(window.unit_ids == tuple(sorted(window.unit_ids, key=unit_order)) for window in windows)
```

- [ ] **Step 3: Запустить тест и подтвердить отсутствие реализации**

Run: `python -m pytest tests/qa/test_semantic_units.py -q`

Expected: FAIL with import error.

- [ ] **Step 4: Реализовать извлечение без HTML-мутаций**

Использовать `build_translation_payload()` как входной контракт. Рекурсивно
собрать видимый текст `text`-фрагментов каждого блока, сохранив отображение
символьного диапазона на block/inline IDs. Не включать comments, opaque и break
как текст. CJK сегментировать отдельными правилами. Для русского при
`razdel_enabled=True` вызывать `razdel.sentenize(visible_text)` и переносить
`start/stop` каждого `Substring` обратно на block/inline ranges. При
`False` вызывать изолированный `LegacyRussianSegmenter`; ни один путь не
мутирует payload.

Unit ID:

```python
identity = "\x1f".join(
    (PREPROCESSING_VERSION, segmenter_id, document_id, block_id, str(start), str(end), normalized_text)
)
unit_id = "u-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
```

- [ ] **Step 5: Проверить fixture и текущий EPUB pipeline**

Run: `python -m pytest tests/qa/test_semantic_units.py tests/test_epub_json_pipeline.py -q`

Expected: PASS; существующая сериализация EPUB не меняется.

- [ ] **Step 6: Зафиксировать этап**

```bash
git add gemini_translator/qa/semantic_units.py gemini_translator/qa/models.py tests/qa/test_semantic_units.py tests/fixtures/qa/segmentation_cases.json
git commit -m "feat: extract stable semantic units from epub"
```

## Task 2: Определить контракт embeddings, валидацию и fallback

**Files:**

- Create: `gemini_translator/qa/embeddings/__init__.py`
- Create: `gemini_translator/qa/embeddings/base.py`
- Create: `gemini_translator/qa/embeddings/factory.py`
- Test: `tests/qa/test_embedding_contract.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    texts: tuple[str, ...]
    language: str
    model: str
    dimensions: int | None = None
    task_type: str = "semantic-similarity"

@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: np.ndarray
    provider: str
    model: str
    dimensions: int

class EmbeddingProvider(Protocol):
    name: str
    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        raise NotImplementedError

def validate_and_normalize_batch(batch: EmbeddingBatch, expected_rows: int) -> EmbeddingBatch:
    raise NotImplementedError
```

- [ ] **Step 1: Написать контрактные тесты массива**

```python
# tests/qa/test_embedding_contract.py
@pytest.mark.asyncio
async def test_provider_returns_normalized_float32_matrix(fake_provider):
    batch = await fake_provider.embed(request_for("один", "два"))
    checked = validate_and_normalize_batch(batch, expected_rows=2)
    assert checked.vectors.dtype == np.float32
    assert checked.vectors.shape == (2, 4)
    np.testing.assert_allclose(np.linalg.norm(checked.vectors, axis=1), 1.0, atol=1e-5)


@pytest.mark.parametrize("vectors", [
    np.ones((1, 4)),
    np.array([[np.nan, 0, 0, 0], [1, 0, 0, 0]]),
    np.zeros((2, 4)),
])
def test_invalid_or_partial_batches_are_rejected(vectors):
    with pytest.raises(EmbeddingContractError):
        validate_and_normalize_batch(batch(vectors), expected_rows=2)
```

- [ ] **Step 2: Запустить тест и подтвердить отсутствие контракта**

Run: `python -m pytest tests/qa/test_embedding_contract.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать протокол и строгую нормализацию**

```python
vectors = np.asarray(batch.vectors, dtype=np.float32)
if vectors.ndim != 2 or vectors.shape[0] != expected_rows:
    raise EmbeddingContractError("Unexpected embedding batch shape")
if not np.isfinite(vectors).all():
    raise EmbeddingContractError("Embedding batch contains NaN or Inf")
norms = np.linalg.norm(vectors, axis=1, keepdims=True)
if np.any(norms <= np.finfo(np.float32).eps):
    raise EmbeddingContractError("Embedding batch contains a zero vector")
normalized = np.ascontiguousarray(vectors / norms, dtype=np.float32)
```

- [ ] **Step 4: Реализовать последовательный fallback**

`FallbackEmbeddingProvider` принимает непустой tuple провайдеров, пробует их по порядку и возвращает первый валидный пакет. Ошибки сохраняются в `EmbeddingUnavailableError.attempts`; если список исчерпан, вызывающий код может перейти в ограниченный режим. Не повторять запрос внутри fallback сверх существующей сетевой retry-политики конкретного адаптера.

- [ ] **Step 5: Проверить контракт**

Run: `python -m pytest tests/qa/test_embedding_contract.py -q`

Expected: PASS.

- [ ] **Step 6: Зафиксировать этап**

```bash
git add gemini_translator/qa/embeddings tests/qa/test_embedding_contract.py
git commit -m "feat: define embedding provider contract"
```

## Task 3: Реализовать Gemini и OpenAI-compatible embedding adapters

**Files:**

- Create: `gemini_translator/qa/embeddings/gemini.py`
- Create: `gemini_translator/qa/embeddings/openai_compatible.py`
- Modify: `gemini_translator/qa/embeddings/factory.py`
- Test: `tests/qa/test_gemini_embedding_provider.py`
- Test: `tests/qa/test_openai_embedding_provider.py`

**Interfaces:**

```python
class GeminiEmbeddingProvider:
    def __init__(self, api_key: str, session_factory, timeout_seconds: float):
        raise NotImplementedError

class OpenAICompatibleEmbeddingProvider:
    def __init__(self, base_url: str, api_key: str, session_factory, timeout_seconds: float):
        raise NotImplementedError
```

- [ ] **Step 1: Написать контрактный тест Gemini HTTP**

```python
@pytest.mark.asyncio
async def test_gemini_uses_batch_embed_contents_and_preserves_order(fake_http):
    fake_http.respond_json(
        {"embeddings": [{"values": [1.0, 0.0]}, {"values": [0.0, 2.0]}]}
    )
    provider = GeminiEmbeddingProvider("secret", fake_http.session_factory, 30)
    result = await provider.embed(request_for("源文", "перевод", model="models/gemini-embedding-001", dimensions=2))

    request = fake_http.last_request
    assert request.url.endswith("/v1beta/models/gemini-embedding-001:batchEmbedContents?key=secret")
    assert [item["content"]["parts"][0]["text"] for item in request.json["requests"]] == ["源文", "перевод"]
    assert result.vectors.shape == (2, 2)
```

- [ ] **Step 2: Написать контрактный тест OpenAI-compatible HTTP**

```python
@pytest.mark.asyncio
async def test_openai_compatible_posts_one_ordered_batch(fake_http):
    fake_http.respond_json({"data": [
        {"index": 1, "embedding": [0.0, 1.0]},
        {"index": 0, "embedding": [1.0, 0.0]},
    ]})
    provider = OpenAICompatibleEmbeddingProvider(
        "https://example.test/v1/chat/completions", "secret", fake_http.session_factory, 30
    )
    result = await provider.embed(request_for("a", "b", model="embed-model", dimensions=2))

    assert fake_http.last_request.url == "https://example.test/v1/embeddings"
    assert fake_http.last_request.json["input"] == ["a", "b"]
    np.testing.assert_array_equal(result.vectors.argmax(axis=1), [0, 1])
```

- [ ] **Step 3: Запустить тесты и подтвердить отсутствие адаптеров**

Run: `python -m pytest tests/qa/test_gemini_embedding_provider.py tests/qa/test_openai_embedding_provider.py -q`

Expected: FAIL with import errors.

- [ ] **Step 4: Реализовать Gemini adapter**

Использовать `batchEmbedContents`, передавать `outputDimensionality`, если указана. API key не должен попадать в exception text или debug log. Проверять HTTP status до разбора JSON, а затем пропускать ответ через `validate_and_normalize_batch()`.

- [ ] **Step 5: Реализовать OpenAI-compatible adapter**

Нормализовать configured URL до `/v1/embeddings`, независимо от того, был передан корень `/v1` или `/v1/chat/completions`. Восстанавливать порядок по обязательному полю `index`, отклонять дубликаты/пропуски и размерности разной длины. Передавать Bearer header только при непустом настоящем ключе, повторив правила `LocalApiHandler` для sentinel-ключей.

- [ ] **Step 6: Добавить factory без связи с моделью перевода**

```python
def create_embedding_provider(config: EmbeddingProviderConfig, session_factory):
    match config.kind:
        case "gemini":
            return GeminiEmbeddingProvider(
                api_key=config.api_key,
                session_factory=session_factory,
                timeout_seconds=config.timeout_seconds,
            )
        case "openai_compatible":
            return OpenAICompatibleEmbeddingProvider(
                base_url=config.base_url,
                api_key=config.api_key,
                session_factory=session_factory,
                timeout_seconds=config.timeout_seconds,
            )
        case "auto":
            return FallbackEmbeddingProvider(tuple(_available_online_providers(config, session_factory)))
        case _:
            raise UnsupportedEmbeddingProvider(config.kind)
```

Конфигурация embedding-модели не читает `worker.model_id` автоматически: пользователь может переводить Gemini Flash, а embeddings получать другой моделью/endpoint.

- [ ] **Step 7: Проверить ошибки и секреты**

Run: `python -m pytest tests/qa/test_embedding_contract.py tests/qa/test_gemini_embedding_provider.py tests/qa/test_openai_embedding_provider.py -q`

Expected: PASS; тесты 400/401/429/500 подтверждают типизированные ошибки и отсутствие API key в `str(error)`.

- [ ] **Step 8: Зафиксировать этап**

```bash
git add gemini_translator/qa/embeddings/gemini.py gemini_translator/qa/embeddings/openai_compatible.py gemini_translator/qa/embeddings/factory.py tests/qa/test_gemini_embedding_provider.py tests/qa/test_openai_embedding_provider.py
git commit -m "feat: add online embedding providers"
```

## Task 4: Добавить content-addressed embedding cache

**Files:**

- Create: `gemini_translator/qa/embeddings/cache.py`
- Modify: `gemini_translator/utils/project_manager.py`
- Test: `tests/qa/test_embedding_cache.py`

**Interfaces:**

```python
class EmbeddingCache:
    def get_many(self, keys: Sequence[EmbeddingCacheKey]) -> dict[EmbeddingCacheKey, np.ndarray]:
        raise NotImplementedError
    def put_many(self, values: Mapping[EmbeddingCacheKey, np.ndarray]) -> None:
        raise NotImplementedError
    def prune(self, max_bytes: int) -> int:
        raise NotImplementedError

class CachedEmbeddingProvider:
    async def embed(self, request: EmbeddingRequest) -> EmbeddingBatch:
        raise NotImplementedError
```

- [ ] **Step 1: Написать тесты hit/miss/invalidation**

```python
@pytest.mark.asyncio
async def test_second_identical_batch_does_not_call_network(tmp_path):
    upstream = CountingProvider()
    provider = CachedEmbeddingProvider(upstream, EmbeddingCache(tmp_path))
    request = request_for("один", "два", model="m1", dimensions=4)

    first = await provider.embed(request)
    second = await provider.embed(request)

    assert upstream.calls == 1
    np.testing.assert_array_equal(first.vectors, second.vectors)


@pytest.mark.asyncio
async def test_model_dimensions_text_and_preprocessing_version_invalidate_cache(tmp_path):
    upstream = CountingProvider()
    cache = EmbeddingCache(tmp_path)
    variants = [
        ("v1", request_for("текст", model="m1", dimensions=4)),
        ("v1", request_for("другой текст", model="m1", dimensions=4)),
        ("v1", request_for("текст", model="m2", dimensions=4)),
        ("v1", request_for("текст", model="m1", dimensions=8)),
        ("v2", request_for("текст", model="m1", dimensions=4)),
    ]
    for preprocessing_version, request in variants:
        provider = CachedEmbeddingProvider(
            upstream,
            cache,
            preprocessing_version=preprocessing_version,
        )
        await provider.embed(request)
    assert upstream.calls == 5
```

- [ ] **Step 2: Запустить тест и подтвердить отсутствие кэша**

Run: `python -m pytest tests/qa/test_embedding_cache.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать ключ и формат хранения**

Ключ SHA-256 включает: нормализованный текст, provider, model, dimensions, task_type, preprocessing version. Метаданные хранить в `translation_qa_embedding_cache/index.json`, векторы — в shard-файлах `.npz` по первым двум hex-символам ключа. Запись каждого shard и index атомарна; массивы загружать с `allow_pickle=False`.

- [ ] **Step 4: Реализовать частичный cache hit**

`CachedEmbeddingProvider` отправляет upstream только отсутствующие тексты, затем восстанавливает исходный порядок и один раз валидирует полный batch. Дубликаты текста внутри запроса должны использовать один cache key и один upstream input.

- [ ] **Step 5: Добавить project path и LRU pruning**

`ProjectManager.get_translation_qa_embedding_cache_dir()` возвращает удаляемую директорию проекта. `prune(max_bytes)` удаляет самые давно использованные записи до лимита, не затрагивая QA journal и backup главы.

- [ ] **Step 6: Проверить кэш**

Run: `python -m pytest tests/qa/test_embedding_cache.py tests/qa/test_embedding_contract.py -q`

Expected: PASS, включая corrupted shard как cache miss без потери проекта.

- [ ] **Step 7: Зафиксировать этап**

```bash
git add gemini_translator/qa/embeddings/cache.py gemini_translator/utils/project_manager.py tests/qa/test_embedding_cache.py
git commit -m "feat: cache semantic embeddings by content"
```

## Task 5: Реализовать NumPy similarity и монотонное many-to-many выравнивание

**Files:**

- Create: `gemini_translator/qa/alignment.py`
- Modify: `gemini_translator/qa/models.py`
- Test: `tests/qa/test_semantic_alignment.py`
- Fixture: `tests/fixtures/qa/alignment_cases.json`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class AlignmentSpan:
    source_unit_ids: tuple[str, ...]
    target_unit_ids: tuple[str, ...]
    similarity: float
    operation: str

@dataclass(frozen=True, slots=True)
class GapCandidate:
    candidate_id: str
    side: str
    source_unit_ids: tuple[str, ...]
    target_unit_ids: tuple[str, ...]
    left_anchor: AlignmentSpan | None
    right_anchor: AlignmentSpan | None
    repairable: bool
    signals: tuple[str, ...]

class MonotonicAligner:
    def align(self, source: EmbeddedUnits, target: EmbeddedUnits) -> AlignmentResult:
        raise NotImplementedError
```

- [ ] **Step 1: Добавить синтетический fixture и падающие тесты операций**

```python
@pytest.mark.parametrize(
    ("case_name", "operations"),
    [
        ("one_to_one", ("1:1",)),
        ("source_split_in_translation", ("1:2",)),
        ("source_merge_in_translation", ("2:1",)),
        ("two_to_two", ("2:2",)),
        ("local_reordering", ("2:2",)),
    ],
)
def test_alignment_operations(alignment_case, case_name, operations):
    result = MonotonicAligner(test_config()).align(*alignment_case(case_name))
    assert tuple(span.operation for span in result.spans) == operations


def test_middle_source_gap_has_two_anchors_and_edge_gap_is_not_repairable(alignment_case):
    middle = MonotonicAligner(test_config()).align(*alignment_case("missing_middle"))
    assert middle.gaps[0].left_anchor is not None
    assert middle.gaps[0].right_anchor is not None
    assert middle.gaps[0].repairable is True

    edge = MonotonicAligner(test_config()).align(*alignment_case("missing_first"))
    assert edge.gaps[0].repairable is False
```

- [ ] **Step 2: Запустить тест и подтвердить отсутствие выравнивателя**

Run: `python -m pytest tests/qa/test_semantic_alignment.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать cosine matrix и span-векторы**

```python
similarity = np.asarray(source.vectors @ target.vectors.T, dtype=np.float32)
similarity = np.clip(similarity, -1.0, 1.0)
```

Для окна 2/3 единиц строить единично нормализованный средний вектор, взвешенный числом видимых символов. Не конкатенировать векторы. Матрица должна жить только во время `align()`.

- [ ] **Step 4: Реализовать banded dynamic programming**

Состояние `(i, j)` хранит лучшую стоимость и предыдущую операцию. Разрешённые переходы: `(1,1)`, `(1,2)`, `(2,1)`, `(2,2)`, `(1,0)`, `(0,1)` и сконфигурированные окна до 3. Сравнивать только клетки внутри полосы вокруг ожидаемой диагонали; ширина вычисляется из разницы числа units и `max_drift_units`.

Стоимость:

```python
match_cost = 1.0 - cosine_similarity
size_penalty = config.merge_penalty * (source_span_size + target_span_size - 2)
gap_cost = config.gap_penalty + local_feature_penalty
```

Tie-breaker детерминирован: меньшая стоимость, затем меньший gap count, затем меньший размер окна, затем порядок операций из конфигурации.

- [ ] **Step 5: Построить gap candidates и якоря**

Объединять последовательные gap-операции на одной стороне. Надёжный anchor — соседний non-gap span с similarity не ниже `anchor_similarity`. `repairable=True` только когда оба anchors есть, принадлежат текущему document, не перекрываются и left предшествует right.

- [ ] **Step 6: Добавить лимиты памяти и детерминизм**

Тест на 1 000 × 1 100 units проверяет, что внутреннее число посещённых DP cells не превышает `max_cells` конфигурации и что два запуска возвращают одинаковые spans/candidates. Если предел превышен, вернуть `AlignmentCapacityError` и разрешить вызывающему коду перейти к chunked alignment, не создавать неограниченную матрицу.

- [ ] **Step 7: Проверить выравнивание**

Run: `python -m pytest tests/qa/test_semantic_alignment.py -q`

Expected: PASS for all fixture cases.

- [ ] **Step 8: Зафиксировать этап**

```bash
git add gemini_translator/qa/alignment.py gemini_translator/qa/models.py tests/qa/test_semantic_alignment.py tests/fixtures/qa/alignment_cases.json
git commit -m "feat: align translation semantics with numpy"
```

## Task 6: Отфильтровать допустимый иностранный текст

**Files:**

- Create: `gemini_translator/qa/foreign_text_filter.py`
- Modify: `gemini_translator/qa/glossary_audit.py`
- Modify: `gemini_translator/qa/models.py`
- Test: `tests/qa/test_foreign_text_filter.py`
- Fixture: `tests/fixtures/qa/foreign_text_cases.json`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ForeignTextDecision:
    category: str
    action: str
    confidence: str
    reasons: tuple[str, ...]

class ForeignTextFilter:
    def classify(self, candidate: GapCandidate, context: CandidateContext) -> ForeignTextDecision:
        raise NotImplementedError
```

- [ ] **Step 1: Добавить quality fixture и тест ложных срабатываний**

Fixture содержит: `Apple`, `iPhone 15 Pro`, имя латиницей, ISBN/артикул, URL/email, название организации, вывеску, цитату и сюжетный диалог на другом языке; отдельные cases — пропущенное отрицание, обычная реплика и термин `MUST_TRANSLATE`.

```python
@pytest.mark.parametrize("case_name", [
    "brand", "person_name", "device_model", "url", "sign", "foreign_dialogue",
    "glossary_keep_original", "glossary_either",
])
def test_allowed_foreign_content_never_becomes_auto_repair(case):
    decision = ForeignTextFilter().classify(*load_case(case_name))
    assert decision.action in {"exclude", "report_only"}
    assert decision.confidence != "high_repair"


def test_must_translate_policy_overrides_brand_heuristic():
    decision = ForeignTextFilter().classify(*load_case("explicit_must_translate"))
    assert decision.action == "send_to_llm_verifier"
```

- [ ] **Step 2: Запустить тест и подтвердить отсутствие фильтра**

Run: `python -m pytest tests/qa/test_foreign_text_filter.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать каскад правил**

Порядок: явная политика глоссария → URL/email/code/model patterns → кавычки/диалог/вывеска с контекстом → имена/бренды → неоднозначность. Regex сам никогда не создаёт `high_repair`; он только исключает, понижает либо отправляет LLM. Все решения сохраняют reasons.

- [ ] **Step 4: Связать с alignment result без мутаций**

Добавить чистую функцию `filter_gap_candidates(result, contexts, glossary) -> CandidateFilterResult`, возвращающую `accepted`, `excluded`, `report_only`. Исходный `AlignmentResult` не менять.

- [ ] **Step 5: Проверить семантический конвейер**

Run: `python -m pytest tests/qa/test_semantic_units.py tests/qa/test_embedding_contract.py tests/qa/test_gemini_embedding_provider.py tests/qa/test_openai_embedding_provider.py tests/qa/test_embedding_cache.py tests/qa/test_semantic_alignment.py tests/qa/test_foreign_text_filter.py -q`

Expected: PASS; ни один fixture допустимого иностранного текста не становится auto-repairable.

- [ ] **Step 6: Зафиксировать этап**

```bash
git add gemini_translator/qa/foreign_text_filter.py gemini_translator/qa/glossary_audit.py gemini_translator/qa/models.py tests/qa/test_foreign_text_filter.py tests/fixtures/qa/foreign_text_cases.json
git commit -m "feat: filter intentional foreign text from QA gaps"
```

## Task 7: Собрать read-only semantic coverage service

**Files:**

- Create: `gemini_translator/qa/coverage_service.py`
- Test: `tests/qa/test_coverage_service.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class CoverageAnalysis:
    mode: str
    source_units: tuple[SemanticUnit, ...]
    target_units: tuple[SemanticUnit, ...]
    alignment: AlignmentResult | None
    candidates: tuple[GapCandidate, ...]
    excluded: tuple[FilteredCandidate, ...]
    warnings: tuple[str, ...]

class SemanticCoverageService:
    async def analyze(self, request: CoverageRequest) -> CoverageAnalysis:
        raise NotImplementedError
```

- [ ] **Step 1: Написать orchestration tests**

Проверить: normal path использует cache/provider/aligner/filter в правильном порядке; cache hit не вызывает provider; provider outage возвращает `mode="statistics_llm_only"` и warning; отмена `asyncio.CancelledError` пробрасывается; сервис не пишет EPUB и не добавляет repair journal entry.

- [ ] **Step 2: Запустить тест и подтвердить отсутствие сервиса**

Run: `python -m pytest tests/qa/test_coverage_service.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать orchestration с dependency injection**

Сервис принимает extractor, provider, aligner, filter и metrics collector через конструктор. Не импортирует Qt, `TranslationEngine` или `UniversalWorker`. В limited mode вычисляет только дешёвые признаки и формирует candidate contexts для последующего LLM-этапа, но не симулирует similarity score.

- [ ] **Step 4: Проверить весь этап**

Run: `python -m pytest tests/qa -q`

Expected: PASS.

Run: `python tools/run_checks.py`

Expected: PASS.

- [ ] **Step 5: Зафиксировать этап**

```bash
git add gemini_translator/qa/coverage_service.py tests/qa/test_coverage_service.py
git commit -m "feat: add semantic translation coverage service"
```

## Completion Gate

- [ ] Одинаковый EPUB payload всегда даёт те же unit IDs.
- [ ] Включённый Razdel корректно сегментирует русский диалог, сокращения и
  многоточия с точными EPUB-смещениями; выключенный использует резервный путь.
- [ ] Абзацы можно объединять/разбивать без ложного gap за счёт many-to-many alignment.
- [ ] Настоящий пропуск в середине имеет левый и правый anchors; краевой пропуск не считается автоматически ремонтопригодным.
- [ ] Gemini и OpenAI-compatible providers соблюдают один контракт и не протекают секретами.
- [ ] Кэш исключает повторный сетевой запрос для неизменившейся главы.
- [ ] Бренды, имена, коды и иностранные диалоги не проходят в автоматический ремонт.
- [ ] Недоступность embeddings явно включает `statistics_llm_only` без падения основного приложения.
- [ ] Ни один код этого плана не изменяет перевод.
- [ ] Полный `python tools/run_checks.py` проходит.

Следующий план: `docs/superpowers/plans/2026-08-28-translation-qa-repair.md`.
