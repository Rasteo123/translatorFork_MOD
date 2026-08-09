# -*- coding: utf-8 -*-
"""
Validation Dialogs Subpackage

This subpackage contains helper modules for the translation validation dialog:
- untranslated_detector: Detection of untranslated words in translated text
- untranslated_fixer_dialog: Dialog for fixing untranslated words (existing)
"""

from .untranslated_detector import (
    UntranslatedWordDetector,
    WordExceptionMatcher,
    HTMLCleaner,
    UnicodeRanges
)

__all__ = [
    # Untranslated Detector
    'UntranslatedWordDetector',
    'WordExceptionMatcher',
    'HTMLCleaner',
    'UnicodeRanges',
]
