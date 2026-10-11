# Grafana for MediaPackage: Design

Date: 2026-10-10
Status: Draft, for review
Branch: `feat/grafana-mediapackage` (from `main`, in its own worktree)

This document has two parts: a product part (PRD: what and why) and a technical part (TRD: how).

It is the third step of observability for the pipeline, after `2026-10-09-grafana-mediaconnect-design.md` and
`2026-10-10-grafana-medialive-design.md`. It adds one dashboard to the Grafana that already runs beside the console.
CloudFront is being designed separately, on `feat/grafana-cloudfront`.

---

# Part 1: PRD

## Purpose
See the origin: whether MediaLive's pushes arrive and are accepted, whether CloudFront's requests are answered and how
fast, and whether anything reaches the origin without going through CloudFront. MediaPackage v2 publishes request
metrics only (in and out); it has no health metrics like MediaLive's, so the dashboard is built around requests.

## Audience
The author, running and writing about the pipeline, and a reviewer running the stack on their own account.

## Goals
1. A **MediaPackage origin** dashboard in the *Live pipeline* folder, linked both ways with *MediaLive channel*.
2. It refreshes every minute, on 60-second CloudWatch data: the publishing interval the user guide guarantees.
3. It needs no editing after a redeploy, and no drop-down: the channel group, channel and endpoint names are fixed.
4. A request that reaches the origin without CloudFront's authorization header shows red.

## Success criteria
1. With the stack on air and the test source sending, the ingest tiles show values within about 2 minutes (live check).
2. Playing through CloudFront fills the egress rows and shows `HeaderSecretMatched`; a direct request to the origin turns
   *Direct origin requests* red (live check).
3. `just test` checks the dashboard file offline; `just test-grafana` proves Grafana loads it.

## Non-goals
- **A 5-second period.** Whether MediaPackage stores data finer than a minute is unknown (*Checked live*, finding 5);
  it is a live check, and switching is a follow-up.
- **An events table.** The EventBridge rule in `modules/observability` captures only `aws.medialive` and
  `aws.mediaconnect`; adding `aws.mediapackage` is a Terraform change, out of scope.
- **Quality scores (`IngressMQCS`, `ChannelMQCS` and their `Sequence` forms).** The account holds none: MediaLive does
  not send them as configured.
- **Dynamic Multiview metrics.** Not used.
- **The `IngestEndpoint` dimension.** Its value is a GUID that changes with every deploy (eight in the account).
- **Alerting** in Grafana, and **a console link** (the console keeps linking to *MediaConnect source*).

## The metrics

All in the `AWS/MediaPackage` namespace. `CG` below is `ChannelGroup, Channel`; `CGE` is `ChannelGroup, Channel,
OriginEndpoint`.

| Metric | Dimensions | Statistic | Why |
|---|---|---|---|
| `IngressRequestCount` | CG, StatusCode | Sum | MediaLive's pushes by status class. A 4xx or 5xx is a refused push: MediaPackage's side of MediaLive's `Output4xxErrors`/`Output5xxErrors`. |
| `IngressBytes` | CG | Sum | Bytes pushed; shown as Mbps (× 8 ÷ 60 ÷ 10⁶). About 9.9 Mbps is the whole ladder (`docs/cost-estimate.md`). |
| `IngressResponseTime` | CG | Average, Maximum | How long the origin takes to accept a push. |
| `ActiveInput` | CG, InputIndex | Minimum | Not in the user guide; read 1 throughout 2026-10-09 (finding 3). Meaning inferred; a live check settles it. |
| `EgressRequestCount` | CGE, StatusCode, RequestType | Sum | CloudFront's requests, manifest or segment, by status class. |
| `EgressBytes` | CGE, RequestType | Sum | Bytes served; shown as Mbps. |
| `EgressResponseTime` | CGE, RequestType | Average, Maximum | A slow manifest stalls players. |
| `EgressRequestCount` | CGE, CDNAuthorizationStatus, CDNAuthorizationStatusDetails | Sum | `Authorized`/`HeaderSecretMatched` proves CloudFront's secret header works; every `Unauthorized` reason is a request that did not come through CloudFront, or a broken CDN-auth setup. |

The meaning of each metric is quoted from the MediaPackage user guide (*Monitoring with CloudWatch metrics*) in the
panel descriptions, except `ActiveInput`, whose description says it is undocumented.

## Checked live (2026-10-10)

Read-only `list-metrics` and `get-metric-statistics` on the author's account; nothing was started.

1. **The account holds the metrics and dimension sets above**, plus aggregates at fewer dimensions. Dimension values:
   `ChannelGroup` and `Channel` `live-sports-aws-demo`; `OriginEndpoint` `live-sports-aws-demo-hls`; `RequestType`
   `manifest`, `segment`; `StatusCode` `2xx`, `4xx` on egress, `2xx` only on ingress; `CDNAuthorizationStatus`
   `Authorized`, `Unauthorized`; `CDNAuthorizationStatusDetails` `HeaderSecretMatched`, `MissingCdnAuthHeader`;
   `InputIndex` `1`; eight `IngestEndpoint` GUIDs.
2. **Request metrics carry one sample per request:** `IngressRequestCount` had Sum = SampleCount (3,139 in the hour from
   18:00 UTC on 2026-10-09, about 0.9 a second), every sample 1.
3. **`ActiveInput`** read 1 in every sample (541 samples in the same hour, minimum and maximum 1). The user guide does
   not list it.
4. **51 egress 4xx** on 2026-10-09 (20 from 18:00, 31 from 21:00). Corrected while rendering (finding 6): they are
   not direct requests.
5. **Resolution unknown.** The user guide says MediaPackage publishes "every minute, if not sooner". All stored data is
   older than 3 hours, so whether a 5-second period returns real data could not be checked.

Found while driving the dashboard in a throwaway Grafana against 2026-10-09 18:00-19:00 UTC (60-second data is kept
15 days):

6. **The egress 4xx are manifest requests made before the first push.** In each of three sessions (2026-10-08 03:00,
   2026-10-09 18:05, 21:20) they fell in the minutes before MediaLive's first push, all `manifest`, and all
   `HeaderSecretMatched` (3,086 authorized = 2,041 manifest 2xx + 20 manifest 4xx + 1,025 segment 2xx): a player open
   through CloudFront asking for a manifest that did not exist yet. *Origin errors* is red for those minutes when going
   live with the player open; its description says so. The only `Unauthorized` requests in two weeks were 2, on
   2026-09-27 04:00, and that hour had 1 egress 4xx: whether a refused request counts as a 4xx is not established.
7. **Metric math on a search works:** `... * 8 / 60 / 1000000` returned about 10.1 Mbps of ingest (54 minutes, 3.0 to
   10.4), and about 5.4 Mbps of segments to one viewer.
8. **Ingest is about 60 pushes a minute**, all 2xx; push response time averages about 77 ms (maximum up to 481 ms);
   manifest responses average about 20 ms, segments about 110 ms.
9. **Grafana paints a missing value in the base threshold colour:** with a green base, every error tile read a green
   *none* with the stack off. The base step is now transparent. An error search that returns an empty series reads 0,
   one that returns nothing reads *none*; both neutral. `ActiveInput`'s minimum over an empty series is no value at
   all, which left the tile blank until it got `noValue` "—".

10. **`SUM()` over a search works** (added after review, when the egress error tiles were found to split into one box
    per request type or refusal reason): it gave 20 for 18:00-19:00 on 2026-10-09, 2 *Direct origin requests* for
    2026-09-27 04:00-05:00, and one empty series for an empty hour, which reads 0.

Not verified:
- **What `ActiveInput` reads when input stops** (0, or no data).
- **The 5-minute and 1-hour tile windows against live data:** the browser pass used a past range.
- **How a search is billed**, as for the other dashboards.

## Cost

GetMetricData costs 0.01 USD per 1,000 metrics requested, only while the dashboard is open, and at a 1-minute refresh
the dashboard queries 60 times an hour.

- 18 metric queries (10 for the graphs, 8 for the tiles: the two error tiles query 4xx and 5xx separately), returning
  about 30 series a refresh when the egress rows are full (manifest and segment, several status classes): about 1,800
  metrics an hour, about **0.02 USD/h**.
- Old deploys share the names, so they add no series, unlike the MediaConnect and MediaLive dashboards' *All* case.
- Without viewers the egress queries return nothing, and the figure is lower.

The cheapest dashboard in the folder.

---

# Part 2: TRD

## Units

| Unit | Change |
|---|---|
| `observability/grafana/dashboards/mediapackage-origin.json` | New: the dashboard. |
| `observability/grafana/dashboards/medialive-channel.json` | A link to the new one. |
| `tools/tests/test_grafana_files.py` | Checks for the new dashboard. |
| `tools/tests/test_grafana_container.py` | The new uid loads. |
| `README.md`, `docs/cost-estimate.md` | A section, a cost row, the live checks. |
| `AGENTS.md` | The "close the Grafana dashboard" line mentions the 1-minute refresh. |

No change to compose, provisioning, the justfile, livectl or Terraform: the provider already loads every file in the
folder. `AGENTS.md`, `README.md`, `docs/cost-estimate.md` and the Grafana tests are also edited on
`feat/grafana-cloudfront`; whichever branch merges second resolves the conflicts.

## Finding the stack

Every metric query is a search scoped by name, period 60:

```
REMOVE_EMPTY(SEARCH('{AWS/MediaPackage,<dimensions>} MetricName="<metric>" ChannelGroup="live-sports-aws-demo"', '<statistic>', 60))
```

where `<dimensions>` is the metric's full set from the table, so aggregates at fewer dimensions never match. No
variable: the names come from `${var.project}-demo` and do not change between deploys. Byte metrics are converted in
the query with metric math (`... * 8 / 60 / 1000000`), unit Mbps.

## The dashboard

- `uid: mediapackage-origin`, title *MediaPackage origin*, tags `live-sports-aws`, `mediapackage`; last 1 hour, refresh
  `1m`, timezone UTC, not editable, shared crosshair; links to `medialive-channel` (`keepTime`), and
  `medialive-channel` links back.
- Every metric query: data source `cloudwatch`, region default, code mode, `statistic` set, period 60. Series are
  labelled by the dimension that varies (`StatusCode`, `RequestType`, `CDNAuthorizationStatusDetails`).
- **Tiles**, neutral without data. A status-class series exists only once a request in that class has happened
  (finding 1: no ingress 4xx series at all), so *no data* is the normal healthy state of an error tile: the error
  tiles, and *Direct origin requests*, read 0 or *none* without data, in a neutral colour (finding 9).
  1. **Ingest bitrate**: mean over the last 5 minutes, Mbps (the newest bucket can still be filling).
  2. **Push errors, last 5 min**: ingress 4xx and 5xx, two boxes, red above 0.
  3. **Input active** (now): `ActiveInput` minimum over the last 5 minutes; 1 reads *active* (green), 0 *inactive*
     (red).
  4. **Origin errors, last 5 min**: egress 4xx and 5xx, two boxes, each `SUM()` across request types, red above 0.
  5. **Direct origin requests, last hour**: `Unauthorized` requests, `SUM()` across reasons, its own query over a fixed
     1-hour range, red above 0.
  6. **Manifest response time, last 5 min**: maximum, ms; no threshold until there is a baseline.
- **Rows**, in signal order:
  - **Ingest from MediaLive:** pushes per minute by status class (with `ActiveInput` on the right axis); ingest Mbps;
    push response time, average and maximum.
  - **Origin to CloudFront:** requests per minute by type and status class; egress Mbps by type; response time by type,
    average and maximum. Each description says: no data means no viewers, not zero.
  - **CDN authorization:** requests per minute by authorization detail, stacked bars; `HeaderSecretMatched` green, every
    other detail red.

## Testing

**Offline, `test_grafana_files.py`:**
- identity: uid, title, refresh `1m`, UTC, last 1 hour, link to `medialive-channel`; `medialive-channel` links back;
- every metric query is `REMOVE_EMPTY(SEARCH(...))` with period 60, data source `cloudwatch`, its statistic, a
  dimension set from the table and `ChannelGroup="live-sports-aws-demo"`;
- together the queries read every metric, dimension set and statistic in the table;
- no query names `IngestEndpoint`, an `MQCS` metric or a `Multiview` metric.

**With Docker, `test-grafana`:** `/api/dashboards/uid/mediapackage-origin` answers 200 and the log has no provisioning
error.

**Live (README checklist, the author's to run):**
1. On air with the test source, the ingest tiles fill within about 2 minutes; ingest reads about 9.9 Mbps.
2. Play through CloudFront: the egress rows fill; *CDN authorization* shows `HeaderSecretMatched`.
3. `curl` the origin endpoint URL directly: 403, and *Direct origin requests* turns red within about 2 minutes.
4. Stop the source: pushes stop; note what *Input active* shows (0 or no data) and fix the tile if needed.
5. Within 3 hours of a session, query a 5-second period; record whether it returns data.
6. The next day, Cost Explorer's GetMetricData line for the hours the dashboard was open.

## Docs

- **README:** in the Grafana section, the MediaPackage dashboard: what each tile means, why direct requests are red,
  why the egress rows are empty without viewers, the cost while open.
- **`docs/cost-estimate.md`:** a row for *MediaPackage origin*.
- **`AGENTS.md`:** the dashboard cost line reflects the 1-minute refresh.
