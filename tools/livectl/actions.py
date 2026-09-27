"""Which actions the console allows right now, and why not.

The page hides what this refuses, and the POST handlers refuse with the same message, so the two cannot disagree.
Messages are written for the person at the page, never copied from the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ACTIONS = ("deploy", "teardown", "scan", "go-live", "go-off-air", "source-start", "source-stop")


@dataclass(frozen=True)
class Refusal:
    status: int
    message: str


@dataclass(frozen=True)
class Situation:
    deployed: bool
    job_running: Optional[str]
    flow_state: Optional[str]
    channel_state: Optional[str]
    source_running: bool = False


def _first(*refusals: Optional[Refusal]) -> Optional[Refusal]:
    return next((r for r in refusals if r is not None), None)


def refusals(s: Situation) -> dict[str, Optional[Refusal]]:
    busy = Refusal(409, f"{s.job_running} is still running") if s.job_running else None
    missing = None if s.deployed else Refusal(400, "Nothing is deployed yet. Use Deploy stack first.")
    on_air = s.flow_state == "ACTIVE" or s.channel_state == "RUNNING"
    fully_on = s.flow_state == "ACTIVE" and s.channel_state == "RUNNING"
    fully_off = s.flow_state == "STANDBY" and s.channel_state == "IDLE"
    off_air_first = Refusal(409, "Go off air first: the flow or the channel is still running.") if on_air else None

    return {
        "deploy": _first(busy, off_air_first and Refusal(
            409, "Go off air first: Terraform cannot update a running MediaLive channel.")),
        # A failed apply can leave billable resources without complete outputs, so teardown never needs them.
        "teardown": _first(busy, off_air_first,
                           Refusal(409, "Stop the test source first.") if s.source_running else None),
        "scan": busy,
        "go-live": _first(busy, missing, Refusal(409, "Already on air.") if fully_on else None),
        "go-off-air": _first(busy, missing, Refusal(409, "Already off air.") if fully_off else None),
        "source-start": _first(
            missing,
            Refusal(409, "The test source is already running.") if s.source_running else None,
            None if s.flow_state == "ACTIVE" else Refusal(409, "The flow is not active: go live first."),
        ),
        "source-stop": None,
    }


def next_action(s: Situation, source_connected: bool = False) -> Optional[str]:
    """The step a person would take next on the way to a playing stream, or None when there is nothing to suggest.

    Deploy, then go live, then send the test source. A source pushed from elsewhere (`just send`, a real encoder)
    counts as connected, so the console does not suggest a second one.
    """
    if s.job_running:
        return None
    if not s.deployed:
        return "deploy"
    if s.flow_state == "STANDBY" and s.channel_state == "IDLE":
        return "go-live"
    if s.flow_state == "ACTIVE" and s.channel_state == "RUNNING" and not (s.source_running or source_connected):
        return "source-start"
    return None
