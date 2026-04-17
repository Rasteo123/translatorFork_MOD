# show_help.py (в корне проекта)

import sys
from PyQt6.QtWidgets import QApplication
from window_branding import install_window_title_branding

# Теперь мы импортируем наш модуль и его зависимости абсолютно чисто
from gemini_translator.utils import markdown_viewer
from gemini_translator.ui.themes import DARK_STYLESHEET

if __name__ == "__main__":
    # Создаем экземпляр приложения
    app = QApplication(sys.argv)
    install_window_title_branding(app)
    
    # Применяем тему, чтобы окно выглядело красиво при прямом запуске
    if DARK_STYLESHEET:
        app.setStyleSheet(DARK_STYLESHEET)
    else:
        app.setStyleSheet(markdown_viewer.FALLBACK_DARK_QSS)
    
    # Проверяем, был ли передан аргумент командной строки (название раздела)
    
    # Вызываем наш просмотрщик в модальном режиме,
    # он по умолчанию откроет главный README.md
    markdown_viewer.show_markdown_viewer(
        section=None,
        window_title="Справка",
        modal=True
    )
