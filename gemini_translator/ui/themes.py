DARK_STYLESHEET = """
/* Global surfaces */
QWidget {
    background-color: #0f141b;
    color: #e6edf5;
    font-family: "Segoe UI", sans-serif;
    font-size: 9pt;
}

QDialog {
    background-color: #0f141b;
}

QFrame#projectHeaderCard,
QFrame#projectPathCard,
QFrame#projectStatsCard,
QFrame#projectActionsCard,
QFrame#actionBar,
QFrame#keyTransferColumn,
QFrame#keyPanelSurface,
QWidget#keyTransferColumn,
QWidget#keyPanelSurface,
QFrame#statusSurface,
QGroupBox {
    background-color: #151c24;
    border: 1px solid #263241;
    border-radius: 12px;
}

QGroupBox {
    margin-top: 12px;
    padding-top: 6px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 12px;
    color: #f2a365;
    font-weight: 600;
}

QLabel#sectionEyebrow,
QLabel#projectCardTitle,
QLabel#mutedCaptionLabel {
    color: #f2a365;
    font-size: 9pt;
    font-weight: 600;
    letter-spacing: 0.3px;
}

QLabel#heroTitle {
    color: #f6f8fb;
    font-size: 15pt;
    font-weight: 700;
}

QLabel#heroSubtitle,
QLabel#projectCardDetail,
QLabel#mutedLabel,
QLabel#helperLabel {
    color: #91a0b5;
}

QLabel#projectCardValue {
    color: #f6f8fb;
    font-size: 11pt;
    font-weight: 600;
}

QLabel#metricValueLabel {
    color: #f6f8fb;
    font-size: 17pt;
    font-weight: 700;
}

QLabel#projectStateLabel,
QLabel#legendChip {
    background-color: #1c2632;
    border: 1px solid #2d3949;
    border-radius: 999px;
    padding: 5px 10px;
    color: #b8c5d6;
}

QLabel#projectStateLabel[ready="true"] {
    background-color: rgba(78, 169, 125, 0.16);
    border: 1px solid rgba(78, 169, 125, 0.45);
    color: #8fddb6;
}

QLabel#legendChip[state="ok"] {
    color: #8fddb6;
}

QLabel#legendChip[state="warm"] {
    color: #f5cf7e;
}

QLabel#legendChip[state="bad"] {
    color: #ef9a9a;
}

/* Tabs */
QTabWidget::pane {
    border: 1px solid #263241;
    background-color: #121922;
    border-radius: 14px;
    top: -1px;
}

QTabBar::tab {
    background: transparent;
    color: #94a3b8;
    border: none;
    padding: 8px 14px 7px 14px;
    margin-right: 6px;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}

QTabBar::tab:selected {
    color: #f6f8fb;
    border-bottom: 2px solid #d87a3a;
}

QTabBar::tab:!selected:hover {
    color: #d5deea;
}

/* Inputs */
QLineEdit,
QTextEdit,
QPlainTextEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {
    background-color: #111821;
    color: #ecf2f9;
    border: 1px solid #2a3441;
    border-radius: 9px;
    padding: 5px 9px;
    selection-background-color: #d87a3a;
    selection-color: #ffffff;
}

QLineEdit:focus,
QTextEdit:focus,
QPlainTextEdit:focus,
QSpinBox:focus,
QDoubleSpinBox:focus,
QComboBox:focus {
    border: 1px solid #d87a3a;
}

QLineEdit:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled,
QComboBox:disabled {
    background-color: #151a20;
    color: #6c7888;
}

QLineEdit#keySearchField {
    min-height: 18px;
}

QComboBox QAbstractItemView {
    background-color: #151c24;
    border: 1px solid #2a3441;
    selection-background-color: #d87a3a;
    selection-color: #ffffff;
    outline: 0;
}

/* Buttons */
QPushButton {
    background-color: #1b2430;
    color: #dce5ef;
    border: 1px solid #2d3949;
    border-radius: 9px;
    padding: 6px 12px;
}

QPushButton:hover {
    background-color: #222d3a;
    border-color: #3a4759;
}

QPushButton:pressed {
    background-color: #10161d;
}

QPushButton:disabled {
    background-color: #141920;
    color: #6b7685;
    border-color: #202833;
}

QPushButton#primaryActionButton {
    background-color: #d87a3a;
    color: #ffffff;
    border: 1px solid #d87a3a;
    font-weight: 700;
    padding: 8px 16px;
}

QPushButton#primaryActionButton:hover {
    background-color: #e18950;
    border-color: #e18950;
}

QPushButton#primaryActionButton:pressed {
    background-color: #bf6a32;
    border-color: #bf6a32;
}

QPushButton#dangerActionButton {
    background-color: #412026;
    color: #ffd8d8;
    border: 1px solid #7a3945;
    font-weight: 600;
}

QPushButton#dangerActionButton:hover {
    background-color: #542630;
    border-color: #93424f;
}

QPushButton#ghostActionButton,
QPushButton#compactActionButton,
QPushButton#projectUtilityButton,
QPushButton#pathActionButton {
    background-color: #111821;
}

QPushButton#pathActionButton {
    font-weight: 600;
}

QPushButton#contextToggleButton {
    background-color: #111821;
    padding: 7px 12px;
}

QPushButton#contextToggleButton:checked {
    background-color: #18354a;
    border: 1px solid #2d6a8f;
    color: #e8f5ff;
}

/* Lists and tables */
QTableWidget,
QListWidget {
    background-color: #10161d;
    alternate-background-color: #141b24;
    border: 1px solid #263241;
    border-radius: 12px;
    gridline-color: #25303d;
    outline: 0;
}

QListWidget#keyListWidget {
    padding: 6px;
}

QListWidget#keyListWidget::item {
    padding: 6px 9px;
    margin: 2px 0;
    border-radius: 8px;
}

QListWidget::item:selected,
QTableWidget::item:selected {
    background-color: rgba(216, 122, 58, 0.24);
    color: #ffffff;
}

QListWidget::item:hover {
    background-color: rgba(216, 122, 58, 0.12);
}

QHeaderView::section {
    background-color: #121922;
    color: #b9c6d7;
    border: none;
    border-bottom: 1px solid #263241;
    padding: 8px 6px;
    font-weight: 600;
}

/* Scroll bars */
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 11px;
    margin: 2px;
}

QScrollBar::handle:vertical {
    background: #2b3644;
    min-height: 28px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #3a4758;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    border: none;
    background: transparent;
    height: 11px;
    margin: 2px;
}

QScrollBar::handle:horizontal {
    background: #2b3644;
    min-width: 28px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: #3a4758;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0;
}

/* Splitters */
QSplitter::handle {
    background-color: #1f2833;
    border: 1px solid #10161d;
}

QSplitter::handle:horizontal {
    width: 8px;
}

QSplitter::handle:vertical {
    height: 8px;
}

QSplitter::handle:hover {
    background-color: #d87a3a;
}

/* Progress and checkboxes */
QProgressBar {
    border: 1px solid #2a3441;
    border-radius: 10px;
    text-align: center;
    background-color: #111821;
    color: #f6f8fb;
    min-height: 20px;
}

QProgressBar::chunk {
    background-color: #d87a3a;
    border-radius: 8px;
}

QCheckBox {
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #405064;
    background-color: #10161d;
}

QCheckBox::indicator:checked {
    background-color: #d87a3a;
    border-color: #d87a3a;
}

QToolTip {
    background-color: #1a212b;
    color: #eef4fb;
    border: 1px solid #364354;
    padding: 6px 8px;
    border-radius: 8px;
}

QMessageBox {
    background-color: #0f141b;
}

QMessageBox QLabel {
    color: #e6edf5;
}
"""
