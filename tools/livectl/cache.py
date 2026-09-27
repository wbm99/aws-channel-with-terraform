"""Remember slow or billed lookups for a short time, so a page that polls every two seconds stays cheap.

The browser asks for the whole pipeline every two seconds. AWS state changes on the order of seconds and
CloudWatch metrics once a minute, so each source is cached for its own interval instead.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable, Optional


class TtlCache:
    """A dict of values that expire. A load that raises is not stored, so the next call tries again."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[Hashable, tuple[float, Any]] = {}

    def get(self, key: Hashable, ttl: float, load: Callable[[], Any]) -> Any:
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        # Loading outside the lock: two threads may both load once, which is cheaper than serialising AWS calls.
        value = load()
        with self._lock:
            self._items[key] = (now, value)
        return value

    def age(self, key: Hashable) -> Optional[float]:
        with self._lock:
            hit = self._items.get(key)
        return None if hit is None else self._clock() - hit[0]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
