# Grafana for MediaConnect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Grafana container, started with the console, showing a 5-second *MediaConnect source* dashboard read
straight from CloudWatch.

**Architecture:** One more compose service (`grafana`, pinned image) with file provisioning: a CloudWatch data source
using the mounted `~/.aws`, and one dashboard whose queries find the live flow with a CloudWatch `SEARCH` on the
project prefix. `just ui` starts and stops it around the console and hands the console its URL for the Endpoints card.

**Tech Stack:** Docker Compose, `grafana/grafana-oss:13.0.2`, Grafana file provisioning (YAML + dashboard JSON),
bash in the justfile, Python 3 + pytest (+ PyYAML, dev only), the existing stdlib console.

**Spec:** `docs/superpowers/specs/2026-10-09-grafana-mediaconnect-design.md`

## Global Constraints

- Image `grafana/grafana-oss:13.0.2`, exact tag.
- Port line exactly `"127.0.0.1:${GRAFANA_PORT:-3000}:3000"`; never without the `127.0.0.1:` prefix.
- `~/.aws` mounted **read-only** at `/aws`, `create_host_path: false`.
- Anonymous access off; `GF_DASHBOARDS_MIN_REFRESH_INTERVAL=5s`; dashboard refresh `5s`, query period `5`.
- Data source uid `cloudwatch`; dashboard uid `mediaconnect-source`, title *MediaConnect source*, folder *Live pipeline*.
- Search expression, exactly: `REMOVE_EMPTY(SEARCH('{AWS/MediaConnect,FlowARN} MetricName="<metric>" live-sports-aws-demo', '<statistic>', 5))`.
- Every CloudWatch query sets `statistic` (the plugin answers 500 without it).
- The five metrics and statistics: `SourceConnected` Maximum, `SourceBitRate` Average, `SourceRoundTripTime` Average,
  `SourcePacketLossPercent` Average, `SourceNotRecoveredPackets` Sum.
- boto3 stays the only runtime dependency; `pyyaml` joins the `dev` extra.
- All tests offline; nothing in this plan calls AWS. Conventional Commits; commit trailer
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A fresh named volume under a UID other than 1000** (CI runs as 1001): Grafana must still start and provision.
   Pinned by `test_grafana_starts_and_provisions` running as the current UID (Task 2).
2. **Port 3000 already taken, or the daemon down:** `just ui` must still start the console, saying why Grafana was
   skipped. Pinned by `test_grafana_up_skips_when_compose_fails` (Task 3).
3. **`GRAFANA_PORT` remapped:** the console's link must use the port Docker actually published, not 3000. Pinned by
   `test_ui_passes_the_published_grafana_url` (Task 3).
4. **Ctrl-C or a crash of the console:** Grafana must be stopped too. Pinned by `test_ui_stops_grafana_when_the_console_exits`,
   with a console that exits non-zero (Task 3).
5. **The legends and tiles with destroyed flows in the search:** only the live flow should show. No offline test can
   render CloudWatch data; it is live check 22 (Task 4), and Task 1 adds the rename transformation that keeps legends
   to flow names.

---

### Task 1: Provisioning files and the dashboard, checked offline

**Files:**
- Create: `observability/grafana/provisioning/datasources/cloudwatch.yaml`
- Create: `observability/grafana/provisioning/dashboards/provider.yaml`
- Create: `observability/grafana/dashboards/mediaconnect-source.json`
- Create: `tools/tests/test_grafana_files.py`
- Modify: `tools/pyproject.toml` (`dev` extra gains `"pyyaml>=6"`)

**Interfaces:**
- Produces: the three files at those paths (Task 2 mounts `observability/grafana/provisioning` at
  `/etc/grafana/provisioning` and `observability/grafana/dashboards` at `/var/lib/grafana/dashboards`); data source uid
  `cloudwatch`; dashboard uid `mediaconnect-source`. `test_grafana_files.py` exposes `ROOT` (repo root `Path`) and
  `load_yaml(path) -> dict` for Task 2's compose checks.

- [ ] **Step 1: Write the failing tests** in `tools/tests/test_grafana_files.py`

```python
METRICS = {"SourceConnected": "Maximum", "SourceBitRate": "Average", "SourceRoundTripTime": "Average",
           "SourcePacketLossPercent": "Average", "SourceNotRecoveredPackets": "Sum"}

def test_the_dashboard_identity_and_refresh():
    d = dashboard()
    assert (d["uid"], d["title"], d["refresh"], d["timezone"]) == ("mediaconnect-source", "MediaConnect source", "5s", "utc")
    assert d["time"] == {"from": "now-15m", "to": "now"}

def test_every_query_searches_this_projects_flows_every_5_seconds():
    for panel, target in targets():          # every (panel, target) pair in the dashboard
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
        assert {"id": "renameByRegex", "options": {"regex": ".*:([^:]+)$", "renamePattern": "$1"}} in panel["transformations"]

def test_units():
    units = [p["fieldConfig"]["defaults"].get("unit") for p in panels()]
    assert units == [None, "bps", "ms", "percent", "short", "bps", "ms", None]

def test_the_loss_graph_puts_not_recovered_on_the_right_axis():
    override = panels()[7]["fieldConfig"]["overrides"][0]
    assert override["matcher"] == {"id": "byFrameRefID", "options": "B"}
    assert {"id": "custom.axisPlacement", "value": "right"} in override["properties"]

def test_the_data_source_is_cloudwatch_with_the_sdk_default_chain():
    ds = load_yaml(PROVISIONING / "datasources/cloudwatch.yaml")["datasources"][0]
    assert (ds["uid"], ds["type"], ds["editable"]) == ("cloudwatch", "cloudwatch", False)
    assert ds["jsonData"] == {"authType": "default", "defaultRegion": "$AWS_REGION"}

def test_the_provider_loads_the_dashboards_folder_read_only():
    provider = load_yaml(PROVISIONING / "dashboards/provider.yaml")["providers"][0]
    assert provider["folder"] == "Live pipeline" and provider["allowUiUpdates"] is False
    assert provider["options"]["path"] == "/var/lib/grafana/dashboards"
```

Helpers in the same file: `ROOT = Path(__file__).resolve().parents[2]`, `PROVISIONING`, `load_yaml`, `dashboard()`
(parses the JSON), `panels()` (the dashboard's `panels`, in order), `targets()`. The loss graph's two targets have
`refId` `A` (packet loss) and `B` (not recovered).

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pip install -q -e "tools[dev]" && .venv/bin/pytest -q tools/tests/test_grafana_files.py`
Expected: FAIL, files not found.

- [ ] **Step 3: Write the three files**

Each target uses the shape the spike proved works through Grafana 13's API:
`{"refId", "datasource": {"type": "cloudwatch", "uid": "cloudwatch"}, "queryMode": "Metrics", "metricQueryType": 0,
"metricEditorMode": 1, "region": "default", "namespace": "AWS/MediaConnect", "metricName", "statistic",
"expression", "period": "5", "id": "", "label": "${PROP('Dim.FlowARN')}"}`. Stat tiles reduce with `lastNotNull`
except the not-recovered tile. Bitrate graphs and tiles use unit `bps` (Grafana scales it to Mbps). Dashboard
`schemaVersion` is whatever Grafana 13 writes when you export a new dashboard; Task 2's smoke test proves it loads.

- [ ] **Step 4: Run the tests and see them pass**

Run: `.venv/bin/pytest -q tools/tests/test_grafana_files.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add observability tools/tests/test_grafana_files.py tools/pyproject.toml
git commit -m "feat: provision a CloudWatch data source and a MediaConnect source dashboard for Grafana"
```

---

### Task 2: The `grafana` compose service and its Docker smoke test

**Files:**
- Modify: `compose.yaml` (new service `grafana`, named volume `grafana-data`)
- Modify: `.env.example` (`GRAFANA_PORT=3000`, `GRAFANA_ADMIN_PASSWORD=`, `AWS_REGION=us-east-1`, each with a one-line
  comment in the file's style)
- Modify: `tools/tests/test_grafana_files.py` (compose checks)
- Create: `tools/tests/test_grafana_container.py`
- Modify: `justfile` (recipe `test-grafana`; `test` runs it)
- Modify: `.github/workflows/image.yml` (the `ui` job also runs `just test-grafana`)

**Interfaces:**
- Consumes: Task 1's paths, `ROOT`, `load_yaml`.
- Produces: compose service name `grafana`, container port `3000`; `just test-grafana`.

- [ ] **Step 1: Write the failing compose checks** (append to `test_grafana_files.py`)

```python
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
```

`environment` is written as a mapping (not a list) so these lookups work.

- [ ] **Step 2: Write the failing smoke test**, `tools/tests/test_grafana_container.py`

Module-level skip unless `shutil.which("docker")` and `docker info` succeeds (reason: "needs Docker"). One test:

```python
def test_grafana_starts_and_provisions(tmp_path):
    # The real compose.yaml, as the current user, a fresh volume, an empty ~/.aws stand-in, a free port.
    ...
    assert get(url + "/api/datasources/uid/cloudwatch")["type"] == "cloudwatch"
    assert get(url + "/api/dashboards/uid/mediaconnect-source")["meta"]["folderTitle"] == "Live pipeline"
    log = compose("logs", "grafana")
    assert "provisioning" not in error_lines(log)   # no level=error line mentions provisioning
```

How it runs compose: `docker compose -f <ROOT>/compose.yaml -p livectl-grafana-test-<pid> up -d grafana` with
environment `HOME=<tmp_path>` (holding an empty `.aws/` dir), `GRAFANA_PORT=0`, `UID`/`GID` from `os.getuid()`/
`os.getgid()`, `GRAFANA_ADMIN_PASSWORD=smoke`; the URL's port from `docker compose ... port grafana 3000`; `get()` uses
basic auth `admin:smoke` and polls `/api/health` for up to 90 s; a `finally` runs `down -v`.

- [ ] **Step 3: Run both and see them fail**

Run: `.venv/bin/pytest -q tools/tests/test_grafana_files.py tools/tests/test_grafana_container.py`
Expected: the compose checks FAIL (no `grafana` service); the smoke test FAILs the same way (or is skipped without
Docker).

- [ ] **Step 4: Add the service** to `compose.yaml` as the Global Constraints and the spec's *The `grafana` service*
  section fix it, including `GF_PLUGINS_PREINSTALL_DISABLED=true`, `GF_ANALYTICS_CHECK_FOR_UPDATES=false`,
  `GF_ANALYTICS_REPORTING_ENABLED=false`. First check those three names against Grafana 13's configuration reference
  (`[plugins] preinstall_disabled`, `[analytics] check_for_updates`, `[analytics] reporting_enabled`); if one differs,
  use the documented name and say so in the commit message. Mount the provisioning and dashboards folders read-only.
  Comment the port line as the console's is commented.

- [ ] **Step 5: Add `test-grafana`** to the justfile: `{{pytest}} -q tools/tests/test_grafana_container.py`. `test`
  needs no change: `test-py` already runs the whole `tools` folder, and the smoke test skips itself without Docker
  (as inside the CI test image). Add `just test-grafana` as a step of the workflow's `ui` job, after `just test-ui`,
  since that job runs on the runner where Docker is available.

- [ ] **Step 6: Run and see them pass**

Run: `.venv/bin/pytest -q tools/tests/test_grafana_files.py tools/tests/test_grafana_container.py`
Expected: all pass (the first run pulls the image). If Grafana cannot write the fresh named volume as a non-472 UID,
switch `grafana-data` to a gitignored bind mount `./.grafana-data` and add it to `.gitignore`; the test stays as it is.

- [ ] **Step 7: Commit**

```bash
git add compose.yaml .env.example tools/tests justfile .github/workflows/image.yml
git commit -m "feat: run Grafana beside the console, on loopback, with a Docker smoke test"
```

---

### Task 3: `just ui` starts and stops Grafana, and the console links to it

**Files:**
- Modify: `justfile` (recipes `grafana-up`, `grafana-down`; `ui`)
- Modify: `compose.yaml` (the console's command gains `--grafana-url`)
- Modify: `tools/livectl/cli.py` (`ui --grafana-url`)
- Modify: `tools/livectl/server.py` (`Console.grafana_url`, the `grafana` endpoint)
- Modify: `tools/livectl/site/endpoints.js` (row *Grafana* with *Open*)
- Create: `tools/tests/test_just_grafana.py`
- Modify: `tools/tests/test_server.py`, `tools/tests/test_ui.py`, `tools/tests/scenarios.py`

**Interfaces:**
- Consumes: compose service `grafana`, port 3000, dashboard uid `mediaconnect-source`.
- Produces: `livectl ui --grafana-url URL` (optional, default none); `Console.grafana_url: Optional[str] = None`;
  `payload["endpoints"]["grafana"]` (the URL or `None`).

- [ ] **Step 1: Write the failing justfile tests**, `tools/tests/test_just_grafana.py`

Skip the module unless `just` is on PATH. Each test runs `just <recipe>` from the repo root with `PATH` starting with a
`tmp_path/bin` holding a fake `docker` script (records its arguments to `calls.txt`; behaviour set per test) and with
`LIVECTL=<tmp_path>/bin/livectl`, a fake that records its arguments and exits with a chosen code.

```python
def test_grafana_up_skips_when_docker_is_missing(...):    # PATH without docker
    assert result.returncode == 0 and "Grafana skipped: docker not found" in result.stdout

def test_grafana_up_skips_when_compose_fails(...):        # fake docker exits 1 for `compose up`
    assert result.returncode == 0 and result.stdout.startswith("Grafana skipped:")

def test_ui_passes_the_published_grafana_url(...):        # fake `docker compose port grafana 3000` prints 127.0.0.1:3999
    assert "--grafana-url http://127.0.0.1:3999/d/mediaconnect-source" in livectl_args()

def test_ui_without_grafana_passes_no_url(...):           # docker missing
    assert "--grafana-url" not in livectl_args()

def test_ui_stops_grafana_when_the_console_exits(...):    # fake livectl exits 3
    assert docker_calls()[-1] == "compose stop grafana"
    assert result.returncode == 3                          # the console's exit code survives
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest -q tools/tests/test_just_grafana.py`
Expected: FAIL (no `grafana-up` recipe; `ui` calls nothing).

- [ ] **Step 3: Write the recipes.** `grafana-up` prints `Grafana skipped: docker not found` when `docker` is missing,
  `Grafana skipped: <last line of compose's error>` when `docker compose up -d grafana` fails, and otherwise prints the
  dashboard URL built from `docker compose port grafana 3000`. `grafana-down` runs `docker compose stop grafana`,
  silent and successful without Docker. `ui` (bash shebang recipe) runs `grafana-up`, takes the URL from its output
  when it starts with `http`, sets `trap 'just grafana-down' EXIT`, then runs
  `{{livectl}} ui --port {{port}} [--grafana-url URL]` and exits with the console's code.

- [ ] **Step 4: Run them and see them pass**

Run: `.venv/bin/pytest -q tools/tests/test_just_grafana.py`
Expected: all pass.

- [ ] **Step 5: Write the failing console tests**

```python
# test_server.py
def test_the_grafana_link_is_an_endpoint_when_given(aws):
    console = stub_console(); console.grafana_url = "http://127.0.0.1:3000/d/mediaconnect-source"
    _, payload = call(console, "GET", "/api/pipeline")
    assert payload["endpoints"]["grafana"] == "http://127.0.0.1:3000/d/mediaconnect-source"

def test_no_grafana_link_without_one(aws):
    _, payload = call(stub_console(), "GET", "/api/pipeline")
    assert payload["endpoints"]["grafana"] is None

# test_ui.py, scenario "on-air-playing" with console.grafana_url set by a new scenario key `grafana`
def test_the_endpoints_card_opens_grafana(open_scenario):
    expect(page.locator('#endpoints [data-copy="grafana"] ~ a')).to_have_attribute(
        "href", "http://127.0.0.1:3000/d/mediaconnect-source")

def test_the_endpoints_card_has_no_grafana_row_without_it(open_scenario):
    expect(page.locator("#endpoints dt", has_text="Grafana")).to_have_count(0)
```

The two "no link" payload paths that return early (`"endpoints": {}`) need no change.

- [ ] **Step 6: Implement.** `cli.py`: `ui.add_argument("--grafana-url", default=None, help=...)`, passed to `Console`.
  `server.py`: the field and `"grafana": console.grafana_url` in `endpoints`. `endpoints.js`: row
  `['grafana', 'Grafana']` after *Player*, with an *Open* link like the player's; rows whose value is missing are
  left out for `grafana` only (the others keep their `—`). `compose.yaml`: the console's command gains
  `"--grafana-url", "http://127.0.0.1:${GRAFANA_PORT:-3000}/d/mediaconnect-source"`.

- [ ] **Step 7: Run the console tests and see them pass**

Run: `.venv/bin/pytest -q tools/tests/test_server.py tools/tests/test_ui.py -k grafana`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add justfile compose.yaml tools
git commit -m "feat: start Grafana with just ui and link to it from the console"
```

---

### Task 4: Docs

**Files:**
- Modify: `README.md` (new `## Grafana` section after *Operating it*; checklist items 18-22; repository layout lists
  `observability/`; *Costs* mentions the dashboard)
- Modify: `docs/cost-estimate.md` (a *While the Grafana dashboard is open* subsection)
- Modify: `AGENTS.md` (*Money first*, *Containers*, the `127.0.0.1:` rule)

- [ ] **Step 1: README `## Grafana`:** how it starts (`docker compose up`, `just ui`, skipped without Docker); open
  `http://127.0.0.1:3000`; first login `admin`/`admin` then a new password, kept in the `grafana-data` volume, or
  `GRAFANA_ADMIN_PASSWORD` set before the first start; the five tiles and three graphs with one line each from the
  spec's metric table; the cost while open (about $0.06/h to $0.40/h, nothing while closed; close the tab when not
  watching); SSO profiles untested; CloudWatch keeps 1-second data for 3 hours.

- [ ] **Step 2: README live checklist**, numbered after 17:
  18. `just ui` starts Grafana too; Ctrl-C stops both (`docker compose ps` shows no grafana).
  19. First login asks for a new password; after `just ui` again it is kept.
  20. On air with the test source: the five tiles show values within about 30 s; note the delay from *Send test
      source* to the first bitrate point.
  21. Stop the source: *Connected* turns red and the bitrate graph drops.
  22. Legends and tiles show only the live flow's name, not destroyed flows or ARNs.
  23. The next day, Cost Explorer's CloudWatch GetMetricData charge for the hours the dashboard was open.

- [ ] **Step 3: `docs/cost-estimate.md`:** the GetMetricData line ($0.01 per 1,000 metrics requested), 8 panels × 720
  refreshes an hour, both figures from the spec's *Cost* section, and that search billing is unverified.

- [ ] **Step 4: AGENTS.md:** *Money first* gains "Close the Grafana dashboard when you are not watching it: each open
  tab queries CloudWatch every 5 seconds."; *Containers* gains one line on the `grafana` service (pinned image,
  `~/.aws` read-only, provisioning from `observability/grafana/`); the `127.0.0.1:` rule names `GRAFANA_PORT` too.

- [ ] **Step 5: Run the full offline suite**

Run: `just fmt && just test`
Expected: everything passes; `test_grafana_container.py` runs where Docker is available.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/cost-estimate.md AGENTS.md
git commit -m "docs: the Grafana dashboard, its cost and its live checks"
```
