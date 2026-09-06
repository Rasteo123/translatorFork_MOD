"""Locate an ancestor widget by class name (handles old wrapper names and new
shell-page names of the renamed session/glossary windows), or by an arbitrary
predicate for the duck-typed "find the ancestor that carries this attribute"
searches scattered across the dialog modules."""
from __future__ import annotations


def find_ancestor_by_predicate(node, predicate, max_depth=None):
    """Walk ``node``, then ``node.parent()``, ``node.parent().parent()``, ...
    and return the first one for which ``predicate(candidate)`` is truthy,
    else ``None``.

    ``node`` itself is the first candidate examined (callers that want to
    skip the starting widget pass ``widget.parent()``). ``max_depth`` caps
    the number of candidates examined (``None`` means no limit). Guards
    against parent cycles with a visited-id set.
    """
    visited = set()
    remaining = max_depth
    current = node
    while current is not None and id(current) not in visited:
        if remaining is not None and remaining <= 0:
            break
        visited.add(id(current))
        if predicate(current):
            return current
        if remaining is not None:
            remaining -= 1
        parent_getter = getattr(current, "parent", None)
        current = parent_getter() if callable(parent_getter) else None
    return None


def find_ancestor_by_class_name(widget, *class_names):
    """Return the nearest ancestor (starting at ``widget.parent()``) whose
    ``__class__.__name__`` is in ``class_names``, else ``None``."""
    names = set(class_names)
    start = widget.parent() if widget is not None else None
    return find_ancestor_by_predicate(start, lambda node: node.__class__.__name__ in names)
