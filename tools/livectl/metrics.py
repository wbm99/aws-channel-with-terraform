"""Read every CloudWatch metric the console shows in a single GetMetricData call.

Metric names and dimensions were checked against the AWS documentation on 2026-09-26, except the CloudFront
dimension set (`DistributionId`, `Region=Global`), which is on the live checklist in the README.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from livectl.targets import Targets

PERIOD_SECONDS = 60
LOOKBACK = timedelta(minutes=5)


@dataclass(frozen=True)
class MetricSpec:
    id: str
    namespace: str
    name: str
    dimensions: tuple[tuple[str, str], ...]
    stat: str


def metric_specs(targets: Targets) -> list[MetricSpec]:
    flow = (("FlowARN", targets.flow_arn),)
    channel = (("ChannelId", targets.channel_id), ("Pipeline", "0"))
    specs = [
        MetricSpec("src_connected", "AWS/MediaConnect", "SourceConnected", flow, "Maximum"),
        MetricSpec("src_bitrate", "AWS/MediaConnect", "SourceBitRate", flow, "Average"),
        MetricSpec("src_rtt", "AWS/MediaConnect", "SourceRoundTripTime", flow, "Average"),
        MetricSpec("src_not_recovered", "AWS/MediaConnect", "SourceNotRecoveredPackets", flow, "Sum"),
        MetricSpec("src_cc_errors", "AWS/MediaConnect", "SourceContinuityCounter", flow, "Sum"),
        MetricSpec("ml_alerts", "AWS/MediaLive", "ActiveAlerts", channel, "Maximum"),
        MetricSpec("ml_fps", "AWS/MediaLive", "InputVideoFrameRate", channel, "Maximum"),
        MetricSpec("ml_input_loss", "AWS/MediaLive", "InputLossSeconds", channel, "Sum"),
    ]
    if targets.channel_group and targets.mediapackage_channel:
        package = (("ChannelGroup", targets.channel_group), ("Channel", targets.mediapackage_channel))
        specs += [
            MetricSpec("mp_ingress_bytes", "AWS/MediaPackage", "IngressBytes", package, "Sum"),
            MetricSpec("mp_egress_5xx", "AWS/MediaPackage", "EgressRequestCount", package + (("StatusCode", "5xx"),), "Sum"),
        ]
    if targets.distribution_id:
        cdn = (("DistributionId", targets.distribution_id), ("Region", "Global"))
        specs += [
            MetricSpec("cf_requests", "AWS/CloudFront", "Requests", cdn, "Sum"),
            MetricSpec("cf_5xx_rate", "AWS/CloudFront", "5xxErrorRate", cdn, "Average"),
        ]
    return specs


def fetch_metrics(cloudwatch, targets: Targets, now: datetime) -> dict[str, Optional[float]]:
    """The newest one-minute value of each metric, or None where CloudWatch has no datapoint."""
    specs = metric_specs(targets)
    response = cloudwatch.get_metric_data(
        MetricDataQueries=[
            {
                "Id": spec.id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": spec.namespace,
                        "MetricName": spec.name,
                        "Dimensions": [{"Name": name, "Value": value} for name, value in spec.dimensions],
                    },
                    "Period": PERIOD_SECONDS,
                    "Stat": spec.stat,
                },
                "ReturnData": True,
            }
            for spec in specs
        ],
        StartTime=now - LOOKBACK,
        EndTime=now,
        ScanBy="TimestampDescending",
    )
    values: dict[str, Optional[float]] = {spec.id: None for spec in specs}
    for result in response.get("MetricDataResults", []):
        if result.get("Values"):
            values[result["Id"]] = result["Values"][0]
    return values
