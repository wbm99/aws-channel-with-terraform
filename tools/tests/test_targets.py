import json

import pytest

from livectl.targets import TargetError, resolve_targets

OUTPUTS = {
    "flow_arn": {"value": "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:demo"},
    "medialive_channel_id": {"value": "7387208"},
    "player_url": {"value": "https://example.cloudfront.net/"},
    "ingest_ip": {"value": "203.0.113.20"},
    "ingest_port": {"value": 5000},
}


def fake_runner(payload):
    calls = []

    def runner(args):
        calls.append(list(args))
        return payload if isinstance(payload, str) else json.dumps(payload)

    runner.calls = calls
    return runner


def test_flags_skip_terraform_entirely():
    def must_not_run(args):
        raise AssertionError("terraform must not be called when both flags are given")

    targets = resolve_targets("arn:flow", "42", "envs/demo", must_not_run)

    assert targets.flow_arn == "arn:flow"
    assert targets.channel_id == "42"
    assert targets.player_url is None


def test_terraform_outputs_are_used_by_default():
    runner = fake_runner(OUTPUTS)

    targets = resolve_targets(None, None, "envs/demo", runner)

    assert runner.calls == [["terraform", "-chdir=envs/demo", "output", "-json"]]
    assert targets.flow_arn.endswith(":demo")
    assert targets.channel_id == "7387208"
    assert targets.player_url == "https://example.cloudfront.net/"
    assert (targets.ingest_ip, targets.ingest_port) == ("203.0.113.20", 5000)


def test_a_flag_overrides_the_matching_terraform_value():
    targets = resolve_targets(None, "999", "envs/demo", fake_runner(OUTPUTS))

    assert targets.channel_id == "999"
    assert targets.flow_arn.endswith(":demo")


def test_missing_outputs_raise_a_helpful_error():
    with pytest.raises(TargetError, match="flow ARN and channel ID"):
        resolve_targets(None, None, "envs/demo", fake_runner({}))


def test_invalid_json_raises():
    with pytest.raises(TargetError, match="valid JSON"):
        resolve_targets(None, None, "envs/demo", fake_runner("not json"))
