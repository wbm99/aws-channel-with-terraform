from datetime import datetime, timezone

import boto3

from conftest import make_workflow
from livectl.status import get_status, source_connected
from livectl.targets import Targets


def test_status_reports_states_and_endpoints(aws):
    base = make_workflow()
    targets = Targets(
        flow_arn=base.flow_arn,
        channel_id=base.channel_id,
        player_url="https://example.cloudfront.net/",
        ingest_ip="203.0.113.20",
        ingest_port=5000,
    )

    status = get_status(boto3.client("mediaconnect"), boto3.client("medialive"), boto3.client("cloudwatch"), targets)

    assert status["flow"] == "STANDBY"
    assert status["channel"] == "IDLE"
    assert status["source_connected"] is None
    assert status["ingest"] == "srt://203.0.113.20:5000"
    assert status["player"] == "https://example.cloudfront.net/"


def test_source_connected_reads_the_latest_datapoint(aws):
    targets = make_workflow()
    cloudwatch = boto3.client("cloudwatch")
    now = datetime.now(timezone.utc)
    cloudwatch.put_metric_data(
        Namespace="AWS/MediaConnect",
        MetricData=[
            {
                "MetricName": "SourceConnected",
                "Dimensions": [{"Name": "FlowARN", "Value": targets.flow_arn}],
                "Timestamp": now,
                "Value": 1.0,
            }
        ],
    )

    assert source_connected(cloudwatch, targets.flow_arn, now=now) is True
