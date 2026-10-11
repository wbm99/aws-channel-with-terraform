"""Follow a cued ad break from the console's button to the HLS playlist.

A cue is sent to MediaLive; whether it worked shows only when MediaPackage writes the break into the playlist as an
EXT-X-DATERANGE, some seconds later. The IDs already in the playlist when the cue was sent are kept as a baseline,
and the first ID that is not among them is taken as this cue's marker. That makes no assumption about how
MediaPackage forms the ID from the SCTE-35 event.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from livectl.manifest import ManifestCheck

NOT_SEEN_AFTER_S = 60


@dataclass(frozen=True)
class Cue:
    event_id: int
    seconds: float
    cued_at_ms: int     # wall clock, for the page
    cued_at: float      # monotonic, for timing
    baseline: frozenset[str]


def _ids(check: Optional[ManifestCheck]) -> frozenset[str]:
    return frozenset(b.id for b in check.breaks) if check else frozenset()


class AdBreaks:
    """The last cue and whether its marker has reached the playlist. Only one break runs at a time."""

    def __init__(self, clock: Callable[[], float], clock_ms: Callable[[], int]) -> None:
        self._clock, self._clock_ms = clock, clock_ms
        self._lock = threading.Lock()
        self._cue: Optional[Cue] = None
        self._seen_at_ms: Optional[int] = None

    def seconds_left(self) -> Optional[float]:
        """Seconds until the last break ends, counted from the cue; None when no break runs."""
        with self._lock:
            if self._cue is None:
                return None
            left = self._cue.cued_at + self._cue.seconds - self._clock()
            return left if left > 0 else None

    def cued(self, event_id: int, seconds: float, check: Optional[ManifestCheck]) -> None:
        with self._lock:
            self._cue = Cue(event_id, seconds, self._clock_ms(), self._clock(), _ids(check))
            self._seen_at_ms = None

    def observe(self, check: Optional[ManifestCheck]) -> None:
        with self._lock:
            if self._cue is None or self._seen_at_ms is not None:
                return
            if _ids(check) - self._cue.baseline:
                self._seen_at_ms = self._clock_ms()

    def status(self) -> Optional[dict]:
        left = self.seconds_left()
        with self._lock:
            cue = self._cue
            if cue is None:
                return None
            if self._seen_at_ms is not None:
                state = "seen"
            elif self._clock() - cue.cued_at > NOT_SEEN_AFTER_S:
                state = "not_seen"
            else:
                state = "waiting"
            return {"event_id": cue.event_id, "seconds": cue.seconds, "cued_at_ms": cue.cued_at_ms,
                    "seen_at_ms": self._seen_at_ms, "state": state, "seconds_left": left}
