# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------
# Этот файл содержит небольшие, но полезные классы и функции общего
# назначения, используемые в разных частях проекта.
# - TokenCounter: подсчет токенов и оценка стоимости.
# - ErrorAnalyzer: анализ ошибок API.
# ---------------------------------------------------------------------------

import math
import re
from typing import Any

from . import cjk_ranges

GEMINI_ASCII_CHARS_PER_TOKEN = 4.0
GEMINI_CYRILLIC_CHARS_PER_TOKEN = 2.2
GEMINI_CJK_CHARS_PER_TOKEN = 1.5
GEMINI_OTHER_CHARS_PER_TOKEN = 2.5

_ASCII_RUN_PATTERN = re.compile(r'[\x00-\x7f]+')
_CYRILLIC_RUN_PATTERN = re.compile(r'[\u0400-\u04ff]+')
# cluster-32 dedup: диапазон (Ext-A + Unified + кана + хангыль) теперь
# живёт в gemini_translator.utils.cjk_ranges.CJK_WITH_EXT_A_RUN_RE — то же
# самое множество символов, что и раньше, один источник истины. Важно: это
# RUN-вариант (с квантификатором "+"), а не CJK_WITH_EXT_A_CHAR_RE — см.
# докстринг у _count_chars ниже про то, зачем здесь нужны именно серии.


def as_list(value: Any, *, sort_sets: bool = False) -> list:
    """Приводит значение к списку.

    Каноническая реализация для cluster-50 (ранее была продублирована в
    ``benchmark/evaluator.py``, ``mcp/commands.py`` и
    ``ui/pages/benchmark_page.py``):

    - ``None`` -> ``[]``.
    - ``list`` возвращается как есть (без копирования).
    - ``tuple`` разворачивается в список своих элементов.
    - ``set``: по умолчанию (``sort_sets=False``) набор целиком оборачивается
      как единственный элемент (``[value]``) — так вели себя evaluator.py и
      benchmark_page.py, у которых не было отдельной ветки для set. Передайте
      ``sort_sets=True``, чтобы получить ``sorted(value)`` — так специально
      делал mcp/commands.py для детерминизма CLI-аргументов.
    - любой другой скаляр оборачивается в список из одного элемента.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        if sort_sets:
            return sorted(value)
        return [value]
    return [value]


def safe_int(
    value: Any,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Безопасно приводит значение к ``int`` с опциональным клампом.

    Каноническая реализация для finding-core-b/design/3 (ранее была
    продублирована минимум в 5 местах: ``consistency_engine.py``,
    ``worker_helpers/provider_orchestrator.py``, ``qa/handler_factory.py``,
    ``ui/dialogs/setup.py``, ``ranobelib/api_upload.py``):

    - ``value`` парсится через ``int()``; при ``TypeError``/``ValueError``
      подставляется ``default``.
    - ``minimum``/``maximum`` по умолчанию ``None`` — без них функция ничего
      не клампает (так вели себя копии в ``setup.py`` и
      ``ranobelib/api_upload.py``, где отрицательные и нулевые значения
      были осмысленны и проходили как есть).
    - Если ``minimum`` передан, результат клампится к нему — причём клампу
      подвергается и ``default``, если ``value`` не распарсилось (так вели
      себя копии в ``consistency_engine.py``, ``provider_orchestrator.py``
      и ``handler_factory.py``: они клампили итог уже ПОСЛЕ подстановки
      default, а не только успешно распарсенное значение).
    - ``maximum``, если передан, клампит результат сверху (было только в
      ``provider_orchestrator.py``).
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    if minimum is not None:
        parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _count_chars(pattern, text):
    # \u0421\u0447\u0438\u0442\u0430\u0435\u043c \u0434\u043b\u0438\u043d\u044b \u043d\u0435\u043f\u0440\u0435\u0440\u044b\u0432\u043d\u044b\u0445 \u0441\u0435\u0440\u0438\u0439 \u0432\u043c\u0435\u0441\u0442\u043e findall \u043f\u043e \u043e\u0434\u043d\u043e\u043c\u0443 \u0441\u0438\u043c\u0432\u043e\u043b\u0443:
    # findall \u043d\u0430 \u043f\u0440\u043e\u043c\u043f\u0442\u0435 \u0432 \u0441\u043e\u0442\u043d\u0438 \u041a\u0411 \u0430\u043b\u043b\u043e\u0446\u0438\u0440\u0443\u0435\u0442 \u0441\u043e\u0442\u043d\u0438 \u0442\u044b\u0441\u044f\u0447 \u0441\u0442\u0440\u043e\u043a-\u043e\u0434\u043d\u043e\u0441\u0438\u043c\u0432\u043e\u043b\u043e\u043a.
    return sum(m.end() - m.start() for m in pattern.finditer(text))


def estimate_gemini_tokens(text):
    """Estimate Gemini input tokens without an API round trip."""
    if not text:
        return 0

    text = str(text)
    ascii_like_chars = _count_chars(_ASCII_RUN_PATTERN, text)
    cyrillic_chars = _count_chars(_CYRILLIC_RUN_PATTERN, text)
    cjk_chars = _count_chars(cjk_ranges.CJK_WITH_EXT_A_RUN_RE, text)
    other_chars = max(0, len(text) - ascii_like_chars - cyrillic_chars - cjk_chars)

    total_tokens = (
        (ascii_like_chars / GEMINI_ASCII_CHARS_PER_TOKEN)
        + (cyrillic_chars / GEMINI_CYRILLIC_CHARS_PER_TOKEN)
        + (cjk_chars / GEMINI_CJK_CHARS_PER_TOKEN)
        + (other_chars / GEMINI_OTHER_CHARS_PER_TOKEN)
    )
    return max(1, int(math.ceil(total_tokens)))
# --- Добавляем глобальную проверку BeautifulSoup, так как она нужна в main.py ---
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("WARNING: beautifulsoup4 library not found. EPUB/HTML processing will be disabled.")
    print("Install it using: pip install beautifulsoup4")

def format_compact_number(value) -> str:
    """Компактно форматирует число: 1_234 -> '1.2K', 2_500_000 -> '2.5M'.

    Канонический хелпер для всех экранов, показывающих «примерное»
    количество токенов/символов рядом с прогрессом (K/M-суффикс).
    Нечисловой/отсутствующий вход трактуется как 0.
    """
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = 0
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def format_thousands(value) -> str:
    """Форматирует целое число с разделением тысяч пробелом: 1234567 -> '1 234 567'.

    Канонический хелпер для точных (не компактных) счётчиков символов/токенов.
    ``None`` форматируется как ``"0"``; прочие нечисловые значения, как и в
    исходной реализации, приводятся через ``int()`` (и могут бросить
    ``ValueError``/``TypeError`` при явно некорректном входе).
    """
    if value is None:
        return "0"
    return f"{int(value):,}".replace(",", " ")


class TokenUsageTrackerMixin:
    """Учёт токенов текущей сессии + подпись/тултип с компактными числами.

    Канонический хелпер для cluster pcluster-03: ``ConsistencyValidatorPage``
    (consistency_checker.py) и ``AITranslationPage``
    (untranslated_fixer_dialog.py) держали побайтово идентичные
    ``_reset_token_usage``/``_update_token_usage_label`` — единственным
    расхождением был текст тултипа ("текущий сеанс" vs "текущую
    AI-сессию"), вынесенный сюда в атрибут ``_token_usage_tooltip_scope``.

    По замечанию ревью (major, issue №1): само ядро «учёта токенов» —
    парсинг payload'а события, клампинг отрицательных значений и накопление
    трёх счётчиков — тоже было побайтово продублировано в телах
    ``ConsistencyValidatorPage.on_token_usage_updated`` и
    ``AITranslationPage._on_token_usage_updated``. Обоснование про разные
    источники события (Qt-сигнал engine.token_usage_updated напрямую у
    ConsistencyValidatorPage против общей EventBus-шины у AITranslationPage)
    относится к ФИЛЬТРУ владения сессией (``_is_owned_session_event``,
    вызывается ВЫШЕ по стеку — в ``_on_global_event`` fixer'а), а не к телу
    самого накопления. Поэтому ядро вынесено сюда как
    ``_accumulate_token_usage`` — каждый класс сохраняет свой тонкий
    Qt-slot/обработчик-делегат с собственной архитектурой подписки и
    собственным именем (``on_token_usage_updated`` /
    ``_on_token_usage_updated``), но тело обоих — один вызов
    ``self._accumulate_token_usage(payload)``.

    StatusBarWidget (issue №2, minor) — третья, самостоятельная копия
    учёта токенов сессии в проекте (парсинг+накопление в
    ``_on_token_usage_updated``, обнуление в ``reset()``, рендер в
    ``_format_token_usage_suffix``) — сюда сознательно НЕ подключена: у неё
    другой набор имён атрибутов (``input_tokens_used`` и т.п.), другой
    формат вывода (суффикс к прогресс-бару, а не подпись+тултип) и
    дополнительный ранний выход на полностью нулевом пакете
    (``if total<=0 and input<=0 and output<=0: return``), которого здесь
    нет. Насильное слияние потребовало бы либо адаптера имён атрибутов,
    либо флага поведения — сочтено того не стоящим при трёх полях расхождения.

    Класс-хозяин обязан перед использованием иметь атрибуты
    ``_token_input_total``, ``_token_output_total``, ``_token_total`` (int)
    и ``token_usage_label`` (любой объект с ``setText``/``setToolTip`` —
    утиная типизация, обычно ``QLabel``; жёсткой зависимости от PyQt в
    этом модуле нет).
    """

    _token_usage_tooltip_scope = "текущий сеанс"

    def _reset_token_usage(self):
        self._token_input_total = 0
        self._token_output_total = 0
        self._token_total = 0
        self._update_token_usage_label()

    def _accumulate_token_usage(self, payload: dict) -> None:
        """Канонический разбор+накопление payload'а события token_usage_updated.

        Общее тело для ``ConsistencyValidatorPage.on_token_usage_updated`` и
        ``AITranslationPage._on_token_usage_updated`` (pcluster-03, issue №1
        ревью) — оба метода лишь делегируют сюда после собственной,
        неизменной логики подписки/фильтрации на уровне вызывающего кода.
        """
        try:
            input_tokens = int((payload or {}).get('input_tokens', 0) or 0)
            output_tokens = int((payload or {}).get('output_tokens', 0) or 0)
            total_tokens = int((payload or {}).get('total_tokens', input_tokens + output_tokens) or 0)
        except (TypeError, ValueError):
            return
        self._token_input_total += max(0, input_tokens)
        self._token_output_total += max(0, output_tokens)
        self._token_total += max(0, total_tokens)
        self._update_token_usage_label()

    def _update_token_usage_label(self):
        total = format_compact_number(self._token_total)
        input_tokens = format_compact_number(self._token_input_total)
        output_tokens = format_compact_number(self._token_output_total)
        self.token_usage_label.setText(f"Токены: ~{total}")
        self.token_usage_label.setToolTip(
            f"Оценка токенов за {self._token_usage_tooltip_scope}: всего ~{total}, "
            f"вход ~{input_tokens}, выход ~{output_tokens}."
        )


class TokenCounter:
    """Подсчет токенов для отслеживания использования API"""
    def __init__(self):
        self.chapters_stats = []

    def estimate_tokens(self, text):
        """
        Оценивает количество токенов в тексте, учитывая разные алфавиты.
        """
        if not text:
            return 0
        return estimate_gemini_tokens(text)

    def estimate_cost(self, input_tokens, output_tokens, model_name="gemini-2.5-pro"):
        """Оценивает стоимость в USD"""
        pricing = {
            "gemini-2.5-pro": {"input": 0.00025, "output": 0.001},
            "gemini-2.5-flash": {"input": 0.000025, "output": 0.0001},
            "gemini-2.0-flash": {"input": 0.000015, "output": 0.00006}
        }
        model_key = "gemini-2.5-pro"
        for key in pricing.keys():
            if key in model_name.lower():
                model_key = key
                break
        rates = pricing[model_key]
        input_cost = (input_tokens / 1000) * rates["input"]
        output_cost = (output_tokens / 1000) * rates["output"]
        return input_cost + output_cost

    def add_chapter_stats(self, chapter_name, html_size, prompt_size, glossary_size, estimated_output):
        """Добавляет статистику для главы"""
        stats = {
            'chapter': chapter_name,
            'html_tokens': self.estimate_tokens(html_size) if isinstance(html_size, str) else html_size,
            'prompt_tokens': self.estimate_tokens(prompt_size) if isinstance(prompt_size, str) else prompt_size,
            'glossary_tokens': self.estimate_tokens(glossary_size) if isinstance(glossary_size, str) else glossary_size,
            'estimated_output_tokens': estimated_output,
            'total_input': 0,
            'estimated_cost': 0
        }
        stats['total_input'] = stats['html_tokens'] + stats['prompt_tokens'] + stats['glossary_tokens']
        stats['estimated_cost'] = self.estimate_cost(stats['total_input'], stats['estimated_output_tokens'])
        self.chapters_stats.append(stats)
        return stats

    def get_estimation_report(self, num_windows=1):
        """Генерирует отчет с оценкой токенов"""
        if not self.chapters_stats:
            return "Нет данных для оценки"

        total_input = sum(ch['total_input'] for ch in self.chapters_stats)
        total_output = sum(ch['estimated_output_tokens'] for ch in self.chapters_stats)
        total_cost = sum(ch['estimated_cost'] for ch in self.chapters_stats)

        if num_windows > 1:
            chapters_per_window = len(self.chapters_stats) / num_windows
            tokens_per_window = total_input / num_windows
            cost_per_window = total_cost / num_windows
            report = f"""
═══════════════════════════════════════════
📊 ОЦЕНКА ИСПОЛЬЗОВАНИЯ ТОКЕНОВ
═══════════════════════════════════════════

📚 АНАЛИЗ КОНТЕНТА:
• Всего глав: {len(self.chapters_stats)}
• Средний размер главы: {total_input // len(self.chapters_stats):,} токенов

📥 ВХОДЯЩИЕ ТОКЕНЫ:
• HTML контент: {sum(ch['html_tokens'] for ch in self.chapters_stats):,}
• Промпт (на главу): {self.chapters_stats[0]['prompt_tokens'] if self.chapters_stats else 0:,}
• Глоссарий (средний): {sum(ch['glossary_tokens'] for ch in self.chapters_stats) // max(1, len(self.chapters_stats)):,}
• ИТОГО входящих: {total_input:,}

📤 ИСХОДЯЩИЕ ТОКЕНЫ (оценка):
• Ожидаемый выход: {total_output:,}
• Коэффициент: ~1.1x от входа

💰 ОЦЕНКА СТОИМОСТИ:
• Общая стоимость: ${total_cost:.4f}
• На главу: ${total_cost / len(self.chapters_stats):.4f}

🖥️ ПАРАЛЛЕЛЬНЫЙ РЕЖИМ ({num_windows} окон):
• Глав на окно: ~{chapters_per_window:.0f}
• Токенов на окно: ~{tokens_per_window:,.0f}
• Стоимость на окно: ~${cost_per_window:.4f}

⚠️ ЛИМИТЫ (Gemini бесплатный тариф):
• TPM (токенов/мин): 2,000,000
• RPM (запросов/мин): зависит от модели
• Ваша нагрузка: ~{(total_input / 60):,.0f} токенов/мин

═══════════════════════════════════════════"""
        else:
            report = f"""
═══════════════════════════════════════════
📊 ОЦЕНКА ИСПОЛЬЗОВАНИЯ ТОКЕНОВ
═══════════════════════════════════════════

📚 АНАЛИЗ КОНТЕНТА:
• Всего глав: {len(self.chapters_stats)}
• Средний размер главы: {total_input // max(1, len(self.chapters_stats)):,} токенов

📥 ВХОДЯЩИЕ ТОКЕНЫ:
• HTML контент: {sum(ch['html_tokens'] for ch in self.chapters_stats):,}
• Промпт: {self.chapters_stats[0]['prompt_tokens'] if self.chapters_stats else 0:,} на главу
• Глоссарий: ~{sum(ch['glossary_tokens'] for ch in self.chapters_stats) // max(1, len(self.chapters_stats)):,} на главу
• ИТОГО: {total_input:,}

📤 ОЖИДАЕМЫЙ ВЫХОД: {total_output:,}

💰 ОЦЕНКА СТОИМОСТИ: ${total_cost:.4f}

═══════════════════════════════════════════"""
        return report


# calculate_potential_output_size: мёртвая копия удалена (cluster-dedup
# finding-utils-io_design_5-calculate-potential-output-siz). Каноническая
# реализация — gemini_translator.utils.epub_tools.calculate_potential_output_size
# (кортеж (total, tags_len), коэффициенты из api_config); именно её
# импортирует единственный вызывающий код (ui/dialogs/setup.py).
