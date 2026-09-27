"""Start and stop the live workflow, waiting for each state change."""

from __future__ import annotations

import time
from typing import Callable, Optional

from livectl.targets import Targets


class WaitTimeout(RuntimeError):
    """Raised when a resource does not reach the expected state in time."""


def wait_for(
    read_state: Callable[[], str],
    target: str,
    *,
    label: str = "resource",
    **wait_kwargs,
) -> None:
    """Poll read_state until it returns target or the timeout (default 600 s) expires."""
    _wait_until(read_state, lambda state: state == target, target, label, **wait_kwargs)


def flow_status(mediaconnect, flow_arn: str) -> str:
    return mediaconnect.describe_flow(FlowArn=flow_arn)["Flow"]["Status"]


def channel_state(medialive, channel_id: str) -> str:
    return medialive.describe_channel(ChannelId=channel_id)["State"]


def _noop(message: str) -> None:
    return None


# States a resource settles in. Anything else (STARTING, STOPPING, UPDATING, ...) is on its way somewhere, and
# acting on it is a guess: a flow seen as UPDATING for four seconds once meant stop_flow was never sent.
FLOW_SETTLED = {"ACTIVE", "STANDBY", "ERROR"}
CHANNEL_SETTLED = {"RUNNING", "IDLE", "CREATE_FAILED", "UPDATE_FAILED"}


def _watching(read_state: Callable[[], str], label: str, log: Callable[[str], None]) -> Callable[[], str]:
    """read_state that logs every change, so a person can see what a long wait is waiting on."""
    last: list[Optional[str]] = [None]

    def read() -> str:
        state = read_state()
        if state != last[0]:
            last[0] = state
            log(f"{label} {state}")
        return state

    return read


def _settle(read_state: Callable[[], str], settled: set[str], label: str, log: Callable[[str], None],
            **wait_kwargs) -> str:
    """Return the resource's state once it is no longer in between states."""
    state = read_state()
    if state in settled:
        return state
    log(f"{label} is {state}; waiting for it to settle")
    watched = _watching(read_state, label, log)
    _wait_until(watched, lambda s: s in settled, f"one of {', '.join(sorted(settled))}", label, **wait_kwargs)
    return watched()


def _wait_until(read_state: Callable[[], str], done: Callable[[str], bool], target: str, label: str, *,
                timeout: float = 600, interval: float = 5, sleep: Callable[[float], None] = time.sleep,
                clock: Callable[[], float] = time.monotonic) -> None:
    deadline = clock() + timeout
    while True:
        state = read_state()
        if done(state):
            return
        if clock() >= deadline:
            raise WaitTimeout(f"{label} did not reach {target} within {timeout:.0f}s (last state: {state})")
        sleep(interval)


def _reach(read_state: Callable[[], str], target: str, label: str, log: Callable[[str], None], **wait_kwargs) -> None:
    _wait_until(_watching(read_state, label, log), lambda s: s == target, target, label, **wait_kwargs)


def start(mediaconnect, medialive, targets: Targets, *, log: Callable[[str], None] = _noop, **wait_kwargs) -> None:
    """Start the flow, wait for ACTIVE, then start the channel and wait for RUNNING. Safe to run twice."""
    read_flow = lambda: flow_status(mediaconnect, targets.flow_arn)  # noqa: E731
    read_channel = lambda: channel_state(medialive, targets.channel_id)  # noqa: E731

    flow = _settle(read_flow, FLOW_SETTLED, "flow", log, **wait_kwargs)
    if flow == "ERROR":
        raise WaitTimeout("the flow is in ERROR; check it in the MediaConnect console before going live")
    if flow == "STANDBY":
        log("starting the flow")
        mediaconnect.start_flow(FlowArn=targets.flow_arn)
    _reach(read_flow, "ACTIVE", "flow", log, **wait_kwargs)

    channel = _settle(read_channel, CHANNEL_SETTLED, "channel", log, **wait_kwargs)
    if channel == "IDLE":
        log("starting the channel")
        medialive.start_channel(ChannelId=targets.channel_id)
    _reach(read_channel, "RUNNING", "channel", log, **wait_kwargs)


def stop(mediaconnect, medialive, targets: Targets, *, log: Callable[[str], None] = _noop, **wait_kwargs) -> None:
    """Stop the channel, wait for IDLE, then stop the flow and wait for STANDBY. Safe to run twice."""
    read_flow = lambda: flow_status(mediaconnect, targets.flow_arn)  # noqa: E731
    read_channel = lambda: channel_state(medialive, targets.channel_id)  # noqa: E731

    channel = _settle(read_channel, CHANNEL_SETTLED, "channel", log, **wait_kwargs)
    if channel == "RUNNING":
        log("stopping the channel")
        medialive.stop_channel(ChannelId=targets.channel_id)
    _reach(read_channel, "IDLE", "channel", log, **wait_kwargs)

    flow = _settle(read_flow, FLOW_SETTLED, "flow", log, **wait_kwargs)
    if flow != "STANDBY":  # ACTIVE, or ERROR: a flow in error still bills until it is stopped
        log("stopping the flow")
        mediaconnect.stop_flow(FlowArn=targets.flow_arn)
    _reach(read_flow, "STANDBY", "flow", log, **wait_kwargs)
