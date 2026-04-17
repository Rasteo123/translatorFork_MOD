# -*- coding: utf-8 -*-
"""
Module for detecting untranslated words in translated text.

This module provides classes for:
1. Matching words against exception lists
2. Detecting untranslated Latin and CJK words
3. Cleaning HTML content for analysis
"""

import re
import html
from typing import Set, List, Tuple, Dict, Any
from bs4 import BeautifulSoup, NavigableString, ProcessingInstruction, Comment, Declaration


# =============================================================================
# CJK and Unicode Character Ranges
# =============================================================================

class UnicodeRanges:
    """Comprehensive Unicode ranges for character classification."""
    
    # CJK Unified Ideographs (Chinese)
    CJK_UNIFIED_IDEOGRAPHS = r'\u4e00-\u9fff'
    CJK_UNIFIED_IDEOGRAPHS_EXT_A = r'\u3400-\u4dbf'
    CJK_UNIFIED_IDEOGRAPHS_EXT_B = r'\U00020000-\U0002a6df'
    CJK_UNIFIED_IDEOGRAPHS_EXT_C = r'\U0002a700-\U0002b73f'
    CJK_UNIFIED_IDEOGRAPHS_EXT_D = r'\U0002b740-\U0002b81f'
    CJK_UNIFIED_IDEOGRAPHS_EXT_E = r'\U0002b820-\U0002ceaf'
    CJK_UNIFIED_IDEOGRAPHS_EXT_F = r'\U0002ceb0-\U0002ebef'
    CJK_COMPATIBILITY_IDEOGRAPHS = r'\uf900-\ufaff'
    CJK_COMPATIBILITY_IDEOGRAPHS_SUPPLEMENT = r'\U0002f800-\U0002fa1f'
    
    # Japanese Hiragana and Katakana
    HIRAGANA = r'\u3040-\u309f'
    HIRAGANA_EXTENDED = r'\u1b001-\u1b11f'
    KATAKANA = r'\u30a0-\u30ff'
    KATAKANA_PHONETIC_EXTENSIONS = r'\u31f0-\u31ff'
    KATAKANA_SMALL = r'\u3248-\u324f'
    KATAKANA_EXTENDED = r'\u1b000-\u1b001'
    
    # Korean Hangul
    HANGUL_SYLLABLES = r'\uac00-\ud7af'
    HANGUL_JAMO = r'\u1100-\u11ff'
    HANGUL_COMPATIBILITY_JAMO = r'\u3130-\u318f'
    HANGUL_JAMO_EXTENDED_A = r'\ua960-\ua97f'
    HANGUL_JAMO_EXTENDED_B = r'\ud7b0-\ud7ff'
    
    # Bopomofo (Zhuyin) - Used for Chinese phonetic notation
    BOPOMOFO = r'\u3100-\u312f'
    BOPOMOFO_EXTENDED = r'\u31a0-\u31bf'
    
    # Other CJK symbols and punctuation
    CJK_SYMBOLS_AND_PUNCTUATION = r'\u3000-\u303f'
    CJK_STROKES = r'\u31c0-\u31ef'
    CJK_RADICALS_SUPPLEMENT = r'\u2e80-\u2eff'
    KANGXI_RADICALS = r'\u2f00-\u2fdf'
    IDEOGRAPHIC_DESCRIPTION_CHARACTERS = r'\u2ff0-\u2fff'
    
    # Combined pattern for all CJK characters (EXPANDED)
    ALL_CJK_PATTERN = (
        f'[{CJK_UNIFIED_IDEOGRAPHS}'
        f'{CJK_UNIFIED_IDEOGRAPHS_EXT_A}'
        f'{HIRAGANA}'
        f'{KATAKANA}'
        f'{HANGUL_SYLLABLES}'
        f'{BOPOMOFO}'
        f'{CJK_COMPATIBILITY_IDEOGRAPHS}'
        f'{CJK_SYMBOLS_AND_PUNCTUATION}'
        f'{KANGXI_RADICALS}'
        f']'
    )
    
    # Extended pattern including less common ranges
    ALL_CJK_EXTENDED_PATTERN = (
        f'[{CJK_UNIFIED_IDEOGRAPHS}'
        f'{CJK_UNIFIED_IDEOGRAPHS_EXT_A}'
        f'{CJK_UNIFIED_IDEOGRAPHS_EXT_B}'
        f'{HIRAGANA}'
        f'{HIRAGANA_EXTENDED}'
        f'{KATAKANA}'
        f'{KATAKANA_PHONETIC_EXTENSIONS}'
        f'{HANGUL_SYLLABLES}'
        f'{HANGUL_JAMO}'
        f'{BOPOMOFO}'
        f'{BOPOMOFO_EXTENDED}'
        f']'
    )


# =============================================================================
# HTML Cleaner
# =============================================================================

class HTMLCleaner:
    """Utility class for cleaning HTML content before text analysis."""
    
    @staticmethod
    def normalize_html_entities(html_content: str) -> str:
        """
        Convert HTML entities to their character equivalents.
        
        Args:
            html_content: Raw HTML content with potential entities
            
        Returns:
            HTML content with entities converted to characters
        """
        try:
            return html.unescape(html_content)
        except Exception:
            return html_content
    
    @staticmethod
    def strip_html_tags_preserving_structure(html_content: str) -> str:
        """
        Remove HTML tags but keep text content intact.
        
        Args:
            html_content: Raw HTML content
            
        Returns:
            Plain text extracted from HTML
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove script, style, and head elements
            for tag in soup(['script', 'style', 'head', 'title', 'meta']):
                tag.decompose()
            
            return soup.get_text(separator=' ', strip=True)
        except Exception:
            return html_content
    
    @staticmethod
    def get_body_text(html_content: str) -> str:
        """
        Extract text content from the body element only.
        
        Args:
            html_content: Raw HTML content
            
        Returns:
            Text content from body element
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            body = soup.find('body')
            
            if body:
                # Remove script and style elements
                for tag in body(['script', 'style']):
                    tag.decompose()
                return body.get_text(separator=' ', strip=True)
            
            return soup.get_text(separator=' ', strip=True)
        except Exception:
            return html_content


# =============================================================================
# Word Exception Matcher
# =============================================================================

class WordExceptionMatcher:
    """
    Handles matching of words against exception lists.
    
    Supports both single-word exceptions and multi-word phrases.
    Uses Unicode-aware word boundaries for accurate matching.
    """
    
    # Pattern for word boundary that works with Unicode
    UNICODE_WORD_BOUNDARY_START = r'(?<!\w)'
    UNICODE_WORD_BOUNDARY_END = r'(?!\w)'
    
    def __init__(self, exceptions: Set[str]):
        """
        Initialize the exception matcher.
        
        Args:
            exceptions: Set of exception words/phrases (case-insensitive)
        """
        self.single_words: Set[str] = set()
        self.phrases: List[str] = []
        self._phrase_patterns: List[Tuple[str, re.Pattern]] = []
        
        self._compile_patterns(exceptions)
    
    def _compile_patterns(self, exceptions: Set[str]):
        """
        Separate and compile exception patterns.
        
        Args:
            exceptions: Set of exception strings
        """
        for exc in exceptions:
            if not exc or not exc.strip():
                continue
                
            exc = exc.strip()
            
            if ' ' in exc:
                # Multi-word phrase
                self.phrases.append(exc)
            else:
                # Single word
                self.single_words.add(exc.lower())
        
        # Sort phrases by length (longest first) for proper matching
        self.phrases.sort(key=len, reverse=True)
        
        # Pre-compile phrase patterns for efficiency
        self._phrase_patterns = []
        for phrase in self.phrases:
            pattern_str = (
                self.UNICODE_WORD_BOUNDARY_START + 
                re.escape(phrase) + 
                self.UNICODE_WORD_BOUNDARY_END
            )
            try:
                pattern = re.compile(pattern_str, re.IGNORECASE | re.UNICODE)
                self._phrase_patterns.append((phrase, pattern))
            except re.error:
                # Skip invalid patterns
                pass
    
    def is_exception(self, word: str) -> bool:
        """
        Check if a single word is in the exception list.
        
        Args:
            word: Word to check
            
        Returns:
            True if word is an exception
        """
        return word.lower() in self.single_words
    
    def remove_phrase_exceptions(self, text: str) -> str:
        """
        Remove phrase exceptions from text by replacing them with spaces.
        
        Args:
            text: Input text to process
            
        Returns:
            Text with phrase exceptions replaced by spaces
        """
        result = text
        
        for phrase, pattern in self._phrase_patterns:
            result = pattern.sub(' ', result)
        
        return result
    
    def find_phrase_matches(self, text: str) -> List[str]:
        """
        Find all phrase exceptions that match in the text.
        
        Args:
            text: Input text to search
            
        Returns:
            List of matched phrases
        """
        matches = []
        
        for phrase, pattern in self._phrase_patterns:
            if pattern.search(text):
                matches.append(phrase)
        
        return matches


# =============================================================================
# Untranslated Word Detector
# =============================================================================

class UntranslatedWordDetector:
    """
    Detects potentially untranslated words in translated text.
    
    The detector looks for:
    1. Latin alphabet words (3+ characters) that may be untranslated
    2. CJK characters that may be untranslated
    
    Words in the exception list are filtered out.
    """
    
    # Minimum word length to consider (for Latin words)
    MIN_LATIN_WORD_LENGTH = 3
    
    # Pattern for removing Cyrillic text (Russian, Ukrainian, Bulgarian, etc.)
    CYRILLIC_PATTERN = re.compile(r'[а-яА-ЯёЁ]+')
    
    # Pattern for removing non-word characters (keeping only letters)
    PURE_WORD_PATTERN = re.compile(r'[\W\d_]+')
    
    # Pattern for detecting CJK characters
    CJK_PATTERN = re.compile(UnicodeRanges.ALL_CJK_PATTERN)
    
    # Pattern for detecting any CJK (extended)
    CJK_EXTENDED_PATTERN = re.compile(UnicodeRanges.ALL_CJK_EXTENDED_PATTERN)
    
    # Pattern for single Latin character (should be ignored - common in ratings/grades)
    SINGLE_LATIN_PATTERN = re.compile(r'^[a-zA-Z]$')
    
    # Pattern for rating/grade formats that are typically not translated
    # Matches: A+, B-, S, E, A, etc. (single letter with optional +/- suffix)
    RATING_PATTERN = re.compile(r'^[A-Sa-s][+-]?$')
    
    def __init__(self, word_exceptions: Set[str]):
        """
        Initialize the detector.
        
        Args:
            word_exceptions: Set of words/phrases to exclude from detection
        """
        self.exception_matcher = WordExceptionMatcher(word_exceptions)
        self.html_cleaner = HTMLCleaner()
    
    def detect(self, translated_content: str) -> List[str]:
        """
        Detect untranslated words in the translated content.
        
        Args:
            translated_content: HTML content of the translation
            
        Returns:
            Sorted list of unique untranslated words (longest first)
        """
        try:
            # Step 1: Normalize HTML entities
            normalized_content = self.html_cleaner.normalize_html_entities(translated_content)
            
            # Step 2: Extract plain text from HTML
            plain_text = self.html_cleaner.strip_html_tags_preserving_structure(normalized_content)
            
            if not plain_text:
                return []
            
            # Step 3: Remove phrase exceptions first
            text_without_phrases = self.exception_matcher.remove_phrase_exceptions(plain_text)
            
            # Step 4: Remove Cyrillic text
            no_cyrillic_text = self.CYRILLIC_PATTERN.sub(' ', text_without_phrases)
            
            # Step 5: Remove non-word characters to get pure residue
            pure_residue_text = self.PURE_WORD_PATTERN.sub(' ', no_cyrillic_text)
            
            # Step 6: Collect untranslated words
            untranslated_words = []
            
            for word in pure_residue_text.split():
                if self._should_include_word(word):
                    untranslated_words.append(word)
            
            # Return sorted unique words (longest first)
            return sorted(list(set(untranslated_words)), key=len, reverse=True)
            
        except Exception as e:
            print(f"[UntranslatedWordDetector] Error during detection: {e}")
            return []
    
    def _should_include_word(self, word: str) -> bool:
        """
        Determine if a word should be included in the untranslated list.
        
        Args:
            word: Word to check
            
        Returns:
            True if word should be flagged as untranslated
        """
        # Skip empty words
        if not word or len(word) < 1:
            return False
        
        # Check if it's a CJK character FIRST (always include, even single chars)
        if self.CJK_PATTERN.search(word):
            return True
        
        # Skip single-character Latin letters (common in ratings/grades like "E", "A", "B")
        if self.SINGLE_LATIN_PATTERN.match(word):
            return False
        
        # Skip rating patterns like A+, B-, S, etc.
        if self.RATING_PATTERN.match(word):
            return False
        
        # For non-CJK words, apply length filter
        if len(word) < self.MIN_LATIN_WORD_LENGTH:
            return False
        
        # Check if word is in exception list
        if self.exception_matcher.is_exception(word.lower()):
            return False
        
        return True
    
    def detect_in_text(self, text: str) -> List[str]:
        """
        Detect untranslated words in plain text (no HTML processing).
        
        Args:
            text: Plain text to analyze
            
        Returns:
            Sorted list of unique untranslated words
        """
        try:
            # Remove phrase exceptions
            text_without_phrases = self.exception_matcher.remove_phrase_exceptions(text)
            
            # Remove Cyrillic
            no_cyrillic = self.CYRILLIC_PATTERN.sub(' ', text_without_phrases)
            
            # Get pure words
            pure_words = self.PURE_WORD_PATTERN.sub(' ', no_cyrillic).split()
            
            # Filter words
            untranslated = [w for w in pure_words if self._should_include_word(w)]
            
            return sorted(list(set(untranslated)), key=len, reverse=True)
            
        except Exception as e:
            print(f"[UntranslatedWordDetector] Error in detect_in_text: {e}")
            return []
    
    def detect_mixed_script(self, translated_content: str) -> List[Dict[str, Any]]:
        """
        Detect CJK characters mixed within translated (Cyrillic) text.
        
        This method specifically looks for cases where CJK characters appear
        within otherwise translated text, e.g., "покачал 頭 головой".
        
        Args:
            translated_content: HTML content of the translation
            
        Returns:
            List of dicts with keys: 'text', 'position', 'context', 'has_cyrillic_nearby'
        """
        try:
            # Normalize and extract plain text
            normalized = self.html_cleaner.normalize_html_entities(translated_content)
            plain_text = self.html_cleaner.strip_html_tags_preserving_structure(normalized)
            
            if not plain_text:
                return []
            
            results = []
            
            # Find all CJK characters with their positions
            for match in self.CJK_PATTERN.finditer(plain_text):
                char = match.group()
                start = match.start()
                end = match.end()
                
                # Get context (surrounding text)
                context_start = max(0, start - 20)
                context_end = min(len(plain_text), end + 20)
                context = plain_text[context_start:context_end]
                
                results.append({
                    'text': char,
                    'position': start,
                    'context': context,
                    'has_cyrillic_nearby': bool(re.search(r'[а-яА-ЯёЁ]', context))
                })
            
            # Filter to only show CJK that appears near Cyrillic (mixed script)
            mixed_results = [r for r in results if r['has_cyrillic_nearby']]
            
            return mixed_results
            
        except Exception as e:
            print(f"[UntranslatedWordDetector] Error in detect_mixed_script: {e}")
            return []
