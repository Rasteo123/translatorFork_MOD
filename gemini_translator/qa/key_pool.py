"""The keys one quality check may spend, and the order it spends them in."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import threading
import time


class QaKeyPool:
    """Rotate the session's keys for QA the way the workers rotate them.

    Measured on a live book: the check was pinned to the first session key,
    the same one the first worker takes.  The model allows twenty requests a
    day per key, so the key was gone within a minute and every later request
    failed on the spot, for the rest of the night.

    The translation takes keys from the front of the list, so QA starts from
    the back: the two meet late, and a key one of them spent today is not the
    first thing the other reaches for.  A key the service calls exhausted is
    dropped for good and reported to the settings, so the workers skip it too.
    A key the service calls busy rests for exactly as long as it asked.  A key
    a worker is using right now is taken only when nothing else is left,
    because the two would then share its few requests a minute.
    """

    def __init__(
        self,
        keys: Iterable[str],
        *,
        model_id: str = "",
        settings_manager=None,
        busy: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        ordered: list[str] = []
        for key in keys or ():
            if not isinstance(key, str):
                continue
            text = key.strip()
            if text and text not in ordered:
                ordered.append(text)
        self._keys: tuple[str, ...] = tuple(reversed(ordered))
        self._model_id = str(model_id or "")
        self._settings = settings_manager
        self._busy = busy if callable(busy) else None
        self._clock = clock
        self._exhausted: set[str] = set()
        self._paused_until: dict[str, float] = {}
        self._cursor = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    @property
    def remaining(self) -> int:
        """How many keys the service has not yet declared spent."""
        with self._lock:
            return sum(1 for key in self._keys if key not in self._exhausted)

    def acquire(self) -> str | None:
        """Return the next key worth trying, or None when nothing is ready now."""
        with self._lock:
            now = self._clock()
            ready = [key for key in self._rotation() if self._is_ready(key, now)]
            if not ready:
                return None
            for key in ready:
                if not self._is_busy(key):
                    self._advance_past(key)
                    return key
            key = ready[0]
            self._advance_past(key)
            return key

    def mark_exhausted(self, key: str) -> None:
        """Drop a key for the rest of the pool's life and tell the settings."""
        with self._lock:
            if key not in self._keys:
                return
            self._exhausted.add(key)
            self._paused_until.pop(key, None)
        marker = getattr(self._settings, "mark_key_as_exhausted", None)
        if callable(marker) and self._model_id:
            try:
                marker(key, self._model_id)
            except Exception:  # noqa: BLE001 - the settings are not the pool's to break
                return

    def pause(self, key: str, seconds: float) -> None:
        """Rest a key for as long as the service asked, and no longer."""
        try:
            delay = max(0.0, float(seconds))
        except (TypeError, ValueError):
            delay = 0.0
        with self._lock:
            if key in self._keys and key not in self._exhausted:
                self._paused_until[key] = self._clock() + delay

    def seconds_until_available(self) -> float | None:
        """How long until some key is ready, or None when none ever will be."""
        with self._lock:
            now = self._clock()
            soonest: float | None = None
            for key in self._keys:
                if key in self._exhausted or self._locally_limited(key):
                    continue
                until = self._paused_until.get(key)
                if until is None or until <= now:
                    return 0.0
                wait = until - now
                soonest = wait if soonest is None else min(soonest, wait)
            return soonest

    # -- internals ---------------------------------------------------------

    def _rotation(self) -> tuple[str, ...]:
        if not self._keys:
            return ()
        cursor = self._cursor % len(self._keys)
        return self._keys[cursor:] + self._keys[:cursor]

    def _advance_past(self, key: str) -> None:
        self._cursor = (self._keys.index(key) + 1) % len(self._keys)

    def _is_ready(self, key: str, now: float) -> bool:
        if key in self._exhausted:
            return False
        until = self._paused_until.get(key)
        if until is not None:
            if until > now:
                return False
            del self._paused_until[key]
        return not self._locally_limited(key)

    def _locally_limited(self, key: str) -> bool:
        """Whether the settings already count this key as spent for the model."""
        settings = self._settings
        if settings is None or not self._model_id:
            return False
        try:
            info = settings.get_key_info(key)
            return bool(info) and bool(settings.is_key_limit_active(info, self._model_id))
        except Exception:  # noqa: BLE001 - an unreadable status is not a red key
            return False

    def _is_busy(self, key: str) -> bool:
        if self._busy is None:
            return False
        try:
            return bool(self._busy(key))
        except Exception:  # noqa: BLE001 - a broken probe means "not busy"
            return False
