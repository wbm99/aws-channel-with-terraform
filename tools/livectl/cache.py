"""Remember slow or billed lookups for a short time, so a page that polls every two seconds stays cheap.

The browser asks for the whole pipeline every two seconds. AWS state changes on the order of seconds and
CloudWatch metrics once a minute, so each source is cached for its own interval instead.

It also has to survive losing the network: only one thread loads a key at a time (the others wait and reuse its
result instead of stacking up blocked AWS calls), a failed refresh can fall back to the last good value, and a
failure is not retried until the TTL has passed.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable, Optional


class TtlCache:
    """A dict of values that expire. A load that raises is not stored."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[Hashable, tuple[float, Any]] = {}
        self._failures: dict[Hashable, tuple[float, str]] = {}
        self._key_locks: dict[Hashable, threading.Lock] = {}

    def _key_lock(self, key: Hashable) -> threading.Lock:
        with self._lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def get(self, key: Hashable, ttl: float, load: Callable[[], Any], *, stale_for: float = 0.0) -> Any:
        """The cached value if younger than ttl, else a fresh load.

        If the load fails and the last good value is younger than stale_for, that value is returned and the failure
        is kept (see `problems`) and not retried until ttl has passed. Otherwise the error propagates.
        """
        with self._key_lock(key):  # one loader per key; the others wait here and then find a fresh value
            now = self._clock()
            with self._lock:
                hit = self._items.get(key)
                failure = self._failures.get(key)
            if hit is not None and now - hit[0] < ttl:
                return hit[1]
            usable = hit is not None and now - hit[0] < stale_for
            if usable and failure is not None and now - failure[0] < ttl:
                return hit[1]
            try:
                value = load()
            except Exception as error:
                if not usable:
                    raise
                with self._lock:
                    self._failures[key] = (now, f"{type(error).__name__}: {error}")
                return hit[1]
            with self._lock:
                self._items[key] = (now, value)
                self._failures.pop(key, None)
            return value

    def problems(self) -> dict[Hashable, tuple[str, float]]:
        """Keys currently served from an older value: {key: (error, age of the value served in seconds)}."""
        now = self._clock()
        with self._lock:
            return {key: (error, now - self._items[key][0]) for key, (_, error) in self._failures.items()
                    if key in self._items}

    def age(self, key: Hashable) -> Optional[float]:
        with self._lock:
            hit = self._items.get(key)
        return None if hit is None else self._clock() - hit[0]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._failures.clear()
