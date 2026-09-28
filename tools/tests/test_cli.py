import boto3
import pytest

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


def test_ui_refuses_every_interface_without_container(capsys):
    with pytest.raises(SystemExit) as exit_:
        main(["ui", "--host", "0.0.0.0", "--no-browser"])

    assert exit_.value.code == 2
    assert "loopback" in capsys.readouterr().err


def test_ui_in_a_container_announces_the_host_url(aws, monkeypatch):
    served = {}
    monkeypatch.setattr("livectl.cli.serve", lambda console, **kwargs: served.update(kwargs))
    monkeypatch.setenv("CONSOLE_URL", "http://127.0.0.1:9000/")

    assert main(["ui", "--host", "0.0.0.0", "--container", "--no-browser"]) == 0

    assert served["host"] == "0.0.0.0" and served["container"] is True
    assert served["url"] == "http://127.0.0.1:9000/"


def test_ui_outside_a_container_ignores_the_container_url(aws, monkeypatch):
    served = {}
    monkeypatch.setattr("livectl.cli.serve", lambda console, **kwargs: served.update(kwargs))
    monkeypatch.setenv("CONSOLE_URL", "http://127.0.0.1:9000/")

    assert main(["ui", "--no-browser"]) == 0

    assert served["container"] is False and served["url"] is None


def test_status_prints_the_identity_first(aws, capsys):
    targets = make_workflow()

    assert main(["status", *flags(targets)]) == 0

    assert capsys.readouterr().out.splitlines()[0].startswith("acting as user moto · account 123456789012")


def test_check_clean_without_credentials_exits_2(monkeypatch, capsys):
    from stubs import sts_client

    real_session = boto3.Session

    class NoCredentials:
        def __init__(self, **kwargs):
            self._session = real_session(**kwargs)
            self.profile_name = None

        def client(self, name, **kwargs):
            return sts_client("no-credentials") if name == "sts" else self._session.client(name, **kwargs)

    monkeypatch.setattr("livectl.cli.boto3.Session", NoCredentials)

    assert main(["check-clean"]) == 2
    assert "No AWS credentials found" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [["status", "--flow-arn", "x", "--channel-id", "1"], ["check-clean"],
                                  ["ui", "--no-browser"]])
def test_a_missing_profile_is_one_clear_line(argv, monkeypatch, capsys):
    monkeypatch.setenv("AWS_PROFILE", "nope")
    monkeypatch.setattr("livectl.cli.serve", lambda console, **kwargs: None)

    assert main(argv) == 2

    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1
    assert "'nope'" in err and "~/.aws/config" in err
