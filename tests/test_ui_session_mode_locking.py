import pytest
from PyQt6.QtWidgets import QApplication, QAbstractItemView
from unittest.mock import MagicMock

from gemini_translator.ui.widgets.key_management_widget import KeyManagementWidget
from gemini_translator.ui.widgets.glossary_widget import GlossaryWidget
from gemini_translator.ui.widgets.preset_widget import PresetWidget
from gemini_translator.ui.widgets.auto_translate_widget import AutoTranslateWidget
from gemini_translator.utils.settings import SettingsManager

@pytest.fixture
def app():
    app = QApplication.instance()
    if not app:
        app = QApplication([])
    return app

def test_key_management_session_mode(app):
    sm = MagicMock()
    widget = KeyManagementWidget(settings_manager=sm, server_manager=MagicMock())
    
    widget.set_session_mode(True)
    assert widget._is_session_active == True
    if hasattr(widget, 'key_table'):
        assert widget.key_table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        
    # double click block
    widget._move_items(MagicMock(), MagicMock()) # should return early, doing nothing
    
    widget.set_session_mode(False)
    assert widget._is_session_active == False

def test_glossary_session_mode(app):
    sm = MagicMock()
    widget = GlossaryWidget(settings_manager=sm)
    
    widget.set_session_mode(True)
    
    # Check that pagination buttons are still enabled
    for btn in [getattr(widget, 'first_page_button', None), getattr(widget, 'prev_page_button', None)]:
        if btn is not None:
            assert btn.isEnabled() == True
            
    assert getattr(widget, 'add_row_btn').isEnabled() == False

def test_preset_session_mode(app):
    sm = MagicMock()
    app.get_settings_manager = lambda: sm
    widget = PresetWidget()
    widget.set_session_mode(True)
    
    if hasattr(widget, 'prompt_edit'):
        assert widget.prompt_edit.isReadOnly() == True

def test_auto_translate_session_mode(app):
    sm = MagicMock()
    sm.get_last_auto_translation_preset_name.return_value = ""
    sm.get_last_auto_translation_settings.return_value = {}
    widget = AutoTranslateWidget(settings_manager=sm)
    widget.set_session_mode(True)
    
    if hasattr(widget, 'open_glossary_btn'):
        assert widget.open_glossary_btn.isEnabled() == False
