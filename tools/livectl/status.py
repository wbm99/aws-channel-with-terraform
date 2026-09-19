"""Report the state of the live workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from livectl.control import channel_state, flow_status
from livectl.targets import Targets


def source_connected(cloudwatch, flow_arn: str, now: Optional[datetime] = None) -> Optional[bool]:
    """True/False from the latest SourceConnected datapoint, or None if there is no recent data."""
    end = now or datetime.now(timezone.utc)
    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/MediaConnect",
        MetricName="SourceConnected",
        Dimensions=[{"Name": "FlowARN", "Value": flow_arn}],
        StartTime=end - timedelta(minutes=5),
        EndTime=end + timedelta(minutes=1),
        Period=60,
        Statistics=["Maximum"],
    )
    points = sorted(response["Datapoints"], key=lambda point: point["Timestamp"])
    if not points:
        return None
    return points[-1]["Maximum"] >= 1


def get_status(mediaconnect, medialive, cloudwatch, targets: Targets) -> dict:
    ingest = f"srt://{targets.ingest_ip}:{targets.ingest_port}" if targets.ingest_ip else None
    return {
        "flow": flow_status(mediaconnect, targets.flow_arn),
        "channel": channel_state(medialive, targets.channel_id),
        "source_connected": source_connected(cloudwatch, targets.flow_arn),
        "ingest": ingest,
        "player": targets.player_url,
    }
