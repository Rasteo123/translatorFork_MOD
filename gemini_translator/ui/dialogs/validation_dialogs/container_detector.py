# -*- coding: utf-8 -*-
"""
Module for detecting container types in HTML content.

This module provides classes for determining how text nodes should be handled
when fixing untranslated words. It classifies containers as:
1. Safe blocks - can use entire container content
2. Dangerous roots - should only use the text node itself (orphan mode)
3. Inline tags - should lift up to parent container
"""

from typing import Tuple, Set, Optional
from bs4 import Tag, NavigableString


# =============================================================================
# Container Type Definitions
# =============================================================================

class ContainerTypes:
    """Defines sets of HTML tags for container classification."""
    
    # Inline tags that should be "lifted through" to find the real container
    INLINE_TAGS: Set[str] = {
        'span', 'a', 'strong', 'em', 'b', 'i', 'u', 'font',
        'small', 'big', 'sub', 'sup', 'strike', 'code', 'var', 'cite',
        'abbr', 'acronym', 'address', 'bdi', 'bdo', 'dfn', 'kbd',
        'mark', 'q', 'rp', 'rt', 'ruby', 's', 'samp', 'time', 'wbr'
    }
    
    # Safe block-level containers where we can use the entire content
    SAFE_BLOCKS: Set[str] = {
        'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'li', 'dt', 'dd', 'blockquote', 'pre', 'caption', 
        'figcaption', 'td', 'th', 'label',
        # Form elements
        'option', 'legend', 'summary'
    }
    
    # Dangerous root containers - use orphan mode for these
    DANGEROUS_ROOTS: Set[str] = {
        'body', 'html', 'main', '[document]',
        'head', 'div', 'section', 'article', 'aside', 'nav',
        'header', 'footer', 'figure', 'details', 'dialog'
    }
    
    # Block-level elements that indicate complex structure
    COMPLEX_BLOCKS: Set[str] = {
        'div', 'section', 'article', 'table', 'ul', 'ol', 'dl',
        'form', 'fieldset', 'menu', 'figure', 'details'
    }


# =============================================================================
# Container Detector
# =============================================================================

class ContainerDetector:
    """
    Detects and classifies HTML containers for text processing.
    
    Determines whether a text node should be processed as:
    1. Part of a safe block (use entire container content)
    2. An orphan (use only the text node itself)
    """
    
    def __init__(self):
        """Initialize the container detector."""
        self.inline_tags = ContainerTypes.INLINE_TAGS
        self.safe_blocks = ContainerTypes.SAFE_BLOCKS
        self.dangerous_roots = ContainerTypes.DANGEROUS_ROOTS
        self.complex_blocks = ContainerTypes.COMPLEX_BLOCKS
    
    def get_effective_container(self, node: NavigableString) -> Optional[Tag]:
        """
        Find the effective container by lifting up through inline tags.
        
        Args:
            node: The text node to find the container for
            
        Returns:
            The effective container tag, or None if not found
        """
        if not hasattr(node, 'parent') or node.parent is None:
            return None
        
        container = node.parent
        
        # Lift up through inline tags
        while container and container.name in self.inline_tags:
            if container.parent:
                container = container.parent
            else:
                break
        
        return container
    
    def classify_container(self, container: Tag) -> Tuple[str, bool]:
        """
        Classify a container and determine if orphan mode should be used.
        
        Args:
            container: The HTML tag to classify
            
        Returns:
            Tuple of (location_description, is_orphan_flag)
        """
        if container is None:
            return "Неизвестный контейнер", True
        
        name = container.name
        
        # Check for dangerous roots first
        if name in self.dangerous_roots:
            return f"Текст-сирота (в <{name}>)", True
        
        # Check for safe blocks
        if name in self.safe_blocks:
            return f"Тег <{name}>", False
        
        # For other containers, check their children
        has_block_children = self._has_block_children(container)
        
        if has_block_children:
            # Container has block children, treat content as orphan
            return f"Текст-сирота (в <{name}>)", True
        
        # Default to safe block treatment
        return f"Тег <{name}>", False
    
    def _has_block_children(self, container: Tag) -> bool:
        """
        Check if a container has block-level children.
        
        Args:
            container: The container tag to check
            
        Returns:
            True if container has block-level children
        """
        block_tags = self.safe_blocks.union(self.complex_blocks)
        
        for child in container.children:
            if hasattr(child, 'name') and child.name in block_tags:
                return True
        
        return False
    
    def should_use_orphan_mode(self, node: NavigableString) -> bool:
        """
        Determine if a text node should be processed in orphan mode.
        
        Args:
            node: The text node to check
            
        Returns:
            True if orphan mode should be used
        """
        container = self.get_effective_container(node)
        
        if container is None:
            return True
        
        _, is_orphan = self.classify_container(container)
        return is_orphan
    
    def get_container_description(self, container: Tag) -> str:
        """
        Get a human-readable description of a container.
        
        Args:
            container: The container tag
            
        Returns:
            Human-readable description string
        """
        if container is None:
            return "Неизвестный контейнер"
        
        name = container.name
        
        if name in self.dangerous_roots:
            return f"Текст-сирота (в <{name}>)"
        
        if name in self.safe_blocks:
            return f"Тег <{name}>"
        
        return f"Тег <{name}>"


# =============================================================================
# Context Treatment Decider
# =============================================================================

class ContextTreatmentDecider:
    """
    Determines how a found text node should be treated.
    
    This is a higher-level class that combines container detection
    with context extraction logic.
    """
    
    MAX_CONTEXT_LENGTH = 2000
    
    def __init__(self):
        """Initialize the treatment decider."""
        self.detector = ContainerDetector()
    
    def decide(self, node: NavigableString) -> Tuple[str, bool, str]:
        """
        Decide treatment for a text node.
        
        Args:
            node: The text node to process
            
        Returns:
            Tuple of (context_text, is_orphan_mode, location_description)
        """
        container = self.detector.get_effective_container(node)
        
        if container is None:
            return str(node).strip(), True, "Текст без контейнера"
        
        # Get classification
        location_desc, is_orphan = self.detector.classify_container(container)
        
        if is_orphan:
            # Orphan mode: use only the text node itself
            context = str(node).strip()
        else:
            # Block mode: use entire container content
            context = "".join(str(child) for child in container.contents).strip()
        
        # Check for overly long context
        if len(context) > self.MAX_CONTEXT_LENGTH:
            # Fall back to orphan mode with truncation
            context = str(node).strip()
            if len(context) > 100:
                context = context[:100] + "..."
            location_desc = f"Текст-сирота (Слишком большой блок <{container.name}>)"
            is_orphan = True
        
        return context, is_orphan, location_desc
    
    def extract_context(self, node: NavigableString) -> str:
        """
        Extract just the context string from a node.
        
        Args:
            node: The text node
            
        Returns:
            Context string for the node
        """
        context, _, _ = self.decide(node)
        return context
    
    def is_orphan(self, node: NavigableString) -> bool:
        """
        Check if a node should be treated as an orphan.
        
        Args:
            node: The text node
            
        Returns:
            True if orphan mode should be used
        """
        _, is_orphan, _ = self.decide(node)
        return is_orphan
