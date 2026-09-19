import boto3

from conftest import make_workflow
from livectl.cli import main


def flags(targets):
    return ["--flow-arn", targets.flow_arn, "--channel-id", targets.channel_id]


def test_status_prints_states(aws, capsys):
    targets = make_workflow()

    code = main(["status", *flags(targets)])

    out = capsys.readouterr().out
    assert code == 0
    assert "flow:" in out and "STANDBY" in out
    assert "channel:" in out and "IDLE" in out


def test_start_then_stop_round_trip(aws, capsys):
    targets = make_workflow()

    assert main(["start", *flags(targets), "--interval", "0"]) == 0
    assert "channel RUNNING" in capsys.readouterr().out
    assert boto3.client("medialive").describe_channel(ChannelId=targets.channel_id)["State"] == "RUNNING"

    assert main(["stop", *flags(targets), "--interval", "0"]) == 0
    assert "flow STANDBY" in capsys.readouterr().out


def test_check_clean_fails_when_resources_exist_and_passes_when_gone(aws, capsys):
    targets = make_workflow("live-sports-aws-demo")

    assert main(["check-clean"]) == 1
    assert "MediaLive channel live-sports-aws-demo" in capsys.readouterr().out

    boto3.client("medialive").delete_channel(ChannelId=targets.channel_id)
    boto3.client("mediaconnect").delete_flow(FlowArn=targets.flow_arn)

    assert main(["check-clean"]) == 0
    assert "clean" in capsys.readouterr().out


def test_missing_targets_exit_with_code_2(aws, capsys):
    code = main(["status"], runner=lambda args: "{}")

    assert code == 2
    assert "could not determine" in capsys.readouterr().err
