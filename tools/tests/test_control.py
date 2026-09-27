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

    assert [m for m in messages if m in ("flow ACTIVE", "channel RUNNING")] == ["flow ACTIVE", "channel RUNNING"]



# --- states in between (first live run, 2026-09-27) ------------------------------------------
#
# When the channel stopped, MediaConnect briefly updated the flow: ACTIVE -> UPDATING -> ACTIVE. The stop logic
# checked during those four seconds, saw UPDATING, never sent stop_flow, and waited ten minutes for STANDBY while
# the flow kept billing. These fakes replay such sequences exactly.


class ScriptedFlow:
    """describe_flow answers from a script; stop_flow and start_flow switch to a second script."""

    def __init__(self, before, after_stop=("STOPPING", "STANDBY"), after_start=("STARTING", "ACTIVE")):
        self.states = list(before)
        self.after_stop, self.after_start = list(after_stop), list(after_start)
        self.calls = []

    def describe_flow(self, FlowArn):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"Flow": {"Status": state}}

    def stop_flow(self, FlowArn):
        self.calls.append("stop_flow")
        self.states = list(self.after_stop)

    def start_flow(self, FlowArn):
        self.calls.append("start_flow")
        self.states = list(self.after_start)


class ScriptedChannel:
    def __init__(self, before, after_stop=("STOPPING", "IDLE"), after_start=("STARTING", "RUNNING")):
        self.states = list(before)
        self.after_stop, self.after_start = list(after_stop), list(after_start)
        self.calls = []

    def describe_channel(self, ChannelId):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"State": state}

    def stop_channel(self, ChannelId):
        self.calls.append("stop_channel")
        self.states = list(self.after_stop)

    def start_channel(self, ChannelId):
        self.calls.append("start_channel")
        self.states = list(self.after_start)


# Real clock, no sleeping, one-second limit: a wait that never ends fails the test instead of hanging it.
FAST = {"sleep": lambda seconds: None, "timeout": 1}

TARGETS = __import__("livectl.targets", fromlist=["Targets"]).Targets(flow_arn="arn:flow", channel_id="1")


def test_stop_waits_for_an_updating_flow_to_settle_and_then_stops_it():
    flow = ScriptedFlow(["UPDATING", "UPDATING", "ACTIVE"])
    channel = ScriptedChannel(["RUNNING"])

    stop(flow, channel, TARGETS, **FAST)

    assert channel.calls == ["stop_channel"]
    assert flow.calls == ["stop_flow"], "the flow must be stopped once it settles, not waited on forever"


def test_start_waits_for_a_stopping_flow_before_starting_it():
    flow = ScriptedFlow(["STOPPING", "STANDBY"])
    channel = ScriptedChannel(["IDLE"])

    start(flow, channel, TARGETS, **FAST)

    assert flow.calls == ["start_flow"]
    assert channel.calls == ["start_channel"]


def test_stop_waits_for_a_starting_channel_before_stopping_it():
    flow = ScriptedFlow(["ACTIVE"])
    channel = ScriptedChannel(["STARTING", "STARTING", "RUNNING"])

    stop(flow, channel, TARGETS, **FAST)

    assert channel.calls == ["stop_channel"]
    assert flow.calls == ["stop_flow"]


def test_every_step_and_every_state_change_is_logged():
    flow = ScriptedFlow(["UPDATING", "ACTIVE"])
    channel = ScriptedChannel(["RUNNING"])
    messages = []

    stop(flow, channel, TARGETS, log=messages.append, **FAST)

    assert messages == [
        "stopping the channel",
        "channel STOPPING",
        "channel IDLE",
        "flow is UPDATING; waiting for it to settle",
        "flow ACTIVE",
        "stopping the flow",
        "flow STOPPING",
        "flow STANDBY",
    ]


def test_a_flow_in_error_is_stopped_rather_than_waited_on():
    flow = ScriptedFlow(["ERROR"])
    channel = ScriptedChannel(["IDLE"])

    stop(flow, channel, TARGETS, **FAST)

    assert flow.calls == ["stop_flow"]
