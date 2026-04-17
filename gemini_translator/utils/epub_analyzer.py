# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# EPUB Analyzer - Pure logic for EPUB structure analysis
# ---------------------------------------------------------------------------
# This module provides analysis capabilities for EPUB files without UI dependencies.
# It detects issues like:
# - Numbering mismatches between filenames and chapter headers
# - Orphaned text outside of paragraph tags
# - Suspicious attributes that appear on most elements
# - Broken line breaks (<br> tags)
# ---------------------------------------------------------------------------

import os
import re
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter

# BS4 imports with fallback
try:
    from bs4 import BeautifulSoup, Tag, NavigableString
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    BeautifulSoup = None
    Tag = None
    NavigableString = None

# Number recognizers (optional)
try:
    from recognizers_text import Culture
    from recognizers_number import recognize_number
    RECOGNIZERS_AVAILABLE = True
except ImportError:
    RECOGNIZERS_AVAILABLE = False


class EpubAnalyzer:
    """
    Pure logic class for EPUB analysis (no UI dependencies).
    
    This class analyzes EPUB files and returns a list of detected issues
    that can be fixed by EpubCleaner.
    
    Usage:
        analyzer = EpubAnalyzer("/path/to/book.epub")
        issues = analyzer.analyze()
        
        for issue in issues:
            print(f"Found: {issue['type']} - {issue.get('desc', '')}")
    """
    
    # Configuration constants
    ANALYSIS_THRESHOLD = 0.90  # 90% of elements must have attribute to be flagged
    MIN_TAG_COUNT_FOR_ANALYSIS = 5  # Minimum occurrences before flagging
    MIN_TAG_COUNT_FOR_LABEL = 1  # Lower threshold for <label> tags
    
    # Regex patterns (compiled once for performance)
    RE_TAG_OPENER = re.compile(r'<([a-zA-Z0-9]+)(\s+[^>]*)?>', re.IGNORECASE)
    RE_ATTRIBUTES = re.compile(r'([a-zA-Z-]+)\s*=\s*["\']([^"\']*)["\']')
    RE_BR = re.compile(r'<br\b[^>]*>', re.IGNORECASE)
    RE_H1 = re.compile(r'<h1\b[^>]*>(.*?)</h1>', re.IGNORECASE | re.DOTALL)
    RE_TITLE = re.compile(r'<title\b[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)
    RE_DIGITS = re.compile(r'\d+')
    
    # Tags to analyze for attributes
    ANALYZED_TAGS = {'p', 'div', 'span', 'body', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'a', 'label'}
    
    # Block-level tags for orphan detection
    BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 
                  'hr', 'ul', 'ol', 'table', 'script', 'style', 'head', 'title', 
                  'meta', 'link', 'br'}
    
    # Inline tags that shouldn't contain orphaned text directly in body
    INLINE_TAGS = {'label', 'span', 'a', 'b', 'i', 'strong', 'em', 'img'}
    
    def __init__(self, epub_path: str, chapters_list: Optional[List[str]] = None):
        """
        Initialize the analyzer.
        
        Args:
            epub_path: Path to the EPUB file (must be accessible on disk).
            chapters_list: Optional list of chapter paths to analyze. 
                          If None, all HTML/XHTML files will be analyzed.
        """
        self.epub_path = epub_path
        self.chapters_list = chapters_list
        self._zipfile = None
    
    def analyze(self) -> List[Dict[str, Any]]:
        """
        Perform full EPUB analysis.
        
        Returns:
            List of issue dictionaries. Each issue has a 'type' key and 
            additional data depending on the issue type.
        """
        import zipfile
        
        stats: Dict[str, Dict] = {}
        br_files_count = 0
        orphaned_text_count = 0
        num_mismatches: List[Dict] = []
        
        try:
            with zipfile.ZipFile(open(self.epub_path, "rb"), "r") as zf:
                # Get chapter list if not provided
                if self.chapters_list is None:
                    self.chapters_list = [
                        name for name in zf.namelist()
                        if name.lower().endswith(('.html', '.xhtml', '.htm'))
                    ]
                
                for name in self.chapters_list:
                    try:
                        result = self._analyze_chapter(zf, name)
                        if result:
                            stats = self._merge_stats(stats, result.get('stats', {}))
                            br_files_count += result.get('br_found', 0)
                            orphaned_text_count += result.get('orphans_found', 0)
                            num_mismatches.extend(result.get('num_mismatches', []))
                    except Exception:
                        continue
            
            return self._compile_issues(stats, br_files_count, orphaned_text_count, num_mismatches)
            
        except Exception as e:
            print(f"[EpubAnalyzer] CRITICAL ERROR: {e}")
            return []
    
    def _analyze_chapter(self, zf, chapter_name: str) -> Optional[Dict[str, Any]]:
        """
        Analyze a single chapter.
        
        Returns:
            Dictionary with analysis results or None on error.
        """
        result = {
            'stats': {},
            'br_found': 0,
            'orphans_found': 0,
            'num_mismatches': []
        }
        
        try:
            content_bytes = zf.read(chapter_name)
            content_str = content_bytes.decode('utf-8', errors='ignore')
            
            # 1. Check numbering mismatch
            target_number = self._extract_target_number(chapter_name)
            if target_number is not None and RECOGNIZERS_AVAILABLE:
                header_text = self._extract_header_text(content_str)
                if header_text:
                    mismatches = self._check_numbering(header_text, target_number, chapter_name)
                    result['num_mismatches'].extend(mismatches)
            
            # 2. Check for <br> tags
            if self.RE_BR.search(content_str):
                result['br_found'] = 1
            
            # 3. Collect tag/attribute statistics
            result['stats'] = self._collect_tag_stats(content_str)
            
            # 4. Check for orphaned text (requires BS4)
            if BS4_AVAILABLE and BeautifulSoup:
                result['orphans_found'] = self._check_orphans(content_str)
            
            return result
            
        except Exception as e:
            print(f"[EpubAnalyzer] Error analyzing {chapter_name}: {e}")
            return None
    
    def _extract_target_number(self, chapter_path: str) -> Optional[int]:
        """Extract expected chapter number from filename."""
        basename = os.path.basename(chapter_path)
        digits_groups = self.RE_DIGITS.findall(basename)
        
        # Only use if there's exactly one number group
        if len(digits_groups) == 1:
            return int(digits_groups[0])
        return None
    
    def _extract_header_text(self, content_str: str) -> str:
        """Extract header text from H1 or title tag."""
        # Try H1 first
        h1_match = self.RE_H1.search(content_str)
        if h1_match:
            return re.sub(r'<[^>]+>', '', h1_match.group(1)).strip()
        
        # Fall back to title
        title_match = self.RE_TITLE.search(content_str)
        if title_match:
            return re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
        
        return ""
    
    def _check_numbering(self, header_text: str, target_number: int, 
                         chapter_name: str) -> List[Dict]:
        """Check if header number matches filename number."""
        mismatches = []
        
        cultures = [Culture.English, Culture.Chinese, Culture.Japanese]
        
        for culture in cultures:
            try:
                results = recognize_number(header_text, culture)
                for res in results:
                    if 'value' in res.resolution:
                        val = res.resolution['value']
                        
                        # Check if the target number is NOT in the recognized numbers
                        all_nums = [r.resolution['value'] for r in results 
                                   if 'value' in r.resolution]
                        
                        if val != target_number and target_number not in all_nums:
                            mismatches.append({
                                'type': 'num_mismatch',
                                'file': chapter_name,
                                'old_fragment': res.text,
                                'new_number': target_number,
                                'context': header_text
                            })
                            return mismatches  # One mismatch per chapter is enough
                            
            except Exception:
                continue
        
        return mismatches
    
    def _collect_tag_stats(self, content_str: str) -> Dict[str, Dict]:
        """Collect statistics about tags and their attributes."""
        stats: Dict[str, Dict] = {}
        
        for match in self.RE_TAG_OPENER.finditer(content_str):
            tag_name = match.group(1).lower()
            
            if tag_name not in self.ANALYZED_TAGS:
                continue
            
            attrs_str = match.group(2)
            
            if tag_name not in stats:
                stats[tag_name] = {'total': 0, 'attrs': Counter()}
            
            stats[tag_name]['total'] += 1
            
            if attrs_str:
                for attr_match in self.RE_ATTRIBUTES.finditer(attrs_str):
                    attr_name = attr_match.group(1).lower()
                    attr_val = attr_match.group(2).strip()
                    
                    if attr_name in ('class', 'style') and attr_val:
                        key = f"{attr_name}={attr_val}"
                        stats[tag_name]['attrs'][key] += 1
        
        return stats
    
    def _check_orphans(self, content_str: str) -> int:
        """Check for orphaned text directly in body."""
        try:
            soup = BeautifulSoup(content_str, 'html.parser')
            
            if not soup.body:
                return 0
            
            for child in soup.body.children:
                # Direct text nodes in body are orphans
                if isinstance(child, NavigableString) and child.strip():
                    return 1
                
                # Inline tags directly in body are also suspicious
                if hasattr(child, 'name') and child.name in self.INLINE_TAGS:
                    return 1
            
            return 0
            
        except Exception:
            return 0
    
    def _merge_stats(self, stats1: Dict, stats2: Dict) -> Dict:
        """Merge two statistics dictionaries."""
        result = dict(stats1)
        
        for tag, data in stats2.items():
            if tag not in result:
                result[tag] = {'total': 0, 'attrs': Counter()}
            
            result[tag]['total'] += data.get('total', 0)
            
            for attr_key, count in data.get('attrs', {}).items():
                result[tag]['attrs'][attr_key] += count
        
        return result
    
    def _compile_issues(self, stats: Dict, br_count: int, orphan_count: int,
                        num_mismatches: List[Dict]) -> List[Dict[str, Any]]:
        """Compile final list of issues from analysis results."""
        issues = []
        
        # A. Numbering mismatches
        if num_mismatches:
            issues.append({
                'type': 'num_mismatch_group',
                'count': len(num_mismatches),
                'items': num_mismatches,
                'desc': (f"Рассинхрон нумерации: {len(num_mismatches)} глав имеют "
                        f"заголовок, не совпадающий с именем файла.\n"
                        f"(Пример: файл '05.xhtml', заголовок 'Глава Четвертая')")
            })
        
        # B. Line breaks
        if br_count > 0:
            issues.append({'type': 'br', 'count': br_count})
        
        # C. Orphaned text
        if orphan_count > 0:
            issues.append({
                'type': 'orphans', 
                'count': orphan_count, 
                'desc': "Обнаружен текст и инлайн-теги вне абзацев."
            })
        
        # D. Suspicious attributes
        for tag, data in stats.items():
            total = data['total']
            min_count = (self.MIN_TAG_COUNT_FOR_LABEL if tag == 'label' 
                        else self.MIN_TAG_COUNT_FOR_ANALYSIS)
            
            if total < min_count:
                continue
            
            for attr_key, count in data['attrs'].items():
                ratio = count / total
                if ratio >= self.ANALYSIS_THRESHOLD:
                    attr_name, attr_val = attr_key.split('=', 1)
                    issues.append({
                        'type': 'attr',
                        'tag': tag,
                        'attr': attr_name,
                        'value': attr_val,
                        'percent': ratio
                    })
        
        return issues


# Convenience function for simple analysis
def analyze_epub(epub_path: str, chapters_list: Optional[List[str]] = None) -> List[Dict]:
    """
    Convenience function to analyze an EPUB file.
    
    Args:
        epub_path: Path to the EPUB file.
        chapters_list: Optional list of chapters to analyze.
        
    Returns:
        List of issue dictionaries.
    """
    analyzer = EpubAnalyzer(epub_path, chapters_list)
    return analyzer.analyze()
