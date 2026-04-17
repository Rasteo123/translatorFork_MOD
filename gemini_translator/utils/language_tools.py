# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# Инструменты для работы с языками
# ---------------------------------------------------------------------------
# Этот файл содержит утилиты для обработки текстов на разных языках,
# включая сегментацию китайского текста с помощью Jieba.
# ---------------------------------------------------------------------------

import re
import unicodedata # Этот импорт должен быть
import itertools
import math
from collections import Counter, defaultdict # Убедимся, что Counter импортирован

import importlib
try:
    import jieba
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False
    print("INFO: jieba library not found. Chinese text segmentation will be limited.")
    print("Install it using: pip install jieba")

try:
    from fuzzywuzzy import fuzz
    try:
        import Levenshtein
    except:
        pass
    FUZZYWUZZY_AVAILABLE = True
except ImportError:
    FUZZYWUZZY_AVAILABLE = False

STOP_WORDS = {'the', 'a', 'an', 'to', 'in', 'on', 'of', 'for', 'with', 'am', 'i'}
CJK_STOP_WORDS = {'的', '是', '一', '不', '人', '我', '了', '在', '有', '和', '之'}
MORPHOLOGY_SUFFIXES_TO_IGNORE = ["'s", "es", "s"]
# Определяем пороги
ORDERED_SEARCH_THRESHOLD = 99      # Уровень 2: Порядок важен, но прощаем морфологию
UNORDERED_WORDS_THRESHOLD = 98     # Уровень 3: Разрешаем перестановку слов
CHAR_SIMILARITY_THRESHOLD = 96     # Уровень 4: Разрешаем опечатки/схожие слова
FUZZY_SEARCH_THRESHOLD = 94        # Уровень 5: Включаем "тяжелый" fuzzy-поиск

class LanguageDetector:
    """Определяет язык текста по символам"""
    
    @staticmethod
    def contains_chinese(text):
        """Проверяет, содержит ли текст китайские иероглифы"""
        chinese_pattern = re.compile(r'[\u4e00-\u9fff]+')
        return bool(chinese_pattern.search(text))
    
    @staticmethod
    def contains_japanese(text):
        """Проверяет, содержит ли текст японские символы (хирагана, катакана)"""
        japanese_pattern = re.compile(r'[\u3040-\u309f\u30a0-\u30ff]+')
        return bool(japanese_pattern.search(text))
    
    @staticmethod
    def contains_korean(text):
        """Проверяет, содержит ли текст корейские символы (хангыль)"""
        korean_pattern = re.compile(r'[\uac00-\ud7af]+')
        return bool(korean_pattern.search(text))
    
    @staticmethod
    def is_cjk_text(text):
        """Проверяет, является ли текст CJK (китайский, японский, корейский)"""
        return (LanguageDetector.contains_chinese(text) or 
                LanguageDetector.contains_japanese(text) or 
                LanguageDetector.contains_korean(text))


class ChineseTextProcessor:
    """Обработчик китайского текста с поддержкой сегментации"""
    
    def __init__(self, freq_power=3, freq_base=10, freq_offset=5, 
                 mult_factor_base=1.0, mult_factor_len_coeff=0.5):
        """
        Инициализирует процессор с параметрами для умной настройки частот.
        """
        self.jieba_initialized = False
        if JIEBA_AVAILABLE:
            self.init_jieba()
            # Сохраняем параметры для настройки весов
            self.freq_power = freq_power
            self.freq_base = freq_base
            self.freq_offset = freq_offset
            self.mult_factor_base = mult_factor_base
            self.mult_factor_len_coeff = mult_factor_len_coeff
    
    def init_jieba(self):
        """Инициализирует jieba (ленивая загрузка словаря)"""
        if JIEBA_AVAILABLE and not self.jieba_initialized:
            try:
                # Отключаем информационные сообщения jieba
                import logging
                jieba.setLogLevel(logging.INFO)
                self.jieba_initialized = True
            except Exception:
                pass
    
    def segment_text(self, text, use_jieba=True, cut_all=False):
        """
        Возвращает СТРОКУ, сегментированную пробелами. 
        Используется для подготовки текста к отправке в AI.
        """
        # Вызываем логику, возвращающую список
        words = self.segment_text_split(text, use_jieba, cut_all)
        # Соединяем в строку
        return " ".join(words)

    def segment_text_split(self, text, use_jieba=True, cut_all=False):
        """
        Возвращает СПИСОК слов (токенов).
        Используется для анализа глоссария.
        """
        if not use_jieba or not JIEBA_AVAILABLE:
            # Если jieba нет, просто делим по пробелам
            return text.split()
            
        if not self.jieba_initialized:
            self.init_jieba()
            
        try:
            # jieba.cut возвращает итератор - преобразуем в список для безопасного повторного использования
            return list(jieba.cut(text, cut_all=cut_all))
        except Exception as e:
            print(f"Error during Chinese segmentation: {e}")
            return text.split()
    
    def _get_word_freq_by_length(self, word):
        """Вычисляет базовую частоту слова на основе его длины."""
        length = len(word)
        return (length ** self.freq_power) * self.freq_base + self.freq_offset

    def _get_multiplication_factor(self, word):
        """Вычисляет коэффициент умножения для существующих слов."""
        length = len(word)
        return self.mult_factor_base + (length * self.mult_factor_len_coeff)

    def add_custom_words(self, glossary):
        """
        ФИНАЛЬНАЯ, ИДЕАЛЬНАЯ ВЕРСИЯ: Сначала очищает термины от мусора,
        а затем находит все нормализованные вариации чистого содержания.
        """
        if not JIEBA_AVAILABLE or not glossary:
            return
            
        if not self.jieba_initialized:
            self.init_jieba()
            
        try:
            cleaner_re = re.compile(r'\W+', re.UNICODE)
            words_to_train = set()
            
            for term in glossary.keys():
                if not LanguageDetector.contains_chinese(term):
                    continue

                # --- ШАГ 1: Получаем чистое содержание термина ---
                # Мы делаем это ОДИН раз в самом начале.
                clean_content_str = cleaner_re.sub(' ', term).strip()

                # Если после очистки ничего не осталось, пропускаем
                if not clean_content_str:
                    continue

                # --- ШАГ 2: Добавляем слова из оригинального чистого содержания ---
                words_to_train.update(clean_content_str.split())

                # --- ШАГ 3: Ищем и добавляем нормализованные вариации ---
                normalized_content_str = unicodedata.normalize('NFKC', clean_content_str)
                
                if clean_content_str != normalized_content_str:
                    words_to_train.update(normalized_content_str.split())

            # --- ШАГ 4: Обучение Jieba на финальном, уникальном наборе слов ---
            for word in words_to_train:
                if not word or not LanguageDetector.contains_chinese(word): 
                    continue

                # === ИСПРАВЛЕНИЕ ЗДЕСЬ ===
                # Заменяем несуществующий get_abs_freqs на прямой доступ к словарю
                current_freq = jieba.dt.FREQ.get(word, 0)
                # =========================
                
                base_freq = self._get_word_freq_by_length(word)
                
                if current_freq > 0:
                    multiplication_factor = self._get_multiplication_factor(word)
                    new_freq = int(current_freq * multiplication_factor) + base_freq
                else:
                    new_freq = base_freq
                jieba.add_word(word, freq=new_freq)

        except Exception as e:
            print(f"Error adding smart custom words to jieba: {e}")
    
    def reset(self):
        """
        Полностью сбрасывает состояние Jieba путем перезагрузки модуля.
        Это единственный надежный способ очистить измененные в памяти частоты слов.
        """
        if JIEBA_AVAILABLE and self.jieba_initialized:
            try:
                # Перезагружаем модуль jieba, чтобы он заново считал свои словари с диска
                importlib.reload(jieba)
                # Сбрасываем флаг, чтобы при следующем вызове произошла ленивая инициализация
                self.jieba_initialized = False
                print("[JIEBA] Состояние словаря Jieba сброшено до стандартного.")
            except Exception as e:
                print(f"[JIEBA ERROR] Не удалось сбросить состояние Jieba: {e}")

class GlossaryRegexService:
    """
    Сервис для предварительной компиляции Regex-паттернов глоссария.
    Версия 2.0: Поддерживает "Clean Wall" поиск для CJK (игнорирует пунктуацию внутри терминов).
    """
    def __init__(self, glossary_dict):
        self.cjk_pattern = None
        self.alpha_pattern = None
        
        # clean_text -> set(original_terms)
        # Использование set критично, так как "Term-A" и "Term A" дадут одинаковый clean_text
        self.cjk_map = defaultdict(set)   
        self.alpha_map = {} # lower_text -> set(original_terms)
        
        self._cleaner = re.compile(r'\W+', re.UNICODE)
        self._compile(glossary_dict)

    def _compile(self, glossary_dict):
        if not glossary_dict: return

        cjk_clean_terms = set()
        alpha_terms = set()

        # 1. Сортировка по длине оригинала (для приоритета)
        sorted_keys = sorted(glossary_dict.keys(), key=len, reverse=True)

        for original in sorted_keys:
            if not original.strip(): continue
            
            if LanguageDetector.is_cjk_text(original):
                # Для CJK: вычищаем всё, оставляя только иероглифы/буквы
                clean_term = self._cleaner.sub('', original).strip()
                if clean_term:
                    self.cjk_map[clean_term].add(original)
                    cjk_clean_terms.add(re.escape(clean_term))
            else:
                # Для Alpha: нижний регистр
                lower_term = original.lower()
                if lower_term not in self.alpha_map:
                    self.alpha_map[lower_term] = set()
                self.alpha_map[lower_term].add(original)
                alpha_terms.add(re.escape(original))

        # 2. Компиляция CJK (по "чистым" терминам)
        if cjk_clean_terms:
            # Сортируем по длине ЧИСТОГО термина, чтобы "LongString" искалась раньше "Long"
            sorted_cjk = sorted(list(cjk_clean_terms), key=len, reverse=True)
            self.cjk_pattern = re.compile('|'.join(sorted_cjk))

        # 3. Компиляция Alpha (с границами слов \b)
        if alpha_terms:
            sorted_alpha = sorted(list(alpha_terms), key=len, reverse=True)
            self.alpha_pattern = re.compile(r'\b(?:' + '|'.join(sorted_alpha) + r')\b', re.IGNORECASE)

    def find_matches(self, text):
        """
        Ищет вхождения терминов в тексте за ОДИН проход.
        Возвращает множество оригинальных ключей глоссария.
        """
        found_originals = set()
        
        # Поиск CJK: Ищем в "стене текста" (без пробелов и пунктуации)
        if self.cjk_pattern:
            clean_text_wall = self._cleaner.sub('', text)
            matches = self.cjk_pattern.findall(clean_text_wall)
            for match in matches:
                if match in self.cjk_map:
                    found_originals.update(self.cjk_map[match])

        # Поиск Alpha: Ищем в оригинальном тексте (нужны пробелы для \b)
        if self.alpha_pattern:
            matches = self.alpha_pattern.findall(text)
            for match in matches:
                lower_match = match.lower()
                if lower_match in self.alpha_map:
                    found_originals.update(self.alpha_map[lower_match])
                    
        return found_originals

    def count_matches(self, text):
        """Подсчитывает реальные вхождения терминов в тексте."""
        found_counts = Counter()

        if self.cjk_pattern:
            clean_text_wall = self._cleaner.sub('', text)
            matches = self.cjk_pattern.findall(clean_text_wall)
            for match in matches:
                for original in self.cjk_map.get(match, ()):
                    found_counts[original] += 1

        if self.alpha_pattern:
            matches = self.alpha_pattern.findall(text)
            for match in matches:
                lower_match = match.lower()
                for original in self.alpha_map.get(lower_match, ()):
                    found_counts[original] += 1

        return found_counts
        
class SmartGlossaryFilter:
    """
    Гибридный фильтр глоссария, использующий Unicode-нормализацию для всех языков,
    Jieba для CJK и fuzzy-поиск "скользящим окном" для алфавитных языков.
    """

    RELATED_SEARCH_THRESHOLD = 89      # Уровень 6: Включаем поиск "родственников" по глоссарию
    
    def __init__(self, chinese_processor=None):
        self.chinese_processor = chinese_processor or ChineseTextProcessor()
        self._universal_cleaner_re = re.compile(r'\W+', re.UNICODE)
    
    def _normalize_text(self, text):
        """Применяет универсальную Unicode-нормализацию NFKC."""
        return unicodedata.normalize('NFKC', text)
    
    def _normalize_word(self, word):
        """
        Удаляет распространенные английские суффиксы/окончания из слова.
        Работает по принципу "первое совпадение".
        """
        for suffix in MORPHOLOGY_SUFFIXES_TO_IGNORE:
            if word.endswith(suffix):
                return word[:-len(suffix)]
        return word
    
    
    
    def filter_glossary_for_text(self, full_glossary, text, 
                                fuzzy_threshold=100, 
                                use_jieba_for_glossary_search=True,
                                similarity_map=None,
                                regex_service=None,
                                find_embedded_subterms=False): # <--- НОВЫЙ АРГУМЕНТ
        """
        Главный метод-фильтр.
        Реализует каскадную логику: Сначала точный Regex-поиск, затем (при необходимости) нечеткий.
        """
        if not full_glossary or not text:
            return {}

        # --- ЭТАП 1: Гарантированный Точный Поиск (GlossaryRegexService) ---
        # Ищем ВСЕ точные вхождения с помощью re.escape (гарантирует нахождение "Shannaro!")
        service_to_use = regex_service
        if not service_to_use:
            # Создаем на лету, если не передан (быстро для малых списков)
            service_to_use = GlossaryRegexService(full_glossary)

        found_keys_list = service_to_use.find_matches(text)
        found_originals = set(found_keys_list) 

        
        # --- ЭТАП 1.5: Добор вложенных подстрок (для Глоссария) ---
        if (find_embedded_subterms or fuzzy_threshold <= UNORDERED_WORDS_THRESHOLD) and found_originals:
            # ОПТИМИЗАЦИЯ "СТЕНА ТЕКСТА"
            # Вместо вложенных циклов или повторного Regex (который "съедает" символы),
            # мы строим одну большую строку из найденных терминов и быстро проверяем наличие 
            # остальных кандидатов в ней.
            
            # Используем непечатный разделитель, чтобы "хвост" одного термина 
            # и "голова" другого случайно не образовали новый термин.
            search_wall = "\0".join(found_originals)
            
            # Берем только те термины, которые еще НЕ найдены
            candidates = set(full_glossary.keys()) - found_originals
            for candidate in candidates:
                # Python оператор 'in' работает на C и экстремально быстр (Boyer-Moore подобный алгоритм).
                # Он находит вхождение, даже если оно внутри другого слова.
                if candidate in search_wall:
                    found_originals.add(candidate)
        
        
        # --- ЭТАП 2: Ранний выход для Строгого Режима (ОПТИМИЗИРОВАНО) ---
        # Для алфавитных языков (EN/RU) Regex с границами слов \b достаточно точен.
        # Но для CJK Regex находит просто подстроки. Нам НУЖНО провалиться дальше, 
        # чтобы Jieba/валидатор проверил, является ли найденное отдельным словом.
        is_cjk_content = LanguageDetector.is_cjk_text(text)
        
        if fuzzy_threshold == 100 and not is_cjk_content:
            return {k: full_glossary[k] for k in found_originals if k in full_glossary}

        # --- ЭТАП 3: Добор нечетких совпадений / Валидация CJK ---
        remaining_glossary = {k: v for k, v in full_glossary.items() if k not in found_originals}

        # Если это CJK и порог 100, remaining_glossary может быть пустым, но мы все равно
        # должны вызвать _find_degree_matches, чтобы валидировать found_originals
        if remaining_glossary or (is_cjk_content and found_originals):
            
            effective_similarity_map = None
            if use_jieba_for_glossary_search:
                self.RELATED_SEARCH_THRESHOLD = 95
            
            # Строим карту только если порог подразумевает поиск родственников
            if fuzzy_threshold <= self.RELATED_SEARCH_THRESHOLD:
                if similarity_map is not None:
                    effective_similarity_map = similarity_map
                else:
                    try:
                        glossary_list = [{'original': k, **(v if isinstance(v, dict) else {'rus': v})} 
                                        for k, v in full_glossary.items()]
                        effective_similarity_map = GlossaryLogic().build_similarity_map(glossary_list, fuzzy_threshold, use_jieba_for_glossary_search)
                    except Exception as e:
                        print(f"Error building similarity map: {e}")
                        effective_similarity_map = {}
            
            # Запускаем "умный" поиск (или валидацию для CJK)
            additional_matches = self._find_degree_matches(
                full_glossary, text, fuzzy_threshold, use_jieba_for_glossary_search, # <-- Передаем full_glossary, чтобы cjk_search видел всё
                similarity_map=effective_similarity_map, pre_found_orig=found_originals
            )
            
            found_originals.update(additional_matches)
    
        # --- ШАГ 4: Формируем результат ---
        return {k: v for k, v in full_glossary.items() if k in found_originals}
    
    def _find_degree_matches(self, full_glossary, text, fuzzy_threshold, use_jieba_for_glossary_search, similarity_map=None, pre_found_orig=set()):
        """
        Метод-диспетчер. Определяет тип текста и вызывает нужный обработчик,
        передавая ему универсальную карту схожести.
        """
        normalized_text = self._normalize_text(text)
    
        if LanguageDetector.is_cjk_text(normalized_text):
            return self.cjk_search(full_glossary, normalized_text, use_jieba_for_glossary_search, 
                                similarity_map, fuzzy_threshold, pre_found_orig)
        else:
            return self.alphabet_search(full_glossary, normalized_text, fuzzy_threshold, similarity_map, pre_found_orig)
    
    def alphabet_search(self, full_glossary, normalized_text, 
                                fuzzy_threshold=100, 
                                similarity_map=None, 
                                pre_found_orig=set()):
        found_originals = set()
        glossary_to_process = full_glossary.copy()

        # --- ЭТАП 1: УПОРЯДОЧЕННЫЙ ПОИСК (ВЫПОЛНЯЕТСЯ ВСЕГДА) ---
        is_strict = (fuzzy_threshold == 100)
        # Находим самые надежные совпадения
        ordered_matches = self._filter_with_ordered_search(
            glossary_to_process, normalized_text, is_strict_mode=is_strict
        )
        
        if ordered_matches:
            found_originals.update(ordered_matches)
            # Исключаем найденное из дальнейшей обработки
            glossary_to_process = {k: v for k, v in glossary_to_process.items() if k not in found_originals}

        # --- ЭТАП 2: ПОИСК РОДСТВЕННИКОВ (ОБНОВЛЕНО) ---
        # Используем объединение "предков": найденные сейчас + найденные ранее точным регексом
        ancestors_pool = ordered_matches | pre_found_orig
        
        if fuzzy_threshold <= self.RELATED_SEARCH_THRESHOLD and similarity_map and ancestors_pool:
            relatives_to_add = set()
            # Ищем родню для всех подтвержденных "якорей"
            for ancestor in ancestors_pool:
                related_items = similarity_map.get(ancestor, [])
                for related_term, similarity_score in related_items:
                    # Добавляем, только если родственник еще не был найден и проходит по порогу
                    if related_term in glossary_to_process and similarity_score >= fuzzy_threshold:
                        relatives_to_add.add(related_term)
            if relatives_to_add:
                found_originals.update(relatives_to_add)
                # Исключаем найденных родственников из дальнейшей обработки
                glossary_to_process = {k: v for k, v in glossary_to_process.items() if k not in found_originals}

        # --- ЭТАП 3: "ТЯЖЕЛЫЙ" НЕЧЕТКИЙ ПОИСК ПО ОСТАТКАМ ---
        # Этот блок выполняется, только если порог ниже 99 и в глоссарии еще что-то осталось
        if fuzzy_threshold < ORDERED_SEARCH_THRESHOLD and glossary_to_process:
            
            prepared_glossary = {}
            glossary_index = defaultdict(set)
            for original_term in glossary_to_process.keys():
                normalized = self._normalize_text(original_term)
                clean_str = self._universal_cleaner_re.sub(' ', normalized.lower()).strip()
                words = clean_str.split()
                if not words: continue
                term_no_spaces = clean_str.replace(" ", "")
                term_data = {
                    'original': original_term, 'clean_str': clean_str, 'words': words,
                    'len_words': len(words), 'len_chars': len(term_no_spaces),
                    'char_set': set(term_no_spaces), 'counter_chars': Counter(term_no_spaces),
                    'word_counter': Counter(words),
                    'first_bigrams': {w[:2] for w in words if len(w) > 1}
                }
                prepared_glossary[original_term] = term_data
                for bigram in term_data['first_bigrams']:
                    glossary_index[bigram].add(original_term)
        
            text_words_list = self._universal_cleaner_re.sub(' ', normalized_text.lower()).strip().split()
            text_len = len(text_words_list)
            WINDOW_SHIFT_RADIUS = 2 
            checked_windows = set()
        
            for i in range(text_len):
                current_word = text_words_list[i]
                if len(current_word) < 2: continue
                
                candidate_keys = glossary_index.get(current_word[:2])
                if not candidate_keys: continue
        
                for term_key in candidate_keys:
                    if term_key in found_originals: continue
        
                    term_data = prepared_glossary[term_key]
                    term_len_words = term_data['len_words']
        
                    # Итерируем по возможным стартовым позициям "плавающего" окна
                    start_range = max(0, i - WINDOW_SHIFT_RADIUS)
                    end_range = i + 1
        
                    for start_pos in range(start_range, end_range):
                        if (start_pos, term_key) in checked_windows:
                            continue
                        checked_windows.add((start_pos, term_key))
                        
                        if start_pos + term_len_words > text_len:
                            continue
                        
                        window_words = text_words_list[start_pos : start_pos + term_len_words]
                        
                        # --- Фильтры-предохранители ---
                        ratio = fuzzy_threshold / 100.0
                        gatekeeper_set_coverage_threshold = ratio * 0.75
                        gatekeeper_freq_coverage_threshold = ratio * 0.6
                        
                        window_text_no_spaces = "".join(window_words)
                        
                        leeway = 1.0 - ratio 
                        min_len = term_data['len_chars'] * (ratio - leeway * 0.5)
                        max_len = term_data['len_chars'] * (1.0 + leeway * 0.5) * 1.2
                        if not (min_len <= len(window_text_no_spaces) <= max_len):
                            continue
                        
                        window_char_set = set(window_text_no_spaces)
                        shared_chars = term_data['char_set'].intersection(window_char_set)
                        set_coverage = len(shared_chars) / len(term_data['char_set']) if term_data['char_set'] else 0
                        if set_coverage < gatekeeper_set_coverage_threshold:
                            continue
        
                        window_counter_chars = Counter(window_text_no_spaces)
                        common_chars = term_data['counter_chars'] & window_counter_chars
                        freq_coverage = sum(common_chars.values()) / term_data['len_chars'] if term_data['len_chars'] > 0 else 0
                        if freq_coverage < gatekeeper_freq_coverage_threshold:
                            continue
        
                        # === Накопительный каскад проверок ===
                        if fuzzy_threshold <= UNORDERED_WORDS_THRESHOLD:
                            if Counter(window_words) == term_data['word_counter']:
                                found_originals.add(term_key)
                                break 
        
                        if term_key in found_originals: break
        
                        if fuzzy_threshold <= CHAR_SIMILARITY_THRESHOLD:
                            strict_freq_coverage_threshold = 0.9 * (ratio ** 2)
                            if freq_coverage >= strict_freq_coverage_threshold:
                                found_originals.add(term_key)
                                break
        
                        if term_key in found_originals: break
        
                        if FUZZYWUZZY_AVAILABLE and fuzzy_threshold <= FUZZY_SEARCH_THRESHOLD:
                            expanded_start = max(0, i - 2)
                            expanded_end = min(text_len, i + term_len_words + 2)
                            expanded_window_text = " ".join(text_words_list[expanded_start:expanded_end])
                            
                            score = fuzz.token_set_ratio(term_data['clean_str'], expanded_window_text) + 5
                            if score >= fuzzy_threshold:
                                found_originals.add(term_key)
                                break
                                
        return found_originals
    
    def cjk_search(self, full_glossary, normalized_text, use_jieba_for_glossary_search, 
                similarity_map=None, fuzzy_threshold=100, pre_found_orig=set()):
        """
        Выполняет валидацию и расширение поиска для CJK.
        Вся работа по первичному поиску теперь выполняется в GlossaryRegexService.
        """
        # === Шаг 1: Получаем кандидатов из RegexService (pre_found_orig) ===
        # Теперь pre_found_orig содержит результаты поиска по Clean Wall.
        initial_candidates = pre_found_orig
        
        if not initial_candidates:
            return set()
    
        # === Шаг 2: Валидация "Якорей" ===
        if fuzzy_threshold > ORDERED_SEARCH_THRESHOLD:
            # При строгом пороге проверяем сегментацию
            is_chinese_case = use_jieba_for_glossary_search and JIEBA_AVAILABLE and LanguageDetector.contains_chinese(normalized_text)
            
            if is_chinese_case:
                validated_anchors = self._validate_chinese_candidates(initial_candidates, normalized_text)
            elif LanguageDetector.contains_korean(normalized_text) and self._is_korean_text_segmented(normalized_text):
                validated_anchors = self._validate_korean_candidates(initial_candidates, normalized_text)
            else:
                validated_anchors = initial_candidates
        else:
            # При мягком пороге доверяем Regex
            validated_anchors = initial_candidates
        
        if not validated_anchors:
            return set()
    
        # === Шаг 3: Расширение (Поиск родственников) ===
        final_results = set(validated_anchors)
        
        if fuzzy_threshold <= self.RELATED_SEARCH_THRESHOLD and similarity_map:
            queue = list(validated_anchors)
            processed = set(validated_anchors)
    
            while queue:
                current_term = queue.pop(0)
                relations = similarity_map.get(current_term, [])
                for related_term, score in relations:
                    if score >= fuzzy_threshold:
                        if related_term not in processed:
                            final_results.add(related_term)
                            processed.add(related_term)
                            queue.append(related_term)
                            
        return final_results

    
    def _validate_korean_candidates(self, candidates, normalized_text):
        """
        Проверяет кандидатов на соответствие корейскому тексту, сегментированному пробелами.
        Использует "скользящее окно" для точного поиска фраз.
        """
        validated = set()
        
        # --- Подготовка глоссария кандидатов ---
        prepared_glossary = {}
        for original_term in candidates:
            clean_term_str = self._universal_cleaner_re.sub(' ', self._normalize_text(original_term)).strip()
            term_word_array = clean_term_str.split()
            if term_word_array:
                prepared_glossary[original_term] = term_word_array

        # --- Подготовка токенов текста ---
        clean_text = self._universal_cleaner_re.sub(' ', normalized_text).strip()
        clean_text_tokens = clean_text.split()
        
        # --- Вызов универсального поисковика для валидации ---
        # Мы можем переиспользовать наш гениальный _search_with_sliding_window!
        validated_terms = self._search_with_sliding_window(prepared_glossary, clean_text_tokens)
        
        return validated_terms
    
    # === НОВЫЙ МЕТОД: Эвристический детектор сегментации корейского текста ===
    def _is_korean_text_segmented(self, text, threshold=0.05, min_length=50):
        """
        Проверяет, является ли корейский текст сегментированным пробелами,
        используя эвристику плотности пробелов.
        """
        if len(text) < min_length:
            # Для очень коротких текстов доверяем простому наличию пробела
            return ' ' in text
        
        space_count = text.count(' ')
        if space_count == 0:
            return False
            
        space_density = space_count / len(text)
        
        return space_density >= threshold
    
    def _validate_chinese_candidates(self, candidates, normalized_text):
        """
        Использует ПРЕДВАРИТЕЛЬНО НАСТРОЕННЫЙ Jieba для валидации кандидатов.
        Этот метод больше не изменяет состояние jieba.
        """
        if not candidates or not JIEBA_AVAILABLE:
            return candidates
        # 1. Сегментируем текст, используя jieba, который уже знает о наших терминах.
        segmented_tokens_dirty = self.chinese_processor.segment_text_split(normalized_text, cut_all=False)
        clean_text_tokens = {self._universal_cleaner_re.sub('', t).strip() for t in segmented_tokens_dirty if t.strip()}
        
        validated = set()
        for term in candidates:
            if not LanguageDetector.contains_chinese(term):
                validated.add(term)
                continue

            # Мы должны проверить каждую часть многословного термина
            clean_term_str = self._universal_cleaner_re.sub(' ', self._normalize_text(term)).strip()
            term_words = clean_term_str.split()
            
            # Термин валиден, если ВСЕ его части найдены как отдельные токены в тексте
            # (Это можно упростить до if clean_term in clean_text_tokens для однословных)
            # Для простоты и надежности, проверим вхождение очищенного термина как единого блока
            clean_term_single_token = "".join(term_words)
            if clean_term_single_token in clean_text_tokens:
                validated.add(term)
        return validated
    
    
    
    
    def _filter_with_ordered_search(self, glossary, normalized_text, is_strict_mode=False):
        """
        Выполняет самый быстрый поиск с учетом порядка слов.
        В 'strict_mode' (для порога 100) не удаляет стоп-слова и не нормализует морфологию.
        """
        if not glossary:
            return set()
    
        processed_to_originals_map = defaultdict(set)
        for original_term in glossary.keys():
            term = self._normalize_text(original_term)
            term = self._universal_cleaner_re.sub(' ', term).strip().lower()
            term_words = term.split()
            if not term_words: continue
    
            # === НОВАЯ ЛОГИКА ВЫБОРА РЕЖИМА ===
            if is_strict_mode:
                # В строгом режиме мы не делаем НИКАКИХ преобразований
                processed_words = term_words
            else:
                # В обычном режиме (для порога 99) мы все смягчаем
                filtered_words = [w for w in term_words if w not in STOP_WORDS]
                processed_words = [self._normalize_word(w) for w in filtered_words]
            
            final_processed_term = " ".join(" ".join(processed_words).split())
    
            if final_processed_term:
                processed_to_originals_map[final_processed_term].add(original_term)
    
        if not processed_to_originals_map:
            return set()
    
        escaped_terms = [re.escape(term) for term in processed_to_originals_map.keys()]
        escaped_terms.sort(key=len, reverse=True)
        
        giant_regex_pattern = r'\b(' + '|'.join(escaped_terms) + r')\b'
        
        searchable_text = self._universal_cleaner_re.sub(' ', normalized_text.lower()).strip()
        searchable_text = " ".join(searchable_text.split())
        
        found_processed_terms = re.findall(giant_regex_pattern, searchable_text)
    
        found_originals = set()
        for found_term in found_processed_terms:
            if found_term in processed_to_originals_map:
                found_originals.update(processed_to_originals_map[found_term])
                
        return found_originals
    
    
    
    def _search_with_sliding_window(self, prepared_glossary, clean_text_tokens):
        """
        Универсальный поисковик, который ищет ТОЧНУЮ ПОСЛЕДОВАТЕЛЬНОСТЬ слов.
        Используется для валидации корейского текста.
        """
        found_originals = set()
        if not clean_text_tokens:
            return found_originals
            
        for original_term, term_word_array in prepared_glossary.items():
            term_len = len(term_word_array)
            if term_len == 0 or term_len > len(clean_text_tokens):
                continue
            
            for i in range(len(clean_text_tokens) - term_len + 1):
                window = clean_text_tokens[i : i + term_len]
                if window == term_word_array:
                    found_originals.add(original_term)
                    break 
        return found_originals




# ---------------------------------------------------------------------------
# --- Логика
# ---------------------------------------------------------------------------
class GlossaryLogic:

    # --------------------------------------------------------------
    # --- ПОСТРОИТЕЛЬ КАРТЫ СХОЖЕСТИ (build_similarity_map) ---
    # --------------------------------------------------------------
    def build_similarity_map(self, glossary_list, fuzzy_threshold, use_jieba_for_glossary_search):
        """
        УМНЫЙ ОРКЕСТРАТОР. Версия 4.0: "Широкое Озеро".
        Строит карту один раз для МИНИМАЛЬНОГО порога системы (75%), 
        чтобы при изменении фильтра в UI данные уже были в кэше.
        """
        if not glossary_list or len(glossary_list) < 2:
            return None
        
        # Определяем границы, при которых вообще имеет смысл строить карту
        # Если CJK - мы строже (95), если нет - мягче (84). 
        # Но это порог активации САМОЙ ФУНКЦИИ поиска.
        
        limit_threshold = 89
        if use_jieba_for_glossary_search:
            limit_threshold = 95
        
        if fuzzy_threshold > limit_threshold:
            return None
        

        # Мы ИГНОРИРУЕМ текущий fuzzy_threshold для целей фильтрации при построении.
        # Мы строим карту для "Технического Дна" (Technical Floor).
        TECHNICAL_FLOOR = 75 
        
        # Передаем TECHNICAL_FLOOR и как порог отсечения, и как параметр широкой сети.
        # Теперь карта всегда будет содержать связи 75%+.
        # А фильтрация (93% или 80%) будет происходить уже при чтении карты в SmartGlossaryFilter.
        final_map = self._build_map_for_entries(glossary_list, 
                                                final_threshold=TECHNICAL_FLOOR, 
                                                wide_threshold=TECHNICAL_FLOOR)
    
        if final_map:
            # Сортировка и уникализация остаются
            for term in final_map:
                unique_relations = sorted(list({rel[0]: rel for rel in final_map[term]}.values()), key=lambda x: x[1], reverse=True)
                final_map[term] = unique_relations
            return final_map
            
        return None
    
    def _build_map_for_entries(self, glossary_list, final_threshold, wide_threshold):
        """
        ФИНАЛЬНАЯ ВЕРСИЯ 5.0: ДВУХКАНАЛЬНЫЙ ПОИСК (ОРИГИНАЛ + ПЕРЕВОД).
        Ищет сходство как по написанию оригинала, так и по смыслу (переводу).
        """
        if not glossary_list:
            return {}
    
        # --- Шаг 1: Подготовка данных (Пре-калькуляция) ---
        # Нам нужны данные и для оригинала, и для перевода
        term_data = {}
        trans_to_originals_map = defaultdict(list) # Для обратного поиска
        unique_translations = [] # Для генератора кандидатов по переводам
        
        seen_trans = set()

        for e in glossary_list:
            orig = e.get('original', '').strip()
            trans = e.get('rus', '').strip()
            if not orig: continue
            
            # Очистка оригинала (для CJK убираем пробелы, для EN оставляем)
            clean_orig = re.sub(r'\s+', '', orig) if LanguageDetector.is_cjk_text(orig) else orig
            
            # Очистка перевода (всегда нормализуем)
            clean_trans = " ".join(trans.split()) # Убираем лишние пробелы
            
            # Сохраняем полные данные
            term_data[orig] = {
                'orig_clean': clean_orig,
                'orig_len': len(clean_orig),
                'orig_counter': Counter(clean_orig),
                
                'trans_clean': clean_trans,
                'trans_len': len(clean_trans),
                'trans_counter': Counter(clean_trans),
                
                'has_trans': bool(clean_trans)
            }
            
            if clean_trans:
                trans_to_originals_map[clean_trans].append(orig)
                if clean_trans not in seen_trans:
                    unique_translations.append({'original': clean_trans}) # Хитрость: подаем перевод в поле original
                    seen_trans.add(clean_trans)

        # --- Шаг 2: Генерация кандидатов (Двухканальная) ---
        
        # Канал А: Кандидаты по Оригиналам
        candidates_orig = self._generate_candidate_pairs(glossary_list)
        
        # Канал Б: Кандидаты по Переводам
        # Генерируем пары похожих переводов
        raw_trans_candidates = self._generate_candidate_pairs(unique_translations)
        
        # Объединяем в единое множество пар оригиналов
        all_candidate_pairs = set(candidates_orig)
        
        # Разворачиваем пары переводов обратно в пары оригиналов
        for trans_a, trans_b in raw_trans_candidates:
            # trans_a и trans_b - это строки переводов. Получаем списки оригиналов для них.
            origs_a = trans_to_originals_map.get(trans_a, [])
            origs_b = trans_to_originals_map.get(trans_b, [])
            
            for oa in origs_a:
                for ob in origs_b:
                    if oa == ob: continue
                    # Сортируем пару для уникальности
                    pair = tuple(sorted((oa, ob)))
                    all_candidate_pairs.add(pair)

        similarity_map = defaultdict(list)
    
        # --- Шаг 3: Оценка кандидатов ---
        # Порог для переводов должен учитывать штраф.
        # Если final=90, то (Trans - 5) >= 90 => Trans >= 95.
        trans_threshold_needed = final_threshold + 5 

        for term1_orig, term2_orig in all_candidate_pairs:
            d1 = term_data.get(term1_orig)
            d2 = term_data.get(term2_orig)
            if not d1 or not d2: continue
            
            best_score = 0.0
            
            # === ПРОВЕРКА ПО ОРИГИНАЛУ ===
            # Быстрый пре-фильтр
            min_l_o, max_l_o = min(d1['orig_len'], d2['orig_len']), max(d1['orig_len'], d2['orig_len'])
            pass_orig = False
            if max_l_o > 0:
                # Фильтр длины
                if (min_l_o / max_l_o) * 100 >= (wide_threshold * 0.8):
                    # Фильтр структуры
                    avg_len = (d1['orig_len'] + d2['orig_len']) / 2.0
                    dyn_min = max(2, int((avg_len * (wide_threshold / 100.0)) * 0.6))
                    
                    if not self._fast_pre_filter(d1['orig_clean'], d2['orig_clean'], min_len=dyn_min, 
                                               counters=(d1['orig_counter'], d2['orig_counter'])):
                         pass_orig = True

            if pass_orig:
                sim_orig = self._calculate_universal_similarity(term1_orig, term2_orig) * 100
                best_score = max(best_score, sim_orig)

            # Если оригинал уже дал проходной балл, перевод можно не считать (экономия),
            # НО если мы хотим найти МАКСИМАЛЬНУЮ схожесть для ранжирования, лучше посчитать.
            # Для оптимизации: считаем перевод, только если оригинал не идеален (< 100).
            
            # === ПРОВЕРКА ПО ПЕРЕВОДУ ===
            if best_score < 100 and d1['has_trans'] and d2['has_trans']:
                # Быстрый пре-фильтр для переводов
                min_l_t, max_l_t = min(d1['trans_len'], d2['trans_len']), max(d1['trans_len'], d2['trans_len'])
                pass_trans = False
                
                if max_l_t > 0:
                    if (min_l_t / max_l_t) * 100 >= (wide_threshold * 0.8):
                        avg_len_t = (d1['trans_len'] + d2['trans_len']) / 2.0
                        # Для перевода порог чуть строже из-за штрафа
                        dyn_min_t = max(2, int((avg_len_t * ((wide_threshold + 5) / 100.0)) * 0.6))
                        
                        if not self._fast_pre_filter(d1['trans_clean'], d2['trans_clean'], min_len=dyn_min_t, 
                                                   counters=(d1['trans_counter'], d2['trans_counter'])):
                            pass_trans = True
                
                if pass_trans:
                    # Считаем чистую схожесть
                    raw_trans_sim = self._calculate_universal_similarity(d1['trans_clean'], d2['trans_clean']) * 100
                    # Применяем штраф
                    adjusted_trans_sim = raw_trans_sim - 5
                    best_score = max(best_score, adjusted_trans_sim)

            # === ФИНАЛ ===
            if best_score >= final_threshold:
                similarity_map[term1_orig].append((term2_orig, best_score))
                similarity_map[term2_orig].append((term1_orig, best_score))
    
        return similarity_map

    
    def _normalize_word(self, word):
        """Удаляет распространенные английские суффиксы/окончания."""
        for suffix in MORPHOLOGY_SUFFIXES_TO_IGNORE:
            if word.endswith(suffix):
                return word[:-len(suffix)]
        return word

    def _get_universal_tokens(self, text):
        """УНИВЕРСАЛЬНЫЙ ТОКЕНИЗАТОР: иероглиф или слово - это токен."""
        if LanguageDetector.is_cjk_text(text):
            return [char for char in text if char.strip()]
        else:
            normalized = unicodedata.normalize('NFKC', text).lower()
            clean_text = re.sub(r'\W+', ' ', normalized, flags=re.UNICODE).strip()
            all_tokens = clean_text.split()
            return [self._normalize_word(token) for token in all_tokens if token not in STOP_WORDS]

    def _calculate_levenshtein_similarity(self, s1, s2):
        """Вычисляет структурную похожесть строк."""
        if not s1 and not s2: return 1.0
        if not s1 or not s2: return 0.0
        m, n = len(s1), len(s2)
        if m < n: s1, s2, m, n = s2, s1, n, m
        if m == 0: return 1.0
        prev_row = list(range(n + 1))
        for i in range(m):
            curr_row = [i + 1] * (n + 1)
            for j in range(n):
                cost = 0 if s1[i] == s2[j] else 1
                curr_row[j+1] = min(curr_row[j] + 1, prev_row[j+1] + 1, prev_row[j] + cost)
            prev_row = curr_row
        return 1.0 - (prev_row[n] / m)

    
    def find_lcs_substring_sequence(self, a, b):
        """
        ФИНАЛЬНАЯ ВЕРСИЯ. Находит самую длинную общую непрерывную подстроку.
        АВТОМАТИЧЕСКИ определяет, нужно ли применять CJK стоп-слова,
        анализируя входные токены.
        """
        stop_words_to_use = set()
        # Эвристика: если хотя бы один из токенов содержит CJK, применяем фильтр.
        # Проверяем только первые несколько токенов для скорости.
        combined_sample = a[:5] + b[:5]
        if any(LanguageDetector.is_cjk_text(token) for token in combined_sample):
            stop_words_to_use = CJK_STOP_WORDS
    
        # Фильтруем входные списки токенов от стоп-слов, если это необходимо
        filtered_a = [token for token in a if token not in stop_words_to_use]
        filtered_b = [token for token in b if token not in stop_words_to_use]
    
        m, n = len(filtered_a), len(filtered_b)
        dp = [[0 for _ in range(n + 1)] for _ in range(m + 1)]
        
        max_len = 0
        end_pos_a = 0
        
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if filtered_a[i - 1] == filtered_b[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                    if dp[i][j] > max_len:
                        max_len = dp[i][j]
                        end_pos_a = i
                else:
                    dp[i][j] = 0
        
        if max_len > 0:
            return filtered_a[end_pos_a - max_len : end_pos_a]
        else:
            return []
    
    def _calculate_bag_of_chars_similarity(self, s1, s2):
        """Вычисляет композиционную похожесть по мешку символов."""
        if not s1 and not s2: return 1.0
        if not s1 or not s2: return 0.0
        counter1, counter2 = Counter(s1), Counter(s2)
        intersection = sum((counter1 & counter2).values())
        total_chars = len(s1) + len(s2)
        return (2.0 * intersection) / total_chars if total_chars > 0 else 1.0

    def _get_best_match_similarity(self, token, other_tokens):
        """Находит лучший % схожести для одного токена среди списка других."""
        if not other_tokens: return 0.0
        best_sim = 0
        for other_token in other_tokens:
            lev_sim = self._calculate_levenshtein_similarity(token, other_token)
            bag_sim = self._calculate_bag_of_chars_similarity(token, other_token)
            best_sim = max(best_sim, lev_sim, bag_sim)
        return best_sim

    # --- НОВЫЙ ЕДИНЫЙ МЕТОД ВЫЧИСЛЕНИЯ СХОЖЕСТИ ---
    def _calculate_universal_similarity(self, s1, s2):
        """
        Вычисляет единый, взвешенный показатель схожести, комбинируя
        анализ "мешка токенов" и глубокий анализ несовпавших токенов.
        """
        if s1 == s2: return 1.0
        
        tokens1 = self._get_universal_tokens(s1)
        tokens2 = self._get_universal_tokens(s2)
        
        if not tokens1 and not tokens2: return 1.0
        if not tokens1 or not tokens2: return 0.0

        counter1, counter2 = Counter(tokens1), Counter(tokens2)
        
        # Шаг 1: Вычисляем "массу" схожести от полностью идентичных токенов
        common_tokens = counter1 & counter2
        similarity_mass_from_common = sum(len(token) * count for token, count in common_tokens.items())

        # Шаг 2: Вычисляем "массу" схожести от "глубокого" анализа уникальных токенов
        unique_to_s1 = list((counter1 - counter2).elements())
        unique_to_s2 = list((counter2 - counter1).elements())
        
        similarity_mass_from_deep_match = 0
        if unique_to_s1 and unique_to_s2:
            # Итерируем по более короткому списку для эффективности
            shorter_list, longer_list = (unique_to_s1, unique_to_s2) if len(unique_to_s1) < len(unique_to_s2) else (unique_to_s2, unique_to_s1)
            
            for token in shorter_list:
                best_sim = self._get_best_match_similarity(token, longer_list)
                # Взвешиваем схожесть на длину текущего токена
                similarity_mass_from_deep_match += best_sim * len(token)

        # Шаг 3: Собираем итоговый показатель
        # Общая "масса" - это сумма длин всех токенов
        total_mass = sum(len(t) for t in tokens1) + sum(len(t) for t in tokens2)
        if total_mass == 0: return 1.0

        # Итоговая схожесть - это отношение всей найденной "массы схожести" к общей "массе"
        # Умножаем на 2, т.к. мы сравниваем два набора
        total_similarity_mass = 2 * similarity_mass_from_common + similarity_mass_from_deep_match
        
        # Нормализуем, чтобы не превысить 1.0
        return min(total_similarity_mass / total_mass, 1.0)


    # --- Остальные методы класса ---
    def find_direct_conflicts(self, glossary_list):
        term_map = defaultdict(list)
        for entry in glossary_list:
            original = entry.get('original', '').strip()
            if original: term_map[original].append(entry)
        direct_conflicts = {}
        for term, entries in term_map.items():
            if len(set((e.get('rus', ''), e.get('note', '')) for e in entries)) > 1:
                direct_conflicts[term] = [{'rus': t, 'note': n} for t, n in set((e.get('rus', ''), e.get('note', '')) for e in entries)]
        return {}, direct_conflicts

    def find_reverse_issues(self, glossary_list):
        issues = defaultdict(lambda: {'complete': [], 'orphans': []})
        for entry in glossary_list:
            original = entry.get('original', '').strip()
            rus = entry.get('rus', '').strip()
            if not rus: continue
            if original: issues[rus]['complete'].append(entry)
            elif entry.get('note', '').strip(): issues[rus]['orphans'].append(entry)
        return {t: d for t, d in issues.items() if len(d['complete']) > 1 or (d['complete'] and d['orphans'])}
    
    def find_overlap_groups(self, glossary_list):
        terms_set = {e.get('original', '').strip() for e in glossary_list if e.get('original', '').strip()}
        groups, inverted = defaultdict(list), defaultdict(list)
        terms = sorted(list(terms_set), key=len)
        for i in range(len(terms)):
            for j in range(i + 1, len(terms)):
                if terms[i] in terms[j]:
                    groups[terms[i]].append(terms[j])
                    inverted[terms[j]].append(terms[i])
        return groups, inverted

    
    def _generate_candidate_pairs(self, glossary_list, min_overlap_len: int = 3): # <--- НОВЫЙ АРГУМЕНТ
        """
        ФИНАЛЬНАЯ ВЕРСИЯ 7.0. Генератор кандидатов по принципу "Интеллектуальной Декомпозиции".
        Реализует двухфазный подход:
        1. Создает оптимальный набор групп для сравнения, рекурсивно разбивая
        слишком большие группы и обогащая подгруппы "остатками" родителя.
        2. Генерирует пары из финального набора групп с защитой от комбинаторного взрыва.
        """
        if not glossary_list or len(glossary_list) < 2:
            return set()
    
        # --- ФАЗА 1: ПОСТРОЕНИЕ ОПТИМАЛЬНЫХ ГРУПП ---
    
        # 1.1: Получаем полную иерархию и сортируем от общих к частным
        all_patterns = self.analyze_patterns_with_substring(
            glossary_list, 
            min_group_size=2, 
            return_hierarchy=True,
            min_overlap_len=min_overlap_len # <--- ПЕРЕДАЕМ ПАРАМЕТР ДАЛЬШЕ
        )
        if not all_patterns:
            return set()
        
        # Сортируем по возрастанию длины паттерна (самые общие - первыми)
        sorted_patterns = sorted(all_patterns.items(), key=lambda item: len(item[0]))
    
        # 1.2: Инициализация
        final_groups_to_process = []
        processed_terms = set()
        SUBDIVISION_THRESHOLD = 200 # Порог, выше которого группа считается "большой" и требует разбивки
        
        # 1.3: Проход сверху-вниз для построения групп
        for i, (pattern, members) in enumerate(sorted_patterns):
            
            # Нас интересуют только те члены, которых мы еще не распределили по группам
            unprocessed_members = members - processed_terms
            if not unprocessed_members:
                continue
    
            # --- ЛОГИКА ПРИНЯТИЯ РЕШЕНИЯ ---
            if len(unprocessed_members) < SUBDIVISION_THRESHOLD:
                # Группа достаточно мала. Добавляем ее "как есть" в финальный список.
                final_groups_to_process.append(unprocessed_members)
                processed_terms.update(unprocessed_members)
            else:
                # ГРУППА БОЛЬШАЯ - НАЧИНАЕМ ДЕКОМПОЗИЦИЮ
                # 1. Находим все более конкретные под-паттерны ДЛЯ ЭТОЙ ГРУППЫ
                sub_patterns_members = []
                all_sub_members = set()
                # Ищем "вперед" по отсортированному списку
                for sub_pattern, sub_members in sorted_patterns[i+1:]:
                    # Под-паттерн должен быть более специфичным и содержать текущий
                    if pattern in sub_pattern and pattern != sub_pattern:
                        # Нас интересуют только те члены подгруппы, что есть в текущей большой группе
                        relevant_sub_members = sub_members & unprocessed_members
                        if relevant_sub_members:
                            sub_patterns_members.append(relevant_sub_members)
                            all_sub_members.update(relevant_sub_members)
                
                # 2. Вычисляем "остатки" (orphans)
                orphans = unprocessed_members - all_sub_members
    
                # 3. Формируем новые, обогащенные группы
                if len(orphans) > 1:
                    # "Остатки" должны быть сравнены между собой
                    final_groups_to_process.append(orphans)
    
                for sub_group_members in sub_patterns_members:
                    # Каждая подгруппа обогащается "остатками" для сравнения с ними
                    enriched_subgroup = sub_group_members | orphans
                    final_groups_to_process.append(enriched_subgroup)
                
                # Вся большая группа считается обработанной
                processed_terms.update(unprocessed_members)
    
        # --- ФАЗА 2: ГЕНЕРАЦИЯ ПАР ИЗ ГОТОВЫХ ГРУПП ---
        
        candidate_pairs = set()
        BRUTE_FORCE_LIMIT = 500 # Финальный порог, выше которого мы не делаем полный перебор
    
        for group in final_groups_to_process:
            if len(group) < 2:
                continue
            
            if len(group) <= BRUTE_FORCE_LIMIT:
                for term1, term2 in itertools.combinations(group, 2):
                    candidate_pairs.add(tuple(sorted((term1, term2))))
            else:
                print(f"[GlossaryLogic] Warning: Final refined group still has {len(group)} members. "
                    f"Skipping brute-force pair generation for this group to ensure performance.")
                    
        print(f"[GlossaryLogic] Generated {len(candidate_pairs)} unique candidate pairs from "
            f"{len(final_groups_to_process)} optimally decomposed groups.")
        return candidate_pairs
    
    # --------------------------------------------------------------------
    # --- УНИВЕРСАЛЬНЫЙ ПРЕ-ФИЛЬТР  ---
    # --------------------------------------------------------------------
    def _fast_pre_filter(self, term1, term2, min_len, counters=None):
        """
        Универсальный фильтр-привратник. Версия 4.0 (C-Optimized).
        Использует difflib (C extension) для мгновенного поиска подстрок 
        и принимает пре-калькулированные Counters.
        
        Возвращает True, если пару нужно ОТБРОСИТЬ.
        """
        # 0. Импорт внутри метода для изоляции зависимостей
        from difflib import SequenceMatcher

        # 1. Проверка "массы пересечения" по мешку символов
        if counters:
            count1, count2 = counters
        else:
            count1, count2 = Counter(term1), Counter(term2)
            
        intersection_mass = sum((count1 & count2).values())
    
        if intersection_mass < min_len:
            return True
    
        # 2. C-Optimized проверка порядка (вместо медленного ручного DP)
        # SequenceMatcher.find_longest_match работает на C и экстремально быстр.
        if min_len > 0:
            matcher = SequenceMatcher(None, term1, term2, autojunk=False)
            match = matcher.find_longest_match(0, len(term1), 0, len(term2))
            
            # Если самая длинная общая подстрока короче порога - структура нарушена
            if match.size < min_len:
                return True
    
        return False

    # --------------------------------------------------------------
    # --- АНАЛИЗАТОР ЧАСТИЧНЫХ НАЛОЖЕНИЙ (partial_overlaps) ---
    # --------------------------------------------------------------
    
    def find_partial_overlaps(self, glossary_list, existing_conflicts_set, chinese_processor=None, min_overlap_len=4):
        """
        Находит частичные наложения (нечеткие конфликты), используя
        высокопроизводительный генератор кандидатов на основе обратного индекса.
        """
        
        # --- Шаг 1: Фильтрация входных данных ---
        # Для конвейера поиска конфликтов важно работать только с теми терминами,
        # для которых конфликты еще не найдены на предыдущих этапах.
        entries_to_check = [
            e for e in glossary_list 
            if e.get('original', '').strip() and e.get('original', '').strip() not in existing_conflicts_set
        ]
    
        # --- Шаг 2: Генерация кандидатов с помощью универсального метода ---
        # Вместо полного перебора O(n^2) мы получаем небольшой, но качественный 
        # список пар для детальной проверки.
        candidate_pairs = self._generate_candidate_pairs(
            entries_to_check, 
            min_overlap_len=min_overlap_len # <--- ПРОБРАСЫВАЕМ ПАРАМЕТР В САМОМ НАЧАЛЕ
        )
    
        # --- Шаг 3: Подготовка данных для анализа ---
        # Создаем карту переводов для быстрого доступа в цикле.
        # Мы используем исходный glossary_list, чтобы иметь доступ ко всем переводам.
        term_data = {
            e['original'].strip(): {'rus': e.get('rus', '').strip()}
            for e in glossary_list if e.get('original', '').strip()
        }
        
        analysis_results = defaultdict(dict)
        
        # --- Шаг 4: Детальный анализ только перспективных пар ---
        # Этот цикл теперь выполняется на порядки реже, чем раньше.
        for term1_orig, term2_orig in candidate_pairs:
            
            # --- Дополнительная быстрая проверка ---
            # Генератор пар нашел их по общим признакам (биграммы, иероглифы).
            # Теперь _fast_pre_filter проверяет общую "массу" и порядок символов.
            # Это важный второй уровень фильтрации.
            clean_term1 = re.sub(r'\s+', '', term1_orig) if LanguageDetector.is_cjk_text(term1_orig) else term1_orig
            clean_term2 = re.sub(r'\s+', '', term2_orig) if LanguageDetector.is_cjk_text(term2_orig) else term2_orig
            
            if self._fast_pre_filter(clean_term1, clean_term2, min_len=min_overlap_len):
                continue
            
            # --- Дорогие вычисления для самых лучших кандидатов ---
            original_similarity = self._calculate_universal_similarity(term1_orig, term2_orig)
            
            avg_len = (len(term1_orig) + len(term2_orig)) / 2.0
            if (avg_len * original_similarity) < min_overlap_len:
                continue
    
            trans1 = term_data.get(term1_orig, {}).get('rus')
            trans2 = term_data.get(term2_orig, {}).get('rus')
            if not trans1 or not trans2: continue
    
            translation_similarity = self._calculate_universal_similarity(trans1, trans2)
            
            group_key = f"{term1_orig}|{term2_orig}"
            analysis_results[group_key]['terms'] = {term1_orig, term2_orig}
            analysis_results[group_key]['original_dossier'] = {'universal_similarity': original_similarity}
            analysis_results[group_key]['translation_dossier'] = {'universal_similarity': translation_similarity}
    
        return analysis_results
    
    def analyze_patterns(self, glossary_list: list, min_group_size: int = 2, return_hierarchy: bool = False):
        """
        Универсальный анализатор паттернов, использующий алгоритм "Гига-глоссарий".
    
        Args:
            glossary_list (list): Список словарей с терминами.
            min_group_size (int): Минимальное количество участников для признания паттерна.
            return_hierarchy (bool): Если True, возвращает все под-паттерны (для UI).
                                    Если False, возвращает только самые длинные паттерны (для AI).
    
        Returns:
            dict: Словарь, где ключ - это паттерн, а значение - множество ID участников.
        """
        if not glossary_list:
            return {}
    
        # --- ШАГ 1: Создание "Гига-Глоссария" ---
        # Мы используем enumerate для получения уникального ID для каждого термина
        giga_glossary = []
        terms_by_id = {i: entry['original'] for i, entry in enumerate(glossary_list) if 'original' in entry}
        
        for term_id, original_text in terms_by_id.items():
            tokens = self._get_universal_tokens(original_text)
            # Генерируем все суффиксы
            for i in range(len(tokens)):
                suffix_tuple = tuple(tokens[i:])
                giga_glossary.append((suffix_tuple, term_id))
        
        # --- ШАГ 2: Сортировка ---
        giga_glossary.sort(key=lambda x: x[0])
        
        # --- ШАГ 3: Один проход и поиск иерархических префиксов (LCP) ---
        patterns_found = defaultdict(set)
        
        for i in range(len(giga_glossary) - 1):
            suffix1_tuple, term_id1 = giga_glossary[i]
            suffix2_tuple, term_id2 = giga_glossary[i+1]
            
            # Нам не нужно сравнивать суффиксы от одного и того же термина
            if term_id1 == term_id2:
                continue
    
            # Находим самый длинный общий префикс между двумя кортежами токенов
            common_prefix_len = 0
            for k in range(min(len(suffix1_tuple), len(suffix2_tuple))):
                if suffix1_tuple[k] == suffix2_tuple[k]:
                    common_prefix_len += 1
                else:
                    break
            
            if common_prefix_len == 0:
                continue
    
            lcp_tokens = suffix1_tuple[:common_prefix_len]
    
            # --- КЛЮЧЕВАЯ ЛОГИКА: Иерархия vs. Только родители ---
            if return_hierarchy:
                # Режим для UI: генерируем все под-паттерны
                for j in range(len(lcp_tokens), 0, -1):
                    pattern_tuple = lcp_tokens[:j]
                    pattern_string = " ".join(pattern_tuple)
                    
                    # Фильтруем слишком короткие/бессмысленные паттерны
                    if len(pattern_tuple) == 1 and len(pattern_string) <= 1:
                        continue
                        
                    patterns_found[pattern_string].add(term_id1)
                    patterns_found[pattern_string].add(term_id2)
            else:
                # Режим для AI: берем только самый длинный паттерн (LCP)
                pattern_tuple = lcp_tokens
                pattern_string = " ".join(pattern_tuple)
                
                if len(pattern_tuple) == 1 and len(pattern_string) <= 1:
                    continue
                    
                patterns_found[pattern_string].add(term_id1)
                patterns_found[pattern_string].add(term_id2)
                
        # --- ШАГ 4: Финальная фильтрация по размеру группы ---
        final_patterns = {}
        for pattern, member_ids in patterns_found.items():
            if len(member_ids) >= min_group_size:
                # Преобразуем ID обратно в оригинальные строки
                final_patterns[pattern] = {terms_by_id[id] for id in member_ids}
                
        return final_patterns
    
    def analyze_patterns_for_ui(self, glossary_list, min_group_size=2):
        """
        Анализатор паттернов для UI.
        Возвращает полную иерархию паттернов для построения фильтров.
        """
        # Вызываем основной движок с флагом return_hierarchy=True
        return self.analyze_patterns(glossary_list, min_group_size, return_hierarchy=True)
    
    
    def analyze_patterns_smart(self, glossary_list, existing_conflicts_set=None, min_group_size=2):
        """
        Универсальный анализатор паттернов 4.0 (Hierarchy Recomposition).
        1. Генерирует полную иерархию.
        2. Схлопывает паттерны с одинаковым составом, выбирая лучшего лидера.
        3. ВОССТАНАВЛИВАЕТ ИЕРАРХИЮ: Если группа A является подмножеством B,
           она сливается с B, делая B единой "семьей" для AI-корректора.
        """
        if existing_conflicts_set is None:
            existing_conflicts_set = set()

        # 1. Фильтруем и готовим данные
        glossary_map = {e.get('original'): e for e in glossary_list}
        clean_glossary_list = [
            e for e in glossary_list 
            if e.get('original', '').strip() and e.get('original').strip() not in existing_conflicts_set
        ]
        
        if not clean_glossary_list:
            return {}

        # 2. Получаем сырую ПОЛНУЮ иерархию
        raw_patterns = self.analyze_patterns(clean_glossary_list, min_group_size, return_hierarchy=True)
        
        # 3. Топологическое Схлопывание (убираем дубликаты-синонимы)
        topology_map = defaultdict(list)
        
        for pattern_string, members in raw_patterns.items():
            # Определение Realized Form (Слитно/Раздельно)
            tokens = pattern_string.split()
            p_with_space = " ".join(tokens)
            p_without_space = "".join(tokens)
            realized_form = p_with_space
            
            exact_match = None
            for m in members:
                m_lower = m.lower()
                if m_lower == p_without_space.lower(): exact_match = m; break
                if m_lower == p_with_space.lower(): exact_match = m; break
            
            if exact_match:
                realized_form = exact_match
            else:
                solid_count = sum(1 for m in list(members)[:20] if p_without_space.lower() in m.lower())
                spaced_count = sum(1 for m in list(members)[:20] if p_with_space.lower() in m.lower())
                if solid_count > spaced_count: realized_form = p_without_space
            
            member_fingerprint = frozenset(members)
            topology_map[member_fingerprint].append(realized_form)

        # Отбираем лучших лидеров для каждой группы
        consolidated_patterns = {}
        for member_set, candidates in topology_map.items():
            if not candidates: continue
            
            best_candidate = max(
                candidates, 
                key=lambda cand: (1 if cand in glossary_map else 0, len(cand))
            )
            consolidated_patterns[best_candidate] = set(member_set)
        
        # 4. ВОССТАНОВЛЕНИЕ ИЕРАРХИИ (Hierarchy Recomposition)
        # Если группа A является подмножеством B, сливаем A в B.
        # Это создает большие "семьи" для корректора.
        
        # Получаем список лидеров, сортируем от больших к малым (по кол-ву членов)
        sorted_leaders = sorted(
            consolidated_patterns.keys(), 
            key=lambda k: len(consolidated_patterns[k]), 
            reverse=True
        )
        
        final_families = {}
        processed_leaders = set()

        for parent_leader in sorted_leaders:
            if parent_leader in processed_leaders: continue
            
            parent_members = consolidated_patterns[parent_leader]
            
            # Ищем детей
            for child_leader in sorted_leaders:
                if child_leader == parent_leader: continue
                if child_leader in processed_leaders: continue
                
                child_members = consolidated_patterns[child_leader]
                
                # Если дочерняя группа является подмножеством родительской
                if child_members.issubset(parent_members):
                    # Родитель поглощает членов ребенка (хотя они и так там есть, это для полноты)
                    parent_members.update(child_members)
                    # Помечаем ребенка как обработанного, он не станет отдельной семьей
                    processed_leaders.add(child_leader)
            
            # Родитель (теперь с полным составом) становится финальной семьей
            final_families[parent_leader] = parent_members
            processed_leaders.add(parent_leader)

        return final_families


    def _analyze_substring_patterns(self, glossary_list: list, min_group_size: int = 4, min_substring_len: int = 3, return_hierarchy: bool = False):
        """
        Второй уровень анализа: ищет общие ПОДСТРОКИ внутри токенов терминов.
        Использует тот же эффективный алгоритм "Гига-глоссарий", но на уровне символов.
        """
        if not glossary_list:
            return {}

        giga_glossary_chars = []
        terms_by_id = {i: entry['original'] for i, entry in enumerate(glossary_list) if 'original' in entry}
        
        for term_id, original_text in terms_by_id.items():
            tokens = self._get_universal_tokens(original_text)
            for token in tokens:
                if len(token) > min_substring_len:
                    for i in range(len(token)):
                        suffix_string = token[i:]
                        giga_glossary_chars.append((suffix_string, term_id))
        
        if not giga_glossary_chars:
            return {}

        giga_glossary_chars.sort(key=lambda x: x[0])
        
        patterns_found = defaultdict(set)
        
        for i in range(len(giga_glossary_chars) - 1):
            suffix1, term_id1 = giga_glossary_chars[i]
            suffix2, term_id2 = giga_glossary_chars[i+1]
            
            if term_id1 == term_id2:
                continue

            # --- НАЧАЛО ИСПРАВЛЕНИЯ
            common_prefix_len = 0
            for k in range(min(len(suffix1), len(suffix2))):
                if suffix1[k] == suffix2[k]:
                    common_prefix_len += 1
                else:
                    break
            
            if common_prefix_len < min_substring_len:
                continue

            common_prefix = suffix1[:common_prefix_len]
            # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

            if return_hierarchy:
                for j in range(len(common_prefix), min_substring_len - 1, -1):
                    pattern = common_prefix[:j]
                    patterns_found[pattern].add(term_id1)
                    patterns_found[pattern].add(term_id2)
            else:
                patterns_found[common_prefix].add(term_id1)
                patterns_found[common_prefix].add(term_id2)

        final_patterns = {}
        for pattern, member_ids in patterns_found.items():
            if len(member_ids) >= min_group_size:
                final_patterns[pattern] = {terms_by_id[id] for id in member_ids}
                
        return final_patterns
    
    def analyze_patterns_with_substring(self, glossary_list: list, min_group_size: int = 2, return_hierarchy: bool = False, min_overlap_len: int = 3): # <--- НОВЫЙ АРГУМЕНТ
        """
        ДВУХУРОВНЕВЫЙ анализатор паттернов.
        1. Уровень токенов: ищет общие последовательности целых слов.
        2. Уровень подстрок: ищет общие части внутри слов.
    
        Args:
            glossary_list (list): Список словарей с терминами.
            min_group_size (int): Минимальное количество участников для паттернов из токенов.
                                  Для подстрок используется удвоенное значение.
            return_hierarchy (bool): Если True, возвращает все под-паттерны (для UI).
                                    Если False, возвращает только самые длинные (для AI).
    
        Returns:
            dict: Словарь, где ключ - это паттерн, а значение - множество терминов-участников.
        """
        if not glossary_list:
            return {}
    
        # --- УРОВЕНЬ 1: Анализ на основе токенов (как и раньше) ---
        giga_glossary_tokens = []
        terms_by_id = {i: entry['original'] for i, entry in enumerate(glossary_list) if 'original' in entry}
        
        for term_id, original_text in terms_by_id.items():
            tokens = self._get_universal_tokens(original_text)
            for i in range(len(tokens)):
                suffix_tuple = tuple(tokens[i:])
                giga_glossary_tokens.append((suffix_tuple, term_id))
        
        giga_glossary_tokens.sort(key=lambda x: x[0])
        
        token_patterns_found = defaultdict(set)
        
        for i in range(len(giga_glossary_tokens) - 1):
            suffix1_tuple, term_id1 = giga_glossary_tokens[i]
            suffix2_tuple, term_id2 = giga_glossary_tokens[i+1]
            
            if term_id1 == term_id2: continue
    
            common_prefix_len = 0
            for k in range(min(len(suffix1_tuple), len(suffix2_tuple))):
                if suffix1_tuple[k] == suffix2_tuple[k]:
                    common_prefix_len += 1
                else:
                    break
            
            if common_prefix_len == 0: continue
    
            lcp_tokens = suffix1_tuple[:common_prefix_len]
    
            if return_hierarchy:
                for j in range(len(lcp_tokens), 0, -1):
                    pattern_tuple = lcp_tokens[:j]
                    pattern_string = " ".join(pattern_tuple)
                    if len(pattern_tuple) == 1 and len(pattern_string) <= 1: continue
                    token_patterns_found[pattern_string].add(term_id1)
                    token_patterns_found[pattern_string].add(term_id2)
            else:
                pattern_tuple = lcp_tokens
                pattern_string = " ".join(pattern_tuple)
                if len(pattern_tuple) == 1 and len(pattern_string) <= 1: continue
                token_patterns_found[pattern_string].add(term_id1)
                token_patterns_found[pattern_string].add(term_id2)
                
        final_token_patterns = {
            p: {terms_by_id[id] for id in ids} 
            for p, ids in token_patterns_found.items() 
            if len(ids) >= min_group_size
        }

        # --- УРОВЕНЬ 2: Анализ на основе подстрок ---
        # Используем удвоенный порог размера группы, как и было предложено.
        min_substring_group_size = min_group_size * 2
        substring_patterns = self._analyze_substring_patterns(
            glossary_list, 
            min_group_size=min_substring_group_size,
            min_substring_len=min_overlap_len, # <--- ПЕРЕДАЕМ ПАРАМЕТР ДАЛЬШЕ
            return_hierarchy=return_hierarchy
        )
        
        # --- ШАГ 3: Объединение результатов ---
        # Паттерны из подстрок добавляются к основным, перезаписывая, если есть совпадения.
        # Это нормально, т.к. группы могут быть разными.
        final_token_patterns.update(substring_patterns)
        
        return final_token_patterns
    
    def find_untranslated_residue(self, glossary_list: list, exceptions_set=None):
        """
        Находит некириллические фрагменты двухступенчатым методом "вычитания".
        Версия 6.0: Сначала удаляет кириллицу, затем универсально очищает пунктуацию и цифры.
        """
        if exceptions_set is None:
            exceptions_set = set()

        # Паттерн №1: Находит и удаляет всю кириллицу.
        cyrillic_pattern = re.compile(r'[а-яА-ЯёЁ]+')
        # Паттерн №2: Находит и удаляет ВСЁ, что не является буквой (цифры, пунктуация, символы).
        # \W - не-буквенно-цифровой символ, \d - цифры, _ - подчеркивание.
        cleanup_pattern = re.compile(r'[\W\d_]+')
        
        residue_map = defaultdict(lambda: {'entries_with_residue': []})

        for entry in glossary_list:
            for field_content, location_name in [(entry.get('rus', ''), 'rus'), (entry.get('note', ''), 'note')]:
                if not field_content:
                    continue

                # Шаг 1: "Вычитаем" кириллицу.
                no_cyrillic_str = cyrillic_pattern.sub(' ', field_content)
                
                # Шаг 2: В том, что осталось, "вычитаем" всю пунктуацию и цифры.
                pure_residue_str = cleanup_pattern.sub(' ', no_cyrillic_str)
                
                # Шаг 3: Собираем чистые "остатки".
                found_residues = pure_residue_str.strip().split()
                
                if not found_residues:
                    continue

                for residue in found_residues:
                    residue_lower = residue.lower()
                    
                    if residue_lower in exceptions_set:
                        continue
                    
                    residue_map[residue_lower]['entries_with_residue'].append({
                        'entry': entry,
                        'location': location_name
                    })

        return dict(residue_map)

class GlossaryReplacer:
    """
    Класс для пакетной обработки HTML-файлов с заменой терминов из глоссария.
    Управляет жизненным циклом временного обучения Jieba.
    """

    def __init__(self, full_glossary_data: dict):
        """
        Инициализирует "мозг" с полным словарем глоссария.
        """
        self.glossary = full_glossary_data
        self.filter = SmartGlossaryFilter()

    def prepare(self):
        """
        Обучает Jieba ОДИН РАЗ, только если в глоссарии есть CJK-термины.
        """
        if not JIEBA_AVAILABLE or not self.glossary:
            return

        # --- ГЛАВНОЕ ИСПРАВЛЕНИЕ: Проверяем, есть ли что обучать ---
        has_cjk_terms = any(LanguageDetector.is_cjk_text(term) for term in self.glossary.keys())
        
        if has_cjk_terms:
            self.filter.chinese_processor.add_custom_words(self.glossary)
            print("[GlossaryReplacer] Jieba has been temporarily trained with CJK terms for batch processing.")
        # --- КОНЕЦ ИСПРАВЛЕНИЯ ---

    def cleanup(self):
        """Сбрасывает Jieba ОДИН РАЗ после обработки серии файлов."""
        if JIEBA_AVAILABLE:
            self.filter.chinese_processor.reset()
            print("[GlossaryReplacer] Jieba state has been reset.")

    def process_html(self, html_content: str) -> str:
        """
        Обрабатывает ОДИН HTML-файл, предполагая, что Jieba уже обучен.
        """
        from ..utils.text import replace_terms_in_html

        if not self.glossary or not html_content:
            return html_content

        try:
            from bs4 import BeautifulSoup
            full_text = BeautifulSoup(html_content, 'html.parser').get_text()
        except ImportError:
            full_text = re.sub('<[^<]+?>', '', html_content)

        found_glossary_subset = self.filter.filter_glossary_for_text(
            full_glossary=self.glossary,
            text=full_text,
            fuzzy_threshold=100
        )

        if not found_glossary_subset:
            return html_content

        replacements = {
            original: data.get('rus')
            for original, data in found_glossary_subset.items()
            if data.get('rus')
        }
        
        if not replacements:
            return html_content

        return replace_terms_in_html(html_content, replacements)
