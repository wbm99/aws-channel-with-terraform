from datetime import datetime, timezone

from botocore.exceptions import ClientError

from livectl.manifest import ManifestCheck
from livectl.pipeline import BAD, OFF, OK, ORDER, UNKNOWN, WARN, Pipeline, denial
from livectl.targets import Targets
from stubs import CHANNEL_ID, FLOW_ARN, stub_aws

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
TARGETS = Targets(
    flow_arn=FLOW_ARN, channel_id=CHANNEL_ID, input_id="4412345", channel_group="live-sports-aws-demo",
    mediapackage_channel="live-sports-aws-demo", mediapackage_endpoint="live-sports-aws-demo-hls",
    distribution_id="E2EXAMPLE", manifest_url="https://d1.cloudfront.net/out/v1/x/index.m3u8",
)
LIVE_METRICS = {"src_connected": 1.0, "src_bitrate": 6_000_000.0, "src_not_recovered": 0.0,
                "src_cc_errors": 0.0, "ml_alerts": 0.0, "ml_fps": 30.0, "mp_ingress_bytes": 74_000_000.0}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def playing_fetch():
    """A CloudFront that serves a playlist whose sequence grows on every fetch of the variant."""
    state = {"seq": 100}

    def fetch(url):
        if url.endswith("index.m3u8"):
            return "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nindex_1.m3u8\n"
        state["seq"] += 1
        return f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{state['seq']}\n"

    return fetch


def make(clients, fetch=None, clock=None, source=None):
    return Pipeline(**clients, fetch=fetch or playing_fetch(), clock=clock or Clock(), now=lambda: NOW,
                    source_status=lambda: source)


def by_id(nodes):
    return {node.id: node for node in nodes}


def test_there_is_one_node_per_resource_in_chain_order():
    nodes = make(stub_aws()).nodes(TARGETS)

    assert [n.id for n in nodes] == list(ORDER)
    assert nodes[0].title == "MediaConnect Source (SRT)" and nodes[-1].title == "Player"


def test_off_air_everything_hourly_is_off():
    nodes = by_id(make(stub_aws()).nodes(TARGETS))

    assert nodes["mediaconnect_flow"].health == OFF
    assert nodes["medialive_channel"].health == OFF
    assert nodes["srt_source"].health == OFF
    assert nodes["player"].health == OFF
    assert nodes["medialive_input"].health == OK, "an attached input is healthy even while the channel is idle"
    assert nodes["cloudfront_cdn"].health == OK


def test_on_air_and_playing_is_ok_all_the_way_along():
    clock = Clock()
    pipeline = make(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=LIVE_METRICS), clock=clock)
    pipeline.nodes(TARGETS)
    clock.now += 5  # past the manifest TTL, so the second look sees the sequence move

    nodes = by_id(pipeline.nodes(TARGETS))

    assert {n.id: n.health for n in nodes.values()} == {i: OK for i in ORDER}
    assert "6.0 Mbps" in nodes["srt_source"].summary


def test_a_running_channel_without_a_source_is_bad_at_the_source():
    metrics = dict(LIVE_METRICS, src_connected=0.0)
    nodes = by_id(make(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=metrics)).nodes(TARGETS))

    assert nodes["srt_source"].health == BAD
    assert nodes["srt_source"].summary == "not connected"


def test_packet_loss_degrades_the_source():
    metrics = dict(LIVE_METRICS, src_not_recovered=12.0)
    nodes = by_id(make(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=metrics)).nodes(TARGETS))

    assert nodes["srt_source"].health == WARN


def test_an_active_alert_degrades_the_channel_and_is_listed():
    clients = stub_aws(flow="ACTIVE", channel="RUNNING", metrics=LIVE_METRICS,
                       alerts=(("InputLoss", "Input video lost"),))
    node = by_id(make(clients).nodes(TARGETS))["medialive_channel"]

    assert node.health == WARN
    assert node.details["alerts"] == ["InputLoss: Input video lost"]
    assert clients["medialive"].calls[-1] == ("list_alerts", {"ChannelId": CHANNEL_ID, "StateFilter": "SET"})


def test_one_failing_call_marks_one_node_unknown_and_the_rest_still_render():
    nodes = by_id(make(stub_aws(fail=("describe_input",))).nodes(TARGETS))

    assert nodes["medialive_input"].health == UNKNOWN
    assert "AccessDenied" in nodes["medialive_input"].error
    assert nodes["mediaconnect_flow"].health == OFF


def test_missing_outputs_make_their_nodes_unknown_with_a_useful_hint():
    minimal = Targets(flow_arn=FLOW_ARN, channel_id=CHANNEL_ID)
    nodes = by_id(make(stub_aws()).nodes(minimal))

    assert nodes["medialive_input"].health == UNKNOWN
    assert "Deploy stack" in nodes["medialive_input"].error


def test_aws_is_read_at_most_once_per_ttl_however_often_the_page_polls():
    clients, clock = stub_aws(), Clock()
    pipeline = make(clients, clock=clock)

    for _ in range(3):
        pipeline.nodes(TARGETS)
        clock.now += 1

    assert len(clients["mediaconnect"].calls) == 1
    assert len(clients["cloudwatch"].calls) == 1


def test_the_player_is_not_fetched_while_the_channel_is_idle():
    calls = []
    make(stub_aws(), fetch=lambda url: calls.append(url) or "").nodes(TARGETS)

    assert calls == []


def test_the_test_source_state_shows_on_the_source_node():
    nodes = by_id(make(stub_aws(), source={"state": "running"}).nodes(TARGETS))

    assert nodes["srt_source"].details["Test source (FFmpeg on this machine)"] == "running"


def test_nodes_serialise_to_plain_json_types():
    import json

    json.dumps([n.to_dict() for n in make(stub_aws()).nodes(TARGETS)])


# --- source state from MediaConnect events (live run, 2026-09-27: the metric lagged by minutes) -------------

import json  # noqa: E402

from stubs import StubClient  # noqa: E402

EVENTS_TARGETS = Targets(**{**TARGETS.__dict__, "events_log_group": "/aws/events/demo"})


def events_logs(*states):
    """A logs stub whose events group holds a flow start followed by Source Health events in these states."""
    now = int(NOW.timestamp() * 1000)

    def message(kind, detail):
        return json.dumps({"source": "aws.mediaconnect", "detail-type": kind, "detail": detail, "resources": [FLOW_ARN]})

    events = [(now - 60_000, message("MediaConnect Flow Status Change", {"currentStatus": "ACTIVE"}))]
    events += [(now - 30_000 + i, message("MediaConnect Source Health", {"current": {"state": s}}))
               for i, s in enumerate(states)]
    return StubClient(filter_log_events=lambda **request: {
        "events": [{"timestamp": t, "message": m, "logStreamName": "s"} for t, m in events]})


def with_logs(clients, logs):
    return Pipeline(**clients, logs=logs, fetch=playing_fetch(), clock=Clock(), now=lambda: NOW)


def test_a_disconnect_event_beats_a_metric_that_still_says_connected():
    node = by_id(with_logs(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=LIVE_METRICS),
                           events_logs("CONNECTED", "DISCONNECTED")).nodes(EVENTS_TARGETS))["srt_source"]

    assert (node.state, node.health, node.summary) == ("DISCONNECTED", BAD, "not connected")
    assert node.details["state from"].startswith("MediaConnect event")


def test_a_connect_event_beats_a_metric_that_still_says_disconnected():
    metrics = dict(LIVE_METRICS, src_connected=0.0)
    node = by_id(with_logs(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=metrics),
                           events_logs("DISCONNECTED", "CONNECTED")).nodes(EVENTS_TARGETS))["srt_source"]

    assert (node.state, node.health) == ("CONNECTED", OK)


def test_without_events_the_metric_still_decides():
    node = by_id(with_logs(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=LIVE_METRICS),
                           StubClient(filter_log_events={"events": []})).nodes(EVENTS_TARGETS))["srt_source"]

    assert node.state == "CONNECTED"
    assert node.details["state from"].startswith("CloudWatch")


def test_mediapackage_is_not_receiving_once_the_channel_stops_whatever_the_last_metric_says():
    """Live: after going off air it still read RECEIVING 6.9 Mbps from a datapoint minutes old."""
    node = by_id(make(stub_aws(flow="STANDBY", channel="IDLE", metrics=LIVE_METRICS)).nodes(TARGETS))["mediapackage_channel"]

    assert (node.state, node.health) == ("IDLE", OFF)
    assert "Mbps" not in node.summary


def test_a_disabled_distribution_is_off_not_an_error():
    """Terraform disables the distribution before deleting it; mid-teardown that is expected, not a fault."""
    clients = stub_aws()
    clients["cloudfront"] = StubClient(get_distribution={"Distribution": {
        "Id": "E2EXAMPLE", "Status": "InProgress", "DomainName": "d1.cloudfront.net",
        "DistributionConfig": {"Enabled": False}}})

    node = by_id(make(clients).nodes(TARGETS))["cloudfront_cdn"]

    assert (node.health, node.summary) == (OFF, "disabled")


def test_while_aws_is_unreachable_the_last_state_is_kept_and_the_problem_is_named():
    """Live: switching Wi-Fi off and on left the console answering nothing until a reload."""
    clients, clock = stub_aws(flow="ACTIVE", channel="RUNNING"), Clock()
    pipeline = make(clients, clock=clock)
    pipeline.nodes(TARGETS)
    assert pipeline.unreachable() is None

    down = ConnectionError("Could not connect to the endpoint URL")
    for client in clients.values():
        for operation in list(client._responses):
            client._responses[operation] = down
    clock.now += 70

    nodes = by_id(pipeline.nodes(TARGETS))

    assert nodes["mediaconnect_flow"].state == "ACTIVE", "the last known state, not a screen of unknowns"
    note = pipeline.unreachable()
    assert "Could not connect" in note and "70 s ago" in note


def test_without_a_source_mediapackage_and_the_player_say_they_carry_the_slate():
    """Live: 10.1 Mbps into MediaPackage with no input. True, but it is MediaLive's input-loss slate, not a picture."""
    clock = Clock()
    metrics = dict(LIVE_METRICS, src_connected=0.0)
    pipeline = make(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=metrics), clock=clock)
    pipeline.nodes(TARGETS)
    clock.now += 5

    nodes = by_id(pipeline.nodes(TARGETS))

    assert (nodes["mediapackage_channel"].health, nodes["mediapackage_channel"].summary) == (
        WARN, "receiving the input-loss slate · 9.9 Mbps")
    assert (nodes["player"].health, nodes["player"].summary) == (WARN, "playing the input-loss slate (no source)")


def client_error(code, operation):
    return ClientError({"Error": {"Code": code, "Message": "User: arn:aws:sts::1:assumed-role/x is not authorized"}},
                       operation)


def test_an_access_denied_names_the_iam_action():
    assert denial(client_error("AccessDeniedException", "DescribeChannel"), "medialive") == \
        "access denied: medialive:DescribeChannel"
    assert denial(client_error("AccessDenied", "GetDistribution"), "cloudfront") == \
        "access denied: cloudfront:GetDistribution"


def test_other_errors_are_not_denials():
    assert denial(client_error("ThrottlingException", "DescribeChannel"), "medialive") is None
    assert denial(RuntimeError("AccessDeniedException: stubbed failure"), "medialive") is None


def test_a_denied_channel_read_shows_the_action_on_the_node():
    clients = stub_aws()
    clients["medialive"]._responses["describe_channel"] = client_error("AccessDeniedException", "DescribeChannel")

    nodes = by_id(make(clients).nodes(TARGETS))

    assert nodes["medialive_channel"].health == UNKNOWN
    assert nodes["medialive_channel"].error == "access denied: medialive:DescribeChannel"
