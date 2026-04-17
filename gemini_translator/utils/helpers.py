# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# Вспомогательные утилиты
# ---------------------------------------------------------------------------
# Этот файл содержит небольшие, но полезные классы и функции общего
# назначения, используемые в разных частях проекта.
# - format_size: форматирование размера файла.
# - TokenCounter: подсчет токенов и оценка стоимости.
# - ErrorAnalyzer: анализ ошибок API.
# ---------------------------------------------------------------------------

import math
import time
import re
# --- Добавляем глобальную проверку BeautifulSoup, так как она нужна в main.py ---
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    print("WARNING: beautifulsoup4 library not found. EPUB/HTML processing will be disabled.")
    print("Install it using: pip install beautifulsoup4")

def format_size(size_bytes):
    """Converts bytes to a human-readable format (KB, MB, GB)."""
    if size_bytes == 0:
        return "0 B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024))) if size_bytes > 0 else 0
    i = min(i, len(size_name) - 1)
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"


class TokenCounter:
    """Подсчет токенов для отслеживания использования API"""
    def __init__(self):
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.tokens_per_minute = []
        self.session_start_time = time.time()
        self.last_minute_check = time.time()
        self.chapters_stats = []

    def estimate_tokens(self, text):
        """
        Оценивает количество токенов в тексте, учитывая разные алфавиты.
        """
        if not text:
            return 0
        
        # Константы из конфига для более точного подсчета
        # Мы могли бы импортировать config, но чтобы избежать циклических зависимостей,
        # лучше продублировать эти простые значения здесь.
        CHARS_PER_ASCII_TOKEN = 4.0
        CHARS_PER_CYRILLIC_TOKEN = 2.2
        CHARS_PER_CJK_TOKEN = 1.5 # CJK символы "тяжелее"

        total_tokens = 0
        
        # Используем регулярные выражения для подсчета символов в каждой категории
        # Латиница, цифры, знаки препинания и спецсимволы JSON
        ascii_like_chars = len(re.findall(r'[a-zA-Z0-9\s.,:"{}\[\]_-]', text))
        # Кириллица
        cyrillic_chars = len(re.findall(r'[а-яА-ЯёЁ]', text))
        # CJK (Китайский, Японский, Корейский)
        cjk_chars = len(re.findall(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', text))

        # Считаем токены для каждой группы
        total_tokens += ascii_like_chars / CHARS_PER_ASCII_TOKEN
        total_tokens += cyrillic_chars / CHARS_PER_CYRILLIC_TOKEN
        total_tokens += cjk_chars / CHARS_PER_CJK_TOKEN

        # Другие символы (например, арабские, греческие и т.д.) считаем по средней норме
        other_chars = len(text) - (ascii_like_chars + cyrillic_chars + cjk_chars)
        total_tokens += other_chars / 2.5 # Среднее значение

        return int(total_tokens)

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

    def add_request(self, input_text, output_text=None):
        """Добавляет запрос в статистику"""
        current_time = time.time()
        input_tokens = self.estimate_tokens(input_text)
        output_tokens = self.estimate_tokens(output_text) if output_text else 0
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.tokens_per_minute.append((current_time, input_tokens, output_tokens))
        cutoff_time = current_time - 60
        self.tokens_per_minute = [(t, i, o) for t, i, o in self.tokens_per_minute if t > cutoff_time]
        return input_tokens, output_tokens

    def format_statistics(self):
        """Форматирует собранную статистику по токенам в читаемую строку."""
        duration_seconds = time.time() - self.session_start_time
        duration_minutes = duration_seconds / 60
        total_tokens = self.total_input_tokens + self.total_output_tokens
        avg_tpm = total_tokens / duration_minutes if duration_minutes > 0 else 0
        estimated_cost = self.estimate_cost(self.total_input_tokens, self.total_output_tokens)
        report = f"""
    ═══════════════════════════════════════════
    📊 СТАТИСТИКА ТОКЕНОВ СЕССИИ
    ═══════════════════════════════════════════
    • Продолжительность: {duration_minutes:.2f} мин.
    • Входящие токены: {self.total_input_tokens:,.0f}
    • Исходящие токены: {self.total_output_tokens:,.0f}
    • ВСЕГО ТОКЕНОВ: {total_tokens:,.0f}
    • Средняя скорость: {avg_tpm:,.0f} токенов/мин.
    • Примерная стоимость: ${estimated_cost:.4f}
    ═══════════════════════════════════════════"""
        return report.strip()


# --- НОВАЯ ВЕРСИЯ ФУНКЦИИ ---
def calculate_potential_output_size(html_content, is_cjk):
    """
    Вычисляет потенциальный размер ответа модели в УСЛОВНЫХ СИМВОЛАХ (где 4 символа ~ 1 токен),
    применяя разные коэффициенты к тегам и тексту.
    """
    try:
        if not BS4_AVAILABLE:
            # Если BeautifulSoup недоступен, используем старый, более простой метод
            multiplier = 10 if is_cjk else 3
            return len(html_content) * multiplier

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 1. Извлекаем только видимый пользователю текст
        # Используем ' ' в качестве разделителя, чтобы избежать склеивания слов
        visible_text = soup.get_text(separator=' ', strip=True)
        
        # 2. Считаем размеры компонентов
        len_html_total = len(html_content)
        len_text_original = len(visible_text)
        len_tags_and_scripts = len_html_total - len_text_original

        # 3. Определяем коэффициенты "разрастания" текста при переводе
        if is_cjk:
            # CJK -> Русский. Текст становится длиннее в 2.5-3 раза.
            # Пример: "你好世界" (4 симв) -> "Привет, мир" (11 симв)
            text_expansion_ratio = 2.8 
        else:
            # Английский -> Русский. Текст удлиняется в среднем на 20-30%.
            text_expansion_ratio = 1.25

        # 4. Рассчитываем потенциальный размер переведенного текста в символах
        potential_text_size_chars = len_text_original * text_expansion_ratio
        
        # 5. Оцениваем, сколько токенов съедят теги и переведенный текст
        # Теги и латиница ~4 символа/токен
        # Кириллица ~2.2 символа/токен
        
        # Мы хотим получить итоговый размер в "условных символах", где 1 токен = 4 символа.
        # Поэтому мы должны "утяжелить" кириллицу.
        # Коэффициент "утяжеления" = (символов/токен в латинице) / (символов/токен в кириллице)
        # 4 / 2.2 = ~1.8
        cyrillic_token_weight = 1.8 

        # Умножаем размер переведенного текста на этот вес
        weighted_text_size = potential_text_size_chars * cyrillic_token_weight
        
        # 6. Складываем "вес" тегов (он не меняется) и "вес" переведенного текста
        final_potential_size = len_tags_and_scripts + weighted_text_size
        
        return int(final_potential_size)

    except Exception as e:
        print(f"[WARN] Ошибка в calculate_potential_output_size: {e}. Используется упрощенный расчет.")
        # В случае любой ошибки парсинга, возвращаем безопасное, но более грубое значение
        multiplier = 10 if is_cjk else 3
        return len(html_content) * multiplier
        
        
def check_value(etalon, value, min_len=None) -> bool:
    """
    Универсальный валидатор.
    Проверяет, что 'value' имеет тот же тип, что и 'etalon'.
    Если min_len не задан, проверяет на "непустоту".
    Если min_len задан, проверяет, что длина value >= min_len.
    Безопасно обрабатывает типы, не имеющие длины.
    
    Примеры:
    check_value([], [1, 2]) -> True
    check_value([], [1, 2], min_len=3) -> False
    check_value([], []) -> False
    check_value("", "abc", min_len=3) -> True
    check_value(0, 5, min_len=1) -> False (т.к. у int нет len())
    """
    # 1. Жесткая проверка типа. Это наша главная защита.
    if not isinstance(value, type(etalon)):
        return False
    
    # 2. Если min_len не указан, используем простую проверку на "истинность".
    if min_len is None:
        return bool(value)
        
    # 3. Если min_len указан, используем безопасную проверку длины.
    try:
        return len(value) >= min_len
    except TypeError:
        # Этот блок сработает, если у 'value' нет метода __len__
        # (например, для чисел, None и т.д.)
        return False