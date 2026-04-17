# -*- coding: utf-8 -*-
"""
Validation Dialogs Subpackage

This subpackage contains helper modules for the translation validation dialog:
- untranslated_detector: Detection of untranslated words in translated text
- container_detector: HTML container type detection for text processing
- context_grouper: Grouping of untranslated word occurrences by context
- highlighting_engine: Text highlighting for QTextEdit widgets
- untranslated_fixer_dialog: Dialog for fixing untranslated words (existing)
"""

from .untranslated_detector import (
    UntranslatedWordDetector,
    WordExceptionMatcher,
    HTMLCleaner,
    UnicodeRanges
)

from .container_detector import (
    ContainerDetector,
    ContextTreatmentDecider,
    ContainerTypes
)

from .context_grouper import (
    ContextGrouper,
    ContextGroup,
    Occurrence,
    GroupedDataConverter
)

from .highlighting_engine import (
    HighlightingEngine,
    WordHighlighter,
    RegexHighlighter,
    HtmlRegexHighlighter,
    HighlightFormats
)

__all__ = [
    # Untranslated Detector
    'UntranslatedWordDetector',
    'WordExceptionMatcher',
    'HTMLCleaner',
    'UnicodeRanges',
    
    # Container Detector
    'ContainerDetector',
    'ContextTreatmentDecider',
    'ContainerTypes',
    
    # Context Grouper
    'ContextGrouper',
    'ContextGroup',
    'Occurrence',
    'GroupedDataConverter',
    
    # Highlighting Engine
    'HighlightingEngine',
    'WordHighlighter',
    'RegexHighlighter',
    'HtmlRegexHighlighter',
    'HighlightFormats',
]
