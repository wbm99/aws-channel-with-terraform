# Grafana for MediaLive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A *MediaLive channel* dashboard in the existing Grafana, 5-second CloudWatch data, with a channel drop-down
whose default *All* shows whichever channel is running.

**Architecture:** One more provisioned dashboard JSON file beside the two MediaConnect ones; no compose, provisioning,
justfile or livectl change. Every metric query is a CloudWatch `SEARCH` on the metric's exact dimension set plus the
`channel` variable, wrapped in `REMOVE_EMPTY`.

**Tech Stack:** Grafana 13.0.2 dashboard JSON, CloudWatch metric math, pytest (+ PyYAML, dev only).

**Spec:** `docs/superpowers/specs/2026-10-10-grafana-medialive-design.md`

## Global Constraints

- Dashboard uid `medialive-channel`, title *MediaLive channel*, tags `live-sports-aws`, `medialive`, refresh `5s`,
  timezone `utc`, time `now-15m` to `now`, `editable: false`, `graphTooltip: 1`.
- Every metric query: data source uid `cloudwatch`, `region: default`, `metricEditorMode: 1`, `statistic` set,
  `period: "5"`, expression exactly
  `REMOVE_EMPTY(SEARCH('{AWS/MediaLive,<dims>} MetricName="<metric>" $channel', '<statistic>', 5))`.
- Variable `channel`: CloudWatch dimension values of `ChannelId` in `AWS/MediaLive` (metric `ActiveAlerts`),
  `includeAll: true`, `allValue: "ChannelId"`, current value *All*, `refresh: 1` (on load), not multi.
  Settled 2026-10-10 against real data: a bare ID term returns only that channel; the term `ChannelId` matches all
  eight, and `REMOVE_EMPTY` keeps those with data.
- The sixteen metrics, statistics and dimension sets: the spec's table, verbatim.
- 22 metric queries: each of the sixteen once in a graph (or, for `ActiveAlerts`, its tile), and again in the six
  tiles that read the last 2 or 5 minutes (a `-- Dashboard --` reuse would get the graph's whole range).
- Nothing in tasks 1-3 calls AWS. Conventional Commits, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **The drop-down is empty** (no credentials, or no metrics yet): *All* must still substitute `ChannelId`, so panels
   work once data arrives. Pinned by the `allValue` assertion in Task 1.
2. **A redeploy within the 15-minute range:** two channels have data under *All*. Single-metric graphs label each
   series by channel ID; multi-metric graphs name series by metric, as on MediaConnect. Pinned by the label test.
3. **A dead channel picked:** panels show "No data", tiles stay neutral, not green. Pinned by the `colorMode`/`noValue`
   assertions in Task 1.
4. **A search on the wrong dimension set** silently returns nothing (e.g. `OutputVideoFrameRate` without
   `OutputName`). Pinned by the per-metric dimension assertion in Task 1.
5. **Grafana does not interpolate `allValue` literally** into a CloudWatch code-mode expression. Checked in the
   browser in Task 3.

---

### Task 1: The dashboard file and its offline tests

**Files:**
- Create: `observability/grafana/dashboards/medialive-channel.json`
- Modify: `tools/tests/test_grafana_files.py` (new section `# --- MediaLive channel ---`)
- Modify: `tools/tests/test_grafana_container.py` (the new uid loads, folder *Live pipeline*)

**Interfaces:**
- Produces: panel titles, in order: stats *Active alerts*, *Input frame rate*, *Input loss, last 5 min*,
  *Dropped frames, last 5 min*, *SVQ time*, *Push errors, last 5 min*; timeseries *Input frame rate*,
  *Input problems*, *Encoder*, *Output frame rate*, *Push to MediaPackage*, *Network*, *Audio*; table
  *Channel events*.

- [ ] **Step 1: Write the failing tests** in `test_grafana_files.py`:
  - `ML_METRICS = {metric: (statistic, dims)}` from the spec's table; `ML = "medialive-channel"`.
  - `test_the_medialive_dashboard_identity_variable_and_links`: identity per Global Constraints; links
    `[("/d/mediaconnect-source", True), ("/d/mediaconnect-detail", True)]`; the variable as in Global Constraints.
  - `test_every_medialive_query_searches_the_picked_channel_every_5_seconds`: expression, statistic, dims, period.
  - `test_the_medialive_dashboard_reads_each_metric_once`: `sorted(read) == sorted(ML_METRICS)`.
  - `test_the_medialive_panels`: the titles above, in order.
  - `test_medialive_tiles_read_now_or_the_last_5_minutes`: "now" tiles (`Active alerts`, `Input frame rate`,
    `SVQ time`) `timeFrom "2m"`, calcs `["lastNotNull"]`; "last 5 min" tiles `timeFrom "5m"`, calcs `["sum"]`.
  - `test_medialive_problem_tiles_turn_red_from_1`: all but `Input frame rate` have steps
    `[(None, "green"), (1, "red")]`, `colorMode "background"`; `Input frame rate` `colorMode "none"`.
  - `test_medialive_legends_name_rendition_audio_or_channel`: label `${PROP('Dim.OutputName')}` on
    `OutputVideoFrameRate`, `${PROP('Dim.AudioDescriptionName')}` on the audio metrics, and a label containing
    `${PROP('Dim.ChannelId')}` on all others.
  - `test_channel_events_reads_medialive_events`: Logs query on `EVENTS_GROUP`, filter on `detail-type` starting
    `MediaLive`, `filterFieldsByName` keeps `@timestamp`, `Event`, `State`, `Detail`, sorted by Time desc.
  - The MediaConnect link test now expects each MediaConnect dashboard to link to the other and to `/d/medialive-channel`.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tools/tests/test_grafana_files.py` — new tests FAIL (file missing).
- [ ] **Step 3: Write the dashboard JSON** following `mediaconnect-source.json`'s panel shape (stat tiles at the top,
  `gridPos` width 4, rows below at width 12 or 24). Generate it with a throwaway script in the scratchpad if that is
  easier; only the JSON is committed. Add the `/d/medialive-channel` link to both MediaConnect dashboards.
- [ ] **Step 4: Run** `just test-py` — PASS; `just test-grafana` — PASS.
- [ ] **Step 5: Commit** `feat: a MediaLive channel dashboard in Grafana`.

### Task 2: Docs

**Files:** `README.md` (Grafana section), `docs/cost-estimate.md` (*While a Grafana dashboard is open* table),
the spec's *Finding the channel* (the settled term).

- [ ] **Step 1:** README: the dashboard, each tile's meaning, the drop-down and why *All* is the default, the live
  checks from the spec appended to the checklist with the next numbers.
- [ ] **Step 2:** cost estimate: row *MediaLive channel*, 22 queries, 15,840 an hour, about 0.16 / up to about 1.38.
- [ ] **Step 3: Commit** `docs: the MediaLive dashboard, its drop-down and its cost`.

### Task 3: Drive it in the browser

- [ ] **Step 1:** start a throwaway Grafana from `compose.yaml` under its own project name, fresh volume, real
  `~/.aws` (read-only), random port.
- [ ] **Step 2:** with Playwright, log in, open `/d/medialive-channel?from=2026-10-09T18:00:00Z&to=2026-10-09T19:00:00Z`.
  Expected: the drop-down lists the channel IDs, *All* selected; the query inspector shows `ChannelId` substituted;
  panels say "No data" (1-second data is older than 3 hours), not an error. Pick `6482454`: the inspector shows the ID.
- [ ] **Step 3:** screenshot the empty-state dashboard for the README check; tear the project down (`down -v`).
