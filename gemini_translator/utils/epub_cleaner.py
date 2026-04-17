# -*- coding: utf-8 -*-

# ---------------------------------------------------------------------------
# EPUB Cleaner - Pure logic for EPUB cleanup operations
# ---------------------------------------------------------------------------
# This module provides cleanup capabilities for EPUB files without UI dependencies.
# It can fix issues like:
# - Numbering mismatches between filenames and chapter headers
# - Orphaned text outside of paragraph tags
# - Suspicious attributes on elements
# - Broken line breaks (<br> tags)
# - Sequential renumbering of chapters
# ---------------------------------------------------------------------------

import os
import re
import io
import zipfile
from typing import List, Dict, Any, Optional, Tuple
from xml.etree import ElementTree as ET

# BS4 imports with fallback
try:
    from bs4 import BeautifulSoup, Tag, NavigableString
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    BeautifulSoup = None
    Tag = None
    NavigableString = None


class EpubCleaner:
    """
    Pure logic class for EPUB cleanup operations (no UI dependencies).
    
    This class applies fixes to EPUB files based on issues detected by EpubAnalyzer.
    
    Usage:
        cleaner = EpubCleaner("/path/to/book.epub")
        files_processed = cleaner.apply_fixes(fixes_list)
        print(f"Fixed {files_processed} files")
    """
    
    # Regex patterns (compiled once for performance)
    RE_HEADER_TAG = re.compile(r'<(h[1-6]|title)[^>]*>.*?</\1>', re.DOTALL | re.IGNORECASE)
    RE_ESCAPE_SPECIAL = re.compile(r'([\\^$.|?*+(){}[\]])')
    
    def __init__(self, epub_path: str):
        """
        Initialize the cleaner.
        
        Args:
            epub_path: Path to the EPUB file (must be accessible on disk).
        """
        self.epub_path = epub_path
        self.tasks = []
        self.files_processed = 0
        self.global_link_updates: Dict[str, Tuple[str, str]] = {}
    
    def apply_fixes(self, fixes: List[Dict[str, Any]]) -> int:
        """
        Apply a list of fixes to the EPUB file.
        
        Args:
            fixes: List of fix dictionaries. Each fix has a 'type' key and
                   additional data depending on the fix type.
                   
        Returns:
            Number of files processed/modified.
        """
        self.tasks = []
        self.files_processed = 0
        self.global_link_updates = {}
        
        # Parse fixes into tasks
        for fix in fixes:
            task = self._parse_fix(fix)
            if task:
                self.tasks.append(task)
        
        # Execute cleanup
        self._run_cleanup()
        
        return self.files_processed
    
    def _parse_fix(self, fix: Dict[str, Any]) -> Optional[Dict]:
        """Parse a fix dictionary into an internal task format."""
        fix_type = fix.get('type')
        
        if fix_type == 'num_mismatch':
            return {
                'type': 'num_mismatch',
                'file': fix.get('file'),
                'old_fragment': fix.get('old_fragment'),
                'new_number': fix.get('new_number')
            }
        
        elif fix_type == 'force_renumber_sequential':
            return {'type': 'force_renumber_sequential'}
        
        elif fix_type == 'br':
            return {'type': 'br'}
        
        elif fix_type == 'orphans':
            return {'type': 'orphans'}
        
        elif fix_type == 'attr':
            tag = fix.get('tag', '')
            attr = fix.get('attr', '')
            value = fix.get('value', '')
            
            # Escape special regex characters in value
            escaped_value = re.escape(value)
            
            return {
                'type': 'attr',
                'tag_re': re.compile(fr'(<{re.escape(tag)}\b[^>]*>)', re.IGNORECASE),
                'attr_re': re.compile(fr'\s+{re.escape(attr)}\s*=\s*["\']{escaped_value}["\']', 
                                     re.IGNORECASE)
            }
        
        return None
    
    def _run_cleanup(self):
        """Execute the cleanup operation on the EPUB file."""
        temp_output_buffer = io.BytesIO()
        
        # Check for force renumbering
        force_renumber = any(t['type'] == 'force_renumber_sequential' for t in self.tasks)
        
        # Get chapter order if needed for sequential renumbering
        ordered_chapters = []
        if force_renumber:
            from .epub_tools import get_epub_chapter_order
            ordered_chapters = get_epub_chapter_order(self.epub_path)
        
        with zipfile.ZipFile(open(self.epub_path, 'rb'), 'r') as zin:
            with zipfile.ZipFile(temp_output_buffer, 'w', zipfile.ZIP_DEFLATED) as zout:
                
                # 1. Read all files into memory
                all_files_content = {}
                for item in zin.infolist():
                    all_files_content[item.filename] = zin.read(item.filename)
                
                # 2A. Force sequential renumbering
                if force_renumber:
                    self._renumber_sequential(all_files_content, ordered_chapters)
                
                # 2B. Individual number mismatch fixes
                elif not force_renumber:
                    for task in [t for t in self.tasks if t.get('type') == 'num_mismatch']:
                        self._fix_num_mismatch(all_files_content, task)
                
                # 3. Global pass (links and other fixes)
                self._apply_global_fixes(all_files_content)
                
                # 4. Write all files to output
                for filename, content_bytes in all_files_content.items():
                    zout.writestr(filename, content_bytes)
        
        # Write back to original file
        temp_output_buffer.seek(0)
        with open(self.epub_path, 'wb') as f:
            f.write(temp_output_buffer.getvalue())
    
    def _renumber_sequential(self, all_files_content: Dict[str, bytes], 
                             ordered_chapters: List[str]) -> None:
        """Apply sequential numbering to all chapters."""
        if not BS4_AVAILABLE or not BeautifulSoup:
            return
        
        current_chapter_index = 1
        
        for filename in ordered_chapters:
            if filename not in all_files_content:
                continue
            
            try:
                content_str = all_files_content[filename].decode('utf-8', errors='ignore')
                soup = BeautifulSoup(content_str, 'html.parser')
                
                header = soup.find(['h1', 'h2', 'h3'])
                title_tag = soup.find('title')
                
                if header:
                    old_text = header.get_text().strip()
                    
                    # Variant 1: Replace first number if exists
                    if re.search(r'\d+', old_text):
                        new_text = re.sub(r'\d+', str(current_chapter_index), 
                                         old_text, count=1)
                    # Variant 2: Add number at start
                    else:
                        new_text = f"({current_chapter_index}) {old_text}"
                    
                    if new_text != old_text:
                        header.string = new_text
                        if title_tag:
                            title_tag.string = new_text
                        
                        content_str = str(soup)
                        all_files_content[filename] = content_str.encode('utf-8')
                        self.global_link_updates[filename] = (old_text, new_text)
                        self.files_processed += 1
                
                current_chapter_index += 1
                
            except Exception as e:
                print(f"[EpubCleaner] Renumber error in {filename}: {e}")
    
    def _fix_num_mismatch(self, all_files_content: Dict[str, bytes], 
                          task: Dict) -> None:
        """Fix a single number mismatch issue."""
        target_file = task['file']
        
        if target_file not in all_files_content:
            return
        
        try:
            content_str = all_files_content[target_file].decode('utf-8', errors='ignore')
            old_fragment = task['old_fragment']
            new_number = str(task['new_number'])
            
            if old_fragment in content_str:
                def replace_in_tag(match):
                    return match.group(0).replace(old_fragment, new_number)
                
                content_str = self.RE_HEADER_TAG.sub(replace_in_tag, content_str)
                
                all_files_content[target_file] = content_str.encode('utf-8')
                self.global_link_updates[target_file] = (old_fragment, new_number)
                self.files_processed += 1
                
        except Exception as e:
            print(f"[EpubCleaner] Error fixing mismatch in {target_file}: {e}")
    
    def _apply_global_fixes(self, all_files_content: Dict[str, bytes]) -> None:
        """Apply global fixes like link updates and attribute removal."""
        if not BS4_AVAILABLE:
            BeautifulSoup = None
        
        for filename, content_bytes in all_files_content.items():
            modified_content = content_bytes
            
            is_html = filename.lower().endswith(('.html', '.xhtml', '.htm'))
            is_nav = (filename.lower().endswith(('.ncx', 'nav.xhtml', 'toc.html')) 
                     or 'toc' in filename.lower())
            
            if is_html or is_nav:
                try:
                    content_str = modified_content.decode('utf-8', errors='ignore')
                    original_str = content_str
                    
                    # 1. Update links for renamed chapters
                    if self.global_link_updates:
                        content_str = self._update_links(content_str, filename)
                    
                    # 2. Apply other tasks
                    for task in self.tasks:
                        if task['type'] == 'br' and '<br' in content_str.lower():
                            from .text import unify_paragraphs_for_ai
                            content_str = unify_paragraphs_for_ai(content_str)
                        
                        elif task['type'] == 'attr':
                            def remove_attr(match, t_re=task['attr_re']):
                                return t_re.sub('', match.group(1))
                            content_str = task['tag_re'].sub(remove_attr, content_str)
                        
                        elif task['type'] == 'orphans' and BS4_AVAILABLE and BeautifulSoup:
                            content_str = self._fix_orphans(content_str)
                    
                    if content_str != original_str:
                        modified_content = content_str.encode('utf-8')
                        if filename not in self.global_link_updates:
                            self.files_processed += 1
                            
                except Exception as e:
                    print(f"[EpubCleaner] Error processing {filename}: {e}")
            
            all_files_content[filename] = modified_content
    
    def _update_links(self, content_str: str, filename: str) -> str:
        """Update links to renamed chapters."""
        for target_file, (old_txt, new_txt) in self.global_link_updates.items():
            target_basename = os.path.basename(target_file)
            
            if target_basename not in content_str:
                continue
            
            esc_old = re.escape(old_txt)
            
            # A. HTML links
            pattern_a = re.compile(
                fr'(<a\b[^>]*href=["\'][^"\']*{re.escape(target_basename)}[^"\']*["\'][^>]*>)(.*?{esc_old}.*?)(</a>)',
                re.IGNORECASE | re.DOTALL
            )
            content_str = pattern_a.sub(
                lambda m: f"{m.group(1)}{m.group(2).replace(old_txt, new_txt)}{m.group(3)}",
                content_str
            )
            
            # B. NCX (Table of Contents)
            if filename.lower().endswith('.ncx'):
                content_str = content_str.replace(
                    f"<text>{old_txt}</text>", 
                    f"<text>{new_txt}</text>"
                )
        
        return content_str
    
    def _fix_orphans(self, content_str: str) -> str:
        """Fix orphaned text by wrapping in paragraph tags."""
        if not BS4_AVAILABLE or not BeautifulSoup:
            return content_str
        
        # Class-level constant for block tags
        BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote',
                      'hr', 'ul', 'ol', 'table', 'script', 'style', 'head', 'title',
                      'meta', 'link', 'br'}
        
        try:
            soup = BeautifulSoup(content_str, 'html.parser')
            
            if not soup.body:
                return content_str
            
            new_contents = []
            buffer_text = []
            
            def flush_buffer():
                if buffer_text:
                    new_p = soup.new_tag('p')
                    for buf_item in buffer_text:
                        new_p.append(buf_item)
                    new_contents.append(new_p)
                    buffer_text.clear()
            
            children = list(soup.body.children)
            
            for child in children:
                is_block = (hasattr(child, 'name') and child.name in BLOCK_TAGS)
                is_whitespace = (isinstance(child, NavigableString) and not child.strip())
                
                if is_block:
                    flush_buffer()
                    new_contents.append(child)
                elif is_whitespace and not buffer_text:
                    new_contents.append(child)
                else:
                    buffer_text.append(child)
            
            flush_buffer()
            soup.body.clear()
            
            for item_node in new_contents:
                soup.body.append(item_node)
            
            return str(soup)
            
        except Exception as e:
            print(f"[EpubCleaner] Error fixing orphans: {e}")
            return content_str


# Convenience function for simple cleanup
def clean_epub(epub_path: str, fixes: List[Dict[str, Any]]) -> int:
    """
    Convenience function to clean an EPUB file.
    
    Args:
        epub_path: Path to the EPUB file.
        fixes: List of fix dictionaries to apply.
        
    Returns:
        Number of files processed/modified.
    """
    cleaner = EpubCleaner(epub_path)
    return cleaner.apply_fixes(fixes)
