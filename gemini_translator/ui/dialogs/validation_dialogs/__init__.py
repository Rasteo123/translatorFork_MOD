# -*- coding: utf-8 -*-
"""
Validation Dialogs Subpackage

This subpackage contains helper modules for the translation validation dialog:
- untranslated_detector: Detection of untranslated words in translated text
- untranslated_fixer_dialog: Dialog for fixing untranslated words (existing)
"""

from .translation_quality_dialog import TranslationQualityDialog
from .translation_quality_models import (
    BookQaReportSnapshot,
    ChapterQaRow,
    ChapterQaTableModel,
)
from .untranslated_detector import (
    UntranslatedWordDetector,
    WordExceptionMatcher,
    HTMLCleaner,
    UnicodeRanges
)

__all__ = [
    # Translation quality report
    'BookQaReportSnapshot',
    'ChapterQaRow',
    'ChapterQaTableModel',
    'TranslationQualityDialog',
    # Untranslated Detector
    'UntranslatedWordDetector',
    'WordExceptionMatcher',
    'HTMLCleaner',
    'UnicodeRanges',
]
