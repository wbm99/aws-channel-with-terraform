# Grafana for CloudFront Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A *CloudFront distribution* dashboard on CloudFront's free default metrics, with a viewer estimate.

**Architecture:** One provisioned dashboard JSON beside the other three, same shape as `medialive-channel.json`: a
distribution drop-down (*All* + `REMOVE_EMPTY`), every query pinned to us-east-1 at a 60-second period, refresh 1m.
No Terraform, compose or livectl change.

**Tech Stack:** Grafana 13.0.2 dashboard JSON, CloudWatch metric math, pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-grafana-cloudfront-design.md`

## Global Constraints

- uid `cloudfront-distribution`, title *CloudFront distribution*, tags `live-sports-aws`, `cloudfront`, refresh `1m`,
  time `now-1h` to `now`, timezone `utc`, `editable: false`, `graphTooltip: 1`.
- Every metric query: region `us-east-1`, period `"60"`, statistic set, expression
  `REMOVE_EMPTY(SEARCH('{AWS/CloudFront,DistributionId,Region} MetricName="<m>" $distribution', '<stat>', 60))`,
  plus ` / $requests_per_viewer` (viewer estimate) or ` * 8 / 60` (delivered bps).
- Metrics: `Requests` Sum, `BytesDownloaded` Sum, `TotalErrorRate` Average, `4xxErrorRate` Average,
  `5xxErrorRate` Average. Never `BytesUploaded`.
- Variables: `distribution` (dimension values, us-east-1, all value `DistributionId`, default *All*, refresh on load);
  `requests_per_viewer` (textbox, default `60`).
- 8 metric queries in all.

## Review Focus

1. **A reviewer's data source defaults to another region:** every query and the variable must say us-east-1, or the
   panels are empty. Pinned by the region assertion.
2. **The textbox emptied by a viewer:** `/ ` with nothing after it is a CloudWatch syntax error on that panel only;
   accepted, the field's description says to keep a number in it.
3. **CloudFront's publishing delay** empties a "last 2 minutes" tile: tiles read the last 5. Pinned by the tile test.
4. **A redeploy within the hour:** two distributions under *All*; labels carry the distribution ID.

---

### Task 1: The dashboard, its tests, its links

- [ ] Tests in `test_grafana_files.py` (`# --- CloudFront distribution ---`): identity and links; variables; every
  query (region, period, statistic, expression); every metric read, none uploaded, 8 queries; panel order
  (stats *≈ Viewers*, *Requests a minute*, *Delivered*, *Error rate*; timeseries *Viewers (estimate)*,
  *Delivered*, *Error rates*); tiles `timeFrom "5m"`, `lastNotNull`, Error rate thresholds green / red from 1;
  units (`bps`, `percent`, *≈ Viewers* `decimals` 0). Every dashboard links to the other three.
- [ ] Run them: FAIL. Write the JSON (throwaway generator in the scratchpad). Run: PASS. `just test-grafana`: PASS.
- [ ] Render in a throwaway Grafana against 2026-10-09 18:30-19:45 UTC: about 1 viewer while playing, ~5.6 Mbps.
- [ ] Commit `feat: a CloudFront distribution dashboard in Grafana`.

### Task 2: Docs

- [ ] README Grafana section, live checks; cost estimate row (8 queries, 480 an hour, 0.005 / 0.04 USD/h).
- [ ] Commit `docs: the CloudFront dashboard, its viewer estimate and its cost`.
