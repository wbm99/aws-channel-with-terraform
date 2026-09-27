import json

import boto3

from livectl.logs import LogReader, event_lines, format_event, is_ours, medialive_lines
from livectl.targets import Targets

FLOW = "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:live-sports-aws-demo"
CHANNEL = "arn:aws:medialive:us-east-1:123456789012:channel:7387208"
TARGETS = Targets(flow_arn=FLOW, channel_id="7387208", channel_arn=CHANNEL, events_log_group="/aws/events/demo")
T = 1_790_000_000_000  # ms


def event(source, kind, detail, resources=(FLOW,)):
    return json.dumps({"source": source, "detail-type": kind, "detail": detail, "resources": list(resources)})


def test_flow_status_change_reads_as_a_transition():
    line = format_event(event("aws.mediaconnect", "MediaConnect Flow Status Change",
                              {"previousStatus": "STANDBY", "currentStatus": "ACTIVE"}), T)

    assert line.tab == "mediaconnect"
    assert line.text.endswith("MediaConnect · flow STANDBY → ACTIVE")


def test_source_health_goes_to_the_srt_tab_with_its_tr_101_290_flags():
    detail = {"unhealthy": True, "current": {"state": "CONNECTED", "tr101": {
        "ts_sync_loss": False, "continuity_count_error": True, "pcr_error": True}}}

    line = format_event(event("aws.mediaconnect", "MediaConnect Source Health", detail), T)

    assert line.tab == "srt"
    assert "source connected" in line.text
    assert "TR 101 290: continuity_count_error, pcr_error" in line.text


def test_medialive_state_change():
    line = format_event(event("aws.medialive", "MediaLive Channel State Change", {"state": "RUNNING"},
                              (CHANNEL,)), T)

    assert (line.tab, line.text.split(" ", 1)[1]) == ("medialive", "MediaLive · channel RUNNING")


def test_an_unknown_event_type_is_summarised_not_dropped():
    line = format_event(event("aws.medialive", "MediaLive Something New", {"x": 1}, (CHANNEL,)), T)

    assert "Something New" in line.text and '"x": 1' in line.text
    assert line.raw is not None


def test_non_json_and_foreign_sources_are_ignored():
    assert format_event("not json", T) is None
    assert format_event(event("aws.s3", "Object Created", {}), T) is None


def test_only_events_about_this_pipeline_are_kept():
    assert is_ours(event("aws.mediaconnect", "MediaConnect Alert", {}, (FLOW + ":source:x",)), TARGETS)
    assert not is_ours(event("aws.mediaconnect", "MediaConnect Alert", {}, ("arn:aws:mediaconnect:::flow:other",)),
                       TARGETS)


def put(logs, group, stream, messages):
    logs.create_log_group(logGroupName=group)
    logs.create_log_stream(logGroupName=group, logStreamName=stream)
    logs.put_log_events(logGroupName=group, logStreamName=stream,
                        logEvents=[{"timestamp": T + i, "message": m} for i, m in enumerate(messages)])


def test_event_lines_filter_by_tab_and_come_back_in_time_order(aws):
    logs = boto3.client("logs")
    put(logs, "/aws/events/demo", "s", [
        event("aws.medialive", "MediaLive Channel State Change", {"state": "STARTING"}, (CHANNEL,)),
        event("aws.mediaconnect", "MediaConnect Flow Status Change",
              {"previousStatus": "STANDBY", "currentStatus": "ACTIVE"}),
    ])
    reader = LogReader(logs)

    assert [l.tab for l in event_lines(reader, TARGETS, "all", T - 1)] == ["medialive", "mediaconnect"]
    assert [l.tab for l in event_lines(reader, TARGETS, "medialive", T - 1)] == ["medialive"]
    assert event_lines(reader, TARGETS, "all", T + 5) == [], "only events after the cursor"


def test_medialive_logs_are_labelled_encoder_or_as_run(aws):
    logs = boto3.client("logs")
    put(logs, "ElementalMediaLive", CHANNEL + "_0_as_run", ["Switched to input mediaconnect-srt"])

    lines = medialive_lines(LogReader(logs), CHANNEL, T - 1)

    assert lines[0].text.endswith("as-run · Switched to input mediaconnect-srt")
