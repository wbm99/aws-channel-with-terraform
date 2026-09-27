from datetime import datetime, timezone

from livectl.manifest import ManifestCheck
from livectl.pipeline import BAD, OFF, OK, ORDER, UNKNOWN, WARN, Pipeline
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
    assert nodes[0].title == "SRT Input Source" and nodes[-1].title == "Player"


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

    assert nodes["srt_source"].details["test source"] == "running"


def test_nodes_serialise_to_plain_json_types():
    import json

    json.dumps([n.to_dict() for n in make(stub_aws()).nodes(TARGETS)])
