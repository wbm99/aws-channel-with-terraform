"""Prove that video reaches viewers: read the HLS playlist through CloudFront and watch it advance.

Every other node says a resource is up. This one says segments are arriving where a viewer fetches them.
The master playlist is multivariant and has no media sequence, so the first variant playlist is read instead.
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urljoin

Fetch = Callable[[str], str]


def http_fetch(url: str, timeout: float = 3.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "livectl"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def first_variant(master: str, base_url: str) -> Optional[str]:
    lines = [line.strip() for line in master.splitlines()]
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            for following in lines[index + 1:]:
                if following and not following.startswith("#"):
                    return urljoin(base_url, following)
    return None


def media_sequence(playlist: str) -> Optional[int]:
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            return int(line.split(":", 1)[1].strip())
    return None


@dataclass(frozen=True)
class ManifestCheck:
    sequence: Optional[int]
    advancing: bool
    seconds_since_change: Optional[float]
    error: Optional[str]


class ManifestWatcher:
    """Remembers the last media sequence it saw, so each check can say whether the stream is moving."""

    def __init__(self, fetch: Fetch = http_fetch, clock: Callable[[], float] = time.monotonic,
                 window: float = 30.0) -> None:
        self._fetch = fetch
        self._clock = clock
        self._window = window
        self._url: Optional[str] = None
        self._sequence: Optional[int] = None
        self._changed_at: Optional[float] = None

    def check(self, url: str) -> ManifestCheck:
        if url != self._url:
            self._url, self._sequence, self._changed_at = url, None, None
        try:
            master = self._fetch(url)
            variant = first_variant(master, url)
            sequence = media_sequence(self._fetch(variant) if variant else master)
        except Exception as error:  # 404 before the first segment, DNS, timeouts: all are "not playing"
            return ManifestCheck(self._sequence, False, None, f"{type(error).__name__}: {error}")
        if sequence is None:
            return ManifestCheck(None, False, None, "the playlist has no #EXT-X-MEDIA-SEQUENCE")

        now = self._clock()
        if self._sequence is not None and sequence != self._sequence:
            self._changed_at = now
        self._sequence = sequence
        if self._changed_at is None:
            return ManifestCheck(sequence, False, None, None)
        since = now - self._changed_at
        return ManifestCheck(sequence, since <= self._window, since, None)
