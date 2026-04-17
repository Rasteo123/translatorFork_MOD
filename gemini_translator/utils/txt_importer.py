# gemini_translator/utils/txt_importer.py

import os
import re
from collections import Counter, defaultdict
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QDialogButtonBox, QLabel,
    QListWidget, QListWidgetItem, QSplitter, QWidget, QHeaderView, QTableWidget,
    QTableWidgetItem, QLineEdit, QGroupBox
)
from PyQt6.QtCore import Qt, QTimer
from .epub_tools import EpubCreator
from .language_tools import LanguageDetector


try:
    from recognizers_text import Culture
    from recognizers_number import recognize_number
    HAS_RECOGNIZERS = True
except ImportError:
    HAS_RECOGNIZERS = False


def smart_replace_number_in_title(title, new_number_int):
    """
    Умная замена числа в заголовке.
    Приоритет:
    1. CJK паттерны (第X章, 第X, 제X장).
    2. Microsoft Recognizers (все языки).
    3. Простая цифра.
    4. Если ничего нет — добавление префикса.
    """
    
    # --- 1. СПЕЦИАЛЬНЫЕ CJK/КАНДЗИ ПАТТЕРНЫ (Самый высокий приоритет) ---
    # Ловит: 第五章, 第5章, 第一, 제5장 (Корейский)
    # Группа 1: Префикс (第 или 제)
    # Группа 2: Само число (Иероглифы или Цифры)
    # Группа 3: Суффикс (章, 回, 节, 장 и т.д. или пусто)
    
    cjk_pattern = re.compile(r'(第|제)\s*([0-9零一二三四五六七八九十百千万两]+)\s*([章节回장편]?)')
    match = cjk_pattern.search(title)
    if match:
        # Заменяем только Группу 2 (число), сохраняя префикс и суффикс
        # span(2) возвращает индексы начала и конца числа
        start, end = match.span(2)
        return title[:start] + str(new_number_int) + title[end:]

    # --- 2. MICROSOFT RECOGNIZERS (Если паттерн не сработал) ---
    if HAS_RECOGNIZERS:
        results = []
        # Добавляем Japanese (он часто лучше ловит смешанные кандзи-числа) и English
        for cult in [Culture.Chinese, Culture.Japanese, Culture.English, Culture.Korean]:
            try:
                # Пытаемся найти
                found = recognize_number(title, cult)
                results.extend(found)
            except: pass
        
        if results:
            # Находим самое левое вхождение
            leftmost = min(results, key=lambda x: x.start)
            start = leftmost.start
            end = leftmost.end
            return title[:start] + str(new_number_int) + title[end:]

    # --- 3. FALLBACK: ОБЫЧНЫЕ ЦИФРЫ ---
    # Если библиотека не подключена или ничего не нашла (например "Chapter One" без библиотеки)
    if re.search(r'\d+', title):
        return re.sub(r'\d+', str(new_number_int), title, count=1)
        
    # --- 4. ЕСЛИ ЧИСЕЛ НЕТ ---
    return f"({new_number_int}) {title}"


class RegexExamplesDialog(QDialog):
    """Простой диалог для выбора примера регулярного выражения."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Примеры RegEx для заголовков")
        self.selected_regex = None
        
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Выберите пример (двойной клик или Enter):"))
        
        self.list_widget = QListWidget()
        # ИСПРАВЛЕНО: Убраны знаки вопроса в конце. Суффикс ОБЯЗАТЕЛЕН.
        # Иначе "第一条" (Первое правило/сообщение) станет главой.
        examples = [
            # 第 + пробелы(опц) + цифры + пробелы(опц) + Иероглиф(Глава/Секция/Раунд)
            {"name": "Китайский: 第 + Число + Иероглиф (Строго)", "regex": r"^第\s*[0-90-9零一二三四五六七八九十百千万两]+\s*[章节回]"},
            # Для редких случаев, когда иероглифа нет, но есть точка (第1. )
            {"name": "Китайский: 第 + Число + Точка", "regex": r"^第\s*[0-90-9零一二三四五六七八九十百千万两]+\."},
            {"name": "Английский: Chapter + Число", "regex": r"^Chapter\s*\d+"},
            {"name": "Русский: Глава + Число", "regex": r"^Глава\s*\d+"},
            {"name": "Просто число (1, 2, 3...)", "regex": r"^\d+\s*$"},
            {"name": "Число с точкой (1. ...)", "regex": r"^\d+\.\s"},
        ]
        
        for ex in examples:
            item = QListWidgetItem(f"{ex['name']}\n    (Пример: {ex['regex']})")
            item.setData(Qt.ItemDataRole.UserRole, ex['regex'])
            self.list_widget.addItem(item)
            
        self.list_widget.itemActivated.connect(self.accept_selection)
        layout.addWidget(self.list_widget)
        
        buttons = QDialogButtonBox()
        buttons.addButton("ОК", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)

        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def accept_selection(self, item=None):
        current_item = self.list_widget.currentItem()
        if not current_item:
            return
        self.selected_regex = current_item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def get_selected_regex(self):
        return self.selected_regex




class TxtChapterAnalyzer:
    """
    Анализирует текстовое содержимое.
    Версия 25.0 (Structure Editor Support):
    - Добавлен метод scan_chapter_boundaries для получения индексов строк.
    - Сохранена обратная совместимость для старых методов.
    """
    def __init__(self, text_content):
        self.lines = text_content.splitlines()
        self.total_char_count = len(text_content)
        # Пре-расчет длин строк (с учетом \n, которого нет в splitlines)
        self.line_lengths = [len(l) + 1 for l in self.lines] 

    def analyze_potential_markers(self):
        total_lines_count = len(self.lines)
        sample_indices = self._get_sample_indices(total_lines_count)
        
        numeric_counter = Counter()
        text_starts = Counter()
        indent_pattern = re.compile(r'^\s*[\u3000\s]{1,2}')
        cjk_num_regex = r'[0-9零一二三四五六七八九十百千万两]+'
        cjk_idea_pat = re.compile(rf'^(第)\s*({cjk_num_regex})\s*([章节回]?)')

        indent_hits = 0
        potential_headers_in_sample = 0

        for i in sample_indices:
            raw_line = self.lines[i]
            line_l = raw_line.lstrip()
            if not line_l or len(line_l) > 300: continue

            cjk_match = cjk_idea_pat.match(line_l)
            if cjk_match:
                suffix = cjk_match.group(3)
                numeric_counter[f"第...{suffix}" if suffix else "第..."] += 1
            
            parts = line_l.split()
            if parts:
                first = parts[0]
                if len(first) > 1 or any('\u4e00' <= c <= '\u9fff' for c in first):
                    text_starts[first] += 1

            if not indent_pattern.match(raw_line) and len(line_l) > 2:
                potential_headers_in_sample += 1
                if i + 1 < total_lines_count and indent_pattern.match(self.lines[i+1]):
                    indent_hits += 1

        candidates = set()
        candidates.add(r'^\d+\.?\s*$')
        for p, _ in numeric_counter.most_common(5):
            if "第" in p:
                sfx = p.split("...")[-1]
                candidates.add(rf'^第\s*{cjk_num_regex}\s*{re.escape(sfx)}' if sfx else rf'^第\s*{cjk_num_regex}')
            else:
                candidates.add(rf'^{re.escape(p)}\s*\d+')
        for word, count in text_starts.most_common(5):
            if count >= 2: candidates.add(rf'^{re.escape(word)}\s')

        final_results = []

        if potential_headers_in_sample > 0 and (indent_hits / potential_headers_in_sample) > 0.4:
            indices = []
            for i in range(total_lines_count - 1):
                if not indent_pattern.match(self.lines[i]) and len(self.lines[i].strip()) > 1:
                    if self.lines[i+1].strip() and indent_pattern.match(self.lines[i+1]):
                        indices.append(i)
            
            score, info = self._validate_indices(indices)
            if score > 0:
                final_results.append((("Контекст (Нет отступа перед 　　)", "__CONTEXT_INDENT_ANALYSIS__", "context_indent"), info['count']))

        for pat in candidates:
            try:
                fast_reg = re.compile(pat.replace('^', r'^\s*'), re.IGNORECASE)
                indices = [idx for idx, line in enumerate(self.lines) if fast_reg.match(line)]
                score, info = self._validate_indices(indices)
                if score > 0:
                    display = self._format_display_name(pat)
                    final_results.append(((display, pat, 'direct_regex'), info['count'], score))
            except: continue

        final_results.sort(key=lambda x: (x[2] if len(x)>2 else 0, x[1]), reverse=True)
        return [item[0:2] for item in final_results]

    def _validate_indices(self, indices):
        count = len(indices)
        if count < 3: return 0, {}
        sizes = []
        last_idx = 0
        for idx in indices:
            sizes.append(sum(self.line_lengths[last_idx:idx]))
            last_idx = idx
        sizes.append(sum(self.line_lengths[last_idx:]))
        avg_size = sum(sizes) / len(sizes)
        max_size = max(sizes)
        score = 100
        ratio = max_size / (avg_size or 1)
        if ratio > 15: score -= 70
        elif ratio > 7: score -= 40
        if avg_size > 100000: score -= 30
        if avg_size < 500: score -= 20
        return max(0, score), {'count': count, 'avg': avg_size, 'max': max_size}

    def _get_sample_indices(self, total):
        indices = list(range(min(5000, total)))
        if total > 15000:
            mid = total // 2
            indices.extend(range(mid - 2500, mid + 2500))
            indices.extend(range(total - 5000, total))
        return sorted(list(set(indices)))

    def _format_display_name(self, pat):
        d = pat.replace(r'[0-9零一二三四五六七八九十百千万两]+', '[Число]').replace(r'\d+', '[Число]')
        d = d.replace('^', '[Начало] ').replace('\\s*', ' ').replace('\\', '').strip()
        return d

    def get_marker_regex(self, marker_word, context, custom_regex=None):
        if custom_regex == r"__CONTEXT_INDENT_ANALYSIS__":
            return None 
        try:
            if custom_regex: return re.compile(custom_regex, re.IGNORECASE)
            return None 
        except re.error:
            return None

    def scan_chapter_boundaries(self, custom_regex=None) -> list:
        """
        Возвращает список словарей с информацией о началах глав.
        Каждый элемент: {'line_idx': int, 'title': str, 'char_idx': int}
        """
        boundaries = []
        is_context_indent_split = (custom_regex == r"__CONTEXT_INDENT_ANALYSIS__")
        marker_regex = self.get_marker_regex(None, None, custom_regex)
        
        if not is_context_indent_split and not marker_regex:
            return []

        indent_pattern = re.compile(r'^\s*[\u3000\s]{1,2}')
        current_char_idx = 0
        
        # Предварительный расчет смещений символов для каждой строки (чтобы не считать в цикле)
        # char_offsets[i] = индекс символа начала строки i
        char_offsets = [0] * (len(self.lines) + 1)
        acc = 0
        for i, length in enumerate(self.line_lengths):
            char_offsets[i] = acc
            acc += length
        
        for i, line in enumerate(self.lines):
            raw_line = line 
            clean_line = raw_line.strip() 
            is_header = False
            
            if not clean_line:
                continue

            if marker_regex:
                if marker_regex.match(clean_line):
                    is_header = True
            
            elif is_context_indent_split:
                if len(clean_line) < 300: 
                    if not indent_pattern.match(raw_line):
                        is_next_line_indented = False
                        if i + 1 < len(self.lines):
                            next_line = self.lines[i+1]
                            if next_line.strip() and indent_pattern.match(next_line):
                                is_next_line_indented = True
                        if is_next_line_indented:
                            is_header = True
            
            if is_header:
                boundaries.append({
                    'line_idx': i,
                    'title': clean_line,
                    'char_idx': char_offsets[i]
                })
                
        return boundaries

    def _split_by_marker(self, marker_word=None, context=None, custom_regex=None):
        # Обертка для сохранения совместимости, использующая новый scan_chapter_boundaries
        boundaries = self.scan_chapter_boundaries(custom_regex)
        if not boundaries:
            if "".join(self.lines).strip():
                 return [self.lines], ["Весь текст"]
            return [], []
            
        chapters_lines = []
        titles = []
        
        # Проверяем, есть ли текст до первой главы (Предисловие)
        if boundaries[0]['line_idx'] > 0:
            preamble_lines = self.lines[0 : boundaries[0]['line_idx']]
            # Исключаем пустые строки в начале/конце, но сохраняем форматирование внутри
            if "".join(preamble_lines).strip():
                full_preamble = "".join(preamble_lines).strip()
                title = "Начало / Метаданные" if len(full_preamble) < 500 else "Предисловие"
                chapters_lines.append([l + "\n" for l in preamble_lines])
                titles.append(title)
        
        for i in range(len(boundaries)):
            start_line = boundaries[i]['line_idx']
            # Конец текущей главы = начало следующей. Для последней главы = конец файла.
            end_line = boundaries[i+1]['line_idx'] if i + 1 < len(boundaries) else len(self.lines)
            
            # Текст главы (включая строку заголовка? Обычно заголовок исключают из тела, 
            # но в scan_boundaries мы нашли строку заголовка. В оригинале clean_line становился заголовком,
            # а тело собиралось дальше. Здесь мы просто берем срез строк.)
            # В старой логике заголовок НЕ попадал в content, он шел в titles.
            # Значит, content берем с start_line + 1
            
            chunk = self.lines[start_line+1 : end_line]
            chapters_lines.append([l + "\n" for l in chunk])
            titles.append(boundaries[i]['title'])
            
        return chapters_lines, titles

    def calculate_stats(self, marker_word=None, context=None, custom_regex=None) -> dict:
        chapters_lines, titles = self._split_by_marker(marker_word, context, custom_regex)
        
        if not chapters_lines:
             return {'count': 0, 'min_val': 0, 'max_val': 0, 'avg': 0}

        valid_chapters_info = []
        
        for title, lines in zip(titles, chapters_lines):
            text = "".join(lines).strip()
            valid_chapters_info.append({
                'length': len(text),
                'title': title,
                'content_snippet': text[:150]
            })
        
        if not valid_chapters_info:
            return {'count': 0, 'min_val': 0, 'max_val': 0, 'avg': 0}

        has_preamble = valid_chapters_info[0]['title'] in ("Начало / Метаданные", "Предисловие")
        min_chap = min(valid_chapters_info, key=lambda x: x['length'])
        max_chap = max(valid_chapters_info, key=lambda x: x['length'])
        total_len = sum(x['length'] for x in valid_chapters_info)
        
        return {
            'count': len(valid_chapters_info),
            'has_preamble': has_preamble,
            'min_val': min_chap['length'],
            'min_title': min_chap['title'],
            'min_snippet': min_chap['content_snippet'],
            'max_val': max_chap['length'],
            'max_title': max_chap['title'],
            'max_snippet': max_chap['content_snippet'],
            'avg': total_len / len(valid_chapters_info)
        }
    
    def split_into_chapters(self, marker_word=None, context=None, custom_regex=None) -> list:
        # Этот метод больше не используется напрямую Wizard'ом во второй фазе, 
        # но оставлен для совместимости или быстрой генерации.
        chapters_lines, titles = self._split_by_marker(marker_word, context, custom_regex)
        chapters_data = []
        for title, lines in zip(titles, chapters_lines):
            content = "".join(lines)
            if content.strip():
                chapters_data.append((title, content))
        return chapters_data





class ChapterViewerDialog(QDialog):
    """Диалог для просмотра содержимого главы и ручного разделения."""
    def __init__(self, chapter_lines, start_line_idx, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Просмотр главы")
        self.resize(700, 600)
        self.lines = chapter_lines
        self.start_line_idx = start_line_idx # Глобальный индекс первой строки этой главы
        self.selected_split_index = None # Глобальный индекс, если выбрали
        self.selected_split_text = None

        layout = QVBoxLayout(self)
        
        # Инструкция
        info_lbl = QLabel("Вы можете выбрать строку и нажать 'Сделать заголовком', чтобы разбить главу.")
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        # Таблица строк
        self.table = QTableWidget()
        self.table.setColumnCount(1)
        self.table.setHorizontalHeaderLabels(["Содержимое строки"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setRowCount(len(self.lines))
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        
        # Заполняем таблицу
        for i, line in enumerate(self.lines):
            item = QTableWidgetItem(line.rstrip())
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.table.setItem(i, 0, item)
            # В вертикальном заголовке показываем относительный номер
            self.table.setVerticalHeaderItem(i, QTableWidgetItem(str(i+1)))

        layout.addWidget(self.table)
        
        btn_layout = QHBoxLayout()
        self.btn_split = QPushButton("Сделать заголовком новой главы")
        self.btn_split.clicked.connect(self.mark_as_header)
        btn_close = QPushButton("Закрыть")
        btn_close.clicked.connect(self.reject)
        
        btn_layout.addWidget(self.btn_split)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def mark_as_header(self):
        row = self.table.currentRow()
        if row < 0:
            return
        
        text = self.lines[row].strip()
        if not text:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Нельзя сделать пустую строку заголовком.")
            return

        # Глобальный индекс строки = start_line_idx + (offset + 1, так как строка заголовка исключена из lines в Viewer)
        # НО: в Viewer мы передаем `lines` как "тело главы".
        # Значит, row 0 в Viewer - это `start_line_idx + 1` (следующая после заголовка).
        global_idx = self.start_line_idx + 1 + row
        
        self.selected_split_index = global_idx
        self.selected_split_text = text
        self.accept()


class SortableTableWidgetItem(QTableWidgetItem):
    """Ячейка, которая умеет правильно сортировать числа."""
    def __lt__(self, other):
        try:
            # Пытаемся сравнить как числа (удаляя пробелы и запятые)
            val1 = float(self.text().replace(' ', '').replace(',', '').replace('симв.', ''))
            val2 = float(other.text().replace(' ', '').replace(',', '').replace('симв.', ''))
            return val1 < val2
        except ValueError:
            # Если не вышло — как текст
            return super().__lt__(other)


class TxtImportWizardDialog(QDialog):
    """
    Мастер импорта TXT (2 этапа).
    Этап 1: Выбор RegEx.
    Этап 2: Редактирование оглавления (Таблица).
    """
    def __init__(self, txt_path, output_dir, parent=None):
        super().__init__(parent)
        self.txt_path = txt_path
        self.output_dir = output_dir
        self.generated_epub_path = None
        
        try:
            with open(txt_path, 'r', encoding='utf-8') as f:
                content = f.read()
            self.analyzer = TxtChapterAnalyzer(content)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Ошибка чтения файла", f"Не удалось прочитать файл:\n{e}")
            QtCore.QTimer.singleShot(0, self.reject)
            return

        self.setWindowTitle("Мастер импорта TXT")
        self.resize(900, 700) # Чуть больше для таблицы
        
        # Данные о структуре: список словарей {'line_idx', 'title', 'char_idx'}
        self.structure_data = [] 

        self.layout_stack = QtWidgets.QStackedWidget(self)
        main_layout = QVBoxLayout(self)
        main_layout.addWidget(self.layout_stack)
        
        # Инициализация страниц
        self.page_regex = QWidget()
        self.page_toc = QWidget()
        
        self._init_page_regex()
        self._init_page_toc()
        
        self.layout_stack.addWidget(self.page_regex)
        self.layout_stack.addWidget(self.page_toc)
        
        self._run_structure_analysis()

    # --- СТРАНИЦА 1: REGEX ---
    def _init_page_regex(self):
        layout = QVBoxLayout(self.page_regex)
        
        # Блок ввода RegEx
        gb_regex = QGroupBox("Шаг 1: Настройка поиска глав")
        l_reg = QHBoxLayout(gb_regex)
        self.regex_input = QLineEdit()
        self.regex_input.setPlaceholderText("Введите RegEx или выберите из списка...")
        btn_ex = QPushButton("Примеры…")
        btn_ex.clicked.connect(self._open_regex_examples)
        l_reg.addWidget(self.regex_input)
        l_reg.addWidget(btn_ex)
        layout.addWidget(gb_regex)

        # Таймер
        self.regex_update_timer = QTimer(self)
        self.regex_update_timer.setSingleShot(True)
        self.regex_update_timer.timeout.connect(self._update_stats_from_regex_input)
        self.regex_input.textChanged.connect(lambda: self.regex_update_timer.start(500))

        # Сплиттер
        splitter = QSplitter(Qt.Orientation.Horizontal)
        
        left_w = QWidget()
        left_l = QVBoxLayout(left_w)
        left_l.addWidget(QLabel("Найденные паттерны:"))
        self.marker_list = QListWidget()
        self.marker_list.currentItemChanged.connect(self._promote_selection_to_regex)
        left_l.addWidget(self.marker_list)
        
        right_w = QWidget()
        right_l = QVBoxLayout(right_w)
        right_l.addWidget(QLabel("Предварительная статистика:"))
        
        # --- ИСПРАВЛЕНИЕ ТАБЛИЦЫ ---
        self.stats_table = QTableWidget(4, 3)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.horizontalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        
        # Настраиваем ширину колонок, чтобы не разъезжалось
        header = self.stats_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        
        labels = ["Количество глав", "Мин. размер", "Макс. размер", "Средний размер"]
        for i, lbl in enumerate(labels):
            item = QTableWidgetItem(lbl)
            item.setFont(QtGui.QFont("", 9, QtGui.QFont.Weight.Bold))
            self.stats_table.setItem(i, 0, item)
            self.stats_table.setItem(i, 1, QTableWidgetItem("-"))
            self.stats_table.setItem(i, 2, QTableWidgetItem(""))
            
        right_l.addWidget(self.stats_table)
        right_l.addStretch()

        splitter.addWidget(left_w)
        splitter.addWidget(right_w)
        splitter.setSizes([350, 550])
        layout.addWidget(splitter)
        
        btn_box = QHBoxLayout()
        btn_cancel = QPushButton("Отмена")
        btn_next = QPushButton("Далее: Редактировать оглавление >>")
        btn_next.setDefault(True)
        
        btn_cancel.clicked.connect(self.reject)
        btn_next.clicked.connect(self.go_to_toc_editor)
        
        btn_box.addStretch()
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_next)
        layout.addLayout(btn_box)
        
    # --- СТРАНИЦА 2: TOC EDITOR ---
    def _init_page_toc(self):
        layout = QVBoxLayout(self.page_toc)
        
        layout.addWidget(QLabel("<b>Шаг 2: Проверка и редактирование структуры</b>"))
        layout.addWidget(QLabel("Нажмите на заголовок столбца для сортировки (например, чтобы найти самые маленькие главы)."))
        
        self.toc_table = QTableWidget()
        self.toc_table.setColumnCount(4)
        self.toc_table.setHorizontalHeaderLabels(["Заголовок главы", "Размер (симв.)", "Индекс (симв.)", "№ Строки"])
        self.toc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.toc_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.toc_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.toc_table.doubleClicked.connect(self.open_chapter_viewer)
        
        # ВКЛЮЧАЕМ СОРТИРОВКУ
        self.toc_table.setSortingEnabled(True)
        
        layout.addWidget(self.toc_table)
        
        tools_layout = QHBoxLayout()
        btn_del = QPushButton("Удалить главу")
        btn_del.setToolTip("Удаляет метку главы. Текст объединяется с предыдущей главой.")
        btn_del.clicked.connect(self.delete_selected_chapter)
        
        btn_view = QPushButton("Просмотр / Разделить...")
        btn_view.clicked.connect(self.open_chapter_viewer)
        
        tools_layout.addWidget(btn_del)
        tools_layout.addWidget(btn_view)
        tools_layout.addStretch()
        layout.addLayout(tools_layout)
        
        gen_group = QGroupBox("Настройки генерации")
        gen_layout = QVBoxLayout(gen_group)
        self.chk_force_renumber = QtWidgets.QCheckBox("⚡ Сквозная перенумерация (1, 2, 3...)")
        self.chk_force_renumber.setStyleSheet("color: #e67e22; font-weight: bold;")
        gen_layout.addWidget(self.chk_force_renumber)
        layout.addWidget(gen_group)
        
        btn_box = QHBoxLayout()
        btn_back = QPushButton("<< Назад")
        btn_create = QPushButton("Создать EPUB")
        btn_create.setDefault(True)
        
        btn_back.clicked.connect(self.go_back_to_regex)
        btn_create.clicked.connect(self.generate_epub)
        
        btn_box.addWidget(btn_back)
        btn_box.addStretch()
        btn_box.addWidget(btn_create)
        layout.addLayout(btn_box)


    # --- ЛОГИКА СТРАНИЦЫ 1 ---
    def _open_regex_examples(self):
        dialog = RegexExamplesDialog(self)
        if dialog.exec():
            r = dialog.get_selected_regex()
            if r: self.regex_input.setText(r)

    def _run_structure_analysis(self):
        self.marker_list.clear()
        structure_markers = self.analyzer.analyze_potential_markers()
        for marker_tuple, count in structure_markers:
            display_name, pattern, mtype = marker_tuple
            item = QListWidgetItem(f"{display_name} ({count} вхождений)")
            item.setData(Qt.ItemDataRole.UserRole, marker_tuple)
            self.marker_list.addItem(item)
        if self.marker_list.count() > 0:
            self.marker_list.setCurrentRow(0)
        else:
            self._update_stats_from_regex_input()

    def _update_stats_from_regex_input(self):
        custom_regex_str = self.regex_input.text().strip()
        if not custom_regex_str:
            stats = {'count': 0}
        else:
            stats = self.analyzer.calculate_stats(custom_regex=custom_regex_str)
        
        is_error = stats.get('count') == 'Ошибка'
        palette = self.regex_input.palette()
        palette.setColor(QtGui.QPalette.ColorRole.Text, QtCore.Qt.GlobalColor.red if is_error else self.style().standardPalette().color(QtGui.QPalette.ColorRole.Text))
        self.regex_input.setPalette(palette)

        self.stats_table.setItem(0, 1, QTableWidgetItem(f"{stats.get('count', 0):,}"))
        self.stats_table.setItem(1, 1, QTableWidgetItem(f"{stats.get('min_val', 0):,} симв."))
        self.stats_table.setItem(2, 1, QTableWidgetItem(f"{stats.get('max_val', 0):,} симв."))
        self.stats_table.setItem(3, 1, QTableWidgetItem(f"{stats.get('avg', 0):,.0f} символов"))

    def _promote_selection_to_regex(self, current, prev):
        if not current: return
        data = current.data(Qt.ItemDataRole.UserRole)
        if len(data) == 3:
             _, value, mtype = data
             if mtype in ['direct_regex', 'context_indent']:
                 self.regex_input.setText(value)
                 return
        self.regex_input.clear()

    # --- ПЕРЕХОД МЕЖДУ СТРАНИЦАМИ ---
    def go_to_toc_editor(self):
        regex = self.regex_input.text().strip()
        if not regex:
            QtWidgets.QMessageBox.warning(self, "Внимание", "Не выбран RegEx.")
            return

        # 1. Получаем базовые границы из Analyzer
        try:
            if regex != "__CONTEXT_INDENT_ANALYSIS__":
                re.compile(regex, re.IGNORECASE)
            boundaries = self.analyzer.scan_chapter_boundaries(custom_regex=regex)
        except re.error:
            QtWidgets.QMessageBox.warning(self, "Ошибка", "Некорректный RegEx.")
            return

        if not boundaries:
            QtWidgets.QMessageBox.warning(self, "Пусто", "Ничего не найдено по этому RegEx.")
            return

        # 2. Добавляем "Предисловие", если текст начинается не с 0
        self.structure_data = []
        if boundaries[0]['line_idx'] > 0:
            # Есть текст до первой главы
            self.structure_data.append({
                'line_idx': 0,
                'title': "Начало / Предисловие",
                'char_idx': 0
            })
        
        self.structure_data.extend(boundaries)
        
        self._refresh_toc_table()
        self.layout_stack.setCurrentWidget(self.page_toc)

    def go_back_to_regex(self):
        self.layout_stack.setCurrentWidget(self.page_regex)

    # --- ЛОГИКА СТРАНИЦЫ 2 (TOC) ---
    def _refresh_toc_table(self):
        # Отключаем сортировку во время обновления, иначе строки будут прыгать при вставке
        self.toc_table.setSortingEnabled(False)
        self.toc_table.setRowCount(0)
        
        # Базовый список всегда должен быть отсортирован по физическому расположению (строкам)
        self.structure_data.sort(key=lambda x: x['line_idx'])
        
        self.toc_table.setRowCount(len(self.structure_data))
        total_lines = len(self.analyzer.lines)
        
        for i, item_data in enumerate(self.structure_data):
            current_line_idx = item_data['line_idx']
            next_line_idx = self.structure_data[i+1]['line_idx'] if i+1 < len(self.structure_data) else total_lines
            
            size = sum(self.analyzer.line_lengths[current_line_idx : next_line_idx])
            
            # 1. Title (Обычный Item)
            t_item = QTableWidgetItem(item_data['title'])
            # ВАЖНО: Сохраняем ссылку на сам словарь данных в ячейку. 
            # Это позволит найти правильную главу даже если таблица отсортирована.
            t_item.setData(Qt.ItemDataRole.UserRole, item_data)
            self.toc_table.setItem(i, 0, t_item)
            
            # 2. Size (SortableItem для чисел)
            s_item = SortableTableWidgetItem(f"{size:,}")
            s_item.setData(Qt.ItemDataRole.UserRole, item_data) # Дублируем данные на всякий случай
            self.toc_table.setItem(i, 1, s_item)
            
            # 3. Char Index (SortableItem)
            c_item = SortableTableWidgetItem(f"{item_data['char_idx']:,}")
            self.toc_table.setItem(i, 2, c_item)
            
            # 4. Line No (SortableItem)
            l_item = SortableTableWidgetItem(str(current_line_idx + 1))
            self.toc_table.setItem(i, 3, l_item)
            
        # Включаем сортировку обратно
        self.toc_table.setSortingEnabled(True)
            
    def delete_selected_chapter(self):
        row = self.toc_table.currentRow()
        if row < 0: return
        
        # Получаем данные из скрытого хранилища (UserRole), так как индекс row 
        # может не совпадать с индексом в списке self.structure_data из-за сортировки
        item = self.toc_table.item(row, 0)
        target_data = item.data(Qt.ItemDataRole.UserRole)
        
        if target_data in self.structure_data:
            self.structure_data.remove(target_data)
            self._refresh_toc_table()

    def open_chapter_viewer(self):
        row = self.toc_table.currentRow()
        if row < 0: return
        
        # 1. Получаем данные выбранной главы
        item = self.toc_table.item(row, 0)
        target_data = item.data(Qt.ItemDataRole.UserRole)
        
        # 2. Находим реальный индекс этой главы в отсортированном по порядку списке (не в таблице!)
        # Это нужно, чтобы найти "следующую" главу и определить конец текста.
        # self.structure_data мы всегда держим отсортированным по line_idx внутри _refresh_toc_table
        try:
            real_index = self.structure_data.index(target_data)
        except ValueError:
            return # Глава не найдена (странно)

        start_idx = target_data['line_idx']
        
        # 3. Определяем конец (начало следующей главы в физическом списке)
        next_idx = len(self.analyzer.lines)
        if real_index + 1 < len(self.structure_data):
            next_idx = self.structure_data[real_index + 1]['line_idx']
            
        is_preamble = (start_idx == 0 and target_data['title'] in ["Начало / Предисловие", "Начало / Метаданные"])
        content_start = start_idx if is_preamble else start_idx + 1
        
        chapter_lines = self.analyzer.lines[content_start : next_idx]
        
        dlg = ChapterViewerDialog(chapter_lines, start_idx, self)
        dlg.setWindowTitle(f"Глава: {target_data['title']}")
        
        if dlg.exec():
            if dlg.selected_split_index is not None:
                new_idx = dlg.selected_split_index
                new_title = dlg.selected_split_text
                char_idx = sum(self.analyzer.line_lengths[:new_idx])
                
                self.structure_data.append({
                    'line_idx': new_idx,
                    'title': new_title,
                    'char_idx': char_idx
                })
                self._refresh_toc_table()

    # --- ГЕНЕРАЦИЯ ---
    def generate_epub(self):
        if not self.structure_data:
            return

        # --- ИСПРАВЛЕНИЕ 1: БЕЗОПАСНОЕ ОБНОВЛЕНИЕ ЗАГОЛОВКОВ ---
        # Мы не полагаемся на порядок строк (row index), а берем ссылку на данные из ячейки.
        for row in range(self.toc_table.rowCount()):
            item = self.toc_table.item(row, 0) # Ячейка с названием
            new_title_text = item.text()
            
            # Получаем ссылку на словарь данных, привязанный к этой строке
            data_dict = item.data(Qt.ItemDataRole.UserRole)
            
            # Обновляем заголовок в самом словаре
            if data_dict:
                data_dict['title'] = new_title_text

        # Теперь сортируем структуру физически по порядку строк в файле,
        # чтобы в книге главы шли правильно, даже если в таблице их отсортировали по размеру.
        self.structure_data.sort(key=lambda x: x['line_idx'])

        base_name = os.path.splitext(os.path.basename(self.txt_path))[0]
        epub_title = base_name.replace('_', ' ').title()
        output_epub_path = os.path.join(self.output_dir, f"{base_name}_imported.epub")
        
        try:
            creator = EpubCreator(title=epub_title, author="Импортировано из TXT")
            
            total_lines = len(self.analyzer.lines)
            
            final_chapters = []
            
            # Сборка контента
            for i, item in enumerate(self.structure_data):
                start = item['line_idx']
                # Конец этой главы = начало следующей (или конец файла)
                end = self.structure_data[i+1]['line_idx'] if i+1 < len(self.structure_data) else total_lines
                
                is_preamble = (start == 0 and item['title'] in ["Начало / Предисловие", "Начало / Метаданные"])
                
                # Если не предисловие, то start-строка — это заголовок, берем контент с start+1
                content_start = start if is_preamble else start + 1
                
                # Защита от пустых диапазонов
                if content_start >= end:
                    lines_slice = []
                else:
                    lines_slice = self.analyzer.lines[content_start : end]
                
                content = "".join([l + "\n" for l in lines_slice])
                final_chapters.append((item['title'], content))

            # --- ИСПРАВЛЕНИЕ 2: УМНАЯ НУМЕРАЦИЯ ---
            if self.chk_force_renumber.isChecked():
                renumbered = []
                for idx, (title, content) in enumerate(final_chapters, 1):
                    # Пропускаем перенумерацию, если это явно Предисловие
                    # (можно настроить логику, но обычно предисловия не нумеруют как "1")
                    if title in ["Начало / Предисловие", "Начало / Метаданные"]:
                        renumbered.append((title, content))
                        continue

                    # Используем новую функцию smart_replace
                    new_title = smart_replace_number_in_title(title, idx)
                    renumbered.append((new_title, content))
                
                final_chapters = renumbered

            # Создание EPUB файлов
            for i, (title, content) in enumerate(final_chapters):
                # Пропускаем совсем пустые главы, если они случайно образовались
                if not content.strip() and not title: continue
                
                # Экранирование HTML внутри текста не нужно, если content чистый текст, 
                # но EpubCreator обычно сам оборачивает. Здесь мы делаем базовую разметку.
                # strip() у каждой строки нужен, чтобы убрать лишние пробелы.
                paragraphs = ''.join([f'<p>{line.strip()}</p>' for line in content.splitlines() if line.strip()])
                
                html_content = f"""<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{title}</title></head>
<body>
<h1>{title}</h1>
{paragraphs}
</body></html>"""
                creator.add_chapter(f"chapter_{i+1}.xhtml", html_content, title)
            
            creator.create_epub(output_epub_path)
            self.generated_epub_path = output_epub_path
            super().accept()
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            QtWidgets.QMessageBox.critical(self, "Ошибка", f"Не удалось создать EPUB: {e}")

    def get_generated_epub_path(self):
        return self.generated_epub_path