"""Start and stop the live workflow, waiting for each state change."""

from __future__ import annotations

import time
from typing import Callable

from livectl.targets import Targets


class WaitTimeout(RuntimeError):
    """Raised when a resource does not reach the expected state in time."""


def wait_for(
    read_state: Callable[[], str],
    target: str,
    *,
    timeout: float = 600,
    interval: float = 5,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    label: str = "resource",
) -> None:
    """Poll read_state until it returns target or the timeout expires."""
    deadline = clock() + timeout
    while True:
        state = read_state()
        if state == target:
            return
        if clock() >= deadline:
            raise WaitTimeout(f"{label} did not reach {target} within {timeout:.0f}s (last state: {state})")
        sleep(interval)


def flow_status(mediaconnect, flow_arn: str) -> str:
    return mediaconnect.describe_flow(FlowArn=flow_arn)["Flow"]["Status"]


def channel_state(medialive, channel_id: str) -> str:
    return medialive.describe_channel(ChannelId=channel_id)["State"]


def _noop(message: str) -> None:
    return None


def start(mediaconnect, medialive, targets: Targets, *, log: Callable[[str], None] = _noop, **wait_kwargs) -> None:
    """Start the flow, wait for ACTIVE, then start the channel and wait for RUNNING."""
    if flow_status(mediaconnect, targets.flow_arn) == "STANDBY":
        mediaconnect.start_flow(FlowArn=targets.flow_arn)
    wait_for(lambda: flow_status(mediaconnect, targets.flow_arn), "ACTIVE", label="flow", **wait_kwargs)
    log("flow ACTIVE")

    if channel_state(medialive, targets.channel_id) == "IDLE":
        medialive.start_channel(ChannelId=targets.channel_id)
    wait_for(lambda: channel_state(medialive, targets.channel_id), "RUNNING", label="channel", **wait_kwargs)
    log("channel RUNNING")


def stop(mediaconnect, medialive, targets: Targets, *, log: Callable[[str], None] = _noop, **wait_kwargs) -> None:
    """Stop the channel, wait for IDLE, then stop the flow and wait for STANDBY."""
    if channel_state(medialive, targets.channel_id) == "RUNNING":
        medialive.stop_channel(ChannelId=targets.channel_id)
    wait_for(lambda: channel_state(medialive, targets.channel_id), "IDLE", label="channel", **wait_kwargs)
    log("channel IDLE")

    if flow_status(mediaconnect, targets.flow_arn) == "ACTIVE":
        mediaconnect.stop_flow(FlowArn=targets.flow_arn)
    wait_for(lambda: flow_status(mediaconnect, targets.flow_arn), "STANDBY", label="flow", **wait_kwargs)
    log("flow STANDBY")
