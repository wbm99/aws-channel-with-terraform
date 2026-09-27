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
    # Stream names cannot contain ":", so MediaLive writes the ARN with underscores (seen live, 2026-09-27).
    put(logs, "ElementalMediaLive", CHANNEL.replace(":", "_") + "_0_as_run", ["Switched to input mediaconnect-srt"])

    lines = medialive_lines(LogReader(logs), CHANNEL, T - 1)

    assert lines[0].text.endswith("as-run · Switched to input mediaconnect-srt")


def test_the_medialive_stream_prefix_is_the_arn_with_underscores():
    calls = []

    class Logs:
        def filter_log_events(self, **request):
            calls.append(request)
            return {"events": []}

    medialive_lines(LogReader(Logs()), CHANNEL, T)

    assert calls[0]["logStreamNamePrefix"] == "arn_aws_medialive_us-east-1_123456789012_channel_7387208"


# Event shapes below were captured from a live run on 2026-09-27.

def text_of(source, kind, detail, resources=(FLOW,)):
    return format_event(event(source, kind, detail, resources), T).text.split(" ", 1)[1]


def test_a_mediaconnect_alert_reads_as_its_code_and_message():
    detail = {"error-code": "SourceStreamError", "error-id": "Qta", "errored": True,
              "error-message": "Source live-sports-aws-demo-srt Stream Error: Timeout. Please investigate the flow source."}

    assert text_of("aws.mediaconnect", "MediaConnect Alert", detail) == (
        "MediaConnect · alert SourceStreamError: Source live-sports-aws-demo-srt Stream Error: Timeout. "
        "Please investigate the flow source.")


def test_a_cleared_mediaconnect_alert_says_so():
    detail = {"error-code": "SourceStreamError", "error-message": "Timeout.", "errored": False}

    assert text_of("aws.mediaconnect", "MediaConnect Alert", detail) == "MediaConnect · alert cleared SourceStreamError"


def test_flow_health_reads_as_a_state_with_the_zero_bitrate_flag():
    detail = {"current": {"isBitrateZero": True, "state": "DISCONNECTED", "tr101": {"pcr_error": False}},
              "previous": {"state": ""}, "unhealthy": True}

    line = format_event(event("aws.mediaconnect", "MediaConnect Flow Health", detail), T)

    assert (line.tab, line.text.split(" ", 1)[1]) == ("mediaconnect", "MediaConnect · flow disconnected, no bitrate")


def test_output_health_reads_as_a_state():
    assert text_of("aws.mediaconnect", "MediaConnect Output Health",
                   {"current": {"state": "SENDING"}, "previous": {"state": "IDLE"}}) == "MediaConnect · output sending"


def test_metadata_and_content_quality_events_are_short():
    assert text_of("aws.mediaconnect", "MediaConnect Flow Source Metadata Changed",
                   {"metadataChangeTime": "2026-09-27T00:49:11Z"}) == "MediaConnect · source metadata changed"
    assert text_of("aws.mediaconnect", "MediaConnect Flow Content Quality",
                   {"streams": [{}, {}]}) == "MediaConnect · content quality report (2 streams)"


def test_medialive_alerts_say_raised_or_cleared():
    detail = {"alarm_state": "SET", "alert_type": "Stopped Receiving UDP Input", "pipeline": "0",
              "message": "Stopped receiving network data on [mediaconnect-srt]"}

    assert text_of("aws.medialive", "MediaLive Channel Alert", detail, (CHANNEL,)) == (
        "MediaLive · alert raised: Stopped Receiving UDP Input (Stopped receiving network data on [mediaconnect-srt])")
    detail["alarm_state"] = "CLEARED"
    assert text_of("aws.medialive", "MediaLive Channel Alert", detail, (CHANNEL,)) == (
        "MediaLive · alert cleared: Stopped Receiving UDP Input")


def test_an_input_change_names_the_input():
    detail = {"pipeline": "0", "message": "Input switch event on pipeline",
              "active_input_attachment_name": "mediaconnect-srt"}

    assert text_of("aws.medialive", "MediaLive Channel Input Change", detail, (CHANNEL,)) == (
        "MediaLive · input switched to mediaconnect-srt")


# --- the SRT source's state from events, not from a metric minutes behind ------------------------

from livectl.logs import SOURCE_PATTERN, latest_source_state  # noqa: E402

OTHER_FLOW = "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-zzz:someone-else"


def health(state, resources=(FLOW,)):
    return event("aws.mediaconnect", "MediaConnect Source Health",
                 {"current": {"state": state}, "unhealthy": state != "CONNECTED"}, resources)


def flow_change(current):
    return event("aws.mediaconnect", "MediaConnect Flow Status Change",
                 {"previousStatus": "STARTING", "currentStatus": current})


def test_the_newest_source_health_event_gives_the_state():
    events = [(T, flow_change("ACTIVE")), (T + 10, health("CONNECTED")), (T + 20, health("DISCONNECTED"))]

    assert latest_source_state(events, FLOW) == ("DISCONNECTED", T + 20)


def test_events_from_before_the_flow_last_started_do_not_count():
    """A CONNECTED from yesterday's session must not make today's empty ingest look connected."""
    events = [(T, health("CONNECTED")), (T + 50, flow_change("ACTIVE"))]

    assert latest_source_state(events, FLOW) is None


def test_other_flows_and_order_of_arrival_do_not_matter():
    events = [(T + 20, health("DISCONNECTED", (OTHER_FLOW,))), (T + 10, health("CONNECTED")), (T, flow_change("ACTIVE"))]

    assert latest_source_state(events, FLOW) == ("CONNECTED", T + 10)


def test_no_events_means_no_answer():
    assert latest_source_state([], FLOW) is None


def test_the_reader_passes_the_filter_pattern_to_cloudwatch():
    calls = []

    class Logs:
        def filter_log_events(self, **request):
            calls.append(request)
            return {"events": []}

    LogReader(Logs()).events("/aws/events/demo", after_ms=T, pattern=SOURCE_PATTERN)

    assert calls[0]["filterPattern"] == '?"MediaConnect Source Health" ?"MediaConnect Flow Status Change"'
