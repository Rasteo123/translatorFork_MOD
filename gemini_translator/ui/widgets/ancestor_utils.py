"""Locate an ancestor widget by class name (handles old wrapper names and new
shell-page names of the renamed session/glossary windows)."""
from __future__ import annotations


def find_ancestor_by_class_name(widget, *class_names):
    """Return the nearest ancestor (starting at ``widget.parent()``) whose
    ``__class__.__name__`` is in ``class_names``, else ``None``."""
    names = set(class_names)
    node = widget.parent() if widget is not None else None
    while node is not None and node.__class__.__name__ not in names:
        node = node.parent()
    return node
