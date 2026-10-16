"""Read every CloudWatch metric the console shows in a single GetMetricData call.

Metric names and dimensions were checked against the AWS documentation on 2026-09-26, except the CloudFront
dimension set (`DistributionId`, `Region=Global`), which is on the live checklist in the README.

With source failover, each SRT listener also gets its own queries on the `SourceARN` dimension, named after its role
(`primary_connected`, `backup_selected`, ...). `SourceARN` was seen live on 2026-10-16; `SourceSelected` on SRT
listeners not yet. Averages over the flow (`src_bitrate`, `src_rtt`) then mix in the idle listener's zeros (a 2000 ms
latency read 1000 ms), so the source node shows the on-air listener's own figures instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

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


def metric_specs(targets: Targets, sources: Sequence[tuple[str, str]] = ()) -> list[MetricSpec]:
    """The queries for targets, plus four per (role, source ARN) in sources."""
    flow = (("FlowARN", targets.flow_arn),)
    channel = (("ChannelId", targets.channel_id), ("Pipeline", "0"))
    specs = [
        MetricSpec("src_connected", "AWS/MediaConnect", "SourceConnected", flow, "Maximum"),
        MetricSpec("src_bitrate", "AWS/MediaConnect", "SourceBitRate", flow, "Average"),
        MetricSpec("src_rtt", "AWS/MediaConnect", "SourceRoundTripTime", flow, "Average"),
        MetricSpec("src_not_recovered", "AWS/MediaConnect", "SourceNotRecoveredPackets", flow, "Sum"),
        MetricSpec("src_cc_errors", "AWS/MediaConnect", "SourceContinuityCounter", flow, "Sum"),
    ]
    for role, arn in sources:
        source = (("SourceARN", arn),)
        specs += [
            MetricSpec(f"{role}_connected", "AWS/MediaConnect", "SourceConnected", source, "Maximum"),
            MetricSpec(f"{role}_bitrate", "AWS/MediaConnect", "SourceBitRate", source, "Average"),
            MetricSpec(f"{role}_rtt", "AWS/MediaConnect", "SourceRoundTripTime", source, "Average"),
            # 1 while the flow takes its content from this source. Averaged, a minute that saw a switch reads
            # between 0 and 1 on both, and the higher one held the air longer.
            MetricSpec(f"{role}_selected", "AWS/MediaConnect", "SourceSelected", source, "Average"),
        ]
    specs += [
        MetricSpec("ml_alerts", "AWS/MediaLive", "ActiveAlerts", channel, "Maximum"),
        MetricSpec("ml_fps", "AWS/MediaLive", "InputVideoFrameRate", channel, "Maximum"),
        MetricSpec("ml_input_loss", "AWS/MediaLive", "InputLossSeconds", channel, "Sum"),
    ]
    if targets.channel_group and targets.mediapackage_channel:
        package = (("ChannelGroup", targets.channel_group), ("Channel", targets.mediapackage_channel))
        specs += [
            MetricSpec("mp_ingress_bytes", "AWS/MediaPackage", "IngressBytes", package, "Sum"),
            MetricSpec("mp_egress_5xx", "AWS/MediaPackage", "EgressRequestCount", package + (("StatusCode", "5xx"),), "Sum"),
            MetricSpec("mp_egress_bytes", "AWS/MediaPackage", "EgressBytes", package, "Sum"),
            MetricSpec("mp_egress_requests", "AWS/MediaPackage", "EgressRequestCount", package, "Sum"),
        ]
    if targets.distribution_id:
        cdn = (("DistributionId", targets.distribution_id), ("Region", "Global"))
        specs += [
            MetricSpec("cf_requests", "AWS/CloudFront", "Requests", cdn, "Sum"),
            MetricSpec("cf_5xx_rate", "AWS/CloudFront", "5xxErrorRate", cdn, "Average"),
            MetricSpec("cf_4xx_rate", "AWS/CloudFront", "4xxErrorRate", cdn, "Average"),
            MetricSpec("cf_bytes_downloaded", "AWS/CloudFront", "BytesDownloaded", cdn, "Sum"),
        ]
    return specs


def fetch_metrics(cloudwatch, targets: Targets, now: datetime,
                  sources: Sequence[tuple[str, str]] = ()) -> dict[str, Optional[float]]:
    """The newest one-minute value of each metric, or None where CloudWatch has no datapoint."""
    specs = metric_specs(targets, sources)
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
