import zipfile
import re
from bs4 import BeautifulSoup
from PyQt6 import QtCore
from PyQt6.QtCore import pyqtSignal, QThread
from num2words import num2words
import emoji
if not hasattr(emoji, 'UNICODE_EMOJI'):
    emoji.UNICODE_EMOJI = {} 

from recognizers_text import Culture
from recognizers_number import recognize_number

class NumeralsExtractionWorker(QtCore.QThread):
    finished = pyqtSignal(list, str) 
    progress = pyqtSignal(int, int)

    def __init__(self, epub_path, html_files, parent=None):
        super().__init__(parent)
        self.epub_path = epub_path
        self.html_files = html_files
        self.is_running = True

    def run(self):
        result_map = {} 
        
        cultures = [
            Culture.English, Culture.Chinese, Culture.Japanese, 
            Culture.French, Culture.Spanish, 'de-de'
        ]
        
        # --- Черный список идиом, похожих на числа ---
        # 万一 (wan yi) - "на всякий случай", часто парсится как 10001 или 11000
        # 千万 (qian wan) - "ни в коем случае" / "обязательно", парсится как 10000000
        idiom_exclusion = {'万一', '千万'}

        # --- Корейские словари (без изменений) ---
        k_ambiguous = {'일', '이', '사'} 
        k_sino = {
            '영':0, '일':1, '이':2, '삼':3, '사':4, '오':5, '육':6, '칠':7, '팔':8, '구':9,
            '십':10, '백':100, '천':1000, '만':10000, '억':100000000, '조':1000000000000
        }
        k_native = {
            '하나':1, '둘':2, '셋':3, '넷':4, '다섯':5, '여섯':6, '일곱':7, '여덟':8, '아홉':9, '열':10,
            '스물':20, '서른':30, '마흔':40, '쉰':50, '예순':60, '일흔':70, '여든':80, '아흔':90,
            '한':1, '두':2, '세':3, '네':4
        }
        
        all_k_keys = list(k_sino.keys()) + list(k_native.keys())
        all_k_keys.sort(key=len, reverse=True)
        k_pattern_str = f"({'|'.join(all_k_keys)})"
        k_regex = re.compile(rf"({k_pattern_str}(\s*{k_pattern_str})*)")

        try:
            with zipfile.ZipFile(self.epub_path, 'r') as zf:
                total = len(self.html_files)
                for i, filename in enumerate(self.html_files):
                    if not self.is_running: break
                    self.progress.emit(i + 1, total)
                    
                    raw = zf.read(filename).decode('utf-8', 'ignore')
                    soup = BeautifulSoup(raw, 'html.parser')
                    
                    # --- 1. ИГНОРИРУЕМ ЗАГОЛОВКИ И СЛУЖЕБНЫЕ ТЕГИ ---
                    for tag in soup(["script", "style", "h1", "h2", "h3", "h4", "h5", "h6", "title"]):
                        tag.extract()
                        
                    text = soup.get_text(separator=' ')
                    
                    # --- A. Стандартный поиск (Microsoft Recognizers) ---
                    for culture in cultures:
                        results = recognize_number(text, culture)
                        for item in results:
                            if item.resolution and 'value' in item.resolution:
                                result_map[item.text] = item.resolution['value']

                    # --- B. Поиск гибридных чисел (40万, 12756千) ---
                    # Этот метод перекрывает стандартный, если находит совпадения
                    mixed_results = self._parse_mixed_cjk_number(text)
                    result_map.update(mixed_results)

                    # --- C. Корейский поиск ---
                    matches = k_regex.finditer(text)
                    for match in matches:
                        phrase = match.group(0)
                        stripped_phrase = phrase.replace(" ", "")
                        if len(stripped_phrase) == 1 and stripped_phrase in k_ambiguous: continue
                        val = self._parse_korean_number(stripped_phrase, k_sino, k_native)
                        if val is not None: result_map[phrase] = val

        except Exception as e:
            self.finished.emit([], f"Ошибка чтения EPUB: {e}")
            return

        final_glossary = []
        
        high_magnitude_markers = [
            'million', 'billion', 'trillion', 
            '万', '亿', '兆', '만', '억', '조', '경', '해'
        ]

        for key_text, val in result_map.items():
            try:
                # Фильтр идиом
                original_clean = key_text.strip()
                if original_clean in idiom_exclusion:
                    continue

                num_val = float(val)
                is_integer = num_val.is_integer()
                if is_integer: num_val = int(num_val)
                
                # --- ЛОГИКА ФИЛЬТРАЦИИ V3 ---
                
                # Проверка: состоит ли строка только из цифр/знаков (12345, 10.5, -99)
                is_pure_digits = re.match(r'^[\d\.,\-\s]+$', original_clean) is not None

                # 1. ПОЛНЫЙ ЗАПРЕТ АРАБСКИХ ЦИФР
                # Если это просто цифры (даже большие, даже дробные) — выбрасываем.
                if is_pure_digits:
                    continue

                # 2. ВЕТКА ДЛЯ СЛОВ И ГИБРИДОВ (40万, 12756千, 1.2 million)
                should_keep = False
                
                # Важно: добавляем '千' (тысяча) к списку маркеров локально, 
                extended_markers = high_magnitude_markers + ['千']
                has_marker = any(marker in original_clean for marker in extended_markers)
                
                if has_marker:
                    # Главный критерий: Число + Иероглиф/Слово (40万, 1.2 million)
                    should_keep = True
                elif is_integer:
                    # Если маркера нет, оставляем только крупные словесные числа (>= 100)
                    # (например, "сто пятьдесят", но убираем "пять")
                    if abs(num_val) >= 100:
                        should_keep = True
                else:
                    # Словесные дроби без маркеров (крайне редко, но оставляем)
                    should_keep = True

                if not should_keep:
                    continue

                rus_text = num2words(num_val, lang='ru')
                
                # --- ИСПРАВЛЕНИЕ РУССКОЙ ГРАММАТИКИ ---
                # 1. Удаляем "ноль целых" / "ноль целая" в начале
                if rus_text.startswith("ноль целых") or rus_text.startswith("ноль целая"):
                    rus_text = re.sub(r'^ноль цел(ых|ая)', '', rus_text).strip()
                
                # 2. Заменяем "целых" и "целая" на "и" в середине (-99.99 -> ...девять и девяносто...)
                if " целых " in rus_text or " целая " in rus_text:
                    rus_text = rus_text.replace(" целых ", " и ").replace(" целая ", " и ")
                
                final_glossary.append({
                    "original": original_clean,
                    "rus": rus_text,
                    "note": "числительное"
                })
            except:
                continue
        
        final_glossary.sort(key=lambda x: len(x['original']), reverse=True)
        self.finished.emit(final_glossary, f"Найдено {len(final_glossary)} сложных числительных.")


    def _parse_mixed_cjk_number(self, text):
        """
        Ручной парсинг смешанных чисел вида '40万', '12.5亿', '12756千'.
        Библиотеки часто пропускают их или возвращают текст вместо значения.
        """
        results = {}
        # Паттерн: Число (целое или дробное) + Множитель
        # M = миллион, B = миллиард (для английского смешанного стиля), плюс CJK множители
        # Включаем T/M/B для английского, если нужно, но здесь фокус на CJK
        
        multipliers = {
            '千': 1000, 
            '万': 10000, '만': 10000,
            '亿': 100000000, '억': 100000000,
            '兆': 1000000000000, '조': 1000000000000,
            '경': 10000000000000000
        }
        
        pattern = re.compile(r'(-?\d+(?:\.\d+)?)\s*([千万亿兆만억조경])')
        
        matches = pattern.finditer(text)
        for match in matches:
            full_str = match.group(0)
            number_part = float(match.group(1))
            unit_char = match.group(2)
            
            if unit_char in multipliers:
                val = number_part * multipliers[unit_char]
                results[full_str] = val
                
        return results
        
    def _parse_korean_number(self, text, k_sino, k_native):
        """Простейший парсер склеенного корейского текста в число"""
        # Это базовая реализация. Для идеальной точности нужны сложные алгоритмы,
        # но для задач "найти числа в тексте" сумматор работает неплохо.
        total = 0
        current_chunk = 0
        
        # Сначала пробуем Sino логику (она сложнее с разрядами)
        # Разбиваем строку на токены. Так как мы склеили пробелы, идем по символам, 
        # но Native слова могут быть длиннее 1 символа.
        # Для простоты: если в тексте есть Native слова, считаем простой суммой (20+1).
        # Если только Sino - учитываем разряды.
        
        is_native = any(k in text for k in k_native.keys())
        
        if is_native:
            # Native логика простая: обычно это десятки + единицы (Сымуль-тасот = 25)
            # Просто ищем известные слова и суммируем
            temp_text = text
            val_acc = 0
            # Жадный поиск
            while temp_text:
                found = False
                for k, v in sorted(k_native.items(), key=lambda x: len(x[0]), reverse=True):
                    if temp_text.startswith(k):
                        val_acc += v
                        temp_text = temp_text[len(k):]
                        found = True
                        break
                if not found: 
                    # Если встретили что-то странное внутри (напр. смешение систем), прерываем
                    # Но в нашем случае регекс гарантирует только цифры.
                    # Может быть Sino цифра внутри Native контекста? Редко.
                    if temp_text[0] in k_sino:
                         val_acc += k_sino[temp_text[0]]
                         temp_text = temp_text[1:]
                    else:
                        break 
            return val_acc
        else:
            # Sino логика (разряды 10, 100, 1000...)
            # 삼백이십오 (3-100-2-10-5) -> 3*100 + 2*10 + 5
            # Проход по символам (они все длиной 1 в Sino)
            
            temp_val = 0 # Текущее число перед разрядом (напр "3" перед "100")
            
            # Особые большие множители, сбрасывающие накопление
            major_units = {'만': 10000, '억': 100000000}
            
            for char in text:
                if char not in k_sino: continue
                n = k_sino[char]
                
                if char in major_units:
                    total += (current_chunk + temp_val) * n
                    current_chunk = 0
                    temp_val = 0
                elif n >= 10: # 10, 100, 1000
                    if temp_val == 0: temp_val = 1 # "Сып" (10) -> это 1*10
                    current_chunk += temp_val * n
                    temp_val = 0
                else: # 0-9
                    temp_val = n
            
            total += current_chunk + temp_val
            return total