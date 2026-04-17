# -*- coding: utf-8 -*-

import sys
import re
import html
import uuid # <-- Убедитесь, что импорт на месте

from pathlib import Path
from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QTextBrowser,
                             QPushButton, QFrame, QWidget, QHBoxLayout, QLabel,
                             QSpacerItem, QSizePolicy, QScrollArea)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QTimer

try:
    from gemini_translator.ui.themes import DARK_STYLESHEET
except ImportError:
    DARK_STYLESHEET = "" # Запасной вариант - пустая строка
# ============================================================================
#  1. Стилизация и Конфигурация
# ============================================================================
try:
    # Импортируем нашу универсальную функцию поиска ресурсов
    from ..api.config import get_resource_path
except ImportError:
    # Запасной вариант на случай, если основной импорт не сработает.
    # Эта логика должна быть идентична той, что в api/config.py.
    def get_resource_path(relative_path):
        if getattr(sys, 'frozen', False):
            executable_dir = Path(os.path.dirname(sys.executable))
            external_path = executable_dir / relative_path
            if external_path.exists():
                return external_path
            if hasattr(sys, '_MEIPASS'):
                internal_dir = Path(sys._MEIPASS)
                internal_path = internal_dir / relative_path
                if internal_path.exists():
                    return internal_path
            return external_path
        else:
            # Поднимаемся на 3 уровня: markdown_viewer.py -> utils -> gemini_translator -> корень
            return Path(__file__).resolve().parents[2] / relative_path

# Путь к файлу справки теперь всегда определяется через эту функцию
HELP_FILE_PATH = get_resource_path("README.md")

# Минимальная темная тема для автономного запуска, если основная не найдена.
# Это язык QSS, который понимает Qt.

FALLBACK_DARK_QSS = """
    QDialog {
        background-color: #2c313c;
    }
    QWidget {
        background-color: #2c313c;
        color: #f0f0f0;
        font-family: Segoe UI, sans-serif;
        font-size: 10pt;
    }
    QPushButton {
        background-color: #4d5666; border: 1px solid #4d5666;
        padding: 5px 10px; border-radius: 4px; color: #f0f0f0;
    }
    QPushButton:hover { background-color: #5a6475; }
    QPushButton:pressed { background-color: #3daee9; }
    QScrollBar:vertical {
        border: none; background: #2c313c; width: 10px;
    }
    QScrollBar::handle:vertical {
        background: #4d5666; min-height: 20px; border-radius: 5px;
    }
    QScrollBar::handle:vertical:hover { background: #5a6475; }
    QScrollBar:horizontal {
        border: none; background: #2c313c; height: 10px;
    }
    QScrollBar::handle:horizontal {
        background: #4d5666; min-width: 20px; border-radius: 5px;
    }
    QScrollBar::handle:horizontal:hover { background: #5a6475; }
"""

DARK_HELP_CSS = """
    body {
        background-color: transparent; 
        color: #f0f0f0;
        font-family: Segoe UI, sans-serif;
        font-size: 10pt;
    }
    /* --- ИЗМЕНЕНИЕ ЗДЕСЬ: Уменьшаем вертикальные отступы --- */
    h1 { font-size: 18pt; font-weight: bold; color: #3daee9; margin-top: 15px; margin-bottom: 8px; border-bottom: 2px solid #3daee9; }
    h2 { font-size: 15pt; font-weight: bold; color: #f0f0f0; margin-top: 12px; margin-bottom: 6px; border-bottom: 1px solid #4d5666; }
    h3 { font-size: 12pt; font-weight: bold; color: #e0e0e0; margin-top: 10px; margin-bottom: 4px; }
    /* -------------------------------------------------------- */
    p, li { line-height: 1.5; }
    a { color: #3daee9; text-decoration: none; }
    a:hover { text-decoration: underline; }
    b, strong { color: #f5f5f5; font-weight: bold; }
    code { font-family: Consolas, monospace; background-color: #1e222a; padding: 2px 5px; border-radius: 3px; font-size: 10pt; color: #f0f0f0; }
"""

# ============================================================================
#  2. Вспомогательные функции (включая get_resource_path)
# ============================================================================

def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'): base_path = Path(sys._MEIPASS)
    else: base_path = Path(".")
    return base_path / relative_path


def markdown_to_html(md_text):
    # Упрощенная версия, которая НЕ обрабатывает многострочный код ```
    inline_code_map = {}
    def isolate_inline_code(match):
        placeholder = uuid.uuid4().hex
        inline_code_map[placeholder] = f'<code>{html.escape(match.group(1), quote=False)}</code>'
        return placeholder
    text_after_pass = re.sub(r'`(.+?)`', isolate_inline_code, md_text)
    def apply_inline_formatting(text):
        text = re.sub(r'\*\*(.+?)\*\*|__(.+?)__', r'<b>\1\2</b>', text)
        text = re.sub(r'\*(.+?)\*|_(.+?)_', r'<i>\1\2</i>', text)
        text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
        return text
    blocks, html_blocks = text_after_pass.strip().split('\n\n'), []
    for block in blocks:
        s_block = block.strip()
        if not s_block: continue
        if s_block in inline_code_map: html_blocks.append(s_block)
        elif s_block.startswith('# '): html_blocks.append(f'<h1>{apply_inline_formatting(s_block[2:])}</h1>')
        elif s_block.startswith('## '): html_blocks.append(f'<h2>{apply_inline_formatting(s_block[3:])}</h2>')
        elif s_block.startswith('### '): html_blocks.append(f'<h3>{apply_inline_formatting(s_block[4:])}</h3>')
        elif s_block.startswith(('* ', '- ')):
            items, html_list = s_block.split('\n'), '<ul>'
            for item in items:
                clean = re.sub(r'^\s*[*-]\s*', '', item).strip()
                if clean: html_list += f'<li>{apply_inline_formatting(clean)}</li>'
            html_blocks.append(html_list + '</ul>')
        else: html_blocks.append(f'<p>{apply_inline_formatting(s_block).replace(chr(10), "<br>")}</p>')
    final_html = '\n'.join(html_blocks)
    for placeholder, html_content in inline_code_map.items():
        final_html = final_html.replace(placeholder, html_content)
    return final_html

def parse_markdown_to_blocks(md_text):
    """Парсит Markdown в список блоков ('html' или 'code')."""
    blocks = []
    parts = re.split(r'(```(\w*)\n(.*?)\n?```)', md_text, flags=re.DOTALL)
    
    html_accumulator = ""
    for i, part in enumerate(parts):
        if not part: continue
        
        if i % 4 == 1:
            if html_accumulator.strip():
                blocks.append(('html', markdown_to_html(html_accumulator)))
                html_accumulator = ""

            lang = parts[i+1]
            # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
            # .rstrip() убирает все пробельные символы (включая \n) только СПРАВА,
            # сохраняя важные отступы СЛЕВА.
            code = parts[i+2].rstrip()
            blocks.append(('code', lang, code))
        
        elif i % 4 == 0:
            html_accumulator += part

    if html_accumulator.strip():
        blocks.append(('html', markdown_to_html(html_accumulator)))
        
    return blocks
    
def _find_all_headers(markdown_text):
    """
    Сканирует весь текст и возвращает список всех найденных заголовков.
    Возвращает: [{'title': str, 'level': int, 'start_pos': int}]
    """
    headers = []
    header_pattern = re.compile(r"^(#{1,6})\s+(.*)", re.MULTILINE)
    for match in header_pattern.finditer(markdown_text):
        level = len(match.group(1))
        title = match.group(2).strip()
        start_pos = match.start()
        headers.append({'title': title, 'level': level, 'start_pos': start_pos})
    return headers

def extract_section(full_md_text, section_title):
    """
    Находит раздел по его названию.
    - Если `section_title` начинается с '## ', ищет частичное совпадение на указанном уровне.
    - Иначе, ищет частичное совпадение на всех уровнях.
    Во всех случаях проверяет на уникальность.
    """
    if not section_title:
        return full_md_text, None

    all_headers = _find_all_headers(full_md_text)
    # <<< НАЧАЛО ИЗМЕНЕНИЙ: Упрощенная гибридная логика >>>
    
    level_match = re.match(r"^(#{1,6})\s+(.*)", section_title)
    
    if level_match:
        # --- СЦЕНАРИЙ 1: Уровень указан ---
        requested_level = len(level_match.group(1))
        requested_title_part = level_match.group(2).strip().lower()
        
        # Ищем ЧАСТИЧНОЕ совпадение, но ТОЛЬКО на заданном уровне
        matches = [
            (i, header) for i, header in enumerate(all_headers)
            if header['level'] == requested_level and requested_title_part in header['title'].strip().lower()
        ]
        
        error_title_part = f"содержащим '{requested_title_part}' на уровне {requested_level}"
        
    else:
        # --- СЦЕНАРИЙ 2: Уровень не указан ---
        requested_title_part = section_title.strip().lower()
        
        # Ищем ЧАСТИЧНОЕ совпадение на ВСЕХ уровнях
        matches = [
            (i, header) for i, header in enumerate(all_headers)
            if requested_title_part in header['title'].strip().lower()
        ]
        
        error_title_part = f"содержащим '{requested_title_part}'"

    # <<< КОНЕЦ ИЗМЕНЕНИЙ >>>

    # Общая логика обработки результатов (остается без изменений)
    if len(matches) == 0:
        return f"## Раздел не найден\n\nРаздел с заголовком, {error_title_part}, не был найден в документе.", "Ошибка"

    if len(matches) > 1:
        ambiguity_info = "\n".join([f"- Уровень {h['level']}: \"{h['title']}\"" for i, h in matches])
        return f"## Неоднозначный заголовок\n\nНайдено несколько разделов, соответствующих запросу {error_title_part}:\n{ambiguity_info}\n\nПожалуйста, уточните запрос.", "Ошибка"

    # Уникальное совпадение найдено
    match_index, matched_header = matches[0]
    
    section_start_pos = matched_header['start_pos']
    section_end_pos = len(full_md_text)

    # Ищем следующий заголовок того же или более высокого уровня
    for i in range(match_index + 1, len(all_headers)):
        next_header = all_headers[i]
        if next_header['level'] <= matched_header['level']:
            section_end_pos = next_header['start_pos']
            break

    extracted_text = full_md_text[section_start_pos:section_end_pos].strip()

    return extracted_text, matched_header['title']

# ============================================================================
#  3. Класс диалогового окна
# ============================================================================
def copy_to_clipboard(text, button):
    original_text = button.text() # Запоминаем исходную иконку "📋"
    QApplication.clipboard().setText(text)
    button.setText("✓") # Ставим галочку
    button.setEnabled(False)
    # Через 2 секунды возвращаем кнопку в исходное состояние
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(2000, lambda: (button.setText(original_text), button.setEnabled(True)))

class CodeBlockWidget(QFrame):
    VERTICAL_PADDING = 12 

    def __init__(self, language, code_text, parent=None):
        super().__init__(parent)
        self.setObjectName("CodeBlockFrame")
        
        # --- НОВИНКА: Состояние виджета (свернут или развернут) ---
        self.is_collapsed = False

        # --- "Хром" виджета (заголовок, кнопки) ---
        header_widget = QWidget(self)
        header_widget.setObjectName("CodeBlockHeader")
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(10, 5, 10, 5)

        lang_text = f"<> {language.upper()}" if language else "<> CODE"
        lang_label = QLabel(lang_text, self)
        lang_label.setObjectName("CodeBlockLangLabel")

        # --- НОВИНКА: Кнопка для сворачивания/разворачивания кода ---
        self.collapse_btn = QPushButton("▲", self) # ▲ - стрелка вверх
        self.collapse_btn.setObjectName("CodeBlockCollapseButton")
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.setFixedSize(30, 26)
        self.collapse_btn.setToolTip("Свернуть/развернуть код")
        self.collapse_btn.clicked.connect(self._toggle_collapse)

        # --- ИЗМЕНЕНИЕ: Новая иконка для кнопки копирования ---
        copy_btn = QPushButton("⧉", self) # ⧉ - два пересекающихся квадрата
        copy_btn.setObjectName("CodeBlockCopyButton")
        copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        copy_btn.setFixedSize(30, 26)
        copy_btn.setToolTip("Копировать код")
        copy_btn.clicked.connect(lambda: copy_to_clipboard(code_text, copy_btn))
        
        # Добавляем элементы в заголовок в правильном порядке
        header_layout.addWidget(lang_label)
        header_layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))
        header_layout.addWidget(self.collapse_btn) # Новая кнопка
        header_layout.addWidget(copy_btn)

        # --- "Контент" виджета (QTextBrowser) ---
        self.code_view = QTextBrowser(self)
        self.code_view.setObjectName("CodeViewer")
        self.code_view.setFrameShape(QFrame.Shape.NoFrame)
        self.code_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.code_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        code_document_css = """
            body, pre { margin: 0; padding: 0; }
            body { 
                background-color: transparent; color: #f0f0f0; 
                font-family: monospace; font-size: 10pt; line-height: 1.3;
            }
        """
        code_html_body = f"<pre>{html.escape(code_text.rstrip())}</pre>"

        self.code_view.document().setDefaultStyleSheet(code_document_css)
        self.code_view.setHtml(code_html_body)

        # --- Компоновка ---
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(1, 1, 1, 1)
        main_layout.setSpacing(0)
        main_layout.addWidget(header_widget)
        main_layout.addWidget(self.code_view)
        
        # --- Стили (добавлены стили для новой кнопки) ---
        self.setStyleSheet(f"""
            /* 1. Главный контейнер. ТЕПЕРЬ ОН ПРОЗРАЧНЫЙ. Отвечает только за рамку. */
            #CodeBlockFrame {{ 
                border: 1px solid #4d5666; 
                border-radius: 5px;
                /* background-color убран отсюда */
            }}

            /* 2. Заголовок. Получает свой УНИКАЛЬНЫЙ, более темный фон. */
            #CodeBlockHeader {{ 
                background-color: #313642; /* <-- Новый, более темный и нейтральный серый */
                border-bottom: 1px solid #4d5666;
                /* Важно: скругляем верхние углы, чтобы соответствовать рамке */
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }}
            #CodeBlockLangLabel {{ 
                color: #c0c5ce; /* <-- Тот самый, менее синий текст */
                font-weight: bold; 
                background-color: transparent; 
            }}
            
            /* 3. Контейнер с кодом. Имеет самый темный фон. */
            #CodeViewer {{
                background-color: #1e222a;
                border: none;
                padding: {self.VERTICAL_PADDING}px 10px;
                /* Важно: скругляем нижние углы */
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
            }}

            /* 4. Кнопки (без изменений) */
            #CodeBlockCopyButton, #CodeBlockCollapseButton {{
                font-size: 12pt; color: #d0d0d0; background-color: #454d5b;
                border: 1px solid #5a6475; border-radius: 3px; padding: 0px;
            }}
            #CodeBlockCopyButton:hover, #CodeBlockCollapseButton:hover {{ background-color: #5a6475; }}
            #CodeBlockCopyButton:pressed, #CodeBlockCollapseButton:pressed {{ background-color: #3daee9; }}
            #CodeBlockCopyButton:disabled {{ color: #8fbc8f; background-color: #454d5b; }}
        """)

        self.code_view.document().documentLayout().documentSizeChanged.connect(self._update_code_view_height)
        self._update_code_view_height(self.code_view.document().size())

    def _update_code_view_height(self, size):
        content_height = int(size.height())
        required_height = content_height + (2 * self.VERTICAL_PADDING)
        self.code_view.setFixedHeight(required_height)

    # --- НОВИНКА: Метод для сворачивания/разворачивания ---
    def _toggle_collapse(self):
        # 1. Инвертируем состояние
        self.is_collapsed = not self.is_collapsed
        
        # 2. Показываем или скрываем виджет с кодом
        self.code_view.setVisible(not self.is_collapsed)
        
        # 3. Меняем иконку на кнопке
        # ▼ - стрелка вниз, ▲ - стрелка вверх
        self.collapse_btn.setText("▼" if self.is_collapsed else "▲")
        
        # 4. (Опционально) Меняем стиль рамки, чтобы было видно, что блок свернут
        if self.is_collapsed:
            self.headerWidget().setStyleSheet("#CodeBlockHeader { border-bottom: none; }")
        else:
            self.headerWidget().setStyleSheet("#CodeBlockHeader { border-bottom: 1px solid #4d5666; }")

    def headerWidget(self):
        # Вспомогательный метод для доступа к виджету заголовка
        return self.layout().itemAt(0).widget()
        
class HelpDialog(QDialog):
    def __init__(self, title, parsed_blocks, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(700, 500)
        self.setWindowFlags(self.windowFlags() | 
                            Qt.WindowType.WindowMinimizeButtonHint | 
                            Qt.WindowType.WindowMaximizeButtonHint)

        # Главный layout диалога
        dialog_layout = QVBoxLayout(self)
        dialog_layout.setContentsMargins(10, 10, 10, 0) # Убираем нижний отступ, он будет у кнопки
        dialog_layout.setSpacing(10)

        # 1. QScrollArea находится СНАРУЖИ
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        # ScrollArea должна быть прозрачной, чтобы фон диалога был виден
        scroll_area.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")

        # 2. НАША "КАРТОЧКА" ("виджет-прослойка") теперь ВНУТРИ QScrollArea
        text_container = QFrame() # Больше не self.text_container
        text_container.setObjectName("TextContainer")
        
        # Компоновка для "карточки"
        container_layout = QVBoxLayout(text_container)
        container_layout.setContentsMargins(15, 10, 15, 10) # Внутренние отступы "карточки"
        container_layout.setSpacing(15) # Расстояние между блоками

        # Динамическое создание виджетов (без изменений)
        for block_type, *data in parsed_blocks:
            if block_type == 'html':
                html_content = data[0]
                text_browser = QTextBrowser(self)
                text_browser.setOpenExternalLinks(True)
                text_browser.setFrameShape(QFrame.Shape.NoFrame)
                text_browser.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                text_browser.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
                text_browser.setStyleSheet("QTextBrowser { background-color: transparent; border: none; }")
                text_browser.document().setDefaultStyleSheet(DARK_HELP_CSS)
                text_browser.setHtml(html_content)
                text_browser.document().documentLayout().documentSizeChanged.connect(
                    lambda size, b=text_browser: self._update_browser_height(size, b)
                )
                self._update_browser_height(text_browser.document().size(), text_browser)
                container_layout.addWidget(text_browser)

            elif block_type == 'code':
                lang, code_text = data
                code_widget = CodeBlockWidget(lang, code_text, self)
                container_layout.addWidget(code_widget)
        
        container_layout.addStretch()
        
        # 3. Устанавливаем нашу "карточку" как виджет для прокрутки
        scroll_area.setWidget(text_container)
        
        # 4. Добавляем QScrollArea в главный layout диалога
        dialog_layout.addWidget(scroll_area)

        # Кнопка "Закрыть"
        close_btn = QPushButton("Закрыть", self)
        close_btn.clicked.connect(self.accept)
        close_btn.setMinimumHeight(30)
        close_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 0, 0, 10) # Отступ теперь здесь
        btn_layout.addStretch()
        btn_layout.addWidget(close_btn)
        dialog_layout.addLayout(btn_layout)

        # --- ФИНАЛЬНЫЕ СТИЛИ для диалога и нашей "карточки" ---
        self.setStyleSheet("""
            #TextContainer {
                background-color: #373e4b;
                border-radius: 5px;
            }
        """)

    def _update_browser_height(self, size, browser):
        content_height = int(size.height())
        margins = browser.document().documentMargin() * 2 
        buffer = 2
        browser.setFixedHeight(content_height + int(margins) + buffer)

# ============================================================================
#  4. Основная функция-контроллер (ОБНОВЛЕННАЯ)
# ============================================================================

def show_markdown_viewer(parent_window=None, modal=True, markdown_text=None, file_path=None, section=None, window_title="Справка"):
    """
    Универсальный просмотрщик Markdown.
    Приоритет: markdown_text > file_path > HELP_FILE_PATH (по умолчанию).
    """
    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
            if DARK_STYLESHEET: app.setStyleSheet(DARK_STYLESHEET)
            else: app.setStyleSheet(FALLBACK_DARK_QSS)

        md_to_show = ""
        final_title = window_title

        if markdown_text is not None:
            md_to_show = markdown_text
        else:
            target_file_path = file_path if file_path else HELP_FILE_PATH
            resource_path = get_resource_path(target_file_path)
            if not resource_path.exists():
                raise FileNotFoundError(f"Файл справки не найден: {resource_path}")
            
            full_md_text = resource_path.read_text(encoding='utf-8')
            md_to_show, extracted_title_part = extract_section(full_md_text, section)

            if extracted_title_part == "Ошибка":
                final_title = "Ошибка"
            elif extracted_title_part:
                final_title = f"{window_title}: {extracted_title_part}"

        blocks = parse_markdown_to_blocks(md_to_show)
        dialog = HelpDialog(final_title, blocks, parent_window)

        # --- АДАПТИВНОЕ ИЗМЕНЕНИЕ РАЗМЕРА ---
        # Рассчитываем и устанавливаем более удобный размер окна
        # на основе разрешения экрана пользователя.
        if parent_window and parent_window.screen():
            screen = parent_window.screen()
        else:
            screen = app.primaryScreen()
        
        if screen:
            available_geometry = screen.availableGeometry()
            # Устанавливаем размер в 60% ширины и 80% высоты доступной области экрана
            width = int(available_geometry.width() * 0.6)
            height = int(available_geometry.height() * 0.8)
            dialog.resize(width, height)

        if modal:
            dialog.exec()
            return None
        else:
            dialog.show()
            return dialog
        
    except Exception as e:
        error_blocks = parse_markdown_to_blocks(f"# Ошибка\n\nНе удалось загрузить справку.\n\n```\n{e}\n```")
        dialog = HelpDialog("Ошибка загрузки справки", error_blocks, parent_window)
        dialog.exec()
        return None

