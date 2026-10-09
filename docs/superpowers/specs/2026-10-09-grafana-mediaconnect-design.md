# Grafana for MediaConnect: Design

Date: 2026-10-09
Status: Draft, for review
Branch: `feat/grafana` (from `main`)

This document has two parts: a product part (PRD: what and why) and a technical part (TRD: how).

It is the first step of observability for the whole pipeline. It covers MediaConnect only; MediaLive, MediaPackage and
CloudFront get their own dashboards later, on the same Grafana.

---

# Part 1: PRD

## Purpose
Watch the SRT contribution feed the way a broadcast engineer would: second by second, on graphs, with the few figures
that say whether the source is healthy. The console's chain shows one-minute values; this shows 5-second ones and
their history.

## Audience
The author, running and writing about the pipeline, and a reviewer running the stack on their own account.

## Goals
1. A Grafana runs locally in a container and shows a **MediaConnect source** dashboard.
2. It starts with the console: `docker compose up` starts both, and so does `just ui`, which also stops Grafana when the
   console exits.
3. The dashboard refreshes every 5 seconds, on 5-second CloudWatch data.
4. It needs no editing after a redeploy, even though the flow's ARN changes every time.
5. Grafana is reachable from this machine only, and asks for a login.

## Success criteria
1. With the stack on air and the test source sending, the five tiles show values within about 30 seconds of
   *Send test source* (live check; the CloudWatch publishing delay is measured then, not assumed).
2. A fresh `docker compose up` (no Grafana volume) comes up with the data source and the dashboard already there.
3. The Grafana port is published on `127.0.0.1` only.
4. `just test` checks the provisioning files offline; `just test-grafana` starts the real image with no AWS credentials
   and proves the provisioning loads.

## Non-goals
- **A local database** (Prometheus, InfluxDB). Grafana reads CloudWatch directly. A database would only add history
  beyond CloudWatch's 3 hours of 1-second data, or data CloudWatch does not have; it can join the compose file later.
- **MediaLive, MediaPackage and CloudFront dashboards.** Later.
- **Alerting** in Grafana.
- **Querying at 1 second.** The data exists, but every query stays at a 5-second period.
- **A flow selector.** The search finds the flow by name; a drop-down is the fallback if the search proves expensive.

## The metrics

The five, all in the `AWS/MediaConnect` namespace with the `FlowARN` dimension:

| Metric | Statistic | Shown as | Why |
|---|---|---|---|
| `SourceConnected` | Maximum | tile: connected (green) / disconnected (red) | Is the encoder there at all. |
| `SourceBitRate` | Average | tile in Mbps, and a graph | Is the feed alive and at the rate it was set to. |
| `SourceRoundTripTime` | Average | tile in ms, and a graph | The network path. Retransmissions only help while RTT stays well under the SRT latency. |
| `SourcePacketLossPercent` | Average | tile in %, and on the loss graph | Loss on the wire before SRT repairs it: the early warning. |
| `SourceNotRecoveredPackets` | Sum | tile (sum of the last 5 minutes), and on the loss graph | Loss SRT could not repair: visible damage. |

Loss without unrecovered packets means SRT is doing its job; both on one graph shows that.

## Checked live and in the documentation (2026-10-09)

1. **MediaConnect publishes every flow metric at 1-second resolution** (since June 2023). CloudWatch keeps 1-second
   data for 3 hours, then rolls it up.
2. **Grafana 13.0.2 reads credentials from a mounted `~/.aws`** with the "AWS SDK Default" auth (throwaway spike, the
   author's account): the data-source health check passed, and a search query returned today's flow at a 5-second
   period (1,151 points) and at 1 second (5,754 points).
3. **The search also returns destroyed flows** from the last two weeks, as empty series.
4. **A CloudWatch query sent through Grafana's API must carry a `statistic`**, even when it is a search expression;
   without one the plugin answers 500.
5. **Running as the host user's UID** (needed: `~/.aws/credentials` is mode 600) makes Grafana's background plugin
   installer fail on its bundled plugins. Harmless, but logged as an error.

Not verified:
- **SSO profiles.** The author's account uses an IAM user's access keys. Whether Grafana's AWS SDK handles an SSO profile
  from the mounted `~/.aws` is untested; the README says so.
- **The CloudWatch publishing delay**, the time between an event and its datapoint being readable. Measured on the
  first live run.
- **How a search is billed.** CloudWatch charges $0.01 per 1,000 metrics requested; whether each flow a search matches
  counts as a metric requested is not documented where we looked. See *Cost*.

## Cost

GetMetricData costs $0.01 per 1,000 metrics requested, and Grafana only queries while a dashboard is open.

- 9 queries (8 panels; *Loss* has two) × 720 refreshes an hour = 6,480 queries an hour.
- If a search counts only the live flow: about **$0.065/h** while open.
- If it counts every flow it matches (7 on 2026-10-09): up to about **$0.45/h** while open. Destroyed flows drop out
  two weeks after their last datapoint.
- Nothing while the dashboard is closed.

The real charge is read in Cost Explorer the day after the first live run (live check). If it is the higher figure and
that matters, the fallback is a flow drop-down: one flow per query.

---

# Part 2: TRD

## Units

| Unit | Purpose |
|---|---|
| `compose.yaml`, service `grafana` | Runs the pinned image, on 127.0.0.1, with the provisioning and `~/.aws` mounted. |
| `observability/grafana/provisioning/datasources/cloudwatch.yaml` | The CloudWatch data source. |
| `observability/grafana/provisioning/dashboards/provider.yaml` | Tells Grafana to load dashboards from the folder below. |
| `observability/grafana/dashboards/mediaconnect-source.json` | The dashboard. |
| `justfile`: `ui`, `grafana-up`, `grafana-down`, `test-grafana` | Start and stop Grafana with the console; the smoke test. |
| `tools/tests/test_grafana_files.py` | Offline checks on the files above. |
| `tools/livectl` Endpoints card | A *Grafana* row linking to the dashboard. |

## The `grafana` service

- **Image:** `grafana/grafana-oss:13.0.2`, pinned like everything else.
- **Port:** `"127.0.0.1:${GRAFANA_PORT:-3000}:3000"`. Same rule as the console: never drop the `127.0.0.1:` prefix.
- **User:** `"${UID:-1000}:${GID:-1000}"`, so it can read `~/.aws/credentials`.
- **Volumes:**
  - `./observability/grafana/provisioning` → `/etc/grafana/provisioning`, read-only;
  - `./observability/grafana/dashboards` → `/var/lib/grafana/dashboards`, read-only;
  - `${HOME}/.aws` → `/aws`, **read-only** (Grafana never writes credentials; SSO refresh is the console's job), with
    `create_host_path: false` as for the console;
  - a named volume `grafana-data` → `/var/lib/grafana`, for the admin password and any edits.
- **Environment:**
  - `AWS_CONFIG_FILE=/aws/config`, `AWS_SHARED_CREDENTIALS_FILE=/aws/credentials`, `AWS_PROFILE=${AWS_PROFILE:-default}`;
  - `AWS_REGION=${AWS_REGION:-us-east-1}`, read by the data source's provisioning file;
  - `GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD:-admin}`. With the default, Grafana asks for a new password
    at the first login; the volume keeps it. Grafana only reads this variable when it creates its database, which the
    README says;
  - `GF_AUTH_ANONYMOUS_ENABLED=false`;
  - `GF_DASHBOARDS_MIN_REFRESH_INTERVAL=5s`, so no one can set a faster refresh and multiply the bill;
  - `GF_PLUGINS_PREINSTALL_DISABLED=true`, `GF_ANALYTICS_CHECK_FOR_UPDATES=false`, `GF_ANALYTICS_REPORTING_ENABLED=false`:
    no plugin installs (the UID error) and no calls home. The exact setting names are checked against Grafana 13's
    configuration reference while implementing.
- `docker compose up` starts it beside the console; no profile.

## Provisioning

- **Data source:** name `CloudWatch`, `uid: cloudwatch`, type `cloudwatch`, `authType: default`,
  `defaultRegion: $AWS_REGION`. Not editable from the UI (`editable: false`), so the file stays the truth.
- **Dashboard provider:** one folder, *Live pipeline*, from `/var/lib/grafana/dashboards`, `allowUiUpdates: false`.

## The dashboard

- `uid: mediaconnect-source`, title *MediaConnect source*, time range last 15 minutes, refresh `5s`, timezone UTC
  (the burned-in clock is UTC).
- Every query: data source `cloudwatch`, region from the data source, code mode with a search expression, period 5:

  ```
  REMOVE_EMPTY(SEARCH('{AWS/MediaConnect,FlowARN} MetricName="<metric>" live-sports-aws-demo', '<statistic>', 5))
  ```

  The project prefix keeps the search to this repository's flows, by the same naming rule `check-clean` relies on.
  Each query also sets `statistic` (finding 4).
- Panels, top row:
  1. **Connected** (stat): `SourceConnected`, Maximum, last value; value mappings 1 → *connected* (green),
     0 → *disconnected* (red); no data → *no source*.
  2. **Bitrate** (stat): `SourceBitRate`, Average, last value, unit bps shown in Mbps.
  3. **Round trip** (stat): `SourceRoundTripTime`, Average, last value, ms.
  4. **Packet loss** (stat): `SourcePacketLossPercent`, Average, last value, %.
  5. **Not recovered, last 5 min** (stat): `SourceNotRecoveredPackets`, Sum, reduced as the total over the last 5
     minutes; red above 0.
- Below:
  6. **Bitrate** (time series).
  7. **Round trip time** (time series).
  8. **Loss** (time series): packet loss % on the left axis, not-recovered packets on the right.
- Series with no values in the range are dropped by CloudWatch's `REMOVE_EMPTY()`, so destroyed flows (which all
  share the live flow's name) neither split the tiles nor fill the legends. Found when the dashboard was first
  rendered against real data: without it each tile showed seven boxes. Legends show the flow's
  name (the last part of the ARN), not the whole ARN.

## `just`

- `grafana-up`: `docker compose up -d grafana`; if `docker` is missing, or the daemon does not answer, prints
  `Grafana skipped: <reason>` and succeeds.
- `grafana-down`: `docker compose stop grafana`, silent if Docker is missing.
- `ui`: runs `grafana-up`, then the console, and runs `grafana-down` when the console exits, Ctrl-C included (a bash
  `trap`). Inside the console's own container there is no Docker, so `grafana-up` skips.
- `test-grafana`: the smoke test (below). `test` runs it when Docker is available and skips it otherwise, like the
  browser tests.

## Console

The Endpoints card gains a row, *Grafana*, with `http://127.0.0.1:3000/d/mediaconnect-source` and an *Open* link. The
port comes from `GRAFANA_PORT` (default 3000), passed to `livectl ui` by the justfile and by compose.

## Testing

**Offline, `tools/tests/test_grafana_files.py`, part of `just test`:**
- the dashboard is valid JSON; its uid, title and refresh are as above, and the refresh is at least 5 seconds;
- every panel's queries use the data source uid `cloudwatch`, name one of the five metrics, set a statistic, and
  search with the `live-sports-aws-demo` prefix and a period of 5;
- `compose.yaml`'s Grafana port line starts with `127.0.0.1:`;
- the service keeps anonymous access off, the 5-second minimum refresh, and `~/.aws` read-only;
- the provisioning files name the same data-source uid the dashboard uses.

YAML is parsed with PyYAML. It is already installed in the dev environment, but only because moto's `responses`
needs it, so the `dev` extra lists `pyyaml` explicitly. boto3 stays the only runtime dependency.

**With Docker, `just test-grafana`** (CI runs it on the runner, beside `test-ui`):
- start the pinned image with the provisioning mounted, an empty `~/.aws` stand-in and a random port;
- wait for `/api/health`;
- check `/api/datasources/uid/cloudwatch` and `/api/dashboards/uid/mediaconnect-source` both answer 200;
- check the container log has no provisioning error;
- remove the container.

**Live (README checklist, the author's to run):**
1. `just ui`: Grafana starts too; Ctrl-C stops both.
2. First login asks for a new password; after a restart it is kept.
3. On air with the test source: the five tiles show values within about 30 seconds. Note the delay between
   *Send test source* and the first bitrate point.
4. Stop the source: *Connected* turns red, the bitrate graph drops.
5. The next day, Cost Explorer's CloudWatch GetMetricData line for the hours the dashboard was open.

## Docs

- **README:** a *Grafana* section: how it starts, the first login, what each tile means, the cost while open, and
  that SSO profiles are untested. The live checks above join the checklist.
- **`docs/cost-estimate.md`:** the GetMetricData line, with both figures.
- **AGENTS.md:** the `127.0.0.1:` rule covers `GRAFANA_PORT`; *Money first* adds "close the Grafana dashboard when you
  are not watching it"; the repository layout lists `observability/`.
- **`.env.example`:** `GRAFANA_PORT`, `GRAFANA_ADMIN_PASSWORD`, `AWS_REGION`.
