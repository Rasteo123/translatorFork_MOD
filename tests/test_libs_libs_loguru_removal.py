"""libs-loguru: замена необязательного loguru на безусловный stdlib logging.

Характеризующие тесты для аудиторского вердикта libs-loguru: подтверждают,
что notifications.py и gemini_reader_v3.py используют logging.getLogger
без try/except-фолбэка на loguru, и что loguru нигде в этих модулях
больше не упоминается.
"""
import inspect
import logging

import gemini_reader_v3 as reader
from gemini_translator.ui import notifications


def test_notifications_logger_is_stdlib_logging():
    """logger в notifications.py — обычный logging.Logger, не loguru."""
    assert isinstance(notifications.logger, logging.Logger)


def test_notifications_source_has_no_loguru_reference():
    """В notifications.py не осталось ни импорта, ни упоминания loguru."""
    source = inspect.getsource(notifications)
    assert "loguru" not in source


def test_reader_logger_is_stdlib_logging_with_gui_handler():
    """logger в gemini_reader_v3.py — stdlib logging с _GuiLoggingHandler."""
    assert isinstance(reader.logger, logging.Logger)
    assert any(
        isinstance(h, reader._GuiLoggingHandler) for h in reader.logger.handlers
    )


def test_reader_module_has_no_loguru_reference():
    """В gemini_reader_v3.py не осталось ни импорта, ни ветки на loguru."""
    source = inspect.getsource(reader)
    assert "loguru" not in source
