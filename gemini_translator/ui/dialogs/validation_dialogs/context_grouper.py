# -*- coding: utf-8 -*-
"""
Module for grouping untranslated word occurrences by context.

This module provides classes for:
1. Grouping identical contexts together for batch processing
2. Tracking occurrence locations for each unique context
3. Managing the relationship between contexts and their source documents
"""

import re
from typing import Dict, List, Set, Any, Optional
from bs4 import BeautifulSoup, NavigableString, ProcessingInstruction, Comment, Declaration

from .container_detector import ContainerDetector, ContextTreatmentDecider


# =============================================================================
# Occurrence Data Classes
# =============================================================================

class Occurrence:
    """Represents a single occurrence of an untranslated word."""
    
    __slots__ = ['target', 'is_orphan', 'row_index', 'soup_ref']
    
    def __init__(self, target: Any, is_orphan: bool, 
                 row_index: int, soup_ref: BeautifulSoup):
        """
        Initialize an occurrence record.
        
        Args:
            target: The HTML element or text node to modify
            is_orphan: Whether this is treated as an orphan node
            row_index: Index of the row in the validation table
            soup_ref: Reference to the BeautifulSoup object
        """
        self.target = target
        self.is_orphan = is_orphan
        self.row_index = row_index
        self.soup_ref = soup_ref


class ContextGroup:
    """Represents a group of occurrences sharing the same context."""
    
    __slots__ = ['term', 'context', 'location_info', 'occurrences']
    
    def __init__(self, term: str, context: str, location_info: str):
        """
        Initialize a context group.
        
        Args:
            term: The untranslated word that was found
            context: The HTML context containing the word
            location_info: Human-readable location description
        """
        self.term = term
        self.context = context
        self.location_info = location_info
        self.occurrences: List[Occurrence] = []
    
    def add_occurrence(self, occurrence: Occurrence):
        """Add an occurrence to this group."""
        self.occurrences.append(occurrence)
    
    @property
    def occurrence_count(self) -> int:
        """Get the number of occurrences in this group."""
        return len(self.occurrences)


# =============================================================================
# Context Grouper
# =============================================================================

class ContextGrouper:
    """
    Groups untranslated word occurrences by their context.
    
    This allows batch processing of identical contexts, reducing
    the number of AI API calls needed for fixes.
    """
    
    MAX_CONTEXT_LENGTH = 2000
    
    def __init__(self):
        """Initialize the context grouper."""
        self.treatment_decider = ContextTreatmentDecider()
    
    def group_occurrences(self, results_data: Dict[int, Dict]) -> Dict[str, ContextGroup]:
        """
        Group all untranslated word occurrences by context.
        
        Args:
            results_data: Dictionary mapping row indices to result data
            
        Returns:
            Dictionary mapping context strings to ContextGroup objects
        """
        grouped_map: Dict[str, ContextGroup] = {}
        processed_ids: Set[int] = set()
        soup_cache: Dict[int, BeautifulSoup] = {}
        
        for row_index, result_data in results_data.items():
            if 'untranslated_words' not in result_data:
                continue
            
            # Get or create soup for this row
            if row_index not in soup_cache:
                html_content = result_data.get('translated_html', '')
                soup_cache[row_index] = BeautifulSoup(html_content, 'html.parser')
            
            soup = soup_cache[row_index]
            
            # Process each untranslated term
            for term in result_data['untranslated_words']:
                self._process_term(
                    soup=soup,
                    term=term,
                    row_index=row_index,
                    grouped_map=grouped_map,
                    processed_ids=processed_ids
                )
        
        return grouped_map
    
    def _process_term(self, soup: BeautifulSoup, term: str, 
                      row_index: int, grouped_map: Dict[str, ContextGroup],
                      processed_ids: Set[int]):
        """
        Process a single term and add occurrences to the grouped map.
        
        Args:
            soup: BeautifulSoup object for the HTML content
            term: The untranslated term to find
            row_index: Index of the row in the validation table
            grouped_map: Dictionary to store grouped results
            processed_ids: Set of already processed node IDs
        """
        # Find all text nodes containing this term
        try:
            term_pattern = re.compile(re.escape(term), re.IGNORECASE)
            text_nodes = soup.find_all(string=term_pattern)
        except re.error:
            # Invalid regex, skip this term
            return
        
        for node in text_nodes:
            # Skip non-visible nodes
            if not self._is_visible_node(node):
                continue
            
            # Skip already processed nodes
            unique_id = id(node)
            if unique_id in processed_ids:
                continue
            processed_ids.add(unique_id)
            
            # Get context and treatment decision
            context, is_orphan, location_desc = self.treatment_decider.decide(node)
            
            if not context:
                continue
            
            # Determine the target element
            if is_orphan:
                target = node
            else:
                container = self.treatment_decider.detector.get_effective_container(node)
                target = container if container else node
            
            # Create or get existing group
            group_key = context
            
            if group_key not in grouped_map:
                grouped_map[group_key] = ContextGroup(
                    term=term,
                    context=context,
                    location_info=location_desc
                )
            
            # Add occurrence to group
            grouped_map[group_key].add_occurrence(Occurrence(
                target=target,
                is_orphan=is_orphan,
                row_index=row_index,
                soup_ref=soup
            ))
    
    def _is_visible_node(self, node: NavigableString) -> bool:
        """
        Check if a text node is visible (not in hidden elements).
        
        Args:
            node: The text node to check
            
        Returns:
            True if the node is visible
        """
        # Skip non-text nodes
        if isinstance(node, (ProcessingInstruction, Comment, Declaration)):
            return False
        
        # Skip nodes in head, script, style, title
        if node.find_parent(['head', 'script', 'style', 'title']):
            return False
        
        return True
    
    def get_groups_for_row(self, results_data: Dict[int, Dict], 
                           row_index: int) -> Dict[str, ContextGroup]:
        """
        Get context groups for a single row.
        
        Args:
            results_data: Dictionary mapping row indices to result data
            row_index: The specific row index to process
            
        Returns:
            Dictionary of context groups for this row only
        """
        if row_index not in results_data:
            return {}
        
        result_data = results_data[row_index]
        
        if 'untranslated_words' not in result_data:
            return {}
        
        # Create soup for this row
        html_content = result_data.get('translated_html', '')
        soup = BeautifulSoup(html_content, 'html.parser')
        
        grouped_map: Dict[str, ContextGroup] = {}
        processed_ids: Set[int] = set()
        
        for term in result_data['untranslated_words']:
            self._process_term(
                soup=soup,
                term=term,
                row_index=row_index,
                grouped_map=grouped_map,
                processed_ids=processed_ids
            )
        
        return grouped_map
    
    def count_total_occurrences(self, grouped_data: Dict[str, ContextGroup]) -> int:
        """
        Count total occurrences across all groups.
        
        Args:
            grouped_data: Dictionary of context groups
            
        Returns:
            Total number of occurrences
        """
        return sum(group.occurrence_count for group in grouped_data.values())
    
    def get_unique_terms(self, grouped_data: Dict[str, ContextGroup]) -> Set[str]:
        """
        Get all unique terms from the grouped data.
        
        Args:
            grouped_data: Dictionary of context groups
            
        Returns:
            Set of unique untranslated terms
        """
        return {group.term for group in grouped_data.values()}


# =============================================================================
# Grouped Data Converter
# =============================================================================

class GroupedDataConverter:
    """
    Converts between grouped data format and the format expected by
    UntranslatedFixerDialog.
    """
    
    @staticmethod
    def to_dialog_format(grouped_data: Dict[str, ContextGroup]) -> List[Dict]:
        """
        Convert grouped data to dialog format.
        
        Args:
            grouped_data: Dictionary of context groups
            
        Returns:
            List of dictionaries in the format expected by UntranslatedFixerDialog
        """
        result = []
        
        for context, group in grouped_data.items():
            result.append({
                'term': group.term,
                'context': group.context,
                'location_info': group.location_info,
                'occurrences': [
                    {
                        'target': occ.target,
                        'is_orphan': occ.is_orphan,
                        'row_index': occ.row_index,
                        'soup_ref': occ.soup_ref
                    }
                    for occ in group.occurrences
                ]
            })
        
        return result
    
    @staticmethod
    def from_dialog_format(dialog_data: List[Dict]) -> Dict[str, ContextGroup]:
        """
        Convert dialog format back to grouped data.
        
        Args:
            dialog_data: List of dictionaries from UntranslatedFixerDialog
            
        Returns:
            Dictionary of context groups
        """
        result = {}
        
        for item in dialog_data:
            group = ContextGroup(
                term=item['term'],
                context=item['context'],
                location_info=item['location_info']
            )
            
            for occ_data in item.get('occurrences', []):
                group.add_occurrence(Occurrence(
                    target=occ_data['target'],
                    is_orphan=occ_data['is_orphan'],
                    row_index=occ_data['row_index'],
                    soup_ref=occ_data['soup_ref']
                ))
            
            result[item['context']] = group
        
        return result
