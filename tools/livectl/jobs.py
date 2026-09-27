"""Run one long operation at a time in the background and keep its output readable.

The console needs `start`, `stop`, `terraform apply` and friends to run without blocking the
HTTP handler, and it needs their output as it arrives. Only one job may run at a time: these
operations change the same resources, and two at once would race.
"""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

RUNNING, SUCCEEDED, FAILED = "running", "succeeded", "failed"
# Terminal colour and cursor codes. Terraform gets -no-color, but anything else a job runs may still send them.
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

Log = Callable[[str], None]
Work = Callable[[Log], None]


class JobBusy(RuntimeError):
    """Raised when a job is submitted while another one is still running."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Job:
    """One unit of work and everything the console needs to render it."""

    name: str
    started_at: str
    state: str = RUNNING
    finished_at: Optional[str] = None
    error: Optional[str] = None
    lines: list[str] = field(default_factory=list)


def command_job(args: Sequence[str], *, popen=subprocess.Popen) -> Work:
    """Build work that runs a command and logs its output line by line.

    stderr is folded into stdout so the log reads in the order Terraform wrote it. `popen` is
    injectable so tests never start a process.
    """

    def work(log: Log) -> None:
        log("$ " + " ".join(args))
        process = popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        with process.stdout:
            for line in process.stdout:
                log(ANSI.sub("", line.rstrip("\n")))
        code = process.wait()
        if code != 0:
            raise RuntimeError(f"{args[0]} exited with status {code}")

    return work


class JobRunner:
    """Holds at most one running job, its log and its outcome."""

    def __init__(self, clock: Callable[[], str] = _now) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._job: Optional[Job] = None
        self._thread: Optional[threading.Thread] = None

    def submit(self, name: str, work: Work) -> Job:
        """Start work in a background thread. Raises JobBusy if one is already running."""
        with self._lock:
            if self._job is not None and self._job.state == RUNNING:
                raise JobBusy(f"{self._job.name} is still running")
            job = Job(name=name, started_at=self._clock())
            self._job = job

        self._thread = threading.Thread(target=self._run, args=(job, work), daemon=True, name=f"livectl-{name}")
        self._thread.start()
        return job

    def summary(self, offset: int = 0, job: Optional[str] = None) -> Optional[dict]:
        """The current job as plain data, with the log lines from `offset` onwards.

        `job` is the key of the job the caller's offset belongs to. When a different job is running now, the offset
        means nothing for it and its lines are returned from the first one.
        """
        with self._lock:
            current = self._job
            if current is None:
                return None
            key = f"{current.name}@{current.started_at}"
            start = offset if job in (None, key) else 0
            return {
                "key": key,
                "name": current.name,
                "state": current.state,
                "started_at": current.started_at,
                "finished_at": current.finished_at,
                "error": current.error,
                "start": start,
                "offset": len(current.lines),
                "lines": current.lines[start:],
            }

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until the running job finishes. For tests and for shutdown."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, job: Job, work: Work) -> None:
        try:
            work(lambda line: self._append(job, line))
        except Exception as error:  # any failure belongs in the log, not in a dead thread
            self._append(job, f"error: {error}")
            self._finish(job, FAILED, str(error))
        else:
            self._finish(job, SUCCEEDED, None)

    def _append(self, job: Job, line: str) -> None:
        with self._lock:
            job.lines.append(line)

    def _finish(self, job: Job, state: str, error: Optional[str]) -> None:
        with self._lock:
            job.state, job.error, job.finished_at = state, error, self._clock()
