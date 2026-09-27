from datetime import datetime, timezone

from livectl.metrics import fetch_metrics, metric_specs
from livectl.targets import Targets
from stubs import CHANNEL_ID, FLOW_ARN, StubClient, metric_response

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
MINIMAL = Targets(flow_arn=FLOW_ARN, channel_id=CHANNEL_ID)
FULL = Targets(flow_arn=FLOW_ARN, channel_id=CHANNEL_ID, channel_group="g", mediapackage_channel="c",
               distribution_id="E2EXAMPLE")


def dims(spec):
    return dict(spec.dimensions)


def test_source_metrics_are_keyed_by_the_flow_arn():
    by_id = {s.id: s for s in metric_specs(MINIMAL)}

    assert by_id["src_connected"].name == "SourceConnected"
    assert by_id["src_connected"].namespace == "AWS/MediaConnect"
    assert dims(by_id["src_connected"]) == {"FlowARN": FLOW_ARN}


def test_channel_metrics_use_channel_id_and_pipeline_zero():
    spec = {s.id: s for s in metric_specs(MINIMAL)}["ml_alerts"]

    assert (spec.namespace, spec.name) == ("AWS/MediaLive", "ActiveAlerts")
    assert dims(spec) == {"ChannelId": CHANNEL_ID, "Pipeline": "0"}


def test_package_and_cdn_metrics_need_their_ids():
    assert not any(s.id.startswith(("mp_", "cf_")) for s in metric_specs(MINIMAL))

    by_id = {s.id: s for s in metric_specs(FULL)}
    assert dims(by_id["mp_ingress_bytes"]) == {"ChannelGroup": "g", "Channel": "c"}
    assert dims(by_id["mp_egress_5xx"])["StatusCode"] == "5xx"
    assert dims(by_id["cf_requests"]) == {"DistributionId": "E2EXAMPLE", "Region": "Global"}


def test_one_call_returns_the_newest_value_per_metric():
    cloudwatch = StubClient(get_metric_data=metric_response({"src_connected": 1.0, "ml_alerts": None}))

    values = fetch_metrics(cloudwatch, MINIMAL, NOW)

    assert len(cloudwatch.calls) == 1
    request = cloudwatch.calls[0][1]
    assert request["ScanBy"] == "TimestampDescending"
    assert request["EndTime"] == NOW
    assert values["src_connected"] == 1.0
    assert values["ml_alerts"] is None
    assert values["src_bitrate"] is None, "a metric missing from the response reads as no data"


def test_delivery_figures_are_read_for_the_cdn_and_mediapackage_tabs():
    by_id = {s.id: s for s in metric_specs(FULL)}

    assert (by_id["cf_bytes_downloaded"].name, by_id["cf_bytes_downloaded"].stat) == ("BytesDownloaded", "Sum")
    assert by_id["cf_4xx_rate"].name == "4xxErrorRate"
    assert (by_id["mp_egress_bytes"].name, dims(by_id["mp_egress_bytes"])) == (
        "EgressBytes", {"ChannelGroup": "g", "Channel": "c"})
    assert by_id["mp_egress_requests"].name == "EgressRequestCount"
