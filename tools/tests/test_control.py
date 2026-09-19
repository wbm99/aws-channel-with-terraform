import boto3
import pytest

from conftest import make_workflow
from livectl.control import WaitTimeout, channel_state, flow_status, start, stop, wait_for

NO_SLEEP = {"sleep": lambda seconds: None}


def test_wait_for_returns_when_target_is_reached():
    states = iter(["STARTING", "STARTING", "ACTIVE"])

    wait_for(lambda: next(states), "ACTIVE", label="flow", **NO_SLEEP)


def test_wait_for_times_out_and_reports_the_last_state():
    ticks = iter(range(0, 1000, 10))

    with pytest.raises(WaitTimeout, match=r"flow did not reach ACTIVE.*last state: STARTING"):
        wait_for(
            lambda: "STARTING",
            "ACTIVE",
            timeout=30,
            interval=1,
            label="flow",
            sleep=lambda seconds: None,
            clock=lambda: next(ticks),
        )


def test_start_brings_up_flow_then_channel(aws):
    targets = make_workflow()
    mc, ml = boto3.client("mediaconnect"), boto3.client("medialive")
    assert flow_status(mc, targets.flow_arn) == "STANDBY"
    assert channel_state(ml, targets.channel_id) == "IDLE"

    start(mc, ml, targets, **NO_SLEEP)

    assert flow_status(mc, targets.flow_arn) == "ACTIVE"
    assert channel_state(ml, targets.channel_id) == "RUNNING"


def test_stop_brings_down_channel_then_flow(aws):
    targets = make_workflow()
    mc, ml = boto3.client("mediaconnect"), boto3.client("medialive")
    start(mc, ml, targets, **NO_SLEEP)

    stop(mc, ml, targets, **NO_SLEEP)

    assert channel_state(ml, targets.channel_id) == "IDLE"
    assert flow_status(mc, targets.flow_arn) == "STANDBY"


def test_start_and_stop_are_idempotent(aws):
    targets = make_workflow()
    mc, ml = boto3.client("mediaconnect"), boto3.client("medialive")

    stop(mc, ml, targets, **NO_SLEEP)
    start(mc, ml, targets, **NO_SLEEP)
    start(mc, ml, targets, **NO_SLEEP)
    stop(mc, ml, targets, **NO_SLEEP)
    stop(mc, ml, targets, **NO_SLEEP)

    assert flow_status(mc, targets.flow_arn) == "STANDBY"


def test_start_logs_each_step(aws):
    targets = make_workflow()
    messages = []

    start(boto3.client("mediaconnect"), boto3.client("medialive"), targets, log=messages.append, **NO_SLEEP)

    assert messages == ["flow ACTIVE", "channel RUNNING"]
