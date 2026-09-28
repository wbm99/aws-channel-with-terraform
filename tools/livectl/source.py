"""Run the FFmpeg test source from the console, one at a time, without ever logging its passphrase.

The passphrase travels to the child in its environment, as `just send` does. FFmpeg still prints its output URL,
which contains `passphrase=...`, so every line is redacted before it is stored. FFmpeg's `frame=` progress lines
update the status instead of filling the buffer.

Known limitation: FFmpeg receives the passphrase inside its SRT URL argument, so it is visible in the local
process list (`ps`) while the source runs. `just send` has the same exposure; FFmpeg's SRT support offers no
other way to pass it.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

# Relative to the working directory, like --tf-dir: an installed livectl (the Docker image) lives in site-packages, far
# from the checkout. `just` runs from the repository root and the container from /work, so both find it.
SCRIPT = Path("source/send-srt.sh")
STOPPED, STARTING, RUNNING, EXITED = "stopped", "starting", "running", "exited"
# Test patterns source/send-srt.sh can generate (its PATTERN variable), in the order the console lists them.
PATTERNS = [
    ("testcard", "Test card"),
    ("smpte", "SMPTE HD colour bars"),
    ("pal", "PAL/EBU 100% colour bars"),
    ("black", "Black"),
    ("standby", "Please stand by"),
]
DEFAULT_PATTERN = "testcard"


class SourceBusy(RuntimeError):
    """Raised when the test source is started while it is already running."""


def now_ms() -> int:
    return int(time.time() * 1000)


class SourceProcess:
    def __init__(self, *, script: "Path | str" = SCRIPT, popen=subprocess.Popen,
                 clock_ms: Callable[[], int] = now_ms, max_lines: int = 500, grace: float = 5.0) -> None:
        self._script, self._popen, self._clock_ms, self._grace = str(script), popen, clock_ms, grace
        self._lock = threading.Lock()
        self._lines: deque[tuple[int, str]] = deque(maxlen=max_lines)
        self._process = None
        self._state = STOPPED
        self._stopping = False
        self._secret: Optional[str] = None
        self._exit_code: Optional[int] = None
        self._progress: Optional[str] = None
        self._last_line: Optional[str] = None
        self._pattern: str = DEFAULT_PATTERN
        self._reader: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state in (STARTING, RUNNING)

    def start(self, *, host: str, port: int, passphrase: str, pattern: str = DEFAULT_PATTERN) -> None:
        if pattern not in dict(PATTERNS):
            raise ValueError(f"unknown pattern {pattern!r}")
        with self._lock:
            if self._state in (STARTING, RUNNING):
                raise SourceBusy("the test source is already running")
            self._state, self._stopping, self._secret = STARTING, False, passphrase
            self._exit_code = self._progress = self._last_line = None
            self._pattern = pattern
        env = {**os.environ, "SRT_HOST": host, "SRT_PORT": str(port), "SRT_PASSPHRASE": passphrase, "PATTERN": pattern}
        process = self._popen([self._script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, bufsize=1, env=env)
        with self._lock:
            self._process = process
        self._reader = threading.Thread(target=self._pump, args=(process,), daemon=True, name="livectl-source")
        self._reader.start()

    def _redact(self, line: str) -> str:
        return line.replace(self._secret, "***") if self._secret else line

    def _pump(self, process) -> None:
        for raw in process.stdout:
            line = self._redact(raw.rstrip("\n"))
            if not line.strip():
                continue
            with self._lock:
                if line.lstrip().startswith("frame="):
                    self._progress = line.strip()
                    if self._state == STARTING:
                        self._state = RUNNING
                    continue
                self._lines.append((self._clock_ms(), line))
                self._last_line = line
        code = process.wait()
        with self._lock:
            self._exit_code = code
            self._state = STOPPED if self._stopping else EXITED
            self._secret = None  # all output has been redacted; nothing else needs it

    def stop(self) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return
            self._stopping = True
        process.terminate()
        try:
            process.wait(timeout=self._grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        # The reader thread records the stop once it has drained the output; wait for it, so that a start right
        # after (switching pattern) does not find the source still marked as running.
        if self._reader is not None:
            self._reader.join(timeout=self._grace)

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "exit_code": self._exit_code,
                "last_error": self._last_line if self._state == EXITED and self._exit_code else None,
                "progress": self._progress,
                "pattern": self._pattern,
            }

    def lines(self, after_ms: int) -> list[tuple[int, str]]:
        with self._lock:
            return [(at, text) for at, text in self._lines if at > after_ms]
