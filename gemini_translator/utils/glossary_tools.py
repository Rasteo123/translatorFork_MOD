# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# Инструменты для работы с глоссарием
# ---------------------------------------------------------------------------

import os
import json
import re
import time
import zipfile
import threading
import random
from ..utils.language_tools import SmartGlossaryFilter, GlossaryRegexService
from ..api import config as api_config
# --- ДОБАВЛЯЕМ ИМПОРТ BEAUTIFULSOUP ---
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


# --- КОНЕЦ ДОБАВЛЕНИЯ ---
# --- НОВАЯ ФУНКЦИЯ ПЕРЕД КЛАССОМ ContextManager ---
def segment_cjk_in_html(html_content, chinese_processor):
    """
    Безопасно сегментирует CJK текст внутри HTML документа, игнорируя теги,
    скрипты, стили и не-иероглифический текст.
    """
    if not BS4_AVAILABLE or not html_content or not chinese_processor:
        return html_content

    soup = BeautifulSoup(html_content, 'html.parser')

    tags_to_ignore = ['style', 'script', 'pre', 'code']
    text_nodes = soup.find_all(string=True)

    for node in text_nodes:
        if node.parent.name in tags_to_ignore:
            continue

        original_text = str(node)
        # Проверяем наличие иероглифов, чтобы не обрабатывать чисто английский текст
        if not re.search(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', original_text):
            continue

        # chinese_processor.segment_text должен быть достаточно умным,
        # чтобы правильно обрабатывать смешанный текст (CJK + English)
        segmented_text = chinese_processor.segment_text(original_text)

        if segmented_text != original_text:
            node.replace_with(segmented_text)

    # Возвращаем измененный HTML
    return str(soup)
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---


class ContextManager:
    """Управляет долгосрочным контекстом: глоссарием, резюме и т.д."""
    def __init__(self, output_folder, use_jieba=True, segment_text=False):
        # Атрибуты по умолчанию
        self.output_folder = output_folder
        self.use_jieba_for_glossary = use_jieba
        self.segment_text_before_translation = segment_text

        # Атрибуты, которые ЗАДАЮТСЯ ИЗВНЕ
        self.global_glossary = {}
        self.version_map = {}
        self.use_dynamic_glossary = False
        self.similarity_map = None # <-- НОВЫЙ АТРИБУТ ДЛЯ КАРТЫ СВЯЗЕЙ

        # Константы
        self.min_term_length = 3
        self.min_term_length_cjk = 1

        try:
            from ..utils.language_tools import ChineseTextProcessor, GlossaryLogic
            self.chinese_processor = ChineseTextProcessor()
            self.glossary_logic = GlossaryLogic()
            # Сохраняем порог как атрибут для легкого доступа
        except ImportError:
            self.chinese_processor = None
            self.glossary_logic = None

        if segment_text and self.chinese_processor is None:
            print("ПРЕДУПРЕЖДЕНИЕ: Запрошена сегментация CJK, но ChineseTextProcessor не доступен.")
            self.segment_text_before_translation = False

    def update_settings(self, settings: dict):
        """
        Обновляет настройки. Строит карту схожести и Regex-сервис ОДИН РАЗ.
        """
        new_glossary = settings.get('full_glossary_data')
        glossary_changed = new_glossary is not None and new_glossary != self.global_glossary

        if glossary_changed:
            self.global_glossary = new_glossary
            # --- НОВОЕ: Перекомпилируем Regex-сервис при смене глоссария ---
            print("[ContextManager] Глоссарий изменился. Перекомпиляция Regex-сервиса для быстрого поиска…")
            self.regex_service = GlossaryRegexService(self.global_glossary)
            # --------------------------------------------------------------

        # Если глоссарий есть, но сервиса нет (первый запуск), создаем его
        if self.global_glossary and not hasattr(self, 'regex_service'):
             self.regex_service = GlossaryRegexService(self.global_glossary)


        self.use_dynamic_glossary = settings.get('dynamic_glossary', self.use_dynamic_glossary)
        self.fuzzy_threshold = settings.get('fuzzy_threshold', getattr(self, 'fuzzy_threshold', 100))
        self.use_jieba_for_glossary = settings.get('use_jieba', self.use_jieba_for_glossary)
        self.segment_text_before_translation = settings.get('segment_cjk_text', self.segment_text_before_translation)

        # Условие 1: Включен ли "нечеткий" режим ВООБЩЕ?
        is_fuzzy_mode_active = self.fuzzy_threshold < 100

        if is_fuzzy_mode_active:
            # Условие 2: Нужно ли ПЕРЕсоздавать карту?
            if (glossary_changed or self.similarity_map is None) and self.glossary_logic and self.global_glossary:
                print("[ContextManager] Нечеткий режим активен. Запрос на построение/обновление карты схожести…")
                glossary_list = [{'original': k, **v} for k, v in self.global_glossary.items()]

                # === КЛЮЧЕВОЙ МОМЕНТ ===
                # Мы передаем fuzzy_threshold из UI, чтобы оркестратор мог принять решение,
                # но сам построитель внутри будет использовать "широкую сеть".
                new_map = self.glossary_logic.build_similarity_map(glossary_list, self.fuzzy_threshold, self.use_jieba_for_glossary)
                self.similarity_map = new_map

                if self.similarity_map:
                    print(f"[ContextManager] Карта схожести успешно создана/обновлена. Связей: {len(self.similarity_map)}")

        if 'project_manager' in settings and settings['project_manager']:
            self.version_map = settings['project_manager'].load_version_map()
        else:
            self.version_map = {}


    def get_glossary_as_json_str(self):
        """Возвращает глоссарий как отформатированную JSON строку."""
        return json.dumps(self.global_glossary, ensure_ascii=False, indent=4)

    def set_glossary_from_json_str(self, json_str):
        """Обновляет глоссарий из JSON строки."""
        try:
            self.global_glossary = json.loads(json_str)
            return True
        except json.JSONDecodeError:
            return False

    def prepare_html_for_translation(self, html_content, log_callback=None):
        """
        Подготавливает HTML контент к переводу, выполняя сегментацию, если необходимо.
        """
        if not self.segment_text_before_translation:
            return html_content

        from ..utils.language_tools import LanguageDetector
        if not LanguageDetector.is_cjk_text(html_content):
            if log_callback: log_callback("[INFO] Сегментация CJK пропущена: в тексте не найдены иероглифы.")
            return html_content

        if not self.chinese_processor:
            if log_callback: log_callback("[WARN] Сегментация CJK пропущена: chinese_processor не инициализирован.")
            return html_content

        try:
            segmented_html = segment_cjk_in_html(html_content, self.chinese_processor)
            if segmented_html != html_content:
                if log_callback: log_callback("[SEGMENT] CJK текст в HTML безопасно сегментирован.")
            return segmented_html
        except Exception as e:
            if log_callback: log_callback(f"[ERROR] Ошибка при безопасной сегментации HTML: {e}")
            return html_content

    def format_glossary_for_prompt(self, text_content=None, current_chapters_list=None):
        """
        Формирует JSON-список терминов.
        Аргумент `current_chapters_list` (список путей к файлам в текущей задаче)
        используется для применения версионности.
        """
        # 1. Применяем версионирование к глобальному глоссарию ПЕРЕД фильтрацией
        effective_glossary_dict = self._apply_versioning(self.global_glossary, current_chapters_list)

        glossary_to_use = {}

        # 2. Фильтрация (используем уже effective_glossary_dict)
        if not self.use_dynamic_glossary:
            glossary_to_use = effective_glossary_dict
        elif text_content and effective_glossary_dict:
            fuzzy_threshold = getattr(self, 'fuzzy_threshold', 90)
            use_jieba = getattr(self, 'use_jieba_for_glossary', True)
            regex_service = getattr(self, 'regex_service', None)

            glossary_to_use = SmartGlossaryFilter().filter_glossary_for_text(
                full_glossary=effective_glossary_dict,
                text=text_content,
                use_jieba_for_glossary_search=use_jieba,
                fuzzy_threshold=fuzzy_threshold,
                similarity_map=self.similarity_map,
                regex_service=regex_service
            )

            original_size = len(self.global_glossary)
            filtered_size = len(glossary_to_use)
            self.last_filter_stats = {
                'original': original_size, 'filtered': filtered_size,
                'reduction_percent': (1 - filtered_size / original_size) * 100 if original_size > 0 else 0
            }
        else:
            self.last_filter_stats = None

        if not glossary_to_use:
            return ""

        # === НОВЫЙ БЛОК: Динамическая генерация пояснений ===
        explanation_block = ""
        all_explanations = api_config.internal_prompts().get("glossary_tag_explanation", {})

        if all_explanations:
            found_triggers_map = {}
            all_notes = [data.get('note', '') for data in glossary_to_use.values() if isinstance(data, dict)]

            for note in all_notes:
                if not note: continue

                for tag_key in all_explanations.keys():
                    if tag_key.startswith('_'): continue

                    for trigger in tag_key.split('/'):
                        # УЛУЧШЕННЫЙ REGEX: \b в начале, (?!\w) в конце
                        pattern = r'\b' + re.escape(trigger) + r'(?!\w)'
                        if re.search(pattern, note, re.IGNORECASE):
                            found_triggers_map.setdefault(tag_key, set()).add(trigger)

            if found_triggers_map:
                explanation_lines = []
                intro = all_explanations.get("_INTRO_TEXT_", "### Glossary Guide")
                explanation_lines.append(intro)

                for tag_key in sorted(found_triggers_map.keys()):
                    explanation = all_explanations[tag_key]
                    found_triggers = found_triggers_map[tag_key]
                    display_tag = "/".join(sorted(list(found_triggers)))
                    explanation_lines.append(f"- **{display_tag}:** {explanation}")

                explanation_block = "\n".join(explanation_lines) + "\n"

        # 2. Формируем JSON-блок глоссария (без изменений)
        lines = []
        lines.append('```json')
        lines.append('// DICTIONARY: {"s": "Source (Find)", "t": "Translation (Output)", "i": "Context (Silent info)"}')
        lines.append('[')

        # Получаем элементы и перемешиваем их через отдельный метод
        raw_items = glossary_to_use.items()
        items = self._reorder_glossary_items(raw_items)

        total_items = len(items)

        for index, (original, data) in enumerate(items):
            rus, note = "", ""
            if isinstance(data, dict):
                rus, note = data.get('rus', ''), data.get('note', '')
            elif isinstance(data, str):
                rus = data

            if not rus: continue

            entry = {"s": original, "t": rus}
            if note: entry["i"] = note

            json_str = json.dumps(entry, ensure_ascii=False)
            comma = "," if index < total_items - 1 else ""
            lines.append(f"  {json_str}{comma}")

        lines.append(']')
        lines.append('```')

        glossary_json_block = "\n" + "\n".join(lines) + "\n"

        # 3. Склеиваем пояснение и глоссарий
        return f"{explanation_block}{glossary_json_block}"

    def _reorder_glossary_items(self, items):
        """
        Перемешивает элементы глоссария перед генерацией JSON.
        Это помогает избежать ложных срабатываний safety-фильтров LLM,
        когда два безобидных слова, оказавшись рядом, образуют запрещенную фразу.
        """
        # Преобразуем dict_items в список
        items_list = list(items)
        # Случайно перемешиваем
        random.shuffle(items_list)
        return items_list

    def _apply_versioning(self, base_glossary, current_chapters_list):
        """
        Возвращает "эффективный" словарь глоссария для текущего списка глав.
        Реализует стратегию "Coexistence" с учетом частичного покрытия пакета.
        """
        if not self.version_map or not current_chapters_list:
            if isinstance(base_glossary, list):
                 return {e['original']: e for e in base_glossary if e.get('original')}
            return base_glossary

        if isinstance(base_glossary, list):
             effective = {e['original']: e.copy() for e in base_glossary if e.get('original')}
        else:
             effective = {k: v.copy() for k, v in base_glossary.items()}

        current_chapters_set = set(current_chapters_list)
        total_chapters_count = len(current_chapters_set)

        for term, rules in self.version_map.items():
            if term not in effective:
                effective[term] = {'original': term, 'rus': '', 'note': ''}

            base_entry = effective[term]

            applicable_overrides = []
            covered_chapters = set() # Отслеживаем, какие главы "закрыты" правилами

            for rule in rules:
                scope = set(rule.get('scope', []))
                # Ищем пересечение
                intersection = current_chapters_set.intersection(scope)

                if intersection:
                    applicable_overrides.append(rule.get('override', {}))
                    covered_chapters.update(intersection)

            if not applicable_overrides:
                continue

            # Проверка полноты покрытия
            is_fully_covered = (len(covered_chapters) == total_chapters_count)

            # СЦЕНАРИЙ 1: Полная и однозначная подмена
            # (Все главы пакета покрыты одним и тем же правилом)
            if is_fully_covered and len(applicable_overrides) == 1:
                override = applicable_overrides[0]
                if 'rus' in override and override['rus']:
                    base_entry['rus'] = override['rus']
                if 'note' in override:
                    base_entry['note'] = override['note']

            # СЦЕНАРИЙ 2: Смешанный режим
            # (Либо разные правила внутри пакета, либо часть глав осталась на "Базе")
            else:
                variants = []

                # Добавляем найденные оверрайды
                for ov in applicable_overrides:
                    eff_rus = ov.get('rus', base_entry.get('rus', '')).strip()
                    eff_note = ov.get('note', base_entry.get('note', '')).strip()
                    variants.append({'rus': eff_rus, 'note': eff_note})

                # ВАЖНО: Если пакет покрыт не полностью, добавляем БАЗОВЫЙ вариант
                if not is_fully_covered:
                    base_rus = base_entry.get('rus', '').strip()
                    base_note = base_entry.get('note', '').strip()
                    variants.append({'rus': base_rus, 'note': base_note})

                # Фильтрация дубликатов (set of tuples)
                unique_variants = []
                seen = set()
                for v in variants:
                    sig = (v['rus'], v['note'])
                    if sig not in seen:
                        seen.add(sig)
                        unique_variants.append(v)

                # Формирование строки
                unique_translations = {v['rus'] for v in unique_variants}
                formatted_parts = []

                if len(unique_translations) > 1:
                    # Разные переводы -> Синтаксис Омонимов: "Перевод (Note)"
                    for v in unique_variants:
                        part = v['rus']
                        if v['note']:
                            part += f" ({v['note']})"
                        formatted_parts.append(part)
                else:
                    # Одинаковые переводы -> Синтаксис Контекста: "Note"
                    # Перевод в base_entry можно оставить любым (они равны)
                    for v in unique_variants:
                        if v['note']:
                            formatted_parts.append(v['note'])

                if formatted_parts:
                    variants_str = " [VARIANTS]: " + " || ".join(formatted_parts)
                    if base_entry.get('note'):
                        base_entry['note'] += variants_str
                    else:
                        base_entry['note'] = variants_str.strip()

        return effective

    def format_glossary_for_ai_generation(self, full_context_glossary: list, text_content: str, include_notes: bool, settings: dict) -> tuple:
        """
        Фильтрует глоссарий по тексту и форматирует его для вставки в промпт.
        """
        found_terms_dict = {}
        if full_context_glossary and text_content:
            glossary_to_filter = {
                entry['original']: {'rus': entry.get('rus', ''), 'note': entry.get('note', '')}
                for entry in full_context_glossary if entry.get('original')
            }

            found_terms_dict = SmartGlossaryFilter().filter_glossary_for_text(
                full_glossary=glossary_to_filter,
                text=text_content,
                fuzzy_threshold=94,
                use_jieba_for_glossary_search=False,
                find_embedded_subterms=True
            )

        filtered_count = len(found_terms_dict)

        if not found_terms_dict:
            return "", 0

        if include_notes:
            merge_mode = settings.get('glossary_merge_mode', 'supplement')
            lines = []

            # 1. Шум для проверки пересечения букв/иероглифов
            noise_chars = set(' .,;:!?"\'()[]{}-–—_=/\\|<>`~@#$%^&*+0123456789\t\n\r')

            # 2. Критические символы для проверки структуры
            structural_chars = set('/()[]+<>')

            # 3. Регулярка для поиска CJK (Китай/Япония/Корея)
            cjk_pattern = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]')

            for original, data in found_terms_dict.items():
                entry_data = data.copy()

                if merge_mode == 'update':
                    rus_clean = str(entry_data.get('rus', '')).lower().strip()
                    orig_clean = str(original).lower().strip()
                    warnings = []

                    # --- ЭТАП 1: Проверка на полную идентичность ---
                    if rus_clean == orig_clean:
                        # Если совпало, это ОК только если это Латиница/Цифры (Plague Inc).
                        # Если внутри есть иероглифы — это НЕПЕРЕВЕДЕННЫЙ текст.
                        if cjk_pattern.search(orig_clean):
                            warnings.append("Term is untranslated (contains CJK characters).")
                        else:
                            # Идентично и безопасно (латиница) -> Добавляем без флагов и идем дальше
                            lines.append(f'  {json.dumps(original, ensure_ascii=False)}: {json.dumps(entry_data, ensure_ascii=False)}')
                            continue

                    # --- ЭТАП 2: Если строки РАЗНЫЕ (или идентичны, но с CJK) ---
                    else:
                        # А. Проверка на грязные артефакты (пересечение символов)
                        set_orig = set(orig_clean) - noise_chars
                        set_rus = set(rus_clean) - noise_chars

                        if not set_orig.isdisjoint(set_rus):
                            warnings.append("Artifacts/Untranslated chars detected.")

                        # Б. Проверка структуры (слэши, скобки)
                        missing_structure = []
                        for char in structural_chars:
                            in_orig = char in orig_clean
                            in_rus = char in rus_clean
                            if in_orig != in_rus:
                                missing_structure.append(char)

                        if missing_structure:
                            warnings.append(f"Structural mismatch (check symbols: {' '.join(missing_structure)}).")

                    # --- ФИНАЛ: Запись ворнингов ---
                    if warnings:
                        entry_data['__WARNING__'] = (
                            f"{' '.join(warnings)} "
                            "Fix if error, ignore if intentional."
                        )

                lines.append(f'  {json.dumps(original, ensure_ascii=False)}: {json.dumps(entry_data, ensure_ascii=False)}')

            body = ",\n".join(lines)
            return "{\n" + body + "\n}", filtered_count
        else:
            context_lines = []
            for original, data in found_terms_dict.items():
                rus = data.get('rus')
                if rus:
                    context_lines.append(f'* "{original}": "{rus}"')
            context_string = "\n".join(context_lines) if context_lines else ""
            return context_string, filtered_count


class TaskPreparer:
    """
    Формирует задачи на основе настроек из UI,
    оперируя только РЕАЛЬНЫМИ размерами входных данных.
    """
    def __init__(self, settings, real_chapter_sizes):
        self.settings = settings
        self.real_chapter_sizes = real_chapter_sizes # <--- Теперь это основной источник данных
        self.sequential_translation = settings.get('sequential_translation', False)
        self.use_batching = settings.get('use_batching', False)
        self.use_chunking = settings.get('chunking', False)
        self.task_input_size_limit = settings.get('task_size_limit', 30000)
        self.epub_path = settings.get('file_path')

    def prepare_tasks(self, chapter_list):
        if self.use_batching:
            return self._prepare_batch_tasks(chapter_list)
        else:
            return self._prepare_individual_and_chunked_tasks(chapter_list)

    def _prepare_batch_tasks(self, chapter_list):
        """
        Готовит ПЕЙЛОАДЫ для пакетной обработки.
        Автоматически разжалует пакеты из 1 главы в обычные задачи 'epub'.
        """
        final_payloads = []
        current_batch_chapters, current_batch_size = [], 0

        # Внутренняя функция для умного добавления: если глава одна — это не пакет
        def _flush_current_batch():
            if not current_batch_chapters: return
            if len(current_batch_chapters) == 1:
                final_payloads.append(('epub', self.epub_path, current_batch_chapters[0]))
            else:
                final_payloads.append(('epub_batch', self.epub_path, tuple(current_batch_chapters)))

        for chapter_file in chapter_list:
            input_size = self.real_chapter_sizes.get(chapter_file, 0)

            # Сценарий 1: Глава сама по себе больше лимита
            if input_size > self.task_input_size_limit:
                if current_batch_chapters:
                    _flush_current_batch()

                # Слишком большая глава всегда идет отдельно как 'epub'
                final_payloads.append(('epub', self.epub_path, chapter_file))
                current_batch_chapters, current_batch_size = [], 0
                continue

            # Сценарий 2: Добавление главы превысит лимит пакета
            if current_batch_size + input_size > self.task_input_size_limit and current_batch_chapters:
                _flush_current_batch()
                # Начинаем новый пакет с текущей главы
                current_batch_chapters, current_batch_size = [chapter_file], input_size
            else:
                # Сценарий 3: Место есть, добавляем в текущий пакет
                current_batch_chapters.append(chapter_file)
                current_batch_size += input_size

        # Сбрасываем остаток после цикла
        if current_batch_chapters:
            _flush_current_batch()

        return final_payloads

    def _prepare_individual_and_chunked_tasks(self, chapter_list):
        """Готовит ПЕЙЛОАДЫ для индивидуальных или разделенных задач."""
        from ..utils.text import split_text_into_chunks
        from ..api import config as api_config

        final_payloads = []
        with open(self.epub_path, 'rb') as epub_file, zipfile.ZipFile(epub_file, "r") as epub_zip:
            for chapter_file in chapter_list:
                real_size = self.real_chapter_sizes.get(chapter_file, 0)

                if not self.use_chunking or real_size <= self.task_input_size_limit:
                    final_payloads.append(('epub', self.epub_path, chapter_file))
                    continue

                try:
                    content = epub_zip.read(chapter_file).decode("utf-8", "ignore")

                    prefix, body_content, suffix = "", content, ""
                    content_lower = content.lower()
                    start_body_tag_pos, end_body_tag_pos = content_lower.find('<body'), content_lower.rfind('</body>')
                    if start_body_tag_pos != -1 and end_body_tag_pos != -1:
                        start_body_content_pos = content_lower.find('>', start_body_tag_pos) + 1
                        prefix, body_content, suffix = content[:start_body_content_pos], content[start_body_content_pos:end_body_tag_pos], content[end_body_tag_pos:]

                    chunks = split_text_into_chunks(body_content, self.task_input_size_limit,
                                                    api_config.chunk_search_window(), api_config.min_chunk_size())

                    for i, chunk_content in enumerate(chunks):
                        final_payloads.append(('epub_chunk', self.epub_path, chapter_file,
                                            chunk_content, i, len(chunks), prefix, suffix))
                except Exception as e:
                    print(f"[ERROR] Критическая ошибка при чанкинге главы {chapter_file}: {e}")
                    final_payloads.append(('epub', self.epub_path, chapter_file))
        return final_payloads


class GlossaryAggregator:
    """
    Умный агрегатор глоссария, использующий метаданные (таймстампы)
    для интеллектуального слияния.
    """
    def __init__(self, initial_glossary: list, merge_mode='supplement'):
        self.merge_mode = merge_mode
        self._initial_glossary = initial_glossary

    def merge(self, new_terms_from_db: list) -> list:
        """
        Сливает начальный глоссарий (из настроек) с уже дедуплицированным списком из БД.

        new_terms_from_db: Список словарей, который УЖЕ прошел очистку в fetch_and_clean_glossary.
                           Здесь нет дубликатов внутри самого списка.
        """
        if self.merge_mode == 'accumulate':
            # Простое объединение
            return self._initial_glossary + new_terms_from_db

        # Карта начального глоссария
        initial_map = {
            entry['original'].lower().strip(): entry
            for entry in self._initial_glossary if entry.get('original')
        }

        final_list = self._initial_glossary[:]

        # Вспомогательный сет для быстрого поиска ключей, которые уже есть в финальном списке
        existing_keys = set(initial_map.keys())

        if self.merge_mode == 'supplement':
            # Логика: БД дополняет начальный список.
            # Если термин из БД уже есть в начальном — игнорируем БД (Initial is King).
            # Если термина нет — добавляем.
            for term in new_terms_from_db:
                key = term.get('original', '').lower().strip()
                if key and key not in existing_keys:
                    final_list.append(term)
                    existing_keys.add(key) # На случай дублей в самой БД (хотя SQL их убрал)

        elif self.merge_mode == 'update':
            # Логика: БД обновляет начальный список.
            # Если термин из БД есть в начальном — заменяем данные в начальном (или пересоздаем список).
            # Но проще всего: создать карту из БД и наложить её поверх карты начального.

            # 1. Создаем карту победителей из БД (они главнее)
            db_map = {
                t['original'].lower().strip(): t
                for t in new_terms_from_db if t.get('original')
            }

            # 2. Сливаем: берем всё из Initial, если ключа нет в DB. Если есть в DB — берем из DB.
            merged_map = initial_map.copy()
            merged_map.update(db_map) # DB перезаписывает Initial

            final_list = list(merged_map.values())

        return final_list
