# Translation QA Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Поднять проект до Python 3.11, NumPy 2.x и pandas 3.x, затем создать общий фундамент для языковых профилей длины, метрик глав, устойчивой статистики книги, журнала терминов и постоянного JSON-журнала QA.

**Architecture:** Новый пакет `gemini_translator.qa` содержит чистые модели и вычисления без зависимости от Qt. NumPy используется для векторных и устойчивых численных операций, pandas — для межглавной агрегации. Существующая валидация и новый QA получают коэффициенты из одного реестра языковых профилей. Постоянным форматом остаётся версионированный JSON; DataFrame создаются только в памяти.

**Tech Stack:** Python 3.11, NumPy `>=2.0,<3`, pandas `>=3.0,<4`, pytest, Ruff, zstandard, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-08-28-translation-completeness-qa-design.md`

## Global Constraints

- Минимальная версия Python во всех CI- и сборочных контурах — `3.11`.
- Поддерживаемые ветки библиотек: pandas 3.x и NumPy 2.x; PyArrow не добавлять.
- `length_ratio = translated_chars / source_chars` после одинаковой нормализации видимого текста.
- Начальные абсолютные профили: алфавитный исходник → русский `0.92–1.20`; китайский/японский/корейский исходник → русский `2.80–3.30`.
- Коэффициент около `2.8` для китайского → русского является нормальным, а не подозрительным сам по себе.
- Статистика книги строится только внутри одной пары `source_language/target_language` и только после пяти подходящих повествовательных глав.
- Устойчивое отклонение: MAD; если MAD равен нулю — IQR. Средний риск начинается с `abs(robust_z) >= 2.5`, высокий — с `>= 3.5`.
- Отклонение только от книжной медианы внутри абсолютного языкового профиля не разрешает автоматическое исправление.
- Явное правило глоссария всегда приоритетнее статистического большинства.
- Этот план не добавляет embeddings, LLM-запросы, правку EPUB, Qt-интерфейс и NumPy-обработку аудио.
- Существующие пользовательские изменения в `gemini_translator/ui/dialogs/epub.py`, `tests/test_epub_build_manager_fill.py`, `.mcp.json` и `.superpowers/` не изменять и не включать в коммиты.

---

## Task 1: Поднять Python и численные зависимости

**Files:**

- Modify: `pyproject.toml`
- Modify: `.github/workflows/tests.yml`
- Modify: `.github/workflows/release.yml`
- Modify: `build_master.py`
- Modify: `requirements.txt`
- Modify: `requirements-translator-only.txt`
- Test: `tests/test_runtime_dependencies.py`

**Interfaces:**

- `build_master.ESSENTIAL_PACKAGES` обязан содержать `numpy` и `pandas`.
- `build_master.FORCED_VERSIONS` обязан выдавать `numpy>=2.0,<3` и `pandas>=3.0,<4`.
- Все поддерживаемые workflow используют Python 3.11.

- [ ] **Step 1: Написать падающий тест единого runtime-контракта**

```python
# tests/test_runtime_dependencies.py
from pathlib import Path

import build_master


ROOT = Path(__file__).resolve().parents[1]


def test_python_and_numeric_dependency_contract_is_consistent():
    assert build_master.FORCED_VERSIONS["numpy"] == ">=2.0,<3"
    assert build_master.FORCED_VERSIONS["pandas"] == ">=3.0,<4"
    assert {"numpy", "pandas"} <= set(build_master.ESSENTIAL_PACKAGES)

    for path in ("requirements.txt", "requirements-translator-only.txt"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "numpy>=2.0,<3" in text
        assert "pandas>=3.0,<4" in text

    assert 'target-version = "py311"' in (ROOT / "pyproject.toml").read_text()
    assert 'python-version: "3.11"' in (ROOT / ".github/workflows/tests.yml").read_text()
    assert 'python-version: "3.11"' in (ROOT / ".github/workflows/release.yml").read_text()
```

- [ ] **Step 2: Запустить тест и подтвердить ожидаемое падение**

Run: `python -m pytest tests/test_runtime_dependencies.py -q`

Expected: FAIL, потому что `FORCED_VERSIONS`/`ESSENTIAL_PACKAGES` ещё не содержат обе библиотеки и workflow/Ruff ещё указывают Python 3.10.

- [ ] **Step 3: Обновить единый источник зависимостей и окружения**

В `build_master.py` добавить:

```python
ESSENTIAL_PACKAGES.update({"numpy", "pandas"})
FORCED_VERSIONS.update(
    {
        "numpy": ">=2.0,<3",
        "pandas": ">=3.0,<4",
    }
)
```

В `requirements.txt` и `requirements-translator-only.txt` записать те же диапазоны. В `pyproject.toml` заменить Ruff target на `py311`; во всех jobs обоих workflow заменить `3.10` на `3.11`.

- [ ] **Step 4: Установить зависимости и проверить импорт**

Run: `python -m pip install -r requirements-dev.txt -r requirements.txt`

Expected: command exits 0.

Run: `python -c "import numpy, pandas; assert numpy.__version__.split('.')[0] == '2'; assert pandas.__version__.split('.')[0] == '3'"`

Expected: command exits 0 with no output.

- [ ] **Step 5: Запустить полный baseline до добавления QA-кода**

Run: `python tools/run_checks.py`

Expected: все существующие проверки проходят на Python 3.11. Если обнаружена несовместимость pandas/NumPy, исправить её в отдельном минимальном коммите до Task 2.

- [ ] **Step 6: Зафиксировать этап**

```bash
git add pyproject.toml .github/workflows/tests.yml .github/workflows/release.yml build_master.py requirements.txt requirements-translator-only.txt tests/test_runtime_dependencies.py
git commit -m "build: require python 3.11 pandas 3 and numpy 2"
```

## Task 2: Вынести языковые профили длины в единый реестр

**Files:**

- Create: `gemini_translator/qa/__init__.py`
- Create: `gemini_translator/qa/ratio_profiles.py`
- Modify: `gemini_translator/ui/dialogs/validation.py`
- Modify: `tests/test_validation_reanalysis.py`
- Test: `tests/qa/test_ratio_profiles.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class RatioProfile:
    key: str
    source_languages: frozenset[str]
    target_language: str
    minimum: float
    maximum: float

def get_ratio_profile(source_language: str, target_language: str) -> RatioProfile:
    raise NotImplementedError

def validation_ratio_presets() -> dict[str, tuple[float, float, str]]:
    raise NotImplementedError
```

- [ ] **Step 1: Написать тесты языковых пар и совместимости старого UI**

```python
# tests/qa/test_ratio_profiles.py
import pytest

from gemini_translator.qa.ratio_profiles import (
    get_ratio_profile,
    validation_ratio_presets,
)


@pytest.mark.parametrize("language", ["zh", "zh-CN", "ja", "ko"])
def test_cjk_to_russian_keeps_current_2_80_boundary(language):
    profile = get_ratio_profile(language, "ru")
    assert (profile.minimum, profile.maximum) == (2.80, 3.30)
    assert profile.contains(2.80)


def test_alphabetic_to_russian_keeps_current_boundaries():
    profile = get_ratio_profile("en", "ru")
    assert (profile.minimum, profile.maximum) == (0.92, 1.20)


def test_ui_presets_are_derived_from_registry():
    presets = validation_ratio_presets()
    assert presets["Иероглифический (象 -> A)"][:2] == (2.80, 3.30)
    assert presets["Алфавитный (A -> A)"][:2] == (0.92, 1.20)
```

- [ ] **Step 2: Запустить тест и подтвердить отсутствие модуля**

Run: `python -m pytest tests/qa/test_ratio_profiles.py -q`

Expected: FAIL with `ModuleNotFoundError: gemini_translator.qa`.

- [ ] **Step 3: Реализовать нормализацию языка и реестр**

```python
# gemini_translator/qa/ratio_profiles.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RatioProfile:
    key: str
    source_languages: frozenset[str]
    target_language: str
    minimum: float
    maximum: float

    def contains(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


CJK_TO_RUSSIAN = RatioProfile(
    key="cjk_to_ru",
    source_languages=frozenset({"zh", "ja", "ko"}),
    target_language="ru",
    minimum=2.80,
    maximum=3.30,
)
ALPHABETIC_TO_RUSSIAN = RatioProfile(
    key="alphabetic_to_ru",
    source_languages=frozenset(),
    target_language="ru",
    minimum=0.92,
    maximum=1.20,
)


def _base_language(value: str) -> str:
    return value.strip().lower().replace("_", "-").split("-", 1)[0]


def get_ratio_profile(source_language: str, target_language: str) -> RatioProfile:
    source = _base_language(source_language)
    target = _base_language(target_language)
    if target == "ru" and source in CJK_TO_RUSSIAN.source_languages:
        return CJK_TO_RUSSIAN
    if target == "ru":
        return ALPHABETIC_TO_RUSSIAN
    raise KeyError(f"Unsupported language pair: {source_language}->{target_language}")
```

`validation_ratio_presets()` должен строить только два абсолютных пресета из этих объектов, а существующие относительные пресеты `Медиана ±…` остаются в `TranslationValidatorPage`.

- [ ] **Step 4: Переключить старую валидацию на общий реестр**

В `TranslationValidatorPage.RATIO_PRESETS` использовать объединение `validation_ratio_presets()` с существующими относительными пунктами. Не менять `_get_current_ratio_bounds()` кроме источника абсолютных значений.

- [ ] **Step 5: Проверить регрессию**

Run: `python -m pytest tests/qa/test_ratio_profiles.py tests/test_validation_reanalysis.py -q`

Expected: PASS; существующий `test_cjk_ratio_preset_uses_2_80_minimum` продолжает проверять границу `2.80`.

- [ ] **Step 6: Зафиксировать этап**

```bash
git add gemini_translator/qa/__init__.py gemini_translator/qa/ratio_profiles.py gemini_translator/ui/dialogs/validation.py tests/qa/test_ratio_profiles.py tests/test_validation_reanalysis.py
git commit -m "refactor: centralize translation ratio profiles"
```

## Task 3: Добавить модели метрик и версионированный JSON-журнал

**Files:**

- Create: `gemini_translator/qa/models.py`
- Create: `gemini_translator/qa/journal.py`
- Modify: `gemini_translator/utils/project_manager.py`
- Test: `tests/qa/test_qa_journal.py`
- Test: `tests/qa/test_qa_models.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ChapterMetrics:
    chapter_id: str
    source_language: str
    target_language: str
    source_chars: int
    translated_chars: int

@dataclass(frozen=True, slots=True)
class QaJournalEntry:
    entry_id: str
    chapter_id: str
    decision: str

class QaJournal:
    @classmethod
    def load(cls, path: Path) -> "QaJournal":
        raise NotImplementedError
    def append(self, entry: QaJournalEntry) -> None:
        raise NotImplementedError
    def save(self, path: Path) -> None:
        raise NotImplementedError
    def to_frame(self) -> pandas.DataFrame:
        raise NotImplementedError
```

- [ ] **Step 1: Зафиксировать сериализуемую схему тестами**

```python
# tests/qa/test_qa_journal.py
from gemini_translator.qa.journal import QaJournal
from gemini_translator.qa.models import ChapterMetrics


def test_journal_round_trip_and_dataframe_schema(tmp_path):
    metrics = ChapterMetrics(
        chapter_id="chapter-1",
        source_language="zh",
        target_language="ru",
        content_kind="narrative",
        source_chars=1000,
        translated_chars=2800,
        source_units=40,
        aligned_units=39,
        possible_gaps=1,
        glossary_expected=5,
        glossary_matched=5,
        glossary_conflicts=0,
        untranslated_by_script={"han": 0},
        allowed_foreign_fragments=1,
        retries=0,
        input_tokens=100,
        output_tokens=300,
        duration_seconds=4.5,
        risk_level="low",
        applied_actions=(),
    )
    journal = QaJournal.empty(book_id="book-1")
    journal.upsert_metrics(metrics)
    path = tmp_path / "translation_qa.json"
    journal.save(path)

    restored = QaJournal.load(path)
    assert restored.metrics["chapter-1"].length_ratio == 2.8
    assert list(restored.metrics_frame().columns) == list(
        ChapterMetrics.dataframe_columns()
    )
```

- [ ] **Step 2: Запустить тест и подтвердить отсутствие моделей**

Run: `python -m pytest tests/qa/test_qa_models.py tests/qa/test_qa_journal.py -q`

Expected: FAIL with import errors.

- [ ] **Step 3: Реализовать неизменяемые модели**

`ChapterMetrics` должен вычислять `length_ratio` свойством с защитой от нулевого исходника и предоставлять `to_dict()/from_dict()/dataframe_columns()`. Добавить перечисления строковых значений для `risk_level`, `candidate_kind`, `decision` и `action`, но сериализовать их обычными строками для читаемости и миграций.

Минимальный JSON-контейнер:

```json
{
  "schema_version": 1,
  "book_id": "book-1",
  "updated_at": "2026-08-28T12:00:00+05:00",
  "metrics": [],
  "candidates": [],
  "repairs": [],
  "glossary_observations": []
}
```

- [ ] **Step 4: Реализовать атомарное сохранение журнала**

`QaJournal.save()` пишет UTF-8 JSON во временный файл рядом с целью, вызывает `flush()` и `os.fsync()`, затем `os.replace()`. `load()` принимает только поддерживаемую версию схемы и при повреждении поднимает `QaJournalCorruptedError`, не перезаписывая файл.

- [ ] **Step 5: Добавить пути проекта**

В `ProjectManager` добавить чистые методы:

```python
def get_translation_qa_journal_path(self) -> Path:
    return Path(self.project_dir) / "translation_qa.json"

def get_translation_qa_backup_dir(self) -> Path:
    return Path(self.project_dir) / "translation_qa_backups"
```

Не использовать существующий validation cache: журнал QA — пользовательская история решений, а не удаляемый кэш.

- [ ] **Step 6: Проверить сериализацию и отказоустойчивость**

Run: `python -m pytest tests/qa/test_qa_models.py tests/qa/test_qa_journal.py -q`

Expected: PASS, включая тест повреждённого JSON и сохранение исходного файла после ошибки.

- [ ] **Step 7: Зафиксировать этап**

```bash
git add gemini_translator/qa/models.py gemini_translator/qa/journal.py gemini_translator/utils/project_manager.py tests/qa/test_qa_models.py tests/qa/test_qa_journal.py
git commit -m "feat: add translation QA metrics journal"
```

## Task 4: Реализовать pandas-анализ книги и устойчивые пороги

**Files:**

- Create: `gemini_translator/qa/book_metrics.py`
- Test: `tests/qa/test_book_metrics.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class BookRatioBaseline:
    language_pair: tuple[str, str]
    sample_size: int
    median: float | None
    scale: float | None
    scale_method: str | None

class BookMetricsAnalyzer:
    def analyze(self, metrics: Iterable[ChapterMetrics]) -> pandas.DataFrame:
        raise NotImplementedError
    def ratio_baseline(self, frame: pandas.DataFrame, chapter_id: str) -> BookRatioBaseline:
        raise NotImplementedError
    def classify_ratio_risk(self, frame: pandas.DataFrame, chapter_id: str) -> RatioRisk:
        raise NotImplementedError
```

- [ ] **Step 1: Написать тесты изоляции языков и устойчивой статистики**

```python
# tests/qa/test_book_metrics.py
def test_chinese_ratio_2_8_is_normal_and_languages_are_not_mixed(metric_factory):
    rows = [metric_factory(f"zh-{i}", "zh", "ru", 1000, value) for i, value in enumerate((2800, 2900, 3000, 2850, 2950))]
    rows += [metric_factory("en-1", "en", "ru", 1000, 900)]
    frame = BookMetricsAnalyzer().analyze(rows)

    result = BookMetricsAnalyzer().classify_ratio_risk(frame, "zh-0")
    assert result.absolute_profile == "cjk_to_ru"
    assert result.within_absolute_profile is True
    assert result.baseline.sample_size == 5
    assert result.auto_fix_allowed is False


def test_iqr_is_used_when_mad_is_zero(metric_factory):
    ratios = (2.8, 2.8, 2.8, 2.8, 3.0, 3.2)
    frame = BookMetricsAnalyzer().analyze(
        metric_factory(str(i), "zh", "ru", 1000, int(ratio * 1000))
        for i, ratio in enumerate(ratios)
    )
    baseline = BookMetricsAnalyzer().ratio_baseline(frame, "5")
    assert baseline.scale_method == "iqr"
    assert baseline.scale > 0
```

Также проверить: четыре главы не формируют baseline; служебная/короткая глава исключается; `abs(robust_z)` классифицируется по 2.5/3.5; абсолютное нарушение профиля отмечается отдельно.

- [ ] **Step 2: Запустить тесты и подтвердить отсутствие анализатора**

Run: `python -m pytest tests/qa/test_book_metrics.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать DataFrame и eligibility mask**

```python
eligible = (
    frame["content_kind"].eq("narrative")
    & frame["source_chars"].ge(MIN_BASELINE_SOURCE_CHARS)
    & frame["risk_level"].ne("failed")
)
same_pair = (
    frame["source_language"].eq(row.source_language)
    & frame["target_language"].eq(row.target_language)
)
sample = frame.loc[eligible & same_pair, "length_ratio"].to_numpy(dtype=np.float64)
```

Использовать векторные операции pandas/NumPy; не применять `.iterrows()` в расчёте признаков. Нормализовать коды языков до базового ISO-кода до группировки.

- [ ] **Step 4: Реализовать MAD/IQR и двойную классификацию**

```python
median = float(np.median(sample))
mad = float(np.median(np.abs(sample - median)))
if mad > 0:
    scale = 1.4826 * mad
    method = "mad"
else:
    q1, q3 = np.quantile(sample, [0.25, 0.75])
    scale = float((q3 - q1) / 1.349)
    method = "iqr"
```

Если итоговый scale равен нулю, robust z должен быть `0.0` для равного медиане значения и бесконечным с правильным знаком для отличающегося. Результат содержит независимые поля `within_absolute_profile`, `robust_z`, `relative_risk`, `requires_deep_check`; `auto_fix_allowed` здесь всегда `False`.

- [ ] **Step 5: Проверить весь фундамент метрик**

Run: `python -m pytest tests/qa/test_ratio_profiles.py tests/qa/test_qa_models.py tests/qa/test_qa_journal.py tests/qa/test_book_metrics.py -q`

Expected: PASS.

- [ ] **Step 6: Зафиксировать этап**

```bash
git add gemini_translator/qa/book_metrics.py tests/qa/test_book_metrics.py
git commit -m "feat: add language-aware book metrics analysis"
```

## Task 5: Добавить журнал наблюдаемых переводов терминов

**Files:**

- Create: `gemini_translator/qa/glossary_audit.py`
- Modify: `gemini_translator/qa/models.py`
- Modify: `gemini_translator/qa/journal.py`
- Test: `tests/qa/test_glossary_audit.py`

**Interfaces:**

```python
class GlossaryPolicy(StrEnum):
    MUST_TRANSLATE = "must_translate"
    KEEP_ORIGINAL = "keep_original"
    EITHER = "either"

@dataclass(frozen=True, slots=True)
class GlossaryObservation:
    original_term: str
    observed_translation: str
    canonical_translation: str | None
    morphology_signature: tuple[str, ...]
    morphology_confidence: str
    chapter_id: str
    occurrences: int
    policy: GlossaryPolicy

class GlossaryAuditor:
    def observations_frame(self, observations: Iterable[GlossaryObservation]) -> pandas.DataFrame:
        raise NotImplementedError
    def conflicts(self, frame: pandas.DataFrame) -> pandas.DataFrame:
        raise NotImplementedError
```

- [ ] **Step 1: Написать тесты большинства, явного правила и словоформ**

```python
# tests/qa/test_glossary_audit.py
def test_statistical_minority_is_reported_but_explicit_glossary_wins():
    observations = repeated_observations("武魂殿", "Зал Духов", chapters=95)
    observations += repeated_observations("武魂殿", "Храм Боевых Душ", chapters=3)
    frame = GlossaryAuditor().observations_frame(observations)
    conflicts = GlossaryAuditor().conflicts(frame)

    conflict = conflicts.iloc[0]
    assert conflict.original_term == "武魂殿"
    assert conflict.dominant_translation == "Зал Духов"
    assert conflict.minority_translation == "Храм Боевых Душ"
    assert conflict.requires_llm_confirmation


def test_inflected_forms_of_canonical_russian_term_are_not_conflicts():
    auditor = GlossaryAuditor(morphology=FakeMorphology.russian_terms())
    frame = auditor.observations_frame([
        observation("武魂殿", "Зал Духов", chapter="1"),
        observation("武魂殿", "Зала Духов", chapter="2"),
        observation("武魂殿", "Залом Духов", chapter="3"),
    ])
    assert auditor.conflicts(frame).empty
    assert frame["morphology_family"].nunique() == 1


def test_lexically_different_translation_is_not_collapsed_as_inflection():
    auditor = GlossaryAuditor(morphology=FakeMorphology.russian_terms())
    frame = auditor.observations_frame([
        observation("武魂殿", "Зал Духов", chapter="1"),
        observation("武魂殿", "Храм Боевых Душ", chapter="2"),
    ])
    assert frame["morphology_family"].nunique() == 2
    assert auditor.conflicts(frame).iloc[0].minority_translation == "Храм Боевых Душ"


def test_ambiguous_or_unavailable_morphology_never_creates_high_conflict():
    auditor = GlossaryAuditor(morphology=None)
    frame = auditor.observations_frame([
        observation("武魂殿", "Зал Духов", chapter="1"),
        observation("武魂殿", "Зала Духов", chapter="2"),
    ])
    conflicts = auditor.conflicts(frame)
    assert conflicts.empty or not conflicts["high_confidence"].any()
```

Добавить отдельный тест, где явный canonical translation отличается от статистического большинства: конфликт определяется относительно canonical, а не большинства.

- [ ] **Step 2: Запустить тесты и подтвердить ожидаемое падение**

Run: `python -m pytest tests/qa/test_glossary_audit.py -q`

Expected: FAIL with import error.

- [ ] **Step 3: Реализовать агрегирование pandas**

DataFrame имеет столбцы `original_term`, `observed_translation`, `normalized_translation`, `morphology_signature`, `morphology_family`, `morphology_confidence`, `canonical_translation`, `chapter_id`, `occurrences`, `policy`. Агрегировать через `groupby(["original_term", "morphology_family"], observed=True)["occurrences"].sum()`, но сохранять все поверхностные формы отдельным списком для отчёта.

Для русского использовать общий ленивый `get_morph_analyzer()` из `gemini_translator.utils.morphology`, не создавать второй `MorphAnalyzer`. Сначала нормализовать Unicode NFKC, регистр, пробелы, кавычки и `ё/е`, затем сравнивать многословные варианты токен за токеном по множествам допустимых `normal_form`. «Зал Духов», «Зала Духов» и «Залом Духов» должны получить одну семью; «Храм Боевых Душ» — другую. Не выбрасывать исходный `observed_translation`.

Если анализатор недоступен, число значимых токенов различается или у токенов нет надёжного общего лемматического пути, ставить `morphology_confidence="ambiguous"`. Такой вариант можно показать как возможное расхождение, но нельзя помечать high-confidence конфликтом или автоматически исправлять только по pandas-статистике.

- [ ] **Step 4: Реализовать правила конфликтов**

- `KEEP_ORIGINAL`: оригинальное написание не конфликт.
- `EITHER`: оригинальное и canonical допустимы.
- `MUST_TRANSLATE`: оригинал в переводе является кандидатом на недоперевод.
- Явный `canonical_translation` — источник истины.
- При отсутствии canonical доминирующий вариант только создаёт `requires_llm_confirmation=True`; статистика не меняет текст.

- [ ] **Step 5: Сохранить наблюдения в общий журнал и проверить round-trip**

Run: `python -m pytest tests/qa/test_glossary_audit.py tests/qa/test_qa_journal.py tests/test_consistency_shared_glossary.py tests/test_term_frequency_tools.py -q`

Expected: PASS.

- [ ] **Step 6: Зафиксировать этап**

```bash
git add gemini_translator/qa/glossary_audit.py gemini_translator/qa/models.py gemini_translator/qa/journal.py tests/qa/test_glossary_audit.py
git commit -m "feat: track glossary translations across chapters"
```

## Task 6: Проверить фундамент и сборку

**Files:**

- Modify only if required by discovered packaging failure: `translatorFork_MOD.spec`
- Modify only if required by discovered packaging failure: `translatorFork-translator-only.spec`

- [ ] **Step 1: Запустить QA-набор**

Run: `python -m pytest tests/qa tests/test_validation_reanalysis.py tests/test_consistency_shared_glossary.py tests/test_term_frequency_tools.py -q`

Expected: PASS.

- [ ] **Step 2: Запустить полный набор проверок**

Run: `python tools/run_checks.py`

Expected: PASS with no Ruff violations.

- [ ] **Step 3: Проверить сборку двух вариантов приложения**

Run: `python build_master.py --help`

Expected: command exits 0 and documents the supported build selector. Затем выполнить документированные команды обычной и translator-only сборки. Обе сборки должны завершиться без missing-module warnings для `numpy`/`pandas`, а собранное приложение должно импортировать `gemini_translator.qa.book_metrics`.

Если PyInstaller не подхватывает библиотеки автоматически, добавить только официальные collection hooks (`collect_submodules`/`collect_data_files`) в соответствующие `.spec` и покрыть это повторной сборкой. Не добавлять ONNX Runtime на этом этапе.

- [ ] **Step 4: Зафиксировать только необходимые сборочные изменения**

```bash
git add translatorFork_MOD.spec translatorFork-translator-only.spec
git commit -m "build: package translation QA numeric runtime"
```

Пропустить этот commit, если `.spec` менять не пришлось.

## Completion Gate

- [ ] Python 3.11, NumPy 2.x и pandas 3.x подтверждены CI и полным тестовым набором.
- [ ] Старое окно валидации и новый анализ используют один реестр коэффициентов.
- [ ] Китайский → русский с коэффициентом `2.8` не помечается абсолютной аномалией.
- [ ] Данные разных языковых пар не смешиваются.
- [ ] Журнал переживает перезапуск и атомарно сохраняется в JSON.
- [ ] pandas-отчёт воспроизводим из JSON без PyArrow.
- [ ] Статистический конфликт термина не исправляет текст без явного правила или последующего LLM-подтверждения.
- [ ] Полный `python tools/run_checks.py` проходит.

Следующий план: `docs/superpowers/plans/2026-08-28-translation-semantic-alignment.md`.
