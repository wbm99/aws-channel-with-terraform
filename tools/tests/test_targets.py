import json

import pytest

from livectl.targets import NotDeployed, TargetError, resolve_targets

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


FULL_OUTPUTS = {
    "flow_arn": {"value": "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:demo"},
    "medialive_channel_id": {"value": "7387208"},
    "medialive_channel_arn": {"value": "arn:aws:medialive:us-east-1:123456789012:channel:7387208"},
    "medialive_input_id": {"value": "4412345"},
    "mediapackage_channel_group": {"value": "live-sports-aws-demo"},
    "mediapackage_channel": {"value": "live-sports-aws-demo"},
    "mediapackage_endpoint": {"value": "live-sports-aws-demo-hls"},
    "distribution_id": {"value": "E2EXAMPLE"},
    "cdn_manifest_url": {"value": "https://d1.cloudfront.net/out/v1/x/index.m3u8"},
    "passphrase_secret_arn": {"value": "arn:aws:secretsmanager:us-east-1:123456789012:secret:srt-AbC"},
}


def test_every_probe_id_is_read_from_the_outputs():
    targets = resolve_targets(None, None, "envs/demo", lambda args: json.dumps(FULL_OUTPUTS))

    assert targets.input_id == "4412345"
    assert targets.channel_arn.endswith(":channel:7387208")
    assert (targets.channel_group, targets.mediapackage_channel, targets.mediapackage_endpoint) == (
        "live-sports-aws-demo", "live-sports-aws-demo", "live-sports-aws-demo-hls")
    assert targets.distribution_id == "E2EXAMPLE"
    assert targets.manifest_url.endswith("index.m3u8")
    assert targets.passphrase_secret_arn.endswith("srt-AbC")


def test_older_state_without_the_new_outputs_still_resolves():
    old = {k: FULL_OUTPUTS[k] for k in ("flow_arn", "medialive_channel_id")}

    targets = resolve_targets(None, None, "envs/demo", lambda args: json.dumps(old))

    assert targets.input_id is None and targets.distribution_id is None


def test_an_empty_stack_is_reported_as_not_deployed():
    with pytest.raises(NotDeployed):
        resolve_targets(None, None, "envs/demo", lambda args: "{}")


def test_not_deployed_is_still_a_target_error_for_the_cli():
    assert issubclass(NotDeployed, TargetError)
