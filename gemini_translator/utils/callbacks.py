"""Shared idiom: safely invoke an optional callback without raising.

Deduplication note (pcluster-22): "call this progress/log callback if it is
callable, and never let its own failure propagate" was implemented as a
separate function under a different name and signature at least four
times — ``chapter_qa_coordinator.check_all_now``'s ``report_progress``
closure (forwarding ``(done, total, chapter_id)`` captured from the
enclosing scope), that same method's ``report_chapter`` closure (forwarding
``(result,)``, with its own extra guard for ``result is None`` kept
in the closure itself since ``safe_call`` has no opinion on that),
``qa.model_bundle._report`` (explicit ``(progress, name, done, total)``),
and ``qa.assembly._report`` (explicit ``(log, message)``). All four share
the exact same core: no-op if the callback is not callable, call it,
swallow any exception it raises. This module holds that shared core as
:func:`safe_call`; each caller keeps its own argument shape by passing
whatever positional arguments its callback expects.

Deliberately excluded from this cluster:
``chapter_qa_coordinator.ChapterQaCoordinator._report`` is a different
function that happens to share the name ``_report`` with the ``qa`` module
copies above — it logs a message with an optional expandable details block
through ``self._log`` and has its own dedicated ``TypeError`` fallback (for
loggers that do not accept the details keywords). It is not a progress/log
callback wrapper and is not part of this idiom.
"""

from __future__ import annotations

from typing import Any, Callable


def safe_call(callback: Callable[..., Any] | None, *args: Any) -> None:
    """Call ``callback(*args)`` if ``callback`` is callable.

    No-op when ``callback`` is not callable. Any exception raised by the
    callback itself is swallowed: a progress display or logger must never
    fail the operation it is merely reporting on.
    """
    if not callable(callback):
        return
    try:
        callback(*args)
    except Exception:  # noqa: BLE001 - a display/logger must never fail the caller
        return
