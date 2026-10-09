"""The Grafana provisioning and compose files, checked offline: what the dashboard queries, and what guards access and
cost. Whether Grafana accepts the files is test_grafana_container.py's job."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAFANA = ROOT / "observability" / "grafana"
PROVISIONING = GRAFANA / "provisioning"
METRICS = {"SourceConnected": "Maximum", "SourceBitRate": "Average", "SourceRoundTripTime": "Average",
           "SourcePacketLossPercent": "Average", "SourceNotRecoveredPackets": "Sum"}


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def dashboard() -> dict:
    return json.loads((GRAFANA / "dashboards" / "mediaconnect-source.json").read_text())


def panels() -> list:
    return dashboard()["panels"]


def targets() -> list:
    return [(panel, target) for panel in panels() for target in panel["targets"]]


def test_the_dashboard_identity_and_refresh():
    d = dashboard()
    assert (d["uid"], d["title"], d["refresh"], d["timezone"]) == ("mediaconnect-source", "MediaConnect source", "5s",
                                                                    "utc")
    assert d["time"] == {"from": "now-15m", "to": "now"}


def test_every_query_searches_this_projects_flows_every_5_seconds():
    # REMOVE_EMPTY: every deploy's flow has the same name, and the search returns the destroyed ones of the last two
    # weeks too. Without it each tile splits into one box per flow and the legends repeat the name.
    for _, target in targets():
        metric = target["metricName"]
        assert target["datasource"]["uid"] == "cloudwatch"
        assert METRICS[metric] == target["statistic"]
        assert target["expression"] == ("REMOVE_EMPTY(SEARCH('{AWS/MediaConnect,FlowARN} MetricName=\"%s\" "
                                        "live-sports-aws-demo', '%s', 5))" % (metric, target["statistic"]))
        assert target["period"] == "5"


def test_the_panels_are_the_five_tiles_and_three_graphs():
    assert [(p["type"], p["title"]) for p in panels()] == [
        ("stat", "Connected"), ("stat", "Bitrate"), ("stat", "Round trip"), ("stat", "Packet loss"),
        ("stat", "Not recovered, last 5 min"), ("timeseries", "Bitrate"), ("timeseries", "Round trip time"),
        ("timeseries", "Loss")]
    assert {t["metricName"] for _, t in targets()} == set(METRICS)


def test_not_recovered_sums_the_last_5_minutes_and_turns_red_above_0():
    tile = panels()[4]
    assert tile["timeFrom"] == "5m" and tile["options"]["reduceOptions"]["calcs"] == ["sum"]
    steps = tile["fieldConfig"]["defaults"]["thresholds"]["steps"]
    assert [(s["value"], s["color"]) for s in steps] == [(None, "green"), (1, "red")]


def test_connected_maps_1_and_0_to_words():
    mappings = panels()[0]["fieldConfig"]["defaults"]["mappings"][0]["options"]
    assert mappings["1"]["text"] == "connected" and mappings["1"]["color"] == "green"
    assert mappings["0"]["text"] == "disconnected" and mappings["0"]["color"] == "red"


def test_legends_show_the_flow_name_not_the_arn():
    for panel in panels():
        assert {"id": "renameByRegex", "options": {"regex": ".*:([^:]+)$", "renamePattern": "$1"}} in panel[
            "transformations"]


def test_units():
    units = [p["fieldConfig"]["defaults"].get("unit") for p in panels()]
    assert units == [None, "bps", "ms", "percent", "short", "bps", "ms", None]


def test_the_loss_graph_puts_not_recovered_on_the_right_axis():
    override = panels()[7]["fieldConfig"]["overrides"][0]
    assert override["matcher"] == {"id": "byFrameRefID", "options": "B"}
    assert {"id": "custom.axisPlacement", "value": "right"} in override["properties"]


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
    assert env["GF_DASHBOARDS_MIN_REFRESH_INTERVAL"] == "5s"
    assert env["GF_SECURITY_ADMIN_PASSWORD"] == "${GRAFANA_ADMIN_PASSWORD:-admin}"
    assert env["AWS_CONFIG_FILE"] == "/aws/config" and env["AWS_SHARED_CREDENTIALS_FILE"] == "/aws/credentials"
    assert env["AWS_PROFILE"] == "${AWS_PROFILE:-default}" and env["AWS_REGION"] == "${AWS_REGION:-us-east-1}"


def test_the_now_tiles_read_only_the_last_2_minutes_and_show_just_the_value():
    # Over the whole range, lastNotNull would keep a stopped flow "connected" for up to 15 minutes. 2 minutes, not
    # 1, leaves room for CloudWatch's publishing delay (unmeasured; live check 20).
    tiles = panels()[:5]
    assert [t["timeFrom"] for t in tiles] == ["2m", "2m", "2m", "2m", "5m"]
    assert {t["options"]["textMode"] for t in tiles} == {"value"}


def test_tiles_say_nothing_is_fine_when_there_is_no_data():
    tiles = panels()[:5]
    assert all(t["hideTimeOverride"] for t in tiles)                     # the titles keep their room
    assert tiles[0]["fieldConfig"]["defaults"]["noValue"] == "no source"
    assert tiles[0]["fieldConfig"]["defaults"]["thresholds"]["steps"][0]["color"] == "text"
    assert [t["options"]["colorMode"] for t in tiles] == ["background", "none", "none", "none", "background"]
