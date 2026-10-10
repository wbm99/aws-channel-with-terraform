# Grafana for MediaLive: Design

Date: 2026-10-10
Status: Draft, for review
Branch: `feat/grafana-medialive` (from `main`)

This document has two parts: a product part (PRD: what and why) and a technical part (TRD: how).

It is the second step of observability for the pipeline, after `2026-10-09-grafana-mediaconnect-design.md`. It adds one
dashboard to the Grafana that already runs beside the console; MediaPackage and CloudFront stay for later.

---

# Part 1: PRD

## Purpose
See the encoder the way the MediaConnect dashboards show the source: second by second, from the input MediaLive receives,
through the encode, to the push into MediaPackage and the audio leaving it. Read top to bottom, the dashboard follows
the signal, so the first row that turns bad says where the problem is.

## Audience
The author, running and writing about the pipeline, and a reviewer running the stack on their own account.

## Goals
1. A **MediaLive channel** dashboard in the *Live pipeline* folder, linked both ways with the two MediaConnect
   dashboards.
2. It refreshes every 5 seconds, on 5-second CloudWatch data, like them.
3. It needs no editing after a redeploy, even though the channel ID changes every time.
4. A drop-down picks one channel; its default, *All*, shows whichever channel is running.

## Success criteria
1. With the stack on air and the test source sending, the tiles show values within about 30 seconds (live check).
2. Stopping the source shows on this dashboard: input loss rises, the input frame rate drops (live check).
3. `just test` checks the dashboard file offline; `just test-grafana` proves Grafana loads it.

## Non-goals
- **MediaPackage and CloudFront dashboards.** Later.
- **Alerting** in Grafana.
- **A drop-down limited to live channels.** It lists every channel CloudWatch holds metrics for (two weeks); see
  *Finding the channel*.
- **STANDARD-channel and failover metrics.** The demo channel is `SINGLE_PIPELINE` with one input.
- **A console link.** The console keeps linking to *MediaConnect source*; the dashboards link to each other.

## The metrics

All in the `AWS/MediaLive` namespace. Sixteen, by the dimensions they carry:

| Metric | Dimensions | Statistic | Why |
|---|---|---|---|
| `ActiveAlerts` | ChannelId, Pipeline | Maximum | MediaLive's own alarms; 0 is good. |
| `InputLossSeconds` | ChannelId, Pipeline | Maximum | 1 in any 5 seconds with no input: the input-loss slate is on air. Not Sum (finding 5). |
| `ChannelInputErrorSeconds` | ChannelId, Pipeline | Maximum | 1 in any 5 seconds with input that could not be used. Same shape as input loss, so the same statistic. |
| `TsInputContinuityCounterErrors` | ChannelId, Pipeline | Sum | TS damage after the SRT leg. Compare with `SourceContinuityCounter` to tell which leg broke. |
| `InputVideoFrameRate` | ChannelId, Pipeline | Average | Should match the source's frame rate. |
| `DroppedFrames` | ChannelId, Pipeline | Sum | Frames the encoder dropped. |
| `FillMsec` | ChannelId, Pipeline | Maximum | Fill MediaLive inserted for missing input over the trailing minute; 60,000 ms is a whole minute (finding 6). |
| `SvqTime` | ChannelId, Pipeline | Average | Percentage of time MediaLive cut quality to stay real-time: the encoder is struggling. |
| `NetworkIn` | ChannelId, Pipeline | Average | Megabits per second in (finding 7). |
| `NetworkOut` | ChannelId, Pipeline | Average | Megabits per second out. |
| `OutputVideoFrameRate` | ChannelId, OutputName, Pipeline | Average | One line per rendition (480p, 720p, 1080p); each should read 30. |
| `ActiveOutputs` | ChannelId, OutputGroupName, Pipeline | Minimum | Outputs running in `hls-to-mediapackage`; Minimum shows a dip. |
| `Output4xxErrors` | ChannelId, OutputGroupName, Pipeline | Sum | MediaPackage refused the push: usually credentials or path. |
| `Output5xxErrors` | ChannelId, OutputGroupName, Pipeline | Sum | MediaPackage failed: its side. |
| `OutputAudioLevelLkfs` | AudioDescriptionName, ChannelId, Pipeline | Average | Loudness of `audio_main`; silence shows at once. |
| `OutputAudioLevelDbfs` | AudioDescriptionName, ChannelId, Pipeline | Average | Level of `audio_main`. |

Left out: `PipelinesLocked` (STANDARD only), `PrimaryInputActive` and every `ActiveInputFailoverLabel` series (no
failover), `ComplexFrcPresent`, `InputTimecodesPresent`.

The meaning of each metric above is checked against the MediaLive user guide while implementing; the descriptions on
the panels quote it.

## Checked live (2026-10-10)

1. **The account holds the 20 metrics listed above and under *Left out*,** with those dimension sets
   (`list-metrics`).
2. **MediaLive publishes every metric once a second.** For channel `6482454`, 2026-10-09 18:40 to 19:40 UTC, all
   thirteen metrics checked had a sample count of 3,600, the output metrics included. A 5-second period is therefore
   real data, as for MediaConnect.
3. **The output and audio dimensions** carry the names from `modules/encode`: `OutputName` 480p, 720p, 1080p;
   `OutputGroupName` `hls-to-mediapackage`; `AudioDescriptionName` `audio_main`.
4. **Eight channel IDs** have metrics from the last two weeks; one is live at a time.

Found while rendering the dashboard against 2026-10-09's data at a 60-second period (a throwaway copy; 5-second data
older than 3 hours is gone):

5. **`InputLossSeconds` goes negative.** Each per-second sample is 1 while input is lost, then one large negative
   sample arrives when input returns: -241.22 at 18:36 and -199.27 at 19:25 on channel `6482454`, -799.02 on
   `7761634`. A sum across a recovery cancels most of the loss and drew the graph's axis down to -3 minutes. The
   maximum of a bucket is reliable: 1 if input was lost in it. The tile therefore says *lost* or *none* for the last 5
   minutes; a count of seconds lost cannot be read from this metric. No other metric checked went negative.
6. **`FillMsec` is a trailing-minute value**, not a per-second amount: it reads 60,000 for every second of a full
   input loss, so a 60-second sum read 3,600,000 (an hour). Maximum reads 0 to 60,000 ms.
7. **`NetworkIn` and `NetworkOut` are stored in Megabits/Second** (`get-metric-statistics` returns the unit); with
   Grafana's `bps` a 10.8 Mbps output showed as "10.8 b/s".
8. **Grafana substitutes the variable literally:** with *All* the browser sent `... MetricName="NetworkIn" ChannelId`,
   with one picked `... 6482454`. The drop-down listed *All* and the eight IDs.

Not verified:
- **MediaLive EventBridge field names** (`detail.state`, `detail.alert_type`, `detail.message`): the events log group
  is destroyed with the stack, so no event could be read today. The table falls back to the raw detail.
- **How a search is billed**, as for MediaConnect.

## Cost

GetMetricData costs 0.01 USD per 1,000 metrics requested, only while the dashboard is open.

- 22 metric queries × 720 refreshes an hour = 15,840 metrics an hour: about **0.16 USD/h** if only the live channel
  counts. The sixteen metrics, plus six that a tile repeats: a tile reads the last 2 or 5 minutes, and a panel reusing
  a graph through the *Dashboard* data source gets the graph's whole range, as on *MediaConnect source*.
- With *All* and every matched channel counting (8 channels; `OutputVideoFrameRate` matches three series each):
  192 series a refresh, about 138,000 an hour, up to about **1.38 USD/h**. Picking one channel in the drop-down
  brings it back to the lower figure.
- The *Channel events* table adds a Logs Insights query over kilobytes: rounds to nothing.

This is the most expensive dashboard in the folder if the higher figure holds; the README says to close it, and to
pick the channel, when watching for long.

---

# Part 2: TRD

## Units

| Unit | Change |
|---|---|
| `observability/grafana/dashboards/medialive-channel.json` | New: the dashboard. |
| `observability/grafana/dashboards/mediaconnect-source.json`, `mediaconnect-detail.json` | A link to the new one. |
| `tools/tests/test_grafana_files.py` | Checks for the new dashboard. |
| `tools/tests/test_grafana_container.py` | The new uid loads. |
| `README.md`, `docs/cost-estimate.md` | A section each; live checks join the checklist. |

No change to compose, provisioning, the justfile or livectl: the provider already loads every file in the folder.

## Finding the channel

MediaConnect metrics carry `FlowARN`, which contains the project name, so a search on `live-sports-aws-demo` finds the
flow. MediaLive metrics carry a numeric `ChannelId` and nothing else naming the channel, and the ID changes with every
deploy. Hence a variable:

- `channel`: CloudWatch *dimension values* query, namespace `AWS/MediaLive`, metric `ActiveAlerts`, key `ChannelId`;
  *Include All* on, *All* the default; refreshed on dashboard load.
- Every metric query is a search on that variable, period 5:

  ```
  REMOVE_EMPTY(SEARCH('{AWS/MediaLive,<dimensions>} MetricName="<metric>" <channel term>', '<statistic>', 5))
  ```

  where `<dimensions>` is the metric's set from the table, so the failover-label series never match.
- With one channel picked, the term restricts to it; with *All*, it matches every channel and `REMOVE_EMPTY` keeps the
  one with data, as it keeps the live flow on the MediaConnect dashboards.

**Settled 2026-10-10** with GetMetricData on the author's account (60-second period, 2026-10-09 17:00 to 22:00 UTC):

- `<channel term>` is the bare `$channel`. A channel ID as a bare search term returned only that channel (94 points),
  the same as `ChannelId="6482454"`; on `OutputVideoFrameRate` it returned the three renditions of that channel.
- *All* has the custom value `ChannelId`, a dimension name every series carries: it matched all eight channels, and
  `REMOVE_EMPTY` kept the two with data in the window. A custom *All* value is substituted literally, and it also
  keeps *All* working while the list is still empty (no credentials yet, or no channel yet).

This scopes to the account and region, not to the project: another MediaLive channel in the same region would show
under *All*. The demo assumes there is none, since one would also be billing about 1.20 USD/h.

## The dashboard

- `uid: medialive-channel`, title *MediaLive channel*, tags `live-sports-aws`, `medialive`; last 15 minutes, refresh
  `5s`, timezone UTC, not editable, shared crosshair; links to `mediaconnect-source` and `mediaconnect-detail`
  (`keepTime`).
- Every metric query: data source `cloudwatch`, region default, code mode, `statistic` set (the plugin answers 500
  without it), period 5. Its label is the rendition (`OutputName`) on *Output frame rate*, else the channel ID; graphs
  with several metrics name each by its refId, as the MediaConnect dashboards do.
- **Tiles**, as on *MediaConnect source*: "now" tiles read the last 2 minutes and show the last value; "last 5 min"
  tiles show the total; neutral without data; red on the threshold given.
  1. **Active alerts** (now), red above 0.
  2. **Input frame rate** (now), fps.
  3. **Input loss, last 5 min**: *lost* (red) if input was missing at any point, else *none* (green).
  4. **Dropped frames, last 5 min**, red above 0.
  5. **SVQ time** (now), %, red above 0.
  6. **Push errors, last 5 min**: two boxes, 4xx and 5xx, red above 0.
- **Rows**, in signal order:
  - **Input:** *Input frame rate* (graph); *Input problems*: input loss seconds, input error seconds, continuity counter
    errors.
  - **Encoder:** *Encoder*: dropped frames and SVQ time % on the left axis, fill ms on the right.
  - **Output:** *Output frame rate* per rendition; *Push to MediaPackage*: 4xx and 5xx as bars, active outputs as a line
    on the right axis.
  - **Network:** in and out, in Mbps.
  - **Audio:** LKFS and dBFS of `audio_main`.
  - **Channel events** (table): the events log group, `detail-type` starting with `MediaLive`, newest first, 50 rows;
    columns Event, State, Alert, Detail over the field names livectl's log formatter reads, with the whole event as
    Detail's last resort.

## Testing

**Offline, `test_grafana_files.py`:**
- identity: uid, title, refresh `5s`, UTC, last 15 minutes, links to both MediaConnect dashboards; both of them link
  back;
- every metric query names one of the sixteen metrics with its statistic, its dimension set, period 5, data source
  `cloudwatch`, `REMOVE_EMPTY` and the channel term;
- together the queries read every one of the sixteen exactly once;
- the variable: dimension values of `ChannelId` in `AWS/MediaLive`, *All* included and the default;
- no query reads a left-out metric.

**With Docker, `test-grafana`:** `/api/dashboards/uid/medialive-channel` answers 200 and the log has no provisioning
error.

**Live (README checklist, the author's to run):**
1. On air with the test source, the tiles fill within about 30 seconds; three lines on *Output frame rate*, each at 30.
2. Stop the source: input loss rises, input frame rate drops, *Channel events* shows the alert. Note the field names
   the events really carry and fix the table if needed.
3. The drop-down lists the old channels; picking the live one shows the same values as *All*.
4. The next day, Cost Explorer's GetMetricData line for the hours the dashboard was open.

## Docs

- **README:** in the Grafana section, the MediaLive dashboard: what each tile means, the drop-down and why *All* is the
  default, the cost while open.
- **`docs/cost-estimate.md`:** a row for *MediaLive channel*, with both figures.
