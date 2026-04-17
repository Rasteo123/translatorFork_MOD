DARK_STYLESHEET = """
/* ----- ОБЩИЕ СТИЛИ ----- */
QWidget {
    background-color: #2c313c;
    color: #f0f0f0;
    font-family: Segoe UI, sans-serif;
    font-size: 10pt;
}

/* ----- ГРУППЫ И ВКЛАДКИ ----- */
QGroupBox {
    border: 1px solid #4d5666;
    border-radius: 5px;
    margin-top: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top center;
    padding: 0 5px;
}
QTabWidget::pane {
    border: 1px solid #4d5666;
    background-color: #373e4b;
}
QTabBar::tab {
    background: #373e4b;
    border: 1px solid #4d5666;
    padding: 6px 12px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background: #4d5666;
    border-bottom-color: #4d5666;
}
QTabBar::tab:!selected:hover {
    background: #454d5b;
}

/* ----- ПОЛЯ ВВОДА ----- */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #373e4b;
    border: 1px solid #4d5666;
    border-radius: 4px;
    padding: 5px;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border: 1px solid #3daee9;
}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
    background-color: #404653;
    color: #888888;
}
QComboBox QAbstractItemView {
    background-color: #373e4b;
    border: 1px solid #4d5666;
    selection-background-color: #4a6984;
}

/* ----- КНОПКИ ----- */
QPushButton {
    background-color: #4d5666;
    border: 1px solid #4d5666;
    padding: 5px 10px;
    border-radius: 4px;
}
QPushButton:hover {
    background-color: #5a6475;
}
QPushButton:pressed {
    background-color: #3daee9;
}
QPushButton:disabled {
    background-color: #404653;
    color: #888888;
}
QToolTip {
    background-color: #1e222a;
    color: #f0f0f0;
    border: 1px solid #4a6984;
    padding: 5px;
    border-radius: 4px;
}

/* ----- ТАБЛИЦЫ И СПИСКИ (ИЗМЕНЕНО) ----- */
QTableWidget, QListWidget {
    background-color: #373e4b;
    gridline-color: #4d5666;
    border: 1px solid #4d5666;
    /* --- НОВОЕ: Цвет альтернативной строки (зебра) для темной темы --- */
    alternate-background-color: #2d323d; 
}
QHeaderView::section {
    background-color: #1e222a;
    padding: 4px;
    border: 1px solid #4d5666;
    font-weight: bold;
}
QTableWidget::item:selected, QListWidget::item:selected {
    background-color: #4a6984; 
    color: #ffffff;
}
QListWidget::item:hover {
    background-color: #454d5b;
}

/* ----- ПОЛОСЫ ПРОКРУТКИ ----- */
QScrollBar:vertical {
    border: none;
    background: #2c313c;
    width: 10px;
    margin: 0px 0px 0px 0px;
}
QScrollBar::handle:vertical {
    background: #4d5666;
    min-height: 20px;
    border-radius: 5px;
}
QScrollBar::handle:vertical:hover {
    background: #5a6475;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    border: none;
    background: #2c313c;
    height: 10px;
    margin: 0px 0px 0px 0px;
}
QScrollBar::handle:horizontal {
    background: #4d5666;
    min-width: 20px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal:hover {
    background: #5a6475;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}

/* ----- ДИАЛОГОВЫЕ ОКНА ----- */
QMessageBox {
    background-color: #2c313c;
}
QMessageBox QLabel {
    color: #f0f0f0;
}

/* ----- SPLITTER ----- */
QSplitter::handle {
    background-color: #4d5666;
    border: 1px solid #2c313c;
}
QSplitter::handle:vertical {
    height: 7px;
}
QSplitter::handle:horizontal {
    width: 7px;
}
QSplitter::handle:hover {
    background-color: #5a6475;
}
QSplitter::handle:pressed {
    background-color: #3daee9;
}

/* ----- ПРОГРЕСС-БАР ----- */
QProgressBar {
    border: 1px solid #4d5666;
    border-radius: 5px;
    text-align: center;
    background-color: transparent; 
    color: #f0f0f0;
}
QProgressBar::chunk {
    background-color: transparent;
    border-radius: 4px;
}

/* --- ЦВЕТНЫЕ СЕГМЕНТЫ --- */
#successBar {
    background-color: #3daee9;
}
#filterBar {
    background-color: #9B59B6;
}
#errorBar {
    background-color: #E74C3C;
}
#pendingBar {
    background-color: #373e4b;
}
"""