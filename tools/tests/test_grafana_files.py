"""The Grafana provisioning and compose files, checked offline: what the dashboard queries, and what guards access and
cost. Whether Grafana accepts the files is test_grafana_container.py's job."""

import json
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
        assert [(link["url"], link["keepTime"]) for link in d["links"]] == [(f"/d/{other}", True)]


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
