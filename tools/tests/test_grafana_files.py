"""The Grafana provisioning and compose files, checked offline: what the dashboard queries, and what guards access and
cost. Whether Grafana accepts the files is test_grafana_container.py's job."""

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAFANA = ROOT / "observability" / "grafana"
PROVISIONING = GRAFANA / "provisioning"
# Every MediaConnect metric the dashboards read, with its statistic.
METRICS = {
    "SourceConnected": "Maximum", "SourceUpTime": "Maximum", "SourceDisconnections": "Sum",
    "SourceBitRate": "Average", "SourceRoundTripTime": "Average", "SourcePacketLossPercent": "Average",
    "SourceNotRecoveredPackets": "Sum",
    "SourceContinuityCounter": "Sum", "SourcePATError": "Sum", "SourcePMTError": "Sum", "SourcePIDError": "Sum",
    "SourceTSSyncLoss": "Sum", "SourceTSByteError": "Sum",
    "SourceDroppedPackets": "Sum", "SourceRecoveredPackets": "Sum", "SourceARQRequests": "Sum",
    "SourceARQRecovered": "Sum", "SourceJitter": "Average", "SourceLatency": "Average",
    "SourcePCRError": "Sum", "SourcePCRAccuracyError": "Sum", "SourcePTSError": "Sum", "SourceCRCError": "Sum",
}
OVERVIEW, DETAIL = "mediaconnect-source", "mediaconnect-detail"
EVENTS_GROUP = "/aws/events/live-sports-aws-demo"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def dashboard(uid: str = OVERVIEW) -> dict:
    return json.loads((GRAFANA / "dashboards" / f"{uid}.json").read_text())


def panels(uid: str = OVERVIEW) -> list:
    """Every panel, rows' nested panels included, in order."""
    out = []
    for panel in dashboard(uid)["panels"]:
        out.append(panel)
        out.extend(panel.get("panels", []))
    return out


def panel(title: str, uid: str = OVERVIEW, kind: str = None) -> dict:
    return next(p for p in panels(uid) if p["title"] == title and (kind is None or p["type"] == kind))


def metric_targets(uid: str) -> list:
    return [t for p in panels(uid) for t in p.get("targets", []) if t.get("queryMode") == "Metrics"]


def names(p: dict) -> dict:
    """refId -> the display name an override gives it."""
    return {o["matcher"]["options"]: next(x["value"] for x in o["properties"] if x["id"] == "displayName")
            for o in p["fieldConfig"]["overrides"]}


def test_the_two_dashboards_identity_refresh_and_links():
    for uid, title, other in [(OVERVIEW, "MediaConnect source", DETAIL),
                              (DETAIL, "MediaConnect source: transport stream & SRT", OVERVIEW)]:
        d = dashboard(uid)
        assert (d["uid"], d["title"], d["refresh"], d["timezone"]) == (uid, title, "5s", "utc")
        assert d["time"] == {"from": "now-15m", "to": "now"}
        assert [(link["url"], link["keepTime"]) for link in d["links"]] == [
            (f"/d/{other}", True), ("/d/medialive-channel", True), ("/d/mediapackage-origin", True),
            ("/d/cloudfront-distribution", True)]


def test_every_metric_query_searches_this_projects_flows_every_5_seconds():
    # REMOVE_EMPTY: every deploy's flow has the same name, and the search returns the destroyed ones of the last two
    # weeks too. Without it each tile splits into one box per flow and the legends repeat the name.
    for uid in (OVERVIEW, DETAIL):
        for target in metric_targets(uid):
            metric = target["metricName"]
            assert target["datasource"]["uid"] == "cloudwatch"
            assert METRICS[metric] == target["statistic"]
            assert target["expression"] == ("REMOVE_EMPTY(SEARCH('{AWS/MediaConnect,FlowARN} MetricName=\"%s\" "
                                            "live-sports-aws-demo', '%s', 5))" % (metric, target["statistic"]))
            assert target["period"] == "5"


def test_together_the_dashboards_read_every_metric_once_each():
    # Each query is billed. Only the overview's four "now" tiles repeat a graph's metric (a tile reads the last
    # minutes, the graph the whole range); the Disconnections tile and the Priority 1 graph reuse other panels' data.
    read = [t["metricName"] for uid in (OVERVIEW, DETAIL) for t in metric_targets(uid)]
    assert set(read) == set(METRICS)
    twice = sorted({m for m in read if read.count(m) > 1})
    assert twice == ["SourceBitRate", "SourceNotRecoveredPackets", "SourcePacketLossPercent", "SourceRoundTripTime"]
    assert len(read) == len(METRICS) + 4
    assert (len(metric_targets(OVERVIEW)), len(metric_targets(DETAIL))) == (11, 16)   # detail: 12 with P2 closed


def test_the_overview_panels():
    assert [(p["type"], p["title"]) for p in panels(OVERVIEW)] == [
        ("stat", "Connected"), ("stat", "Up for"), ("stat", "Bitrate"), ("stat", "Round trip"),
        ("stat", "Packet loss"), ("stat", "Not recovered, last 5 min"), ("stat", "Disconnections"),
        ("timeseries", "Bitrate"), ("timeseries", "Round trip time"), ("timeseries", "Loss"),
        ("table", "Source events")]


def test_the_detail_panels():
    assert [(p["type"], p["title"]) for p in panels(DETAIL)] == [
        ("stat", "TR 101 290 Priority 1, errors in range"), ("timeseries", "TR 101 290 Priority 1"),
        ("timeseries", "SRT recovery"), ("timeseries", "Network"), ("table", "Source events"),
        ("row", "TR 101 290 Priority 2 (encoder timing; queried only when open)"),
        ("timeseries", "TR 101 290 Priority 2")]


def test_priority_2_sits_in_a_collapsed_row_so_it_costs_nothing_until_opened():
    row = next(p for p in dashboard(DETAIL)["panels"] if p["type"] == "row")
    assert row["collapsed"] is True
    assert [t["metricName"] for t in row["panels"][0]["targets"]] == [
        "SourcePCRError", "SourcePCRAccuracyError", "SourcePTSError", "SourceCRCError"]


def test_the_now_tiles_read_only_the_last_2_minutes_and_show_just_the_value():
    # Over the whole range, lastNotNull would keep a stopped flow "connected" for up to 15 minutes. 2 minutes, not
    # 1, leaves room for CloudWatch's publishing delay (unmeasured; live check 20).
    for title in ("Connected", "Up for", "Bitrate", "Round trip", "Packet loss"):
        tile = panel(title, kind="stat")
        assert (tile["timeFrom"], tile["hideTimeOverride"]) == ("2m", True), title
        assert tile["options"]["textMode"] == "value"


def test_tiles_say_nothing_is_fine_when_there_is_no_data():
    connected = panel("Connected")["fieldConfig"]["defaults"]
    assert connected["noValue"] == "no source" and connected["thresholds"]["steps"][0]["color"] == "text"
    for title in ("Up for", "Bitrate", "Round trip", "Packet loss"):
        assert panel(title, kind="stat")["options"]["colorMode"] == "none", title


def test_connected_maps_1_and_0_to_words():
    mappings = panel("Connected")["fieldConfig"]["defaults"]["mappings"][0]["options"]
    assert mappings["1"]["text"] == "connected" and mappings["1"]["color"] == "green"
    assert mappings["0"]["text"] == "disconnected" and mappings["0"]["color"] == "red"


def test_counters_turn_red_above_0():
    for uid, title in [(OVERVIEW, "Not recovered, last 5 min"), (OVERVIEW, "Disconnections"),
                       (DETAIL, "TR 101 290 Priority 1, errors in range")]:
        tile = panel(title, uid)
        assert tile["options"]["reduceOptions"]["calcs"] == ["sum"] and tile["options"]["colorMode"] == "background"
        steps = tile["fieldConfig"]["defaults"]["thresholds"]["steps"]
        assert [(s["value"], s["color"]) for s in steps] == [(None, "green"), (1, "red")], title
    assert panel("Not recovered, last 5 min")["timeFrom"] == "5m"


def test_disconnections_reuse_the_bitrate_graphs_query_and_show_as_red_bars_on_it():
    graph = panel("Bitrate", kind="timeseries")
    tile = panel("Disconnections")
    assert tile["datasource"]["uid"] == "-- Dashboard --"
    assert tile["targets"][0]["panelId"] == graph["id"]
    assert {"id": "filterByRefId", "options": {"include": "B"}} in tile["transformations"]
    assert names(graph) == {"A": "Bitrate", "B": "Disconnections"}
    bars = next(o for o in graph["fieldConfig"]["overrides"] if o["matcher"]["options"] == "B")["properties"]
    assert {"id": "custom.drawStyle", "value": "bars"} in bars
    assert {"id": "custom.axisPlacement", "value": "right"} in bars


def test_the_priority_1_graph_reuses_the_tiles_query():
    tiles = panel("TR 101 290 Priority 1, errors in range", DETAIL)
    graph = panel("TR 101 290 Priority 1", DETAIL)
    assert graph["datasource"]["uid"] == "-- Dashboard --" and graph["targets"][0]["panelId"] == tiles["id"]
    expected = {"A": "Continuity", "B": "PAT", "C": "PMT", "D": "PID", "E": "TS sync loss", "F": "TS byte"}
    assert names(tiles) == expected and names(graph) == expected
    assert tiles["options"]["textMode"] == "value_and_name"      # six boxes: each needs its name


def test_graphs_name_their_series():
    assert names(panel("Loss")) == {"A": "Packet loss", "B": "Not recovered"}
    assert names(panel("SRT recovery", DETAIL)) == {"A": "Dropped", "B": "Recovered", "C": "ARQ requested",
                                                     "D": "ARQ recovered"}
    assert names(panel("Network", DETAIL)) == {"A": "Jitter", "B": "Latency"}
    assert names(panel("TR 101 290 Priority 2", DETAIL)) == {"A": "PCR", "B": "PCR accuracy", "C": "PTS", "D": "CRC"}


def test_legends_show_the_flow_name_not_the_arn():
    for uid in (OVERVIEW, DETAIL):
        for p in panels(uid):
            if p["type"] in ("stat", "timeseries"):
                assert {"id": "renameByRegex", "options": {"regex": ".*:([^:]+)$", "renamePattern": "$1"}} in p[
                    "transformations"], p["title"]


def test_units():
    assert [panel(t, kind="stat")["fieldConfig"]["defaults"].get("unit") for t in
            ("Connected", "Up for", "Bitrate", "Round trip", "Packet loss")] == [None, "s", "bps", "ms", "percent"]
    assert panel("Network", DETAIL)["fieldConfig"]["defaults"]["unit"] == "ms"


def test_both_dashboards_list_the_source_events_from_the_event_log_group():
    for uid in (OVERVIEW, DETAIL):
        query = panel("Source events", uid)["targets"][0]
        assert query["queryMode"] == "Logs" and query["logGroupNames"] == [EVENTS_GROUP]
        for kind in ("MediaConnect Source Health", "MediaConnect Alert", "MediaConnect Flow Status Change"):
            assert f'"{kind}"' in query["expression"]
        # Hyphenated fields are backticked whole, or Logs Insights reads them as empty (found against real events).
        assert "`detail.error-code`" in query["expression"] and "`detail.error-message`" in query["expression"]
        assert "Flow Source Metadata Changed" not in query["expression"]   # one every ~20 s: it would bury the rest
        table = panel("Source events", uid)
        # Grafana adds a "View this query in CloudWatch console" column; keep only the four that say something.
        assert table["transformations"][0] == {"id": "filterFieldsByName", "options": {"include": {
            "names": ["@timestamp", "Event", "State", "Alert", "Detail"]}}}
        # An alert's raise and its clear are otherwise identical rows: errored comes back as 1 or 0.
        assert "detail.errored as Alert" in query["expression"]
        alert = table["fieldConfig"]["overrides"][0]
        assert alert["matcher"] == {"id": "byName", "options": "Alert"}
        mapping = alert["properties"][0]["value"][0]["options"]
        assert (mapping["1"]["text"], mapping["0"]["text"]) == ("raised", "cleared")
        assert table["options"]["sortBy"] == [{"displayName": "Time", "desc": True}]   # the response comes oldest first


def test_the_events_group_is_the_one_the_observability_module_creates():
    module = (ROOT / "modules" / "observability" / "main.tf").read_text()
    assert 'name              = "/aws/events/${var.name}"' in module
    assert EVENTS_GROUP == "/aws/events/" + "live-sports-aws" + "-demo"


def test_the_data_source_is_cloudwatch_with_the_sdk_default_chain():
    ds = load_yaml(PROVISIONING / "datasources" / "cloudwatch.yaml")["datasources"][0]
    assert (ds["uid"], ds["type"], ds["editable"]) == ("cloudwatch", "cloudwatch", False)
    assert ds["jsonData"] == {"authType": "default", "defaultRegion": "$AWS_REGION"}


def test_the_provider_loads_the_dashboards_folder_read_only():
    provider = load_yaml(PROVISIONING / "dashboards" / "provider.yaml")["providers"][0]
    assert provider["folder"] == "Live pipeline" and provider["allowUiUpdates"] is False
    assert provider["options"]["path"] == "/var/lib/grafana/dashboards"


# --- compose.yaml -----------------------------------------------------------------------------------------------

def compose() -> dict:
    return load_yaml(ROOT / "compose.yaml")


def test_grafana_is_published_on_loopback_only():
    assert compose()["services"]["grafana"]["ports"] == ["127.0.0.1:${GRAFANA_PORT:-3000}:3000"]


def test_grafana_is_pinned_runs_as_the_host_user_and_reads_aws_read_only():
    g = compose()["services"]["grafana"]
    assert g["image"] == "grafana/grafana-oss:13.0.2" and g["user"] == "${UID:-1000}:${GID:-1000}"
    aws = next(v for v in g["volumes"] if isinstance(v, dict) and v["target"] == "/aws")
    assert aws["read_only"] is True and aws["bind"] == {"create_host_path": False}


def test_grafana_settings_that_guard_access_and_cost():
    env = compose()["services"]["grafana"]["environment"]
    assert env["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    # DNS rebinding: any Host but 127.0.0.1 is redirected there (the console checks its Host header the same way).
    assert env["GF_SERVER_DOMAIN"] == "127.0.0.1" and env["GF_SERVER_ENFORCE_DOMAIN"] == "true"
    assert env["GF_SERVER_ROOT_URL"] == "http://127.0.0.1:${GRAFANA_PORT:-3000}/"
    assert env["GF_DASHBOARDS_MIN_REFRESH_INTERVAL"] == "5s"
    assert env["GF_SECURITY_ADMIN_PASSWORD"] == "${GRAFANA_ADMIN_PASSWORD:-admin}"
    assert env["AWS_CONFIG_FILE"] == "/aws/config" and env["AWS_SHARED_CREDENTIALS_FILE"] == "/aws/credentials"
    assert env["AWS_PROFILE"] == "${AWS_PROFILE:-default}" and env["AWS_REGION"] == "${AWS_REGION:-us-east-1}"


# --- MediaLive channel ------------------------------------------------------------------------------------------

ML = "medialive-channel"
# Every MediaLive metric the dashboard reads: its statistic and the exact dimension set a search must name, or it
# silently matches nothing.
CHANNEL = "ChannelId,Pipeline"
ML_METRICS = {
    # Maximum, not Sum: InputLossSeconds adds 1 a second while input is lost, then one large negative sample when it
    # returns (-241 on 2026-10-09), so a sum across a recovery cancels the loss and dips below 0. The maximum of a
    # bucket is 1 if input was lost in it. ChannelInputErrorSeconds has the same shape and is treated the same way.
    "ActiveAlerts": ("Maximum", CHANNEL), "InputLossSeconds": ("Maximum", CHANNEL),
    "ChannelInputErrorSeconds": ("Maximum", CHANNEL), "TsInputContinuityCounterErrors": ("Sum", CHANNEL),
    "InputVideoFrameRate": ("Average", CHANNEL), "DroppedFrames": ("Sum", CHANNEL), "FillMsec": ("Maximum", CHANNEL),
    "SvqTime": ("Average", CHANNEL), "NetworkIn": ("Average", CHANNEL), "NetworkOut": ("Average", CHANNEL),
    "OutputVideoFrameRate": ("Average", "ChannelId,OutputName,Pipeline"),
    "ActiveOutputs": ("Minimum", "ChannelId,OutputGroupName,Pipeline"),
    "Output4xxErrors": ("Sum", "ChannelId,OutputGroupName,Pipeline"),
    "Output5xxErrors": ("Sum", "ChannelId,OutputGroupName,Pipeline"),
    "OutputAudioLevelLkfs": ("Average", "AudioDescriptionName,ChannelId,Pipeline"),
    "OutputAudioLevelDbfs": ("Average", "AudioDescriptionName,ChannelId,Pipeline"),
}
NOW_TILES = ("Active alerts", "Input frame rate", "SVQ time")
LAST_5_MIN_TILES = ("Input loss, last 5 min", "Dropped frames, last 5 min", "Push errors, last 5 min")


def test_the_medialive_dashboard_identity_and_links():
    d = dashboard(ML)
    assert (d["uid"], d["title"], d["refresh"], d["timezone"]) == (ML, "MediaLive channel", "5s", "utc")
    assert d["time"] == {"from": "now-15m", "to": "now"}
    assert (d["editable"], d["graphTooltip"], d["tags"]) == (False, 1, ["live-sports-aws", "medialive"])
    assert [(link["url"], link["keepTime"]) for link in d["links"]] == [
        (f"/d/{OVERVIEW}", True), (f"/d/{DETAIL}", True), ("/d/mediapackage-origin", True),
        ("/d/cloudfront-distribution", True)]


def test_the_channel_drop_down_defaults_to_all_which_searches_every_channel():
    # A channel ID is a number with no name in it and changes with every deploy, so the search cannot find "ours" by
    # name as the MediaConnect dashboards do. All substitutes the term ChannelId, which every series carries, and
    # REMOVE_EMPTY keeps the channels with data: the running one. Settled against real data on 2026-10-10. The
    # custom value also keeps All working while the list is still empty.
    [variable] = dashboard(ML)["templating"]["list"]
    assert (variable["name"], variable["type"], variable["datasource"]["uid"]) == ("channel", "query", "cloudwatch")
    query = variable["query"]
    assert (query["queryType"], query["namespace"], query["metricName"], query["dimensionKey"]) == (
        "dimensionValues", "AWS/MediaLive", "ActiveAlerts", "ChannelId")
    assert (variable["includeAll"], variable["allValue"], variable["multi"], variable["refresh"]) == (
        True, "ChannelId", False, 1)
    assert variable["current"]["value"] == "$__all"


def test_every_medialive_query_searches_the_picked_channel_every_5_seconds():
    for target in metric_targets(ML):
        metric = target["metricName"]
        statistic, dims = ML_METRICS[metric]
        assert target["datasource"]["uid"] == "cloudwatch" and target["namespace"] == "AWS/MediaLive"
        assert target["statistic"] == statistic, metric
        assert target["expression"] == ("REMOVE_EMPTY(SEARCH('{AWS/MediaLive,%s} MetricName=\"%s\" $channel', "
                                        "'%s', 5))" % (dims, metric, statistic))
        assert target["period"] == "5"


def test_the_medialive_dashboard_reads_every_metric_and_repeats_only_the_tiles():
    # A tile reading the last minutes cannot reuse a graph's whole range, so the five that show a graph's metric
    # query it again. Active alerts has no graph.
    read = [t["metricName"] for t in metric_targets(ML)]
    assert set(read) == set(ML_METRICS)
    assert sorted(m for m in set(read) if read.count(m) > 1) == [
        "DroppedFrames", "InputLossSeconds", "InputVideoFrameRate", "Output4xxErrors", "Output5xxErrors", "SvqTime"]
    assert len(read) == 22


def test_the_medialive_panels_follow_the_signal():
    assert [(p["type"], p["title"]) for p in panels(ML)] == [
        ("stat", "Active alerts"), ("stat", "Input frame rate"), ("stat", "Input loss, last 5 min"),
        ("stat", "Dropped frames, last 5 min"), ("stat", "SVQ time"), ("stat", "Push errors, last 5 min"),
        ("timeseries", "Input frame rate"), ("timeseries", "Input problems"), ("timeseries", "Encoder"),
        ("timeseries", "Output frame rate"), ("timeseries", "Push to MediaPackage"), ("timeseries", "Network"),
        ("timeseries", "Audio"), ("table", "Channel events")]


def test_medialive_tiles_read_now_or_the_last_5_minutes():
    for title in NOW_TILES:
        tile = panel(title, ML, "stat")
        assert (tile["timeFrom"], tile["hideTimeOverride"]) == ("2m", True), title
        assert tile["options"]["reduceOptions"]["calcs"] == ["lastNotNull"], title
    for title in LAST_5_MIN_TILES:
        tile = panel(title, ML, "stat")
        assert (tile["timeFrom"], tile["hideTimeOverride"]) == ("5m", True), title
        expected = ["max"] if title == "Input loss, last 5 min" else ["sum"]
        assert tile["options"]["reduceOptions"]["calcs"] == expected, title


def test_medialive_problem_tiles_turn_red_from_1():
    for title in ("Active alerts", "SVQ time", "Dropped frames, last 5 min", "Push errors, last 5 min"):
        tile = panel(title, ML, "stat")
        steps = tile["fieldConfig"]["defaults"]["thresholds"]["steps"]
        assert [(s["value"], s["color"]) for s in steps] == [(None, "green"), (1, "red")], title
        assert tile["options"]["colorMode"] == "background", title
    # A frame rate is not good or bad on its own: neutral, with or without data.
    assert panel("Input frame rate", ML, "stat")["options"]["colorMode"] == "none"
    assert names(panel("Push errors, last 5 min", ML)) == {"A": "4xx", "B": "5xx"}
    assert panel("Push errors, last 5 min", ML)["options"]["textMode"] == "value_and_name"


def test_medialive_graphs_name_their_series():
    assert names(panel("Input problems", ML)) == {"A": "Input loss", "B": "Input errors", "C": "Continuity errors"}
    assert names(panel("Encoder", ML)) == {"A": "Dropped frames", "B": "SVQ time", "C": "Fill"}
    assert names(panel("Push to MediaPackage", ML)) == {"A": "4xx", "B": "5xx", "C": "Active outputs"}
    assert names(panel("Network", ML)) == {"A": "In", "B": "Out"}
    assert names(panel("Audio", ML)) == {"A": "Loudness (LKFS)", "B": "Level (dBFS)"}


def test_medialive_legends_name_the_rendition_or_the_channel():
    # Graphs with one metric keep CloudWatch's label: the rendition on Output frame rate, else the channel ID, which
    # also tells two channels apart after a redeploy within the range.
    for target in metric_targets(ML):
        expected = "${PROP('Dim.OutputName')}" if target["metricName"] == "OutputVideoFrameRate" \
            else "${PROP('Dim.ChannelId')}"
        assert target["label"] == expected, target["metricName"]


def test_medialive_units():
    unit = lambda title, kind=None: panel(title, ML, kind)["fieldConfig"]["defaults"].get("unit")
    assert unit("Input frame rate", "stat") == unit("Input frame rate", "timeseries") == "suffix: fps"
    assert unit("Output frame rate") == "suffix: fps"
    assert unit("SVQ time") == "percent"
    # CloudWatch stores NetworkIn and NetworkOut in Megabits/Second; bps showed a 10 Mbps output as "10 b/s".
    assert unit("Network") == "Mbits"
    # Each FillMsec sample is the fill over the trailing minute (60,000 throughout an input loss, read on
    # 2026-10-09), so a sum over 5 seconds would count the same fill five times.
    fill = next(t for t in metric_targets(ML) if t["metricName"] == "FillMsec")
    assert fill["statistic"] == "Maximum"


def test_channel_events_list_medialive_events_from_the_event_log_group():
    table = panel("Channel events", ML)
    query = table["targets"][0]
    assert query["queryMode"] == "Logs" and query["logGroupNames"] == [EVENTS_GROUP]
    assert "filter `detail-type` like /^MediaLive /" in query["expression"]
    # The field names livectl's log formatter reads; unverified against a real event (live check).
    for field in ("detail.state", "detail.alarm_state", "detail.alert_type", "detail.message"):
        assert field in query["expression"], field
    assert table["transformations"][0] == {"id": "filterFieldsByName", "options": {"include": {
        "names": ["@timestamp", "Event", "State", "Alert", "Detail"]}}}
    assert table["options"]["sortBy"] == [{"displayName": "Time", "desc": True}]


def test_medialive_counter_graphs_start_at_0():
    # Without it, Input problems drew its seconds axis down to -3 minutes. Audio levels are negative: not there.
    for title in ("Input problems", "Encoder", "Push to MediaPackage", "Network"):
        assert panel(title, ML)["fieldConfig"]["defaults"]["custom"]["axisSoftMin"] == 0, title
    assert "axisSoftMin" not in panel("Audio", ML)["fieldConfig"]["defaults"]["custom"]


def test_input_loss_says_lost_or_none():
    # Any part of a second lost counts: samples at the edge of a loss read 0.77.
    tile = panel("Input loss, last 5 min", ML)["fieldConfig"]["defaults"]
    assert [(s["value"], s["color"]) for s in tile["thresholds"]["steps"]] == [(None, "green"), (0.01, "red")]
    [none, lost] = tile["mappings"]
    assert (none["type"], none["options"]["0"]["text"]) == ("value", "none")
    assert (lost["type"], lost["options"]["from"], lost["options"]["result"]["text"]) == ("range", 0.01, "lost")


# --- MediaPackage origin ----------------------------------------------------------------------------------------

MP = "mediapackage-origin"
CG, CGE = "ChannelGroup,Channel", "ChannelGroup,Channel,OriginEndpoint"
# (metric, dims) -> statistics. EgressRequestCount is read twice, by status and by CDN authorization, so a check keyed
# on the metric name alone would let one replace the other unnoticed.
MP_METRICS = {
    ("IngressRequestCount", CG + ",StatusCode"): {"Sum"},
    ("IngressBytes", CG): {"Sum"},
    ("IngressResponseTime", CG): {"Average", "Maximum"},
    ("ActiveInput", CG + ",InputIndex"): {"Minimum"},
    ("EgressRequestCount", CGE + ",StatusCode,RequestType"): {"Sum"},
    ("EgressBytes", CGE + ",RequestType"): {"Sum"},
    ("EgressResponseTime", CGE + ",RequestType"): {"Average", "Maximum"},
    ("EgressRequestCount", CGE + ",CDNAuthorizationStatus,CDNAuthorizationStatusDetails"): {"Sum"},
}
# Bytes per 60-second bucket to megabits per second.
MBPS = " * 8 / 60 / 1000000"
MP_EXPRESSION = re.compile(
    r"(?P<sum>SUM\()?REMOVE_EMPTY\(SEARCH\('\{AWS/MediaPackage,(?P<dims>[A-Za-z,]+)\} MetricName=\"(?P<metric>\w+)\" "
    r"ChannelGroup=\"live-sports-aws-demo\"(?P<filter>(?: \w+=\"[\w-]+\")?)', '(?P<statistic>\w+)', 60\)\)(?(sum)\))"
    r"(?P<math>.*)")
ERROR_TILES = ("Push errors, last 5 min", "Origin errors, last 5 min", "Direct origin requests, last hour")
EGRESS_GRAPHS = ("Requests", "Egress bitrate", "Response time", "Requests by authorization")


def mp_parse(target: dict) -> tuple:
    """(metric, dims, filter, statistic, math) of a MediaPackage query; fails on any other shape."""
    match = MP_EXPRESSION.fullmatch(target["expression"])
    assert match, target["expression"]
    return match["metric"], match["dims"], match["filter"].strip(), match["statistic"], match["math"]


def mp_summed(target: dict) -> bool:
    return MP_EXPRESSION.fullmatch(target["expression"])["sum"] is not None


def mp_graph_targets() -> list:
    return [t for p in panels(MP) if p["type"] == "timeseries" for t in p["targets"]]


def mp_tile(title: str) -> dict:
    return panel(title, MP, "stat")


def test_the_mediapackage_dashboard_identity_and_links():
    d = dashboard(MP)
    assert (d["uid"], d["title"], d["refresh"], d["timezone"]) == (MP, "MediaPackage origin", "1m", "utc")
    assert d["time"] == {"from": "now-1h", "to": "now"}
    assert (d["editable"], d["graphTooltip"], d["tags"]) == (False, 1, ["live-sports-aws", "mediapackage"])
    # No drop-down: the channel group, channel and endpoint keep their names across deploys.
    assert d.get("templating", {}).get("list", []) == []
    assert [(link["url"], link["keepTime"]) for link in d["links"]] == [
        (f"/d/{OVERVIEW}", True), (f"/d/{DETAIL}", True), (f"/d/{ML}", True), ("/d/cloudfront-distribution", True)]


def test_every_mediapackage_query_searches_this_projects_origin_every_minute():
    # The user guide promises a sample "every minute, if not sooner"; finer data is a live check.
    for target in metric_targets(MP):
        metric, dims, _, statistic, math = mp_parse(target)
        assert target["datasource"]["uid"] == "cloudwatch" and target["namespace"] == "AWS/MediaPackage"
        assert (target["metricName"], target["statistic"], target["period"]) == (metric, statistic, "60")
        # Code mode, or Grafana builds its own query from the fields and ignores the expression.
        assert (target["metricEditorMode"], target["region"], target["queryMode"]) == (1, "default", "Metrics")
        assert statistic in MP_METRICS[(metric, dims)], (metric, dims, statistic)
        assert math == (MBPS if metric.endswith("Bytes") else ""), metric


def test_the_mediapackage_dashboard_reads_every_metric_and_statistic():
    graphs = [mp_parse(t) for t in mp_graph_targets()]
    assert {(m, d, s) for m, d, _, s, _ in graphs} == {(m, d, s) for (m, d), ss in MP_METRICS.items() for s in ss}
    assert [f for _, _, f, _, _ in graphs if f] == []
    assert not any(mp_summed(t) for t in mp_graph_targets())
    assert (len(graphs), len(metric_targets(MP))) == (10, 18)


def test_the_mediapackage_panels_follow_the_signal():
    assert [(p["type"], p["title"]) for p in panels(MP)] == [
        ("stat", "Ingest bitrate"), ("stat", "Push errors, last 5 min"), ("stat", "Input active"),
        ("stat", "Origin errors, last 5 min"), ("stat", "Direct origin requests, last hour"),
        ("stat", "Manifest response time, last 5 min"),
        ("row", "Ingest from MediaLive"),
        ("timeseries", "Pushes"), ("timeseries", "Ingest bitrate"), ("timeseries", "Push response time"),
        ("row", "Origin to CloudFront"),
        ("timeseries", "Requests"), ("timeseries", "Egress bitrate"), ("timeseries", "Response time"),
        ("row", "CDN authorization"),
        ("timeseries", "Requests by authorization")]
    assert [p.get("collapsed", False) for p in panels(MP) if p["type"] == "row"] == [False] * 3


def test_mediapackage_tiles():
    def shape(title):
        tile = mp_tile(title)
        queries = [mp_parse(t) for t in tile["targets"]]
        return ((tile["timeFrom"], tile["hideTimeOverride"]), tile["options"]["reduceOptions"]["calcs"],
                [(m, d, f, s) for m, d, f, s, _ in queries])

    status, egress_status = CG + ",StatusCode", CGE + ",StatusCode,RequestType"
    auth = CGE + ",CDNAuthorizationStatus,CDNAuthorizationStatusDetails"
    # The mean of 5 minutes: the newest bucket can still be filling at a 1-minute refresh.
    assert shape("Ingest bitrate") == (("5m", True), ["mean"], [("IngressBytes", CG, "", "Sum")])
    assert shape("Push errors, last 5 min") == (("5m", True), ["sum"], [
        ("IngressRequestCount", status, 'StatusCode="4xx"', "Sum"),
        ("IngressRequestCount", status, 'StatusCode="5xx"', "Sum")])
    assert shape("Input active") == (("5m", True), ["min"], [("ActiveInput", CG + ",InputIndex", "", "Minimum")])
    assert shape("Origin errors, last 5 min") == (("5m", True), ["sum"], [
        ("EgressRequestCount", egress_status, 'StatusCode="4xx"', "Sum"),
        ("EgressRequestCount", egress_status, 'StatusCode="5xx"', "Sum")])
    assert shape("Direct origin requests, last hour") == (("1h", True), ["sum"], [
        ("EgressRequestCount", auth, 'CDNAuthorizationStatus="Unauthorized"', "Sum")])
    assert shape("Manifest response time, last 5 min") == (("5m", True), ["max"], [
        ("EgressResponseTime", CGE + ",RequestType", 'RequestType="manifest"', "Maximum")])
    for title in ("Push errors, last 5 min", "Origin errors, last 5 min"):
        assert names(mp_tile(title)) == {"A": "4xx", "B": "5xx"}, title
        assert mp_tile(title)["options"]["textMode"] == "value_and_name", title
    assert mp_tile("Direct origin requests, last hour")["options"]["textMode"] == "value"
    # One total per box: these searches return a series per request type or per refusal reason, and without SUM each
    # became its own box, all labelled 4xx (found in review). Ingress has one series per status class already.
    summed = {p["title"] for p in panels(MP) if p["type"] == "stat" and any(mp_summed(t) for t in p["targets"])}
    assert summed == {"Origin errors, last 5 min", "Direct origin requests, last hour"}
    for title in summed:
        assert all(mp_summed(t) for t in mp_tile(title)["targets"]), title
    assert mp_tile("Manifest response time, last 5 min")["fieldConfig"]["defaults"]["unit"] == "ms"


def test_mediapackage_error_tiles_read_none_without_data():
    # A status class has a series only once a request in it has happened: a healthy stack has no 4xx series at all
    # (none on ingress in two weeks), so no data is the good case and must not look like a fault.
    for title in ERROR_TILES:
        tile = mp_tile(title)
        defaults = tile["fieldConfig"]["defaults"]
        assert defaults["noValue"] == "none", title
        assert tile["options"]["colorMode"] == "background", title
        # Transparent, not green: Grafana paints a missing value in the base step's colour, so a green base made a
        # stopped stack look healthy (seen in the browser, 2026-10-10). A count, when there is one, is at least 1.
        assert [(s["value"], s["color"]) for s in defaults["thresholds"]["steps"]] == [
            (None, "transparent"), (1, "red")], title
    for title in ("Ingest bitrate", "Manifest response time, last 5 min"):
        assert mp_tile(title)["options"]["colorMode"] == "none", title


def test_input_active_says_active_or_inactive():
    defaults = mp_tile("Input active")["fieldConfig"]["defaults"]
    [mapping] = defaults["mappings"]
    assert mapping["type"] == "value"
    assert {k: (v["text"], v["color"]) for k, v in mapping["options"].items()} == {
        "0": ("inactive", "red"), "1": ("active", "green")}
    # Without it the tile is blank when the stack is off: the minimum of an empty series is no value at all.
    assert defaults["noValue"] == "\u2014"
    assert defaults["thresholds"]["steps"] == [{"value": None, "color": "transparent"}]


def test_mediapackage_series_labels():
    expected = {
        ("IngressRequestCount", "Sum"): ["${PROP('Dim.StatusCode')}"],
        ("IngressBytes", "Sum"): ["Ingest"],
        ("IngressResponseTime", "Average"): ["Average"], ("IngressResponseTime", "Maximum"): ["Maximum"],
        ("ActiveInput", "Minimum"): ["Input active"],
        ("EgressBytes", "Sum"): ["${PROP('Dim.RequestType')}"],
        ("EgressResponseTime", "Average"): ["${PROP('Dim.RequestType')} average"],
        ("EgressResponseTime", "Maximum"): ["${PROP('Dim.RequestType')} max"],
        ("EgressRequestCount", "Sum"): ["${PROP('Dim.RequestType')} ${PROP('Dim.StatusCode')}",
                                        "${PROP('Dim.CDNAuthorizationStatusDetails')}"],
    }
    seen = {}
    for target in mp_graph_targets():
        metric, _, _, statistic, _ = mp_parse(target)
        seen.setdefault((metric, statistic), []).append(target["label"])
    assert seen == expected


def test_cdn_authorization_is_red_except_a_matched_secret():
    graph = panel("Requests by authorization", MP)
    defaults = graph["fieldConfig"]["defaults"]
    assert (defaults["custom"]["drawStyle"], defaults["custom"]["stacking"]["mode"]) == ("bars", "normal")
    assert defaults["color"] == {"mode": "fixed", "fixedColor": "red"}
    [override] = graph["fieldConfig"]["overrides"]
    assert override["matcher"] == {"id": "byName", "options": "HeaderSecretMatched"}
    assert override["properties"] == [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]


def test_mediapackage_units_and_axes():
    unit = lambda title, kind=None: panel(title, MP, kind)["fieldConfig"]["defaults"].get("unit")
    assert unit("Ingest bitrate", "stat") == unit("Ingest bitrate", "timeseries") == unit("Egress bitrate") == "Mbits"
    assert unit("Push response time") == unit("Response time") == "ms"
    [right] = [o for o in panel("Pushes", MP)["fieldConfig"]["overrides"] if o["matcher"]["options"] == "B"]
    assert {"id": "custom.axisPlacement", "value": "right"} in right["properties"]
    for p in panels(MP):
        if p["type"] == "timeseries":
            assert p["fieldConfig"]["defaults"]["custom"]["axisSoftMin"] == 0, p["title"]


def test_mediapackage_egress_panels_say_no_data_means_no_viewers():
    for title in EGRESS_GRAPHS:
        assert "no viewers" in panel(title, MP)["description"], title


def test_input_active_is_described_as_undocumented():
    assert "not in the MediaPackage user guide" in mp_tile("Input active")["description"]


def test_no_mediapackage_query_reads_a_left_out_metric():
    # IngestEndpoint is a GUID that changes with every deploy; no MQCS data exists; multiview is not used.
    for target in metric_targets(MP):
        for word in ("IngestEndpoint", "MQCS", "Multiview"):
            assert word not in target["expression"], (word, target["expression"])



# --- CloudFront distribution ------------------------------------------------------------------------------------

CF = "cloudfront-distribution"
# The free default metrics only: the additional ones are billed per distribution, and every deploy creates one.
CF_METRICS = {"Requests": "Sum", "BytesDownloaded": "Sum", "TotalErrorRate": "Average", "4xxErrorRate": "Average",
              "5xxErrorRate": "Average"}
CF_SEARCH = "REMOVE_EMPTY(SEARCH('{AWS/CloudFront,DistributionId,Region} MetricName=\"%s\" $distribution', '%s', 60))"


def test_the_cloudfront_dashboard_identity_and_links():
    d = dashboard(CF)
    # CloudFront publishes once a minute: a faster refresh would buy the same points again.
    assert (d["uid"], d["title"], d["refresh"], d["timezone"]) == (CF, "CloudFront distribution", "1m", "utc")
    assert d["time"] == {"from": "now-1h", "to": "now"}
    assert (d["editable"], d["graphTooltip"], d["tags"]) == (False, 1, ["live-sports-aws", "cloudfront"])
    assert [(link["url"], link["keepTime"]) for link in d["links"]] == [
        (f"/d/{OVERVIEW}", True), (f"/d/{DETAIL}", True), (f"/d/{ML}", True), ("/d/mediapackage-origin", True)]


def test_the_cloudfront_variables():
    distribution, per_viewer = dashboard(CF)["templating"]["list"]
    assert (distribution["name"], distribution["type"], distribution["datasource"]["uid"]) == (
        "distribution", "query", "cloudwatch")
    query = distribution["query"]
    assert (query["queryType"], query["namespace"], query["metricName"], query["dimensionKey"], query["region"]) == (
        "dimensionValues", "AWS/CloudFront", "Requests", "DistributionId", "us-east-1")
    assert (distribution["includeAll"], distribution["allValue"], distribution["multi"], distribution["refresh"]) == (
        True, "DistributionId", False, 1)
    assert distribution["current"]["value"] == "$__all"
    # One player made about 60 requests a minute on 2026-10-09 (5.6 Mbps: one 1080p stream). Recalibrated live.
    assert (per_viewer["name"], per_viewer["type"], per_viewer["query"]) == ("requests_per_viewer", "textbox", "60")


def test_every_cloudfront_query_reads_us_east_1_every_minute():
    # CloudFront's metrics exist only in us-east-1, whatever region the data source defaults to.
    for target in metric_targets(CF):
        metric = target["metricName"]
        statistic = CF_METRICS[metric]
        assert (target["region"], target["namespace"], target["period"]) == ("us-east-1", "AWS/CloudFront", "60")
        assert target["statistic"] == statistic and target["datasource"]["uid"] == "cloudwatch"
        assert target["expression"] in (CF_SEARCH % (metric, statistic),
                                        CF_SEARCH % (metric, statistic) + " / $requests_per_viewer",
                                        CF_SEARCH % (metric, statistic) + " * 8 / 60"), target["expression"]
        assert target["label"] == "${PROP('Dim.DistributionId')}"


def test_the_cloudfront_dashboard_reads_the_free_metrics_only():
    read = [t["metricName"] for t in metric_targets(CF)]
    assert set(read) == set(CF_METRICS) and "BytesUploaded" not in read
    assert len(read) == 8


def test_the_viewer_estimate_divides_requests_and_delivered_is_bits_per_second():
    for title in ("≈ Viewers", "Viewers (estimate)"):
        estimate = panel(title, CF)["targets"][0]
        assert estimate["metricName"] == "Requests" and estimate["expression"].endswith(" / $requests_per_viewer")
    for title, kind in (("Delivered", "stat"), ("Delivered", "timeseries")):
        delivered = panel(title, CF, kind)
        assert delivered["targets"][0]["expression"].endswith(" * 8 / 60")
        assert delivered["fieldConfig"]["defaults"]["unit"] == "bps"


def test_the_cloudfront_panels():
    assert [(p["type"], p["title"]) for p in panels(CF)] == [
        ("stat", "≈ Viewers"), ("stat", "Requests a minute"), ("stat", "Delivered"), ("stat", "Error rate"),
        ("timeseries", "Viewers (estimate)"), ("timeseries", "Delivered"), ("timeseries", "Error rates")]


def test_cloudfront_tiles_read_the_last_5_minutes():
    # 5, not 2: CloudFront's points are a minute apart and arrive late.
    for title in ("≈ Viewers", "Requests a minute", "Delivered", "Error rate"):
        tile = panel(title, CF, "stat")
        assert (tile["timeFrom"], tile["hideTimeOverride"]) == ("5m", True), title
        assert tile["options"]["reduceOptions"]["calcs"] == ["lastNotNull"], title
    for title in ("≈ Viewers", "Requests a minute", "Delivered"):
        assert panel(title, CF, "stat")["options"]["colorMode"] == "none", title
    error = panel("Error rate", CF, "stat")
    steps = error["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert [(s["value"], s["color"]) for s in steps] == [(None, "green"), (1, "red")]
    assert error["fieldConfig"]["defaults"]["unit"] == "percent"
    assert panel("≈ Viewers", CF)["fieldConfig"]["defaults"]["decimals"] == 0


def test_cloudfront_graphs_name_their_series():
    # The estimate alone: requests drawn beside it are the same line scaled, and hid it (rendered 2026-10-10).
    assert [t["refId"] for t in panel("Viewers (estimate)", CF)["targets"]] == ["A"]
    assert names(panel("Error rates", CF)) == {"A": "4xx", "B": "5xx"}
    assert panel("Error rates", CF)["fieldConfig"]["defaults"]["unit"] == "percent"
