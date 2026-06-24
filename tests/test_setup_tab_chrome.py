from pathlib import Path


def test_translator_main_tabs_are_not_document_mode():
    setup_source = Path("gemini_translator/ui/dialogs/setup.py").read_text(encoding="utf-8")

    assert "self.tabs_group.setDocumentMode(False)" in setup_source
    assert "self.tabs_group.setDocumentMode(True)" not in setup_source


def test_bottom_action_bar_keeps_standard_buttons():
    setup_source = Path("gemini_translator/ui/dialogs/setup.py").read_text(encoding="utf-8")

    assert "AnimatedActionButton" not in setup_source
    assert 'self.use_project_settings_btn = QtWidgets.QPushButton("Глобальные настройки")' in setup_source
    assert 'self.start_btn = QPushButton("Старт перевода")' in setup_source
    assert 'self.stop_btn = QPushButton("Плавный стоп")' in setup_source
    assert 'self.dry_run_btn = QPushButton("Пробный запуск")' in setup_source
    assert 'self.close_btn = QPushButton("В меню")' in setup_source
    assert 'self.proxy_button = QPushButton("Прокси")' in setup_source
