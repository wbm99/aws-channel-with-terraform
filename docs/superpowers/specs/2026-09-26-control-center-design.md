# livectl Control Center: Design

Date: 2026-09-26
Status: Approved design, not implemented
Branch: `feat/control-center` (stacked on `feat/agent-guide-just-and-ui`)

This document has two parts: a product part (PRD: what and why) and a technical part (TRD: how).

---

# Part 1: PRD

## Purpose
Turn `livectl ui` from a small button panel into the control center for the project: one screen to deploy, operate,
monitor, troubleshoot and tear down the live SRT-to-HLS pipeline, with the player on the same screen.

## Problems with the current console
1. **Control names do not say what they do.** "Start" reads like "create everything", but creating is "Apply". "Check
   clean" says nothing about leftovers or billing.
2. **Pipeline state is too coarse.** Only three facts are read (flow status, channel state, one `SourceConnected`
   datapoint), rolled into LIVE / PARTIAL / IDLE. It cannot show the MediaLive input, MediaPackage, CloudFront, or
   whether video actually reaches viewers. "Channel running, no source connected" shows as LIVE.
3. **Copying the player URL copies a stray `↗`.** The copy button reads the row's `textContent`, which includes the arrow
   inside the link.
4. **No logs or events.** Troubleshooting needs the AWS console.
5. **No player and no source control.** The test source runs in a separate terminal (`just send`).

## Goals
1. Every control is named for its effect and shown only when it can work.
2. Every resource in the chain has its own visible state, health and key metrics.
3. A one-line verdict for the whole pipeline that tells the truth, including "on air with no source".
4. Per-resource logs and events in a side panel.
5. The test source can be started and stopped from the console.
6. The player is on the same screen.
7. Watching the pipeline costs almost nothing: no CloudWatch call per browser poll.

## Success criteria
- Each verdict in the verdict table (below) is reachable in a scenario fixture and rendered correctly in a browser test.
- In every scenario, only the controls allowed by the action rules are visible, and the server refuses the others.
- Copy puts exactly the URL on the clipboard.
- The SRT passphrase never appears in the page, in a log tab, or in a file.
- `just test` still passes with no AWS credentials.

## Out of scope
- MediaPackage and CloudFront access logs (the heavier "full observability" option).
- A frontend framework or build step (React, Vite, npm).
- Alarms or notifications.
- Renaming the CLI commands (`livectl start` and friends stay).
- More than one environment.

## Constraints
- boto3 remains the only runtime dependency. The server stays on the standard library `http.server`.
- The console binds to loopback only, as now.
- AWS clients, process launchers and clocks are injected, never constructed in logic (AGENTS.md).
- Anything needing deployed AWS resources is verified by the user, not by the implementation work.

---

# Part 2: TRD

## Screen

Layout: a main area plus a collapsible right-hand log panel.

```
┌ header: live-sports-aws · demo   [verdict]                    [$ rate · minutes] ┐
├──────────────────────────────────────────────────────────────┬──────────────────┤
│ SRT Input Source → MediaConnect Flow → MediaLive Input →      │ tabs:            │
│ MediaLive Channel → MediaPackage Channel → CloudFront CDN →   │ All · SRT Source │
│ Player                                   (click selects node) │ MediaConnect ·   │
├───────────────────────────────┬──────────────────────────────┤ MediaLive ·      │
│ player (iframe)               │ selected node: every field,  │ MediaPackage ·   │
│                               │ IDs/ARNs, AWS console link   │ CloudFront · Jobs│
├───────────────────────────────┴──────────────────────────────┤                  │
│ Infrastructure: Deploy stack · Tear down stack · Scan for     │ log lines …      │
│ leftovers   Broadcast: Go live · Go off air · Send / Stop     │                  │
│ test source                                                   │                  │
├──────────────────────────────────────────────────────────────┤                  │
│ Endpoints: SRT ingest · Player · HLS manifest · passphrase ARN│                  │
└──────────────────────────────────────────────────────────────┴──────────────────┘
```

### Controls

| Label | Group | Does | Replaces |
|---|---|---|---|
| Deploy stack | Infrastructure | `terraform apply -auto-approve` | Apply |
| Tear down stack | Infrastructure | `terraform destroy -auto-approve`, typed confirmation `destroy` | Destroy |
| Scan for leftovers | Infrastructure | Lists billable resources with the project prefix | Check clean |
| Go live | Broadcast | Start flow, wait ACTIVE; start channel, wait RUNNING | Start |
| Go off air | Broadcast | Stop test source; stop channel, wait IDLE; stop flow, wait STANDBY | Stop |
| Send test source | Broadcast | Run `source/send-srt.sh` against the ingest | (new) |
| Stop test source | Broadcast | Stop that process | (new) |

A button is rendered only when the action rules allow it. The server enforces the same rules.

### Action rules

| Action | Allowed when | Otherwise |
|---|---|---|
| Deploy stack | No job running | 409 "*job* is still running" |
| Tear down stack | No job running, test source stopped, and neither flow ACTIVE nor channel RUNNING is known (a failed probe does not block teardown) | 409 "go off air first" |
| Scan for leftovers | No job running | 409 |
| Go live | Deployed, no job running | 400 not deployed / 409 busy; idempotent as today |
| Go off air | Deployed, no job running | idempotent |
| Send test source | Flow ACTIVE, source stopped | 409 "the flow is not active" |
| Stop test source | Always | no-op if already stopped |

Error texts are written for the person reading the page, never passed through from the CLI layer (for example, never
"pass --flow-arn/--channel-id").

### Verdict

Computed from the node list, first matching row wins:

| Verdict | Health | Condition |
|---|---|---|
| Not deployed | off | No Terraform outputs |
| Deploying… / Tearing down… | busy | That job is running |
| Going live… / Going off air… | busy | That job is running |
| Partly on | bad | Exactly one of flow ACTIVE / channel RUNNING |
| On air · no source | bad | Channel RUNNING, `SourceConnected` = 0 or source state disconnected |
| On air · source degraded | warn | Connected, and `SourceNotRecoveredPackets` > 0, `SourceContinuityCounter` > 0, or `ActiveAlerts` > 0 |
| On air · not reaching viewers | warn | Channel RUNNING, source connected, manifest check not advancing |
| On air · playing | ok | Manifest media sequence advancing through CloudFront |
| Off air | off | Flow not ACTIVE and channel not RUNNING |
| Unknown | unknown | Probes failed |

On source loss, MediaLive (with the encode module's default input-loss settings) repeats the last frame for 1 s, sends
black for 1 s, then a black slate indefinitely, and the HLS output keeps emitting. So "no source" still plays, as black.

### Cost display

Hourly rate = sum over resources that are up, using `docs/cost-estimate.md`:

| Resource up | USD/hour |
|---|---|
| Flow ACTIVE | 0.29 (flow 0.16 + input 0.09 + output 0.04) |
| Channel RUNNING | 1.195 (input + three outputs) |
| MediaPackage receiving (IngressBytes > 0) | 0.25 (per-GB, estimated at this bitrate) |

The figures live in one Python constant with a comment pointing at the cost doc. The header shows the rate and minutes
since this console saw billing start. "Off air" means nothing hourly is billing (MediaPackage bills per GB).

### Endpoints

SRT ingest (`srt://ip:port`), player URL, HLS manifest URL, and the Secrets Manager ARN of the passphrase (never the
passphrase itself). Copy buttons copy a value held in JavaScript, never the displayed text.

## Backend

### `livectl/pipeline.py`

One probe per node. Each takes injected clients and returns a frozen `NodeStatus`:

```python
@dataclass(frozen=True)
class NodeStatus:
    id: str                 # "srt_source", "mediaconnect_flow", ...
    title: str              # "SRT Input Source", ...
    state: Optional[str]    # raw AWS state, e.g. "RUNNING"
    health: str             # ok | warn | bad | off | unknown
    summary: str            # one line for the chain
    metrics: dict           # name -> value
    details: dict           # ARNs, IDs, extra fields for the detail pane
    console_url: Optional[str]
    checked_at: str         # ISO time of the data
    error: Optional[str]
```

A probe that raises becomes a node with `health="unknown"` and the error text. One failed call never blanks the screen.

| Node id | Title | AWS calls |
|---|---|---|
| `srt_source` | SRT Input Source | metrics batch + `SourceProcess.status()` |
| `mediaconnect_flow` | MediaConnect Flow | `mediaconnect.describe_flow` |
| `medialive_input` | MediaLive Input | `medialive.describe_input` |
| `medialive_channel` | MediaLive Channel | `medialive.describe_channel`, `medialive.list_alerts` (active only) |
| `mediapackage_channel` | MediaPackage Channel | `mediapackagev2.get_channel`, `mediapackagev2.get_origin_endpoint` |
| `cloudfront_cdn` | CloudFront CDN | `cloudfront.get_distribution` |
| `player` | Player | HTTPS GET through CloudFront (see manifest check) |

### Metrics (one `cloudwatch.get_metric_data` call)

All verified against AWS documentation on 2026-09-26.

| Namespace | Metrics | Dimensions |
|---|---|---|
| `AWS/MediaConnect` | `SourceConnected`, `SourceBitRate`, `SourceRoundTripTime`, `SourceNotRecoveredPackets`, `SourceContinuityCounter` | `FlowARN` |
| `AWS/MediaLive` | `ActiveAlerts`, `InputVideoFrameRate`, `InputLossSeconds` | `ChannelId`, `Pipeline` = `0` |
| `AWS/MediaPackage` | `IngressBytes`; `EgressRequestCount` | `ChannelGroup`, `Channel`; plus `StatusCode` for egress |
| `AWS/CloudFront` | `Requests`, `5xxErrorRate` | `DistributionId`, `Region` = `Global` |

The CloudFront dimension set is the one item here still to be confirmed in the docs during implementation.

### Manifest check

The master playlist (`index.m3u8`) is multivariant and has no media sequence. The check fetches it through CloudFront,
takes the first variant URI, fetches that playlist, and reads `#EXT-X-MEDIA-SEQUENCE`. "Advancing" means the value grew
since the previous check within the last 30 s. Uses `urllib.request` (standard library) with a 3 s timeout, injected as
a `fetch(url) -> str` callable so tests never touch the network.

### Caching

`TtlCache(clock)` keyed by data source:

| Data | TTL |
|---|---|
| `Describe*` / `Get*` state calls | 5 s |
| Manifest check | 4 s |
| CloudWatch metrics batch | 60 s |

The browser polls `/api/pipeline` every 2 s and mostly receives cached data. Each node carries `checked_at`, and the
page shows staleness ("metrics 40 s ago").

### `livectl/verdict.py`

Pure functions: `verdict(nodes, job, source) -> Verdict` and `hourly_rate(nodes) -> float`. No I/O.

### Targets and Terraform outputs

`Targets` grows to carry every ID the probes need. New outputs:

| Output | Where | Value |
|---|---|---|
| `medialive_input_id` | `modules/encode` → `envs/demo` | `aws_medialive_input.this.id` |
| `mediapackage_channel_group` | `modules/package` → `envs/demo` | channel group name |
| `mediapackage_channel` | `modules/package` → `envs/demo` | channel name |
| `mediapackage_endpoint` | `modules/package` → `envs/demo` | origin endpoint name |
| `distribution_id` | `modules/delivery` (exists) → `envs/demo` | distribution ID |
| `cdn_manifest_url` | `envs/demo` | `https://<distribution domain><hls_manifest_path>` |
| `events_log_group` | `modules/observability` → `envs/demo` | log group name |

Every output has a `description`. `resolve_targets` keeps working with only `flow_arn` and `medialive_channel_id`
present (older state), and marks the missing nodes `unknown` rather than failing.

### API

| Method and path | Does |
|---|---|
| `GET /api/pipeline` | verdict, rate, nodes, endpoints, current job summary, source status, allowed actions |
| `GET /api/logs?stream=<tab>&after=<token>` | next page of log lines for one tab |
| `POST /api/deploy`, `/api/teardown` (`{"confirm":"destroy"}`), `/api/scan` | Terraform and leftover jobs |
| `POST /api/go-live`, `/api/go-off-air` | broadcast jobs |
| `POST /api/source/start`, `/api/source/stop` | test source |

`/api/status` and the old action paths are removed; the page is the only client.

`allowed_actions` in the pipeline payload is computed by the same function the POST handlers use to refuse, so the page
and the server cannot disagree.

## Logs and events

### Terraform: new module `modules/observability`

Standard module layout (`versions.tf`, `variables.tf`, `outputs.tf`, `main.tf`, `tests/observability.tftest.hcl`).

- `aws_cloudwatch_log_group` `/aws/events/${var.name}`, retention 7 days. Destroyed with the stack.
- `aws_cloudwatch_event_rule` with pattern `{"source": ["aws.medialive", "aws.mediaconnect"]}`.
- `aws_cloudwatch_event_target` to the log group.
- `aws_cloudwatch_log_resource_policy` allowing `events.amazonaws.com` to `logs:CreateLogStream` and `logs:PutLogEvents`
  on that group. Without it the rule delivers nothing and nothing reports an error; this goes on the live checklist.

Events used (verified 2026-09-26, EventBridge event reference):
- MediaLive: `MediaLive Channel State Change`, `MediaLive Channel Alert`, `MediaLive Channel Input Change`.
- MediaConnect: `MediaConnect Flow Status Change`, `MediaConnect Alert`, `MediaConnect Source Health`,
  `MediaConnect Flow Health`, `MediaConnect Output Health`. Source Health carries the SRT source state
  (`connected`, `receiving`, `disconnected`, `idle`) and TR 101 290 flags.

### Terraform: `modules/encode`

New variable `log_level` (default `INFO`, validated against `ERROR`, `WARNING`, `INFO`, `DEBUG`, `DISABLED`), passed to
the channel. Encoder logs are billed as CloudWatch Logs ingestion; as-run logs are free and always written.

### `ElementalMediaLive` log group

MediaLive writes encoder and as-run logs to this fixed log group, which it creates itself, one stream per channel
ARN/pipeline (as-run streams end in `_as_run`). It already exists in the account from the rehearsal. Terraform does
**not** manage it, because creating it would fail with `ResourceAlreadyExists`. "Scan for leftovers" reports it as
informational (log storage only, not hourly billing) and does not fail on it.

### Log tabs

| Tab | Content |
|---|---|
| All | EventBridge events and job start/finish lines, merged by time |
| SRT Source | Test source output (redacted) and `MediaConnect Source Health` events |
| MediaConnect | Flow Status Change, Alert, Flow Health, Output Health events |
| MediaLive | Channel State Change, Alert, Input Change events; encoder and as-run log lines |
| MediaPackage | "Access logs are not enabled." plus an ingest chart from the metrics |
| CloudFront | "Access logs are not enabled." plus requests and 5xx charts from the metrics |
| Jobs | Terraform, go-live and go-off-air output |

`livectl/logs.py` reads with `logs.filter_log_events` (start time plus `nextToken`) and turns each EventBridge event into
one line, for example `12:00:51 MediaLive · channel STARTING → RUNNING`. The raw JSON is available on click. The page
polls only the visible tab, every 5 s.

## Test source: `livectl/source.py`

`SourceProcess` runs at most one `source/send-srt.sh` child, independent of the job runner so a running source never
blocks "Go off air".

- Injected: a process launcher and `read_passphrase() -> str` (Secrets Manager in `cli.py`).
- The passphrase is read at the moment of "Send test source", passed only in the child's environment
  (`SRT_PASSPHRASE`), never stored on the `Console`, never written to a file.
- Output goes to a bounded buffer (last 500 lines). **Every line is redacted** (passphrase → `***`) before it is stored,
  because FFmpeg prints the output URL, which contains `passphrase=`.
- Status: `stopped`, `starting`, `running`, `exited (code N)` with the last error line.
- Stop: SIGTERM, then SIGKILL after 5 s. Registered with `atexit` and run on Ctrl-C, so FFmpeg never outlives the
  console.

Known limitation, documented next to the state-file one: FFmpeg receives the passphrase as part of its SRT URL argument,
so it is visible in the local process list (`ps`) while the source runs. This is also true of `just send` today. FFmpeg's
SRT protocol offers no other way to pass it.

## Frontend

Native ES modules under `tools/livectl/site/`, no build step, no dependencies:
`index.html`, `app.css`, `app.js`, `api.js`, `chain.js`, `node-detail.js`, `controls.js`, `logs-panel.js`,
`endpoints.js`, `format.js`. The existing design tokens and bundled Inter font are kept.

Packaging: `tools/pyproject.toml` `package-data` must add `site/*.js` and `site/*.css`; the server's `CONTENT_TYPES`
gains `.js` and `.css`. Without both, an installed `livectl` serves a page with no scripts.

The player is an `<iframe>` of the deployed player page (`player_url`), which avoids CORS from `127.0.0.1` and keeps a
single player implementation. When not deployed, the frame shows a placeholder.

## Testing

All offline.

1. **pytest (moto, or stubs where moto lacks an API):** each probe and its failure case; every verdict row; cost
   per resource; `TtlCache` with a fake clock; manifest check with a fake `fetch`; `SourceProcess` with a fake
   launcher, including redaction, SIGKILL fallback and stop on exit; every action-rule refusal; EventBridge event
   formatting; existing start/stop idempotency tests unchanged.
2. **terraform test:** `modules/observability` (rule pattern, target, retention, resource policy principal);
   `modules/encode` (`log_level` validation).
3. **Browser tests:** `tools/tests/scenarios.py` defines named scenarios that run the real server with stub clients:
   `not-deployed`, `deploying`, `off-air`, `going-live`, `on-air-no-source`, `on-air-playing`, `degraded`, `partly-on`,
   `teardown-refused`, `probe-error`. Playwright for Python (dev extra only, driving the installed Chrome) checks in each
   scenario: the verdict text, which buttons are visible, exact clipboard contents after Copy, tab switching, node
   selection, and that error text is written for a person. `just test-ui` runs them; `just test` runs them only when
   Playwright is installed.
4. **Not covered offline (live checklist in the README, run by the user):** real AWS responses for each probe;
   EventBridge delivery into the log group; the iframe playing; the manifest check through real CloudFront; the
   CloudFront metric dimensions.

## Documentation

- README: console section rewritten with the new names, screen and live checklist.
- `justfile`: recipe comments use the new vocabulary (`start` → "go live").
- AGENTS.md: correct the MediaLive figure (~0.78 → ~1.20 USD/hour); mention `modules/observability` in the module list.
- README lessons/limitations: the `ps` passphrase exposure, next to the state-file limitation.

## Delivery phases

Each phase ends with `just test` green and something usable.

1. **Backend.** `pipeline.py`, `verdict.py`, `TtlCache`, manifest check, extended `Targets`, new Terraform outputs,
   `/api/pipeline`, action rules. The old page keeps working against a compatibility shim until phase 2 replaces it.
2. **Frontend.** New layout, renamed controls, copy fix, player iframe, scenario fixtures and browser tests.
3. **Logs and test source.** `modules/observability`, encode `log_level`, `logs.py`, `/api/logs`, log panel,
   `SourceProcess`, source controls.

## Risks

- **moto coverage.** moto may not implement `medialive.list_alerts`, `mediapackagev2` or CloudFront metrics; those
  probes are tested with small stubs instead.
- **EventBridge to CloudWatch Logs** silently delivers nothing if the resource policy is wrong. Mitigated by a test on
  the policy and a live checklist item.
- **CloudWatch Logs read pricing** was not looked up. Mitigated by polling only the visible tab.
- **Log group `ElementalMediaLive`** persists after destroy by design; reported, not deleted.
