"""How far a job has got, read from its own output. Pure functions, no I/O.

Terraform states its plan ("Plan: 4 to add, 1 to change, 0 to destroy.") and then prints one "... complete after"
line per resource, so deploy and teardown progress is exact. Going live and off air have two milestones each: the
MediaConnect flow and the MediaLive channel reaching their target states.
"""

from __future__ import annotations

import re
from typing import Optional

PLAN = re.compile(r"^Plan: (\d+) to add, (\d+) to change, (\d+) to destroy\.")
DONE = re.compile(r": (Creation|Modifications|Destruction) complete after ")
NO_CHANGES = "No changes."

MILESTONES = {
    "go-live": [("MediaConnect flow ACTIVE", "MediaConnect flow active"),
                ("MediaLive channel RUNNING", "MediaLive channel running")],
    "go-off-air": [("MediaLive channel IDLE", "MediaLive channel idle"),
                   ("MediaConnect flow STANDBY", "MediaConnect flow in standby")],
}


def _result(done: int, total: Optional[int], label: str, state: str) -> dict:
    if state == "succeeded":
        return {"done": total if total is not None else done, "total": total, "percent": 100, "label": label}
    percent = 0 if not total else min(100, done * 100 // total)
    return {"done": done, "total": total, "percent": percent, "label": label}


def _terraform(lines: list[str], state: str) -> dict:
    total: Optional[int] = None
    done = 0
    for line in lines:
        if line.startswith(NO_CHANGES):
            return {"done": 0, "total": 0, "percent": 100, "label": "no changes"}
        plan = PLAN.match(line)
        if plan:
            total = sum(int(n) for n in plan.groups())
        elif DONE.search(line):
            done += 1
    if total is None:
        label = {"succeeded": "done", "failed": "stopped before planning"}.get(state, "planning")
        return _result(0, None, label, state)
    return _result(done, total, f"{done} of {total} resources", state)


def _milestones(steps: list[tuple[str, str]], lines: list[str], state: str) -> dict:
    reached = [label for marker, label in steps if marker in lines]
    label = reached[-1] if reached else "starting"
    return _result(len(reached), len(steps), label, state)


def job_progress(name: str, lines: list[str], state: str) -> Optional[dict]:
    """{done, total, percent, label} for jobs that can be measured, else None."""
    if name in ("deploy", "teardown"):
        return _terraform(lines, state)
    if name in MILESTONES:
        return _milestones(MILESTONES[name], lines, state)
    return None
