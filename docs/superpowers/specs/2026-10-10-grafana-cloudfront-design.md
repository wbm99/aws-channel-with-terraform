# Grafana for CloudFront: Design

Date: 2026-10-10
Status: Approved in chat, 2026-10-10
Branch: `feat/grafana-cloudfront` (from `main`)

This document has two parts: a product part (PRD: what and why) and a technical part (TRD: how).

It is the third step of observability for the pipeline, after `2026-10-09-grafana-mediaconnect-design.md` and
`2026-10-10-grafana-medialive-design.md`. It adds one dashboard and changes nothing in the stack.

---

# Part 1: PRD

## Purpose
See what the CDN delivers: how much, to roughly how many viewers, and whether any of it fails.

## Goals
1. A **CloudFront distribution** dashboard in the *Live pipeline* folder, linked both ways with the other three.
2. It reads only CloudFront's free default metrics. **No Terraform change, nothing new billed while the stack runs.**
3. An estimate of current viewers, clearly labelled as one.
4. It needs no editing after a redeploy: a drop-down picks the distribution, *All* by default, as on *MediaLive
   channel*.

## Non-goals, decided in chat
- **CloudFront additional metrics** (cache hit rate, origin latency, per-code error rates). CloudFront's page says
  their rate "is charged only once per month, per metric (up to 8 metrics per distribution)"; every `just up` creates a
  new distribution, so read literally that is up to 2.40 USD per deploy. Too much for this demo.
- **Viewers from access logs.** Standard logs (v2 too) are "usually" delivered "within an hour", sometimes up to 24
  hours late, and reliably only "about four hours after you enable logging": after a one-hour demo has been destroyed.
- **Real-time logs** (Kinesis, Lambda): a real count, but a new billed subsystem bigger than this dashboard.

## The metrics

`AWS/CloudFront`, region **us-east-1** only, dimensions `DistributionId` and `Region` (`Global`), published at
1-minute granularity:

| Metric | Statistic | Why |
|---|---|---|
| `Requests` | Sum | Requests a minute; also the viewer estimate. |
| `BytesDownloaded` | Sum | Bytes delivered a minute, shown as bits per second. |
| `TotalErrorRate` | Average | % of requests answered 4xx or 5xx. |
| `4xxErrorRate` | Average | % answered 4xx: viewer or URL problems. |
| `5xxErrorRate` | Average | % answered 5xx: CloudFront or MediaPackage failed. |

Left out: `BytesUploaded` (the player never uploads).

## The viewer estimate

An HLS player is a stream of independent requests; nothing in AWS counts viewers. The estimate is

```
≈ viewers = requests a minute ÷ requests per viewer a minute
```

with *requests per viewer* a field on the dashboard, **default 60**. On 2026-10-09, 18:34-18:37 UTC, distribution
`E20QHHXWZL2B2S` served 63-65 requests and 5.63 Mbps a minute. 5.6 Mbps is one player on the 1080p rung (5 Mbps
video, 128 kbps audio, TS overhead), and the player page makes no requests of its own besides hls.js, so one viewer
made about 60 requests a minute. Six-second segments alone would give 20; why hls.js makes three times that is not
known. The estimate rounds badly with few viewers and drifts when players switch rung or stall; the panel says so,
and a live check recalibrates the default.

## Checked live (2026-10-10, read-only)

1. **Six metrics exist** for 8 distribution IDs from the last two weeks, dimensions `DistributionId`, `Region`.
2. **The search, the division and the multiplication work in CloudWatch:**
   `REMOVE_EMPTY(SEARCH('{AWS/CloudFront,DistributionId,Region} MetricName="Requests" DistributionId', 'Sum', 60)) / 60`
   and `... MetricName="BytesDownloaded" ... * 8 / 60` returned one series for the distribution with data.
3. **A bare distribution ID** as the search term returned only that distribution.

## Cost

GetMetricData at 0.01 USD per 1,000 metrics, only while the dashboard is open; refresh every minute (60 an hour).

- 8 metric queries × 60 = 480 an hour: about **0.005 USD/h** if only the live distribution counts.
- With *All* and every matched distribution counting (8): about 3,840 an hour, about **0.04 USD/h**.

The cheapest dashboard in the folder, by a factor of about 30.

---

# Part 2: TRD

## Units

| Unit | Change |
|---|---|
| `observability/grafana/dashboards/cloudfront-distribution.json` | New. |
| The three existing dashboards | A link to the new one. |
| `tools/tests/test_grafana_files.py`, `test_grafana_container.py` | Checks for the new dashboard. |
| `README.md`, `docs/cost-estimate.md` | A section, a row, live checks. |

## The dashboard

- `uid: cloudfront-distribution`, title *CloudFront distribution*, tags `live-sports-aws`, `cloudfront`; last 1 hour,
  refresh **`1m`**, UTC, not editable, shared crosshair; links to the other three (`keepTime`).
- Variables:
  - `distribution`: CloudWatch dimension values of `DistributionId` in `AWS/CloudFront` (metric `Requests`), region
    `us-east-1`, *Include All* on, custom all value `DistributionId`, *All* the default, refreshed on load.
  - `requests_per_viewer`: textbox, label *Requests per viewer a minute*, default `60`.
- Every metric query: data source `cloudwatch`, **region `us-east-1`**, code mode, statistic set, period 60:

  ```
  REMOVE_EMPTY(SEARCH('{AWS/CloudFront,DistributionId,Region} MetricName="<metric>" $distribution', '<statistic>', 60))
  ```

  with ` / $requests_per_viewer` appended for the viewer estimate and ` * 8 / 60` for delivered bits per second.
  Label: the distribution ID.
- **Tiles** ("now" = the last complete minute, read over the last 5 minutes to allow for CloudFront's delay):
  1. **≈ Viewers**: the estimate, last value, no decimals, neutral colour.
  2. **Requests a minute**: last value, neutral.
  3. **Delivered**: bits per second, last value, neutral.
  4. **Error rate**: `TotalErrorRate`, last value, %, green, red from 1%.
- **Graphs:**
  - **Viewers (estimate)**: the estimate alone. Drawn beside it, requests a minute were the same line scaled and hid
    it (found in the render); the *Requests a minute* tile keeps that figure.
  - **Delivered**: bits per second.
  - **Error rates**: 4xx and 5xx, %.

## Testing

**Offline:** identity, refresh `1m`, links (all four dashboards link to each other); both variables; every query's
metric, statistic, region `us-east-1`, period 60 and expression; the viewer and bits-per-second expressions; tiles'
window, reduction and thresholds; every metric read; no `BytesUploaded`. **Docker:** the dashboard provisions.
**Rendered against 2026-10-09's data** in a throwaway Grafana (1-minute data is kept for 15 days, so this is the real
dashboard, not a copy).

**Rendered 2026-10-10** against 2026-10-09 18:30-19:45 UTC: the drop-down listed *All* and the eight distributions;
*≈ Viewers* read 1 (61 requests ÷ 60), *Delivered* 6.16 Mb/s, *Error rate* 0 %, no query errors; with the field at 30
the estimate read 2.

**Live (README checklist):**
1. With exactly one player open, *≈ Viewers* reads about 1; if not, note requests a minute and set the default.
2. Open a second player: the estimate roughly doubles.
3. Note the delay between opening the player and the first requests point.

## Docs

README: the dashboard, what the estimate is and is not, why there is no cache hit rate. Cost estimate: a row.
