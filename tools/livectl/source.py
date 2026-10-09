"""Run the FFmpeg test source from the console, one at a time, without ever logging its passphrase.

The passphrase travels to the child in its environment, as `just send` does. FFmpeg still prints its output URL,
which contains `passphrase=...`, so every line is redacted before it is stored. FFmpeg's `frame=` progress lines
update the status instead of filling the buffer.

Known limitation: FFmpeg receives the passphrase inside its SRT URL argument, so it is visible in the local
process list (`ps`) while the source runs. `just send` has the same exposure; FFmpeg's SRT support offers no
other way to pass it.

A start can be refused by the listener: MediaConnect takes one SRT sender, and after a restart it may still hold the
old connection until SRT notices it went quiet (about 5 s) if the old FFmpeg's shutdown packet was lost. FFmpeg then
prints `Connection setup failure` and exits before sending a frame. That case is retried a few times, with growing
pauses, before the source is reported as exited.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

# Relative to the working directory, like --tf-dir: an installed livectl (the Docker image) lives in site-packages, far
# from the checkout. `just` runs from the repository root and the container from /work, so both find it.
SCRIPT = Path("source/send-srt.sh")
STOPPED, STARTING, RUNNING, EXITED = "stopped", "starting", "running", "exited"
# What libsrt reports, through FFmpeg, when the listener rejects the caller or never answers its handshake.
SETUP_FAILURE = "Connection setup failure"
# Pauses before each new attempt after a setup failure: about 7 s in all, past SRT's 5 s peer idle timeout.
RETRY_DELAYS = (1.0, 2.0, 4.0)
# Test patterns source/send-srt.sh can generate (its PATTERN variable), in the order the console lists them.
PATTERNS = [
    ("testcard", "Test card"),
    ("smpte", "SMPTE HD colour bars"),
    ("pal", "PAL/EBU 100% colour bars"),
    ("black", "Black"),
    ("standby", "Please stand by"),
]


def _enum(key, group, label, values):
    return {"key": key, "group": group, "label": label, "kind": "enum",
            "values": [{"id": value, "label": text} for value, text in values]}


def _range(key, group, label, low, high, step, unit):
    return {"key": key, "group": group, "label": label, "kind": "range", "min": low, "max": high, "step": step,
            "unit": unit}


def _text(key, group, label):
    return {"key": key, "group": group, "label": label, "kind": "text", "max_length": TEXT_MAX}


TEXT_MAX = 60
# Every setting of the test source and what it may be, in display order. This is the only list of allowed values:
# the console builds its form from it, the API validates against it, and source/send-srt.sh mirrors it for
# `just send`. Everything stays inside the channel's input class (AVC, HD, MAX_10_MBPS); see modules/encode.
CHOICES = [
    _enum("pattern", "Video", "Pattern", PATTERNS),
    _enum("size", "Video", "Resolution", [("1280x720", "1280×720"), ("1920x1080", "1920×1080")]),
    _enum("fps", "Video", "Frame rate", [(25, "25"), (30, "30"), (50, "50"), (60, "60")]),
    _range("video_kbps", "Video", "Bitrate", 500, 10000, 100, "kbps"),
    _enum("gop_seconds", "Video", "Keyframe interval", [(1, "1 s"), (2, "2 s"), (4, "4 s")]),
    _enum("audio_codec", "Audio", "Codec", [("aac", "AAC"), ("mp2", "MP2"), ("ac3", "AC-3")]),
    _enum("audio_kbps", "Audio", "Bitrate", [(n, f"{n} kbps") for n in (64, 96, 128, 192, 256)]),
    _enum("tone", "Audio", "Tone", [("440", "440 Hz"), ("1000", "1 kHz"), ("silence", "Silence")]),
    _text("service_name", "MPEG-TS", "Service name"),
    _text("service_provider", "MPEG-TS", "Provider"),
    _range("program_number", "MPEG-TS", "Program number", 1, 65535, 1, ""),
    _range("latency_ms", "SRT", "Latency", 20, 8000, 10, "ms"),
]
_BY_KEY = {choice["key"]: choice for choice in CHOICES}


class SettingsError(ValueError):
    """A setting outside what the source may send; names the field so the page can show the message beside it."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field, self.message = field, message


@dataclass(frozen=True)
class SourceSettings:
    """What the test source sends. Every value is checked against CHOICES by `merged`."""

    pattern: str = "testcard"
    size: str = "1920x1080"
    fps: int = 30
    video_kbps: int = 6000
    gop_seconds: int = 2
    audio_codec: str = "aac"
    audio_kbps: int = 128
    tone: str = "1000"
    service_name: str = "livectl test source"
    service_provider: str = "livectl"
    program_number: int = 1
    latency_ms: int = 120

    def merged(self, partial: Mapping[str, object]) -> "SourceSettings":
        """A copy with the given keys changed, refused at the first value outside its choice."""
        for key, value in partial.items():
            _check(key, value)
        return dataclasses.replace(self, **partial)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    def to_env(self) -> dict[str, str]:
        """The environment source/send-srt.sh reads."""
        return {
            "PATTERN": self.pattern, "SIZE": self.size, "FPS": str(self.fps), "BITRATE": f"{self.video_kbps}k",
            "GOP_SECONDS": str(self.gop_seconds), "AUDIO_CODEC": self.audio_codec,
            "AUDIO_BITRATE": f"{self.audio_kbps}k", "TONE": self.tone, "SERVICE_NAME": self.service_name,
            "SERVICE_PROVIDER": self.service_provider, "PROGRAM_NUMBER": str(self.program_number),
            "SRT_LATENCY_MS": str(self.latency_ms),
        }


def _check(key: str, value: object) -> None:
    choice = _BY_KEY.get(key)
    if choice is None:
        raise SettingsError(key, f"Unknown setting {key!r}.")
    label = choice["label"] if choice["group"] != "Audio" or key == "tone" else "Audio " + choice["label"].lower()
    if choice["kind"] == "enum":
        ids = [entry["id"] for entry in choice["values"]]
        if not any(type(value) is type(i) and value == i for i in ids):
            raise SettingsError(key, f"{label} must be one of {', '.join(str(i) for i in ids)}.")
    elif choice["kind"] == "range":
        low, high, unit = choice["min"], choice["max"], choice["unit"]
        if type(value) is not int or not low <= value <= high:
            shown = " (0.5 to 10 Mbps)" if key == "video_kbps" else ""
            raise SettingsError(key, f"{label} must be a whole number between {low} and {high}"
                                     f"{' ' + unit if unit else ''}{shown}.")
    elif (type(value) is not str or not 1 <= len(value) <= TEXT_MAX
          or any(unicodedata.category(ch) == "Cc" for ch in value)):
        raise SettingsError(key, f"{label} must be 1 to {TEXT_MAX} characters, without control characters.")


class SourceBusy(RuntimeError):
    """Raised when the test source is started while it is already running."""


def now_ms() -> int:
    return int(time.time() * 1000)


class SourceProcess:
    """One FFmpeg at a time. Each start is a new generation: a reader thread left over from an older process (its
    stop timed out waiting for it) may drain its output, but never changes the state, the buffer or the redaction
    of the process that replaced it. A launch whose SRT connection is refused before the first frame is retried
    after each of `retry_delays`; a stop cancels the pause."""

    def __init__(self, *, script: "Path | str" = SCRIPT, popen=subprocess.Popen,
                 clock_ms: Callable[[], int] = now_ms, max_lines: int = 500, grace: float = 5.0,
                 retry_delays: Sequence[float] = RETRY_DELAYS) -> None:
        self._script, self._popen, self._clock_ms, self._grace = str(script), popen, clock_ms, grace
        self._retry_delays = tuple(retry_delays)
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._restart_lock = threading.Lock()
        self._lines: deque[tuple[int, str]] = deque(maxlen=max_lines)
        self._process = None
        self._generation = 0
        self._state = STOPPED
        self._stopping = False
        self._exit_code: Optional[int] = None
        self._progress: Optional[str] = None
        self._last_line: Optional[str] = None
        self._settings = SourceSettings()
        self._reader: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state in (STARTING, RUNNING)

    def start(self, *, host: str, port: int, passphrase: str, settings: SourceSettings = SourceSettings()) -> None:
        with self._restart_lock:
            self._start(host, port, passphrase, settings)

    def _start(self, host: str, port: int, passphrase: str, settings: SourceSettings) -> None:
        with self._lock:
            if self._state in (STARTING, RUNNING):
                raise SourceBusy("the test source is already running")
            self._generation += 1
            generation = self._generation
            self._state, self._stopping, self._settings = STARTING, False, settings
            self._exit_code = self._progress = self._last_line = None
            self._cancel = cancel = threading.Event()
        env = {**os.environ, **settings.to_env(),
               "SRT_HOST": host, "SRT_PORT": str(port), "SRT_PASSPHRASE": passphrase}

        def launch():
            return self._popen([self._script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1, env=env)

        try:
            process = launch()
        except OSError as error:
            # Nothing was launched: say why instead of staying "starting", which would refuse every later start.
            line = f"could not start {self._script}: {error.strerror or error}"
            with self._lock:
                if generation == self._generation:
                    self._state, self._exit_code, self._last_line = EXITED, -1, line
                    self._lines.append((self._clock_ms(), line))
            raise
        with self._lock:
            self._process = process
        self._reader = threading.Thread(target=self._pump, args=(process, generation, passphrase, launch, cancel),
                                        daemon=True, name="livectl-source")
        self._reader.start()

    def restart(self, *, host: str, port: int, passphrase: str, settings: SourceSettings) -> None:
        """Stop, then start on new settings. Two restarts at once run one after the other, never interleaved. If
        the listener still holds the old connection, the start is retried (see the module docstring)."""
        with self._restart_lock:
            self._stop()
            self._start(host, port, passphrase, settings)

    def _pump(self, process, generation: int, secret: str, launch, cancel: threading.Event) -> None:
        for attempt, delay in enumerate((*self._retry_delays, None), start=1):
            refused = self._drain(process, generation, secret)
            code = process.wait()
            with self._lock:
                if generation != self._generation or self._state not in (STARTING, RUNNING):
                    return
                if not (refused and delay is not None and self._state == STARTING and not self._stopping):
                    self._exit_code = code
                    self._state = STOPPED if self._stopping else EXITED
                    return
                self._note(f"SRT connection refused; trying again in {delay:g} s "
                           f"(attempt {attempt + 1} of {len(self._retry_delays) + 1})")
            if cancel.wait(delay):
                return  # stopped during the pause; the stop has already recorded it
            with self._lock:  # launch under the lock, so a stop either cancels it or finds the new process
                if generation != self._generation or self._stopping:
                    return
                try:
                    process = self._process = launch()
                except OSError as error:
                    self._note(f"could not start {self._script}: {error.strerror or error}")
                    self._state, self._exit_code = EXITED, -1
                    return

    def _drain(self, process, generation: int, secret: str) -> bool:
        """Read one FFmpeg's output to the end; True if libsrt reported that the connection was refused."""
        refused = False
        for raw in process.stdout:
            line = raw.rstrip("\n").replace(secret, "***")
            if not line.strip():
                continue
            refused = refused or SETUP_FAILURE in line
            with self._lock:
                if generation != self._generation:
                    continue  # a newer process owns the state; keep draining so the old one can exit
                if line.lstrip().startswith("frame="):
                    self._progress = line.strip()
                    if self._state == STARTING:
                        self._state = RUNNING
                    continue
                self._note(line)
        return refused

    def _note(self, line: str) -> None:
        """Keep a line for the log and as the last error. Called with the lock held."""
        self._lines.append((self._clock_ms(), line))
        self._last_line = line

    def stop(self) -> None:
        # Waits for a restart in progress, then stops what it started: a Stop pressed after Apply always wins.
        with self._restart_lock:
            self._stop()

    def _stop(self) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                if self._state in (STARTING, RUNNING):  # pausing before another attempt: cancel it
                    self._generation += 1
                    self._state, self._stopping = STOPPED, True
                    self._cancel.set()
                return
            self._stopping = True
            self._cancel.set()
        process.terminate()
        try:
            code = process.wait(timeout=self._grace)
        except subprocess.TimeoutExpired:
            process.kill()
            code = process.wait()
        # The reader records the stop once it has drained the output. If it has not finished within the grace
        # period, record the stop here and retire its generation, so a start right after (an Apply) neither finds
        # the source still running nor has its state overwritten when the old reader finally ends.
        if self._reader is not None:
            self._reader.join(timeout=self._grace)
        with self._lock:
            if self._process is process and self._state in (STARTING, RUNNING):
                self._generation += 1
                self._exit_code, self._state = code, STOPPED

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "exit_code": self._exit_code,
                "last_error": self._last_line if self._state == EXITED and self._exit_code else None,
                "progress": self._progress,
                "settings": self._settings.to_dict(),
            }

    def lines(self, after_ms: int) -> list[tuple[int, str]]:
        with self._lock:
            return [(at, text) for at, text in self._lines if at > after_ms]
