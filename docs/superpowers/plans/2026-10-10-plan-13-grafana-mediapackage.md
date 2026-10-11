# Grafana for MediaPackage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A *MediaPackage origin* dashboard in the existing Grafana: ingest from MediaLive, egress to CloudFront and CDN
authorization, on 60-second CloudWatch data, refreshed every minute.

**Architecture:** One more provisioned dashboard JSON file beside the three existing ones; no compose, provisioning,
justfile, livectl or Terraform change. Every metric query is a CloudWatch `SEARCH` on the metric's full dimension set,
scoped by `ChannelGroup="live-sports-aws-demo"`, wrapped in `REMOVE_EMPTY`. No template variable.

**Tech Stack:** Grafana 13.0.2 dashboard JSON, CloudWatch metric math, pytest (+ PyYAML, dev only), Playwright for the
browser pass.

**Spec:** `docs/superpowers/specs/2026-10-10-grafana-mediapackage-design.md`

**Where:** the worktree `/home/wmacedo/terraform-test-project-mediapackage`, branch `feat/grafana-mediapackage`.
Another session uses the main checkout on `feat/grafana-cloudfront`: never switch branches or commit there. The
worktree has no `.venv`; run tests with
`LIVECTL_PYTEST=/home/wmacedo/terraform-test-project/.venv/bin/pytest just test-py` (likewise `test-grafana`).

## Global Constraints

- Dashboard uid `mediapackage-origin`, title *MediaPackage origin*, tags `["live-sports-aws", "mediapackage"]`, refresh
  `1m`, timezone `utc`, time `now-1h` to `now`, `editable: false`, `graphTooltip: 1`.
- Links: `mediapackage-origin` → `[("/d/medialive-channel", True)]` (title *MediaLive channel*); `medialive-channel`
  gains a third link `("/d/mediapackage-origin", True)` (title *MediaPackage origin*).
- Every metric query: data source uid `cloudwatch`, `queryMode: "Metrics"`, `metricEditorMode: 1`, `region: "default"`,
  `namespace: "AWS/MediaPackage"`, `metricName` and `statistic` set, `period: "60"`, expression exactly

  ```
  REMOVE_EMPTY(SEARCH('{AWS/MediaPackage,<dims>} MetricName="<metric>" ChannelGroup="live-sports-aws-demo"<filter>', '<statistic>', 60))<math>
  ```

  `<filter>` is empty or one ` Name="value"` term (tiles only); `<math>` is empty, or ` * 8 / 60 / 1000000` on byte
  metrics (unit `Mbits`).
- Dimension sets (`<dims>`), verbatim:

  | Key | Metric | `<dims>` | Statistic(s) |
  |---|---|---|---|
  | ingress-requests | `IngressRequestCount` | `ChannelGroup,Channel,StatusCode` | Sum |
  | ingress-bytes | `IngressBytes` | `ChannelGroup,Channel` | Sum |
  | ingress-time | `IngressResponseTime` | `ChannelGroup,Channel` | Average, Maximum |
  | active-input | `ActiveInput` | `ChannelGroup,Channel,InputIndex` | Minimum |
  | egress-requests | `EgressRequestCount` | `ChannelGroup,Channel,OriginEndpoint,StatusCode,RequestType` | Sum |
  | egress-bytes | `EgressBytes` | `ChannelGroup,Channel,OriginEndpoint,RequestType` | Sum |
  | egress-time | `EgressResponseTime` | `ChannelGroup,Channel,OriginEndpoint,RequestType` | Average, Maximum |
  | cdn-auth | `EgressRequestCount` | `ChannelGroup,Channel,OriginEndpoint,CDNAuthorizationStatus,CDNAuthorizationStatusDetails` | Sum |

- 18 metric queries: 10 in graphs, 8 in tiles (spec, *Cost*).
- Never: `IngestEndpoint`, any `MQCS` metric, any `Multiview` metric, a template variable.
- Nothing in tasks 1-2 calls AWS. Task 3 reads CloudWatch only through Grafana, for one dashboard view.
- Conventional Commits, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A healthy stack has no error series at all** (a status class exists only once a request in it happened). Error
   tiles must read *none* in a neutral colour, never *No data* in red or green. Pinned by
   `test_mediapackage_error_tiles_read_none_without_data` in Task 1.
2. **No viewers:** the egress queries return nothing. Panels must not error and their descriptions must say no data means
   no viewers. Pinned by `test_mediapackage_egress_panels_say_no_data_means_no_viewers` in Task 1.
3. **Metric math on a `SEARCH` array** (`... * 8 / 60 / 1000000`): if CloudWatch or Grafana's code mode refuses it, the
   bitrate panels error. Checked in the browser in Task 3; fallback there.
4. **`EgressRequestCount` read under two dimension sets** (status, and CDN authorization): a test keyed on metric name
   alone would let one silently replace the other. Pinned by keying the tests on `(metric, dims, statistic)` in Task 1.
5. **`ActiveInput` reading 0 vs. stopping** when input stops: the tile maps 0 to *inactive* and no data stays neutral;
   which one happens is live check 33 (Task 2).

---

### Task 1: The dashboard file and its offline tests

**Files:**
- Create: `observability/grafana/dashboards/mediapackage-origin.json`
- Modify: `observability/grafana/dashboards/medialive-channel.json` (`links`: append one)
- Modify: `tools/tests/test_grafana_files.py` (new section `# --- MediaPackage origin ---` at the end; the MediaLive
  identity test's link list gains `("/d/mediapackage-origin", True)`)
- Modify: `tools/tests/test_grafana_container.py` (the new uid loads, folder *Live pipeline*)

**Interfaces:**
- Consumes: the helpers already in `test_grafana_files.py`: `dashboard`, `panels`, `panel`, `metric_targets`, `names`.
- Produces: panel `(type, title)` in order:
  stats *Ingest bitrate*, *Push errors, last 5 min*, *Input active*, *Origin errors, last 5 min*,
  *Direct origin requests, last hour*, *Manifest response time, last 5 min*;
  rows (`type: "row"`, not collapsed) *Ingest from MediaLive*, *Origin to CloudFront*, *CDN authorization*, each
  followed by its timeseries: *Pushes* (A `IngressRequestCount`, B `ActiveInput` on the right axis), *Ingest bitrate*,
  *Push response time* (A Average, B Maximum); *Requests*, *Egress bitrate*, *Response time* (A Average, B Maximum);
  *Requests by authorization*.

- [ ] **Step 1: Write the failing tests** in `test_grafana_files.py`:

  ```python
  MP = "mediapackage-origin"
  GROUP = "live-sports-aws-demo"
  CG, CGE = "ChannelGroup,Channel", "ChannelGroup,Channel,OriginEndpoint"
  # (metric, dims) -> statistics. EgressRequestCount appears twice: by status and by CDN authorization.
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
  MBPS = " * 8 / 60 / 1000000"
  ```

  A helper `mp_parse(target) -> (metric, dims, filter, statistic, math)` that matches the expression against the
  Global Constraints form with one regex, and fails the test if it does not match.
  - `test_the_mediapackage_dashboard_identity_and_links`: identity per Global Constraints; `templating.list == []`;
    links `[("/d/medialive-channel", True)]`.
  - `test_every_mediapackage_query_searches_this_projects_origin_every_minute`: for each metric target, `mp_parse`
    succeeds; data source `cloudwatch`, namespace `AWS/MediaPackage`, `target["metricName"] == metric`,
    `target["statistic"] == statistic`, `period == "60"`; `statistic in MP_METRICS[(metric, dims)]`;
    `math == MBPS` exactly when the metric ends in `Bytes`, else `""`.
  - `test_the_mediapackage_dashboard_reads_every_metric_and_statistic`:
    `{(m, d, s) for graph targets} == {(m, d, s) for (m, d), ss in MP_METRICS.items() for s in ss}`;
    `len(metric_targets(MP)) == 18`; no graph target has a filter.
  - `test_the_mediapackage_panels_follow_the_signal`: the `(type, title)` list from Interfaces.
  - `test_mediapackage_tiles`: per tile, `(timeFrom, hideTimeOverride)`, calcs and its targets' `(metric, filter)`:
    *Ingest bitrate* `("5m", True)`, `["lastNotNull"]`, IngressBytes no filter;
    *Push errors, last 5 min* `("5m", True)`, `["sum"]`, A `StatusCode="4xx"`, B `StatusCode="5xx"`, names
    `{"A": "4xx", "B": "5xx"}`, `textMode "value_and_name"`;
    *Input active* `("5m", True)`, `["min"]`, ActiveInput;
    *Origin errors, last 5 min* as push errors on `EgressRequestCount` (status dims);
    *Direct origin requests, last hour* `("1h", True)`, `["sum"]`, cdn-auth dims with `CDNAuthorizationStatus="Unauthorized"`;
    *Manifest response time, last 5 min* `("5m", True)`, `["max"]`, `EgressResponseTime` Maximum with
    `RequestType="manifest"`, unit `ms`.
  - `test_mediapackage_error_tiles_read_none_without_data`: for the two error tiles and *Direct origin requests*:
    `noValue == "none"`, `colorMode == "background"`, steps `[(None, "green"), (1, "red")]`. *Ingest bitrate* and
    *Manifest response time* have `colorMode "none"`.
  - `test_input_active_says_active_or_inactive`: value mappings `0 → "inactive"` (red), `1 → "active"` (green);
    `noValue` absent (Grafana's neutral *No data*).
  - `test_mediapackage_series_labels`: label `${PROP('Dim.StatusCode')}` on ingress requests;
    `${PROP('Dim.RequestType')} ${PROP('Dim.StatusCode')}` on egress requests; `${PROP('Dim.RequestType')}` on egress
    bytes; `${PROP('Dim.RequestType')} average` / `${PROP('Dim.RequestType')} max` on egress time;
    `Average` / `Maximum` on ingress time; `Input active` on ActiveInput; `Ingest` on ingress bytes;
    `${PROP('Dim.CDNAuthorizationStatusDetails')}` on cdn-auth.
  - `test_cdn_authorization_is_red_except_a_matched_secret`: *Requests by authorization* draws bars, stacked
    (`custom.drawStyle "bars"`, `custom.stacking.mode "normal"`), default colour fixed red, one override by name
    `HeaderSecretMatched` → fixed green.
  - `test_mediapackage_units_and_axes`: `Mbits` on both bitrate graphs and the *Ingest bitrate* tile; `ms` on both
    response-time graphs; *Pushes* override for refId B has `custom.axisPlacement "right"`; every timeseries has
    `custom.axisSoftMin == 0`.
  - `test_mediapackage_egress_panels_say_no_data_means_no_viewers`: the three *Origin to CloudFront* graphs and
    *Requests by authorization* have a `description` containing `"no viewers"`.
  - `test_input_active_is_described_as_undocumented`: the *Input active* tile's description contains
    `"not in the MediaPackage user guide"`.
  - `test_no_mediapackage_query_reads_a_left_out_metric`: no expression contains `IngestEndpoint`, `MQCS` or
    `Multiview`.

  In `test_grafana_container.py`, after the MediaLive block: `/api/dashboards/uid/mediapackage-origin` → folder
  *Live pipeline*, title *MediaPackage origin*.

- [ ] **Step 2: Run** `LIVECTL_PYTEST=/home/wmacedo/terraform-test-project/.venv/bin/pytest just test-py`.
  Expected: the new tests FAIL (file missing), and the MediaLive identity test fails on the missing link.

- [ ] **Step 3: Write `mediapackage-origin.json`** in the shape of `medialive-channel.json` (stat tiles 4 wide at the
  top, two rows of three; graphs 8 wide, three per row; the authorization graph 24 wide). Each panel `description`
  quotes the user guide's sentence for its metric (*Monitoring with CloudWatch metrics*), plus the "no viewers" and
  "not in the MediaPackage user guide" sentences the tests pin. Generating it with a throwaway script in the scratchpad
  is fine; only the JSON is committed. Append the link to `medialive-channel.json`.

- [ ] **Step 4: Run** `just test-py` and `just test-grafana` (same `LIVECTL_PYTEST`). Expected: PASS, apart from the
  known flaky player-frame test.

- [ ] **Step 5: Commit**

  ```bash
  git add observability/grafana/dashboards tools/tests/test_grafana_files.py tools/tests/test_grafana_container.py
  git commit -m "feat: a MediaPackage origin dashboard in Grafana"
  ```

### Task 2: Docs

**Files:** `README.md` (Grafana section and the live checklist), `docs/cost-estimate.md` (*While a Grafana dashboard
is open*), `AGENTS.md` (the *Close the Grafana dashboard* bullet).

- [ ] **Step 1: README.** In the Grafana section, a *MediaPackage origin* part: each tile's meaning; why direct origin
  requests are red (anything reaching the origin without CloudFront's header); why the egress rows are empty without
  viewers; why error tiles read *none*; 1-minute refresh and why (the user guide guarantees one-minute data); about
  0.02 USD/h while open. Append the spec's six live checks to the checklist as 30-35.
- [ ] **Step 2: Cost estimate.** A row *MediaPackage origin*: 18 queries, about 30 series a refresh, 60 refreshes an
  hour, about 1,800 metrics an hour, about 0.02 USD/h, same figure when every deploy counts (shared names). Change the
  section's opening line, which says every dashboard refreshes every 5 seconds.
- [ ] **Step 3: AGENTS.md.** The bullet says MediaPackage refreshes every minute and keeps the cost range's upper figure.
  Expect a conflict with `feat/grafana-cloudfront`, which edits the same bullet; do not pre-empt it.
- [ ] **Step 4: Commit** `docs: the MediaPackage dashboard, its tiles, its cost and its live checks`.

### Task 3: Drive it in the browser against real data

60-second data is kept for 15 days, so the 2026-10-09 session renders.

- [ ] **Step 1:** start a throwaway Grafana from `compose.yaml` under its own project name
  (`-p mp-check`), fresh volume, real `~/.aws` (read-only), `GRAFANA_PORT=0`.
- [ ] **Step 2:** with Playwright, log in and open
  `/d/mediapackage-origin?from=2026-10-09T18:00:00Z&to=2026-10-09T19:00:00Z`. Expected: *Pushes* about 52 a minute,
  2xx; *Ingest bitrate* about 9.9 Mbps; *Requests by authorization* has red `MissingCdnAuthHeader` bars (20 egress 4xx
  in that hour); no panel shows an error. Open the query inspector on *Ingest bitrate* and confirm the math was
  sent and answered.
  - If CloudWatch or Grafana refuses the math: drop `<math>` from the expression, convert in Grafana instead (unit
    `decbytes` over the period, or a *Binary operation* transformation), update the constraint and the test, and say so
    in the spec's *Checked live*.
- [ ] **Step 3:** follow each tile and link: *MediaLive channel* ↔ *MediaPackage origin* keep the time range; tiles read
  *none* / *No data* (the tiles look at "now", which has no session).
- [ ] **Step 4:** add what Step 2 showed to the spec's *Checked live*; tear the project down (`down -v`).
- [ ] **Step 5: Commit** `docs: what the MediaPackage dashboard showed against real data`.
