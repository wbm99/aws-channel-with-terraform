import boto3
import pytest
from moto import mock_aws

from livectl.targets import Targets

ROLE_ARN = "arn:aws:iam::123456789012:role/test-role"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def aws():
    with mock_aws():
        yield


def make_workflow(name: str = "live-sports-aws-demo") -> Targets:
    """Create a MediaConnect flow and a MediaLive channel in moto and return their IDs."""
    mediaconnect = boto3.client("mediaconnect")
    medialive = boto3.client("medialive")
    flow = mediaconnect.create_flow(
        Name=name,
        Source={
            "Name": f"{name}-srt",
            "Protocol": "srt-listener",
            "IngestPort": 5000,
            "WhitelistCidr": "203.0.113.10/32",
        },
    )
    channel = medialive.create_channel(
        Name=name,
        ChannelClass="SINGLE_PIPELINE",
        RoleArn=ROLE_ARN,
        InputSpecification={"Codec": "AVC", "Resolution": "HD", "MaximumBitrate": "MAX_10_MBPS"},
        Destinations=[],
        EncoderSettings={
            "AudioDescriptions": [],
            "OutputGroups": [],
            "TimecodeConfig": {"Source": "EMBEDDED"},
            "VideoDescriptions": [],
        },
    )
    return Targets(flow_arn=flow["Flow"]["FlowArn"], channel_id=channel["Channel"]["Id"])
