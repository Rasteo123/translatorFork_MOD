"""The keys one quality check may spend, and the order it spends them in."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
import threading
import time

# How many refusals in a row a key takes before it is rested and replaced.  The
# translation gives a key a second yellow card before dismissing it: one 429
# is a busy minute, not a bad key, and the worker waits on the same key.
STRIKES_BEFORE_SWITCH = 2
# How many distinct keys may be rested within one window before the whole pool
# rests.  Three keys refused inside a minute means the limit is shared, and a
# fourth key only adds to the pile the service is already refusing.
STORM_KEYS = 3
STORM_WINDOW_SECONDS = 60.0
# The most requests the pool hands out per minute, whatever the keys allow.
# Measured on a live book: the check asked all 150 keys within two minutes
# when the service throttled everyone; an hour later the accounts behind the
# keys were disabled.
DEFAULT_MAX_REQUESTS_PER_MINUTE = 20


class QaKeyPool:
    """Spend the session's keys for QA the way the translation workers do.

    One key at a time, kept until the service turns it away.  The translation
    takes keys from the front of the list, so QA starts from the back: the two
    meet late, and a key one of them spent today is not the first thing the
    other reaches for.  A key the service calls exhausted is dropped for good
    and reported to the settings, so the workers skip it too.  A key the
    service calls busy is asked again after the pause it named; only a second
    refusal in a row rests it and moves on.  A key a worker is using right now
    is taken only when nothing else is left.  The pool as a whole never hands
    out more than its minute budget, and rests entirely when several keys are
    refused within a minute, because then the limit is shared and switching
    keys is what gets accounts disabled.
    """

    def __init__(
        self,
        keys: Iterable[str],
        *,
        model_id: str = "",
        settings_manager=None,
        busy: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_requests_per_minute: int = DEFAULT_MAX_REQUESTS_PER_MINUTE,
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
        try:
            self._per_minute = max(0, int(max_requests_per_minute))
        except (TypeError, ValueError):
            self._per_minute = DEFAULT_MAX_REQUESTS_PER_MINUTE
        self._exhausted: set[str] = set()
        self._paused_until: dict[str, float] = {}
        self._strikes: dict[str, int] = {}
        self._rested_at: dict[str, float] = {}
        self._cooldown_until = 0.0
        self._handed_out: deque[float] = deque()
        self._current: str | None = None
        self._blocked_reason: str | None = None
        self._cursor = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._keys)

    def acquire(self) -> str | None:
        """Return the key to ask next, or None when nothing may be asked now.

        The same key comes back as long as it is usable: switching keys is the
        exception, not the rhythm.
        """
        with self._lock:
            if self._blocked_reason is not None:
                return None
            now = self._clock()
            if now < self._cooldown_until:
                return None
            self._forget_old_handouts(now)
            if self._per_minute and len(self._handed_out) >= self._per_minute:
                return None
            current = self._current
            if current is not None and self._locally_limited(current):
                self._strikes.pop(current, None)
                self._current = current = None
            if current is not None and self._is_ready(current, now):
                self._handed_out.append(now)
                return current
            if current is not None and self._strikes.get(current):
                # The first refusal waits on this key. Other callers must
                # neither use it early nor turn that wait into key rotation.
                return None
            ready = [key for key in self._rotation() if self._is_ready(key, now)]
            if not ready:
                self._current = None
                return None
            key = next((item for item in ready if not self._is_busy(item)), ready[0])
            self._current = key
            self._advance_past(key)
            self._handed_out.append(now)
            return key

    def note_success(self, key: str) -> None:
        """A key that answered has proven itself; its yellow cards are torn up."""
        with self._lock:
            # A request already in flight before another request's 429 is not
            # evidence that the new pause may be cancelled. A post-deadline
            # acquisition removes the pause before a fresh success clears it.
            if key not in self._paused_until:
                self._strikes.pop(key, None)

    def note_throttled(self, key: str, seconds: float) -> bool:
        """Record one "try later" from the service.

        Returns False when the caller should wait the named pause and ask the
        same key again, True when the key has been rested and the caller should
        take the next one.
        """
        with self._lock:
            if key not in self._keys or key in self._exhausted:
                return True
            strikes = self._strikes.get(key, 0) + 1
            if strikes < STRIKES_BEFORE_SWITCH:
                self._strikes[key] = strikes
                self._paused_until[key] = max(
                    self._paused_until.get(key, 0.0), self._clock() + seconds
                )
                return False
            self._strikes.pop(key, None)
            self._pause_locked(key, seconds, self._clock())
            return True

    def mark_exhausted(self, key: str) -> None:
        """Drop a key for the rest of the pool's life and tell the settings."""
        with self._lock:
            if key not in self._keys:
                return
            self._exhausted.add(key)
            self._paused_until.pop(key, None)
            self._strikes.pop(key, None)
            if self._current == key:
                self._current = None
        marker = getattr(self._settings, "mark_key_as_exhausted", None)
        if callable(marker) and self._model_id:
            try:
                marker(key, self._model_id)
            except Exception:  # noqa: BLE001 - the settings are not the pool's to break
                return

    def seconds_until_available(self) -> float | None:
        """How long until some key may be asked, or None when none ever will be."""
        with self._lock:
            if self._blocked_reason is not None:
                return None
            now = self._clock()
            if now < self._cooldown_until:
                return self._cooldown_until - now
            self._forget_old_handouts(now)
            if self._per_minute and len(self._handed_out) >= self._per_minute:
                return max(0.0, STORM_WINDOW_SECONDS - (now - self._handed_out[0]))
            if (
                self._current is not None
                and self._strikes.get(self._current)
                and not self._locally_limited(self._current)
            ):
                until = self._paused_until.get(self._current, now)
                return max(0.0, until - now)
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

    @property
    def blocked_reason(self) -> str | None:
        with self._lock:
            return self._blocked_reason

    def block(self, reason: str) -> None:
        """Stop subsequent QA calls after an access refusal, without rotating."""
        with self._lock:
            self._blocked_reason = reason

    # -- internals ---------------------------------------------------------

    def _pause_locked(self, key: str, seconds: float, now: float) -> None:
        try:
            delay = max(0.0, float(seconds))
        except (TypeError, ValueError):
            delay = 0.0
        if key not in self._keys or key in self._exhausted:
            return
        self._paused_until[key] = max(self._paused_until.get(key, 0.0), now + delay)
        self._rested_at[key] = now
        if self._current == key:
            self._current = None
        recent = [
            item
            for item, rested in self._rested_at.items()
            if now - rested <= STORM_WINDOW_SECONDS
        ]
        if len(recent) >= STORM_KEYS:
            self._cooldown_until = max(
                self._cooldown_until, now + max(STORM_WINDOW_SECONDS, delay)
            )
            self._rested_at.clear()

    def _forget_old_handouts(self, now: float) -> None:
        while self._handed_out and now - self._handed_out[0] >= STORM_WINDOW_SECONDS:
            self._handed_out.popleft()

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
