# -*- coding: utf-8 -*-
"""
Module for text highlighting in QTextEdit widgets.

This module provides classes for:
1. Word-based highlighting (for untranslated terms)
2. Regex-based highlighting (for custom searches)
3. Unified highlighting engine combining both approaches
"""

import re
from typing import List, Optional
from PyQt6.QtWidgets import QTextEdit
from PyQt6.QtGui import QTextCharFormat, QFont, QColor, QTextCursor, QBrush
from PyQt6.QtCore import QRegularExpression


# =============================================================================
# Word Highlighter
# =============================================================================

class WordHighlighter:
    """
    Handles word-based highlighting for untranslated terms.
    
    Uses lookaround assertions for proper word boundary detection
    with Latin alphabet words.
    """
    
    # Highlight format for untranslated words (orange background)
    HIGHLIGHT_FORMAT = QTextCharFormat()
    HIGHLIGHT_FORMAT.setBackground(QColor(255, 165, 0, 100))
    HIGHLIGHT_FORMAT.setFontWeight(QFont.Weight.Bold)
    
    def create_selections(self, text_edit: QTextEdit, 
                          words: List[str]) -> List[QTextEdit.ExtraSelection]:
        """
        Create selection objects for word highlighting.
        
        Args:
            text_edit: The QTextEdit widget to highlight
            words: List of words to highlight
            
        Returns:
            List of ExtraSelection objects
        """
        selections = []
        document = text_edit.document()
        
        for word in words:
            pattern_str = self._build_pattern(word)
            
            try:
                q_regex = QRegularExpression(
                    pattern_str,
                    QRegularExpression.PatternOption.CaseInsensitiveOption
                )
                
                if not q_regex.isValid():
                    continue
                
                cursor = document.find(q_regex)
                while not cursor.isNull():
                    selection = QTextEdit.ExtraSelection()
                    selection.format = QTextCharFormat(self.HIGHLIGHT_FORMAT)
                    selection.cursor = cursor
                    selections.append(selection)
                    cursor = document.find(q_regex, cursor)
                    
            except Exception:
                # Skip invalid patterns
                continue
        
        return selections
    
    def _build_pattern(self, word: str) -> str:
        """
        Build regex pattern for word with proper boundaries.
        
        Args:
            word: Word to build pattern for
            
        Returns:
            Regex pattern string
        """
        # For pure Latin words, use lookaround boundaries
        if re.fullmatch(r'[a-zA-Z]+', word):
            # Lookbehind: not preceded by letter
            # Lookahead: not followed by letter
            # This allows finding "Level" inside "Level5" or "Item_1"
            return f"(?<![a-zA-Z]){re.escape(word)}(?![a-zA-Z])"
        
        # For other words, use simple escape
        return re.escape(word)
    
    def highlight_all(self, text_edit: QTextEdit, words: List[str]):
        """
        Apply word highlighting directly to a text edit widget.
        
        Args:
            text_edit: The QTextEdit widget to highlight
            words: List of words to highlight
        """
        selections = self.create_selections(text_edit, words)
        existing = text_edit.extraSelections()
        
        # Merge with existing selections
        all_selections = existing + selections
        text_edit.setExtraSelections(all_selections)


# =============================================================================
# Regex Highlighter
# =============================================================================

class RegexHighlighter:
    """
    Handles regex-based highlighting for custom searches.
    
    Supports both simple string matching and complex regular expressions.
    """
    
    # Highlight format for regex matches (cyan background)
    HIGHLIGHT_FORMAT = QTextCharFormat()
    HIGHLIGHT_FORMAT.setBackground(QColor(0, 191, 255, 100))
    
    def create_selections(self, text_edit: QTextEdit,
                          pattern: str,
                          case_sensitive: bool = False) -> List[QTextEdit.ExtraSelection]:
        """
        Create selection objects for regex highlighting.
        
        Args:
            text_edit: The QTextEdit widget to highlight
            pattern: Regex pattern string
            case_sensitive: Whether matching should be case-sensitive
            
        Returns:
            List of ExtraSelection objects
        """
        selections = []
        
        try:
            # Build regex flags
            flags = QRegularExpression.PatternOption.DotMatchesEverythingOption
            if not case_sensitive:
                flags |= QRegularExpression.PatternOption.CaseInsensitiveOption
            
            q_regex = QRegularExpression(pattern, flags)
            
            if not q_regex.isValid():
                return selections
            
            document = text_edit.document()
            cursor = document.find(q_regex)
            
            while not cursor.isNull():
                selection = QTextEdit.ExtraSelection()
                selection.format = QTextCharFormat(self.HIGHLIGHT_FORMAT)
                selection.cursor = cursor
                selections.append(selection)
                cursor = document.find(q_regex, cursor)
                
        except Exception as e:
            print(f"[RegexHighlighter] Error: {e}")
        
        return selections
    
    def create_selections_from_python_matches(
            self, text_edit: QTextEdit,
            matches: List) -> List[QTextEdit.ExtraSelection]:
        """
        Create selections from pre-computed Python regex matches.
        
        This is useful when matches were already found in raw HTML
        and need to be highlighted in the displayed text.
        
        Args:
            text_edit: The QTextEdit widget to highlight
            matches: List of Python regex match objects
            
        Returns:
            List of ExtraSelection objects
        """
        selections = []
        document = text_edit.document()
        search_cursor = QTextCursor(document)
        
        for match in matches:
            matched_text = match.group(0)
            
            # Find this text in the document
            found_cursor = document.find(matched_text, search_cursor)
            
            if not found_cursor.isNull():
                selection = QTextEdit.ExtraSelection()
                selection.format = QTextCharFormat(self.HIGHLIGHT_FORMAT)
                selection.cursor = found_cursor
                selections.append(selection)
                search_cursor = found_cursor
        
        return selections


# =============================================================================
# HTML-Aware Regex Highlighter
# =============================================================================

class HtmlRegexHighlighter:
    """
    Advanced highlighter that handles regex matching in HTML content.
    
    This class performs two-stage highlighting:
    1. Find matches in raw HTML
    2. Extract visible text and highlight in the displayed document
    """
    
    HIGHLIGHT_FORMAT = QTextCharFormat()
    HIGHLIGHT_FORMAT.setBackground(QColor(0, 191, 255, 100))
    
    def __init__(self, results_data: dict, row_index: int):
        """
        Initialize the HTML-aware highlighter.
        
        Args:
            results_data: Dictionary containing translated_html for the row
            row_index: Index of the row in the validation table
        """
        self.results_data = results_data
        self.row_index = row_index
    
    def create_selections(self, text_edit: QTextEdit,
                          pattern: str,
                          case_sensitive: bool = False) -> List[QTextEdit.ExtraSelection]:
        """
        Create selections with HTML-aware matching.
        
        Args:
            text_edit: The QTextEdit widget to highlight
            pattern: Regex pattern string
            case_sensitive: Whether matching should be case-sensitive
            
        Returns:
            List of ExtraSelection objects
        """
        from bs4 import BeautifulSoup
        
        selections = []
        
        # Get raw HTML for this row
        if self.row_index not in self.results_data:
            return selections
        
        raw_html = self.results_data[self.row_index].get('translated_html', '')
        
        try:
            # Build Python regex
            flags = re.DOTALL
            if not case_sensitive:
                flags |= re.IGNORECASE
            
            python_regex = re.compile(pattern, flags)
            
            # Find matches in raw HTML
            matches = list(python_regex.finditer(raw_html))
            
            # Use cursor for sequential search
            document = text_edit.document()
            search_cursor = QTextCursor(document)
            
            for match in matches:
                # Extract visible text from matched HTML fragment
                matched_html = match.group(0)
                visible_text = BeautifulSoup(matched_html, 'html.parser').get_text().strip()
                
                if not visible_text:
                    continue
                
                # Find and highlight visible text
                found_cursor = document.find(visible_text, search_cursor)
                
                if not found_cursor.isNull():
                    selection = QTextEdit.ExtraSelection()
                    selection.format = QTextCharFormat(self.HIGHLIGHT_FORMAT)
                    selection.cursor = found_cursor
                    selections.append(selection)
                    search_cursor = found_cursor
                    
        except re.error as e:
            print(f"[HtmlRegexHighlighter] Regex error: {e}")
        except Exception as e:
            print(f"[HtmlRegexHighlighter] Error: {e}")
        
        return selections


# =============================================================================
# Unified Highlighting Engine
# =============================================================================

class HighlightingEngine:
    """
    Unified engine for all text highlighting operations.
    
    Combines word highlighting and regex highlighting into a single interface.
    """
    
    def __init__(self, results_data: dict = None, row_index: int = 0):
        """
        Initialize the highlighting engine.
        
        Args:
            results_data: Optional dictionary of results data for HTML-aware matching
            row_index: Row index for HTML-aware matching
        """
        self.word_highlighter = WordHighlighter()
        self.regex_highlighter = RegexHighlighter()
        self.html_regex_highlighter = HtmlRegexHighlighter(
            results_data or {}, row_index
        )
        self.results_data = results_data
        self.row_index = row_index
    
    def apply_highlights(self, text_edit: QTextEdit,
                         words_to_highlight: List[str] = None,
                         regex_pattern: str = None,
                         case_sensitive: bool = False,
                         use_html_aware: bool = True):
        """
        Apply all highlights to a text edit widget.
        
        Args:
            text_edit: The QTextEdit widget to highlight
            words_to_highlight: List of words to highlight (orange)
            regex_pattern: Regex pattern for additional highlighting (cyan)
            case_sensitive: Whether regex matching should be case-sensitive
            use_html_aware: Whether to use HTML-aware regex matching
            
        Returns:
            List of all applied selections
        """
        all_selections = []
        
        # Apply word highlighting
        if words_to_highlight:
            word_selections = self.word_highlighter.create_selections(
                text_edit, words_to_highlight
            )
            all_selections.extend(word_selections)
        
        # Apply regex highlighting
        if regex_pattern:
            if use_html_aware and self.results_data:
                regex_selections = self.html_regex_highlighter.create_selections(
                    text_edit, regex_pattern, case_sensitive
                )
            else:
                regex_selections = self.regex_highlighter.create_selections(
                    text_edit, regex_pattern, case_sensitive
                )
            all_selections.extend(regex_selections)
        
        # Apply all selections to the widget
        text_edit.setExtraSelections(all_selections)
        
        return all_selections
    
    def clear_highlights(self, text_edit: QTextEdit):
        """
        Clear all highlights from a text edit widget.
        
        Args:
            text_edit: The QTextEdit widget to clear
        """
        text_edit.setExtraSelections([])
    
    def highlight_words_only(self, text_edit: QTextEdit, 
                             words: List[str]) -> List[QTextEdit.ExtraSelection]:
        """
        Apply only word highlighting.
        
        Args:
            text_edit: The QTextEdit widget to highlight
            words: List of words to highlight
            
        Returns:
            List of applied selections
        """
        selections = self.word_highlighter.create_selections(text_edit, words)
        text_edit.setExtraSelections(selections)
        return selections
    
    def highlight_regex_only(self, text_edit: QTextEdit,
                             pattern: str,
                             case_sensitive: bool = False) -> List[QTextEdit.ExtraSelection]:
        """
        Apply only regex highlighting.
        
        Args:
            text_edit: The QTextEdit widget to highlight
            pattern: Regex pattern string
            case_sensitive: Whether matching should be case-sensitive
            
        Returns:
            List of applied selections
        """
        if self.results_data:
            selections = self.html_regex_highlighter.create_selections(
                text_edit, pattern, case_sensitive
            )
        else:
            selections = self.regex_highlighter.create_selections(
                text_edit, pattern, case_sensitive
            )
        
        text_edit.setExtraSelections(selections)
        return selections


# =============================================================================
# Highlight Format Presets
# =============================================================================

class HighlightFormats:
    """Pre-defined highlight formats for common use cases."""
    
    @staticmethod
    def untranslated_format() -> QTextCharFormat:
        """Orange background for untranslated words."""
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 165, 0, 100))
        fmt.setFontWeight(QFont.Weight.Bold)
        return fmt
    
    @staticmethod
    def regex_match_format() -> QTextCharFormat:
        """Cyan background for regex matches."""
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(0, 191, 255, 100))
        return fmt
    
    @staticmethod
    def error_format() -> QTextCharFormat:
        """Red background for errors."""
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(255, 0, 0, 100))
        fmt.setForeground(QColor(255, 255, 255))
        return fmt
    
    @staticmethod
    def success_format() -> QTextCharFormat:
        """Green background for success."""
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(0, 255, 0, 100))
        return fmt
