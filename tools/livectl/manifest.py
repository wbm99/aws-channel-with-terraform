"""Prove that video reaches viewers: read the HLS playlist through CloudFront and watch it advance.

Every other node says a resource is up. This one says segments are arriving where a viewer fetches them.
The master playlist is multivariant and has no media sequence, so the first variant playlist is read instead. The
same playlist carries the ad breaks MediaPackage writes from SCTE-35, as EXT-X-DATERANGE tags.
"""

from __future__ import annotations

import re
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


# KEY=value or KEY="value"; a quoted value may hold commas.
_ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


@dataclass(frozen=True)
class DateRange:
    """An ad break in the playlist: an EXT-X-DATERANGE carrying SCTE35-OUT."""

    id: str
    start: str
    seconds: Optional[float]
    event_id: Optional[int] = None  # the splice_event_id inside SCTE35-OUT, when it is a readable splice_insert


def splice_event_id(scte35: str) -> Optional[int]:
    """The splice_event_id of a splice_info_section given in hex, as SCTE35-OUT carries it; None if it is not an
    unencrypted splice_insert. The command type is byte 13 and the event ID bytes 14-17 (SCTE 35, section 9)."""
    try:
        data = bytes.fromhex(scte35[2:] if scte35[:2].lower() == "0x" else scte35)
    except ValueError:
        return None
    if len(data) < 18 or data[0] != 0xFC or data[13] != 0x05:
        return None
    return int.from_bytes(data[14:18], "big")


def date_ranges(playlist: str) -> tuple[DateRange, ...]:
    """The ad breaks in a media playlist. The length is DURATION, else PLANNED-DURATION, else unknown (None)."""
    breaks = []
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-DATERANGE:"):
            continue
        attributes = {key: value.strip('"') for key, value in _ATTRIBUTE.findall(line.split(":", 1)[1])}
        if "SCTE35-OUT" not in attributes or "ID" not in attributes:
            continue
        length = attributes.get("DURATION") or attributes.get("PLANNED-DURATION")
        breaks.append(DateRange(attributes["ID"], attributes.get("START-DATE", ""),
                                float(length) if length else None, splice_event_id(attributes["SCTE35-OUT"])))
    return tuple(breaks)


@dataclass(frozen=True)
class ManifestCheck:
    sequence: Optional[int]
    advancing: bool
    seconds_since_change: Optional[float]
    error: Optional[str]
    breaks: tuple[DateRange, ...] = ()


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
            playlist = self._fetch(variant) if variant else master
            sequence = media_sequence(playlist)
        except Exception as error:  # 404 before the first segment, DNS, timeouts: all are "not playing"
            return ManifestCheck(self._sequence, False, None, f"{type(error).__name__}: {error}")
        if sequence is None:
            return ManifestCheck(None, False, None, "the playlist has no #EXT-X-MEDIA-SEQUENCE")

        now = self._clock()
        if self._sequence is not None and sequence != self._sequence:
            self._changed_at = now
        self._sequence = sequence
        breaks = date_ranges(playlist)
        if self._changed_at is None:
            return ManifestCheck(sequence, False, None, None, breaks)
        since = now - self._changed_at
        return ManifestCheck(sequence, since <= self._window, since, None, breaks)
