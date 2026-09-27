# Control Center Logs, Test Source and Docs Implementation Plan (Plan 7 of 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-resource logs and events in the side panel, the FFmpeg test source started and stopped from the console with its passphrase never reaching a log line, and documentation that matches what was built, including a live checklist for everything offline tests cannot prove.

**Architecture:** A new Terraform module, `modules/observability`, sends every MediaLive and MediaConnect EventBridge event into one log group; `modules/encode` gains a `log_level`. `livectl/logs.py` reads that group and the `ElementalMediaLive` group and turns events into one-line summaries. `livectl/source.py` runs `source/send-srt.sh` as a child process, separate from the job runner, and redacts the passphrase from its output. The server gains `GET /api/logs` and `POST /api/source/start|stop`; the page gains one log tab per resource and the source buttons.

**Tech Stack:** Terraform >= 1.11 (mocked providers); Python >= 3.10, boto3, pytest, moto; JavaScript modules; Playwright for Python.

**Spec:** `docs/superpowers/specs/2026-09-26-control-center-design.md`
**Depends on:** Plans 5 and 6.

## Global Constraints

- The SRT passphrase is read from Secrets Manager at the moment "Send test source" is clicked, passed only in the child's environment, redacted from every output line, and never written to a file, the page or a log. It is kept in `SourceProcess` memory only while the child runs, for redaction.
- The test source stops before the channel on "Go off air", and when the console exits.
- EventBridge event field names were checked for MediaConnect Source Health and Flow Status Change. MediaLive field names (`state`, `alert_type`, `message`, `alarm_state`) are not verified: the formatter falls back to a compact JSON summary for anything it does not recognise, and the live checklist covers them.
- Every new Terraform variable and output has a `description`; the new module has a test file.
- Every task ends with `just test` green and a commit.
- Nothing in this plan touches a real AWS account. The live checklist in Task 8 is the user's to run.

## File Structure

```
modules/observability/{versions.tf,variables.tf,outputs.tf,main.tf,tests/observability.tftest.hcl}  (create)
modules/encode/{variables.tf,main.tf,tests/encode.tftest.hcl}   (modify: log_level)
envs/demo/{main.tf,outputs.tf}                                  (modify: observability, events_log_group)
tools/livectl/logs.py                                           (create)
tools/livectl/source.py                                         (create)
tools/livectl/clean.py                                          (modify: informational log group)
tools/livectl/server.py, cli.py                                 (modify)
tools/livectl/site/{api.js,logs-panel.js,app.js,app.css}        (modify)
tools/tests/test_logs.py, test_source.py                        (create)
tools/tests/test_clean.py, test_server.py, test_ui.py, scenarios.py, stubs.py   (modify)
README.md, AGENTS.md, justfile                                  (modify)
```

---

### Task 1: `modules/observability`

**Files:**
- Create: `modules/observability/versions.tf`, `variables.tf`, `outputs.tf`, `main.tf`, `tests/observability.tftest.hcl`
- Modify: `envs/demo/main.tf`, `envs/demo/outputs.tf`

**Interfaces:**
- Produces: module inputs `name`, `retention_days` (default 7); outputs `log_group_name`, `rule_name`; envs/demo output `events_log_group`.

- [ ] **Step 1: Write the failing test**

`modules/observability/tests/observability.tftest.hcl`:
```hcl
mock_provider "aws" {
  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:/aws/events/live-demo"
    }
  }
}

variables {
  name = "live-demo"
}

run "media_events_reach_the_log_group" {
  command = apply

  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.media.event_pattern).source == ["aws.medialive", "aws.mediaconnect"]
    error_message = "The rule must match MediaLive and MediaConnect events."
  }

  assert {
    condition     = aws_cloudwatch_event_target.logs.arn == aws_cloudwatch_log_group.events.arn
    error_message = "The rule must deliver to the events log group."
  }

  assert {
    condition     = aws_cloudwatch_log_group.events.name == "/aws/events/live-demo"
    error_message = "The log group is named /aws/events/<name>."
  }

  assert {
    condition     = aws_cloudwatch_log_group.events.retention_in_days == 7
    error_message = "Default retention is seven days."
  }
}

run "eventbridge_may_write_to_the_group" {
  command = apply

  assert {
    condition = contains(
      jsondecode(aws_cloudwatch_log_resource_policy.events.policy_document).Statement[0].Principal.Service,
      "events.amazonaws.com",
    )
    error_message = "Without a resource policy for events.amazonaws.com the rule silently delivers nothing."
  }

  assert {
    condition     = jsondecode(aws_cloudwatch_log_resource_policy.events.policy_document).Statement[0].Resource == "${aws_cloudwatch_log_group.events.arn}:*"
    error_message = "The policy must cover the streams of this log group only."
  }
}

run "rejects_an_unsupported_retention" {
  command = plan

  variables {
    retention_days = 8
  }

  expect_failures = [var.retention_days]
}
```
Run: `terraform -chdir=modules/observability init -backend=false >/dev/null; terraform -chdir=modules/observability test`
Expected: FAIL (no configuration).

- [ ] **Step 2: Implement**

`versions.tf`:
```hcl
terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}
```
`variables.tf`:
```hcl
variable "name" {
  description = "Base name for the log group, rule and resource policy."
  type        = string
}

variable "retention_days" {
  description = "Days to keep MediaLive and MediaConnect events in the log group."
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.retention_days)
    error_message = "retention_days must be one of 1, 3, 5, 7, 14 or 30."
  }
}
```
`main.tf`:
```hcl
# EventBridge -> CloudWatch Logs gives the console one timeline of state changes, alerts and source health,
# including TR 101 290 flags, for a few cents a month.
resource "aws_cloudwatch_log_group" "events" {
  name              = "/aws/events/${var.name}"
  retention_in_days = var.retention_days
}

resource "aws_cloudwatch_event_rule" "media" {
  name        = "${var.name}-media-events"
  description = "MediaLive and MediaConnect state changes, alerts and health events for the livectl console."

  event_pattern = jsonencode({
    source = ["aws.medialive", "aws.mediaconnect"]
  })
}

resource "aws_cloudwatch_event_target" "logs" {
  rule = aws_cloudwatch_event_rule.media.name
  arn  = aws_cloudwatch_log_group.events.arn
}

# Without this, the rule matches, reports no error, and delivers nothing.
resource "aws_cloudwatch_log_resource_policy" "events" {
  policy_name = "${var.name}-events-to-logs"
  policy_document = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["events.amazonaws.com", "delivery.logs.amazonaws.com"] }
      Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource  = "${aws_cloudwatch_log_group.events.arn}:*"
    }]
  })
}
```
`outputs.tf`:
```hcl
output "log_group_name" {
  description = "Name of the log group that receives MediaLive and MediaConnect events."
  value       = aws_cloudwatch_log_group.events.name
}

output "rule_name" {
  description = "Name of the EventBridge rule."
  value       = aws_cloudwatch_event_rule.media.name
}
```
In `envs/demo/main.tf`, add:
```hcl
module "observability" {
  source = "../../modules/observability"

  name = "${var.project}-demo"
}
```
In `envs/demo/outputs.tf`, add:
```hcl
output "events_log_group" {
  description = "Log group holding MediaLive and MediaConnect events, read by the console."
  value       = module.observability.log_group_name
}
```

- [ ] **Step 3: Verify and commit**

Run: `just fmt && terraform -chdir=modules/observability test && terraform -chdir=envs/demo validate`
Expected: 3 passed; valid. (`envs/demo` needs `terraform -chdir=envs/demo init -backend=false` only if `envs/demo/.terraform` is missing, and after adding a module it needs `terraform -chdir=envs/demo get`.)
```bash
git add modules/observability envs/demo/main.tf envs/demo/outputs.tf
git commit -m "feat: send medialive and mediaconnect events to a log group"
```

---

### Task 2: MediaLive encoder log level

**Files:**
- Modify: `modules/encode/variables.tf`, `modules/encode/main.tf`, `modules/encode/tests/encode.tftest.hcl`

- [ ] **Step 1: Write the failing tests**

Append to `modules/encode/tests/encode.tftest.hcl`:
```hcl
run "writes_encoder_logs_at_info_by_default" {
  command = plan

  assert {
    condition     = aws_medialive_channel.this.log_level == "INFO"
    error_message = "Encoder logs default to INFO."
  }
}

run "rejects_an_unknown_log_level" {
  command = plan

  variables {
    log_level = "VERBOSE"
  }

  expect_failures = [var.log_level]
}
```
Run: `terraform -chdir=modules/encode test` → FAIL.

- [ ] **Step 2: Implement**

Append to `modules/encode/variables.tf`:
```hcl
variable "log_level" {
  description = "MediaLive encoder log level, written to the ElementalMediaLive log group. As-run logs are written regardless and are free."
  type        = string
  default     = "INFO"

  validation {
    condition     = contains(["ERROR", "WARNING", "INFO", "DEBUG", "DISABLED"], var.log_level)
    error_message = "log_level must be ERROR, WARNING, INFO, DEBUG or DISABLED."
  }
}
```
In `aws_medialive_channel.this`, after `start_channel = false`, add `log_level = var.log_level`.

- [ ] **Step 3: Verify and commit**

Run: `just fmt && terraform -chdir=modules/encode test` → PASS.
```bash
git add modules/encode
git commit -m "feat: write medialive encoder logs at a configurable level"
```

---

### Task 3: Reading and formatting logs

**Files:**
- Create: `tools/livectl/logs.py`, `tools/tests/test_logs.py`

**Interfaces:**
- Produces: `LOG_TABS = ("all", "srt", "mediaconnect", "medialive")`; `ELEMENTAL_GROUP = "ElementalMediaLive"`; `LOOKBACK_MS`; `LogLine` (frozen: `at_ms, tab, text, raw=None`, `to_dict()`); `clock_label(ms) -> "HH:MM:SS"`; `format_event(message, at_ms) -> Optional[LogLine]`; `is_ours(message, targets) -> bool`; `LogReader(logs).events(group, *, after_ms, stream_prefix=None) -> list[tuple[int, str, str]]`; `event_lines(reader, targets, tab, after_ms) -> list[LogLine]`; `medialive_lines(reader, channel_arn, after_ms) -> list[LogLine]`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_logs.py`:
```python
import json

import boto3

from livectl.logs import LogReader, event_lines, format_event, is_ours, medialive_lines
from livectl.targets import Targets

FLOW = "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:live-sports-aws-demo"
CHANNEL = "arn:aws:medialive:us-east-1:123456789012:channel:7387208"
TARGETS = Targets(flow_arn=FLOW, channel_id="7387208", channel_arn=CHANNEL, events_log_group="/aws/events/demo")
T = 1_790_000_000_000  # ms


def event(source, kind, detail, resources=(FLOW,)):
    return json.dumps({"source": source, "detail-type": kind, "detail": detail, "resources": list(resources)})


def test_flow_status_change_reads_as_a_transition():
    line = format_event(event("aws.mediaconnect", "MediaConnect Flow Status Change",
                              {"previousStatus": "STANDBY", "currentStatus": "ACTIVE"}), T)

    assert line.tab == "mediaconnect"
    assert line.text.endswith("MediaConnect · flow STANDBY → ACTIVE")


def test_source_health_goes_to_the_srt_tab_with_its_tr_101_290_flags():
    detail = {"unhealthy": True, "current": {"state": "CONNECTED", "tr101": {
        "ts_sync_loss": False, "continuity_count_error": True, "pcr_error": True}}}

    line = format_event(event("aws.mediaconnect", "MediaConnect Source Health", detail), T)

    assert line.tab == "srt"
    assert "source connected" in line.text
    assert "TR 101 290: continuity_count_error, pcr_error" in line.text


def test_medialive_state_change():
    line = format_event(event("aws.medialive", "MediaLive Channel State Change", {"state": "RUNNING"},
                              (CHANNEL,)), T)

    assert (line.tab, line.text.split(" ", 1)[1]) == ("medialive", "MediaLive · channel RUNNING")


def test_an_unknown_event_type_is_summarised_not_dropped():
    line = format_event(event("aws.medialive", "MediaLive Something New", {"x": 1}, (CHANNEL,)), T)

    assert "Something New" in line.text and '"x": 1' in line.text
    assert line.raw is not None


def test_non_json_and_foreign_sources_are_ignored():
    assert format_event("not json", T) is None
    assert format_event(event("aws.s3", "Object Created", {}), T) is None


def test_only_events_about_this_pipeline_are_kept():
    assert is_ours(event("aws.mediaconnect", "MediaConnect Alert", {}, (FLOW + ":source:x",)), TARGETS)
    assert not is_ours(event("aws.mediaconnect", "MediaConnect Alert", {}, ("arn:aws:mediaconnect:::flow:other",)),
                       TARGETS)


def put(logs, group, stream, messages):
    logs.create_log_group(logGroupName=group)
    logs.create_log_stream(logGroupName=group, logStreamName=stream)
    logs.put_log_events(logGroupName=group, logStreamName=stream,
                        logEvents=[{"timestamp": T + i, "message": m} for i, m in enumerate(messages)])


def test_event_lines_filter_by_tab_and_come_back_in_time_order(aws):
    logs = boto3.client("logs")
    put(logs, "/aws/events/demo", "s", [
        event("aws.medialive", "MediaLive Channel State Change", {"state": "STARTING"}, (CHANNEL,)),
        event("aws.mediaconnect", "MediaConnect Flow Status Change",
              {"previousStatus": "STANDBY", "currentStatus": "ACTIVE"}),
    ])
    reader = LogReader(logs)

    assert [l.tab for l in event_lines(reader, TARGETS, "all", T - 1)] == ["medialive", "mediaconnect"]
    assert [l.tab for l in event_lines(reader, TARGETS, "medialive", T - 1)] == ["medialive"]
    assert event_lines(reader, TARGETS, "all", T + 5) == [], "only events after the cursor"


def test_medialive_logs_are_labelled_encoder_or_as_run(aws):
    logs = boto3.client("logs")
    put(logs, "ElementalMediaLive", CHANNEL + "_0_as_run", ["Switched to input mediaconnect-srt"])

    lines = medialive_lines(LogReader(logs), CHANNEL, T - 1)

    assert lines[0].text.endswith("as-run · Switched to input mediaconnect-srt")
```
Run → FAIL (no module). If moto rejects `:` in stream names, change the stream to `CHANNEL.replace(":", "_")` in the test and use `stream_prefix` accordingly; the real stream name format is on the live checklist.

- [ ] **Step 2: Implement**

`tools/livectl/logs.py`:
```python
"""Turn CloudWatch Logs into the console's per-resource log lines.

Two sources: the events log group (EventBridge events from MediaLive and MediaConnect, one JSON document per line)
and MediaLive's own `ElementalMediaLive` group (encoder and as-run logs, one stream per channel ARN and pipeline).
Event field names for MediaConnect Source Health and Flow Status Change are from the AWS documentation; anything
not recognised is summarised as compact JSON rather than dropped.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

from livectl.targets import Targets

LOG_TABS = ("all", "srt", "mediaconnect", "medialive")
ELEMENTAL_GROUP = "ElementalMediaLive"
LOOKBACK_MS = 30 * 60 * 1000
MAX_PAGES = 5


@dataclass(frozen=True)
class LogLine:
    at_ms: int
    tab: str
    text: str
    raw: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def clock_label(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%H:%M:%S")


def _brief(detail: dict, limit: int = 160) -> str:
    text = json.dumps(detail, separators=(", ", ": "), sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _mediaconnect(kind: str, detail: dict) -> tuple[str, str]:
    if kind == "MediaConnect Source Health":
        current = detail.get("current") or {}
        flags = [name for name, on in (current.get("tr101") or {}).items() if on]
        text = f"MediaConnect · source {str(current.get('state', '?')).lower()}"
        return "srt", text + (f" · TR 101 290: {', '.join(flags)}" if flags else "")
    if kind == "MediaConnect Flow Status Change":
        return "mediaconnect", f"MediaConnect · flow {detail.get('previousStatus', '?')} → {detail.get('currentStatus', '?')}"
    return "mediaconnect", f"MediaConnect · {kind.removeprefix('MediaConnect ')}: {_brief(detail)}"


def _medialive(kind: str, detail: dict) -> str:
    if kind == "MediaLive Channel State Change" and "state" in detail:
        return f"MediaLive · channel {detail['state']}"
    if kind == "MediaLive Channel Alert" and "message" in detail:
        state = detail.get("alarm_state", "")
        return f"MediaLive · alert {state} {detail.get('alert_type', '')}: {detail['message']}".replace("  ", " ")
    return f"MediaLive · {kind.removeprefix('MediaLive ')}: {_brief(detail)}"


def format_event(message: str, at_ms: int) -> Optional[LogLine]:
    try:
        event = json.loads(message)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    source, kind, detail = event.get("source"), event.get("detail-type", ""), event.get("detail") or {}
    if source == "aws.mediaconnect":
        tab, text = _mediaconnect(kind, detail)
    elif source == "aws.medialive":
        tab, text = "medialive", _medialive(kind, detail)
    else:
        return None
    return LogLine(at_ms, tab, f"{clock_label(at_ms)} {text}", raw=message)


def is_ours(message: str, targets: Targets) -> bool:
    """The rule matches every media event in the account; keep the ones about this flow or channel."""
    try:
        resources = json.loads(message).get("resources") or []
    except (ValueError, AttributeError):
        return False
    if not resources:
        return True
    return any(r.startswith(targets.flow_arn) or (targets.channel_arn and r.startswith(targets.channel_arn))
               for r in resources)


class LogReader:
    def __init__(self, logs) -> None:
        self._logs = logs

    def events(self, group: str, *, after_ms: int, stream_prefix: Optional[str] = None) -> list[tuple[int, str, str]]:
        """(timestamp, stream, message) newer than after_ms, oldest first, at most a few pages per call."""
        request: dict = {"logGroupName": group, "startTime": after_ms + 1}
        if stream_prefix:
            request["logStreamNamePrefix"] = stream_prefix
        found: list[tuple[int, str, str]] = []
        for _ in range(MAX_PAGES):
            response = self._logs.filter_log_events(**request)
            found += [(e["timestamp"], e.get("logStreamName", ""), e["message"]) for e in response.get("events", [])]
            if not response.get("nextToken"):
                break
            request["nextToken"] = response["nextToken"]
        return sorted(found)


def event_lines(reader: LogReader, targets: Targets, tab: str, after_ms: int) -> list[LogLine]:
    lines = []
    for at_ms, _, message in reader.events(targets.events_log_group, after_ms=after_ms):
        line = format_event(message, at_ms)
        if line and is_ours(message, targets) and (tab == "all" or line.tab == tab):
            lines.append(line)
    return lines


def medialive_lines(reader: LogReader, channel_arn: str, after_ms: int) -> list[LogLine]:
    lines = []
    for at_ms, stream, message in reader.events(ELEMENTAL_GROUP, after_ms=after_ms, stream_prefix=channel_arn):
        kind = "as-run" if stream.endswith("_as_run") else "encoder"
        lines.append(LogLine(at_ms, "medialive", f"{clock_label(at_ms)} {kind} · {message.strip()}"))
    return lines
```

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools/tests/test_logs.py` → PASS.
```bash
git add tools/livectl/logs.py tools/tests/test_logs.py
git commit -m "feat: read media events and medialive logs as console lines"
```

---

### Task 4: The test source process

**Files:**
- Create: `tools/livectl/source.py`, `tools/tests/test_source.py`

**Interfaces:**
- Produces: `SCRIPT` (repo `source/send-srt.sh`); `SourceBusy(RuntimeError)`; `SourceProcess(*, script=SCRIPT, popen=subprocess.Popen, clock_ms=now_ms, max_lines=500, grace=5.0)` with `start(*, host, port, passphrase)`, `stop()`, `status() -> dict` (`state`: `stopped|starting|running|exited`, `exit_code`, `last_error`, `progress`), `running -> bool`, `lines(after_ms) -> list[tuple[int, str]]`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_source.py`:
```python
import subprocess
import threading
import time

import pytest

from livectl.source import SourceBusy, SourceProcess

SECRET = "Zq7aXk2m9PbR4tLc8vN1sW6yH3dJ5fG0"
BANNER = f"Output #0, mpegts, to 'srt://203.0.113.20:5000?mode=caller&passphrase={SECRET}&pbkeylen=32':"


class FakeProcess:
    """A child whose output is fed line by line, and which can ignore SIGTERM."""

    def __init__(self, lines, *, code=0, ignores_term=False):
        self._lines, self._code, self._ignores_term = lines, code, ignores_term
        self._done = threading.Event()
        self.signals = []
        self.stdout = self._feed()

    def _feed(self):
        for line in self._lines:
            yield line + "\n"
        self._done.wait(10)

    def poll(self):
        return self._code if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return self._code

    def terminate(self):
        self.signals.append("TERM")
        if not self._ignores_term:
            self._code = -15
            self._done.set()

    def kill(self):
        self.signals.append("KILL")
        self._code = -9
        self._done.set()

    def finish(self):
        self._done.set()


def launcher(process):
    def popen(args, **kwargs):
        popen.args, popen.kwargs = args, kwargs
        return process

    return popen


def settle(source, state, timeout=2.0):
    deadline = time.monotonic() + timeout
    while source.status()["state"] != state and time.monotonic() < deadline:
        time.sleep(0.01)
    assert source.status()["state"] == state


def test_the_passphrase_goes_in_the_environment_never_the_command_line():
    process = FakeProcess([])
    popen = launcher(process)
    source = SourceProcess(popen=popen, script="send-srt.sh")

    source.start(host="203.0.113.20", port=5000, passphrase=SECRET)
    process.finish()

    assert SECRET not in " ".join(popen.args)
    assert popen.kwargs["env"]["SRT_PASSPHRASE"] == SECRET
    assert popen.kwargs["env"]["SRT_HOST"] == "203.0.113.20"


def test_ffmpegs_banner_never_reaches_the_buffer_with_the_passphrase():
    process = FakeProcess([BANNER, "frame=   30 fps= 30 q=23.0 size=  512kB"])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")

    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "running")

    text = "\n".join(line for _, line in source.lines(0))
    assert SECRET not in text
    assert "passphrase=***" in text
    process.finish()


def test_progress_lines_update_the_status_instead_of_flooding_the_log():
    process = FakeProcess([f"frame= {n} fps=30" for n in range(100)])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")

    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "running")
    time.sleep(0.05)

    assert source.lines(0) == []
    assert source.status()["progress"].startswith("frame=")
    process.finish()


def test_a_second_start_is_refused_while_running():
    process = FakeProcess(["frame= 1"])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")
    source.start(host="h", port=5000, passphrase=SECRET)

    with pytest.raises(SourceBusy):
        source.start(host="h", port=5000, passphrase=SECRET)
    process.finish()


def test_stop_terminates_and_reports_stopped():
    process = FakeProcess(["frame= 1"])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")
    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "running")

    source.stop()

    assert process.signals == ["TERM"]
    settle(source, "stopped")


def test_stop_kills_a_child_that_ignores_sigterm():
    process = FakeProcess(["frame= 1"], ignores_term=True)
    source = SourceProcess(popen=launcher(process), script="send-srt.sh", grace=0.05)
    source.start(host="h", port=5000, passphrase=SECRET)

    source.stop()

    assert process.signals == ["TERM", "KILL"]


def test_a_crash_is_reported_with_its_last_line():
    process = FakeProcess(["Connection to srt://h:5000 failed: Input/output error"], code=1)
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")

    source.start(host="h", port=5000, passphrase=SECRET)
    process.finish()
    settle(source, "exited")

    status = source.status()
    assert status["exit_code"] == 1
    assert "failed" in status["last_error"]


def test_stop_with_nothing_running_is_a_no_op():
    SourceProcess(popen=launcher(FakeProcess([])), script="send-srt.sh").stop()


def test_the_buffer_is_bounded():
    process = FakeProcess([f"line {n}" for n in range(50)])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh", max_lines=10)
    source.start(host="h", port=5000, passphrase=SECRET)
    process.finish()
    settle(source, "exited")

    assert [line for _, line in source.lines(0)][-1] == "line 49"
    assert len(source.lines(0)) == 10
```
Run → FAIL (no module).

- [ ] **Step 2: Implement**

`tools/livectl/source.py`:
```python
"""Run the FFmpeg test source from the console, one at a time, without ever logging its passphrase.

The passphrase travels to the child in its environment, as `just send` does. FFmpeg still prints its output URL,
which contains `passphrase=...`, so every line is redacted before it is stored. FFmpeg's `frame=` progress lines
update the status instead of filling the buffer.

Known limitation: FFmpeg receives the passphrase inside its SRT URL argument, so it is visible in the local
process list (`ps`) while the source runs. `just send` has the same exposure; FFmpeg's SRT support offers no
other way to pass it.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional

SCRIPT = Path(__file__).resolve().parents[2] / "source" / "send-srt.sh"
STOPPED, STARTING, RUNNING, EXITED = "stopped", "starting", "running", "exited"


class SourceBusy(RuntimeError):
    """Raised when the test source is started while it is already running."""


def now_ms() -> int:
    return int(time.time() * 1000)


class SourceProcess:
    def __init__(self, *, script: "Path | str" = SCRIPT, popen=subprocess.Popen,
                 clock_ms: Callable[[], int] = now_ms, max_lines: int = 500, grace: float = 5.0) -> None:
        self._script, self._popen, self._clock_ms, self._grace = str(script), popen, clock_ms, grace
        self._lock = threading.Lock()
        self._lines: deque[tuple[int, str]] = deque(maxlen=max_lines)
        self._process = None
        self._state = STOPPED
        self._stopping = False
        self._secret: Optional[str] = None
        self._exit_code: Optional[int] = None
        self._progress: Optional[str] = None
        self._last_line: Optional[str] = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state in (STARTING, RUNNING)

    def start(self, *, host: str, port: int, passphrase: str) -> None:
        with self._lock:
            if self._state in (STARTING, RUNNING):
                raise SourceBusy("the test source is already running")
            self._state, self._stopping, self._secret = STARTING, False, passphrase
            self._exit_code = self._progress = self._last_line = None
        env = {**os.environ, "SRT_HOST": host, "SRT_PORT": str(port), "SRT_PASSPHRASE": passphrase}
        process = self._popen([self._script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, bufsize=1, env=env)
        with self._lock:
            self._process = process
        threading.Thread(target=self._pump, args=(process,), daemon=True, name="livectl-source").start()

    def _redact(self, line: str) -> str:
        return line.replace(self._secret, "***") if self._secret else line

    def _pump(self, process) -> None:
        for raw in process.stdout:
            line = self._redact(raw.rstrip("\n"))
            if not line.strip():
                continue
            with self._lock:
                if line.lstrip().startswith("frame="):
                    self._progress = line.strip()
                    if self._state == STARTING:
                        self._state = RUNNING
                    continue
                self._lines.append((self._clock_ms(), line))
                self._last_line = line
        code = process.wait()
        with self._lock:
            self._exit_code = code
            self._state = STOPPED if self._stopping else EXITED
            self._secret = None  # all output has been redacted; nothing else needs it

    def stop(self) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                return
            self._stopping = True
        process.terminate()
        try:
            process.wait(timeout=self._grace)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "exit_code": self._exit_code,
                "last_error": self._last_line if self._state == EXITED and self._exit_code else None,
                "progress": self._progress,
            }

    def lines(self, after_ms: int) -> list[tuple[int, str]]:
        with self._lock:
            return [(at, text) for at, text in self._lines if at > after_ms]
```
A child that ends on its own with code 0 is `exited` with `exit_code` 0 and no `last_error`; only a stop from the console reads `stopped`.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools/tests/test_source.py` → PASS.
```bash
git add tools/livectl/source.py tools/tests/test_source.py
git commit -m "feat: run the test source from the console with its passphrase redacted"
```

---

### Task 5: Leftover scan reports the MediaLive log group

**Files:**
- Modify: `tools/livectl/clean.py`, `tools/livectl/server.py` (`_check_clean_work`), `tools/livectl/cli.py` (`check-clean`), `tools/tests/test_clean.py`

**Interfaces:**
- Produces: `find_informational(logs, prefix) -> list[str]` (never fails the check).

- [ ] **Step 1: Write the failing test**

Append to `tools/tests/test_clean.py`:
```python
from livectl.clean import find_informational


def test_the_medialive_log_group_is_reported_but_does_not_fail_the_check(aws):
    logs = boto3.client("logs")
    logs.create_log_group(logGroupName="ElementalMediaLive")

    info = find_informational(logs, "live-sports-aws")

    assert info == ["CloudWatch log group ElementalMediaLive (log storage only, not billed by the hour)"]
    assert find_leftovers(*clients(), prefix="live-sports-aws") == []


def test_no_log_group_means_no_information(aws):
    assert find_informational(boto3.client("logs"), "live-sports-aws") == []
```
Run → FAIL.

- [ ] **Step 2: Implement**

Append to `clean.py`:
```python
def find_informational(logs, prefix: str) -> list[str]:
    """Things that outlive a destroy on purpose. Reported, never counted as leftovers.

    MediaLive creates `ElementalMediaLive` itself and keeps writing to it, so Terraform does not manage it.
    """
    found = []
    for page in _pages(logs, "describe_log_groups", logGroupNamePrefix="ElementalMediaLive"):
        for group in page.get("logGroups", []):
            found.append(f"CloudWatch log group {group['logGroupName']} (log storage only, not billed by the hour)")
    return found
```
In `server.py` `_check_clean_work`, after listing leftovers and before raising: `for line in find_informational(console.logs, console.prefix): log("info: " + line)` (guarded by `if console.logs is not None`). In `cli.py` `check-clean`, print the same lines prefixed `info:` after the leftover report, without changing the exit code. `prefix` is accepted for symmetry and future use.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools` → PASS.
```bash
git add tools/livectl/clean.py tools/livectl/server.py tools/livectl/cli.py tools/tests/test_clean.py
git commit -m "feat: report the medialive log group after a teardown without failing the scan"
```

---

### Task 6: Server routes for logs and the source

**Files:**
- Modify: `tools/livectl/server.py`, `tools/livectl/cli.py`, `tools/tests/test_server.py`

**Interfaces:**
- `Console` gains: `logs: Any = None`; `source: SourceProcess = field(default_factory=SourceProcess)`; `read_passphrase: Callable[[str], str] = _no_passphrase` (raises `RuntimeError`); `clock_ms: Callable[[], int] = now_ms`. `__post_init__` passes `source_status=self.source.status` to the `Pipeline` it builds.
- `ROUTED_ACTIONS` gains `source-start`, `source-stop`. `Situation.source_running` comes from `console.source.running`. The pipeline payload gains `source` (the status dict).
- `GET /api/logs?tab=<all|srt|mediaconnect|medialive>&after=<ms>` → `{"lines": [LogLine dicts], "after": ms, "notes": [str]}`.
- `POST /api/source/start` → 202 / 409 / 502; `POST /api/source/stop` → 200.
- `/api/go-off-air` stops the source first.
- `serve()` stops the source in a `finally`, so Ctrl-C never leaves FFmpeg pushing.

- [ ] **Step 1: Write the failing tests**

Append to `tools/tests/test_server.py`:
```python
from livectl.source import SourceProcess
from test_source import SECRET, FakeProcess, launcher


def source_console(flow="ACTIVE", channel="RUNNING", process=None, passphrase=SECRET):
    console = stub_console(flow=flow, channel=channel)
    console.source = SourceProcess(popen=launcher(process or FakeProcess(["frame= 1"])), script="send-srt.sh")
    console.read_passphrase = lambda arn: passphrase
    console.forget_targets()
    return console


def test_source_start_is_refused_while_the_flow_is_off(aws):
    status, payload = call(source_console(flow="STANDBY", channel="IDLE"), "POST", "/api/source/start")

    assert status == 409 and "go live first" in payload["error"]


def test_source_start_reads_the_passphrase_and_never_returns_it(aws):
    console = source_console()
    console.runner = lambda args: json.dumps(dict(OUTPUTS, passphrase_secret_arn={"value": "arn:secret"}))
    console.forget_targets()

    status, payload = call(console, "POST", "/api/source/start")
    _, pipeline = call(console, "GET", "/api/pipeline")

    assert status == 202
    assert SECRET not in json.dumps(payload) + json.dumps(pipeline)
    assert pipeline["source"]["state"] in ("starting", "running")
    console.source.stop()


def test_a_passphrase_that_cannot_be_read_is_a_readable_error(aws):
    console = source_console()
    console.runner = lambda args: json.dumps(dict(OUTPUTS, passphrase_secret_arn={"value": "arn:secret"}))
    console.forget_targets()

    def denied(arn):
        raise RuntimeError("AccessDeniedException")

    console.read_passphrase = denied
    status, payload = call(console, "POST", "/api/source/start")

    assert status == 502 and "Secrets Manager" in payload["error"]


def test_go_off_air_stops_the_source_first(aws):
    process = FakeProcess(["frame= 1"])
    console = deployed_console()
    console.source = SourceProcess(popen=launcher(process), script="send-srt.sh")
    console.source.start(host="h", port=5000, passphrase=SECRET)
    call(console, "POST", "/api/go-live")
    console.jobs.wait(10)

    call(console, "POST", "/api/go-off-air")
    console.jobs.wait(10)

    assert process.signals == ["TERM"]
    assert "test source stopped" in console.jobs.summary()["lines"][0]


def test_logs_without_a_stack_say_so(aws):
    status, payload = call(make_console(), "GET", "/api/logs", query={"tab": ["all"]})

    assert status == 200 and payload["lines"] == []
    assert "Nothing is deployed" in payload["notes"][0]


def test_an_unknown_tab_is_a_400(aws):
    assert call(make_console(), "GET", "/api/logs", query={"tab": ["nope"]})[0] == 400


def test_the_srt_tab_includes_the_redacted_ffmpeg_output(aws):
    from test_source import BANNER, settle

    process = FakeProcess([BANNER, "frame= 1"])
    console = source_console(process=process)
    console.source.start(host="h", port=5000, passphrase=SECRET)
    settle(console.source, "running")

    _, payload = call(console, "GET", "/api/logs", query={"tab": ["srt"], "after": ["1"]})

    texts = [line["text"] for line in payload["lines"]]
    assert any("passphrase=***" in t for t in texts)
    assert SECRET not in json.dumps(payload)
    process.finish()
```
Run → FAIL.

- [ ] **Step 2: Implement**

In `server.py`:
```python
from livectl.logs import LOG_TABS, LOOKBACK_MS, LogLine, LogReader, clock_label, event_lines, medialive_lines
from livectl.pipeline import REDEPLOY_HINT
from livectl.source import SourceBusy, SourceProcess, now_ms


def _no_passphrase(arn: str) -> str:
    raise RuntimeError("no Secrets Manager client configured")
```
Add the `Console` fields listed above (after the existing defaulted fields), and in `__post_init__` build the pipeline with `source_status=self.source.status`. In `_snapshot`, set `source_running=console.source.running` in both `Situation`s and add `"source": console.source.status()` to both payloads. Extend `ROUTED_ACTIONS` with `"source-start", "source-stop"`.

Logs:
```python
def _logs_payload(console: Console, tab: str, after: int) -> dict:
    lines: list[LogLine] = []
    notes: list[str] = []
    if tab == "srt":
        lines += [LogLine(at, "srt", f"{clock_label(at)} ffmpeg · {text}") for at, text in console.source.lines(after)]
    try:
        targets = console.targets()
    except TargetError:
        notes.append("Nothing is deployed, so there are no AWS logs to read.")
        targets = None
    if targets is not None and console.logs is not None:
        reader = LogReader(console.logs)
        if targets.events_log_group:
            try:
                lines += event_lines(reader, targets, tab, after)
            except Exception as error:
                notes.append(f"Could not read the event log ({type(error).__name__}).")
        else:
            notes.append(f"events_log_group {REDEPLOY_HINT}")
        if tab == "medialive" and targets.channel_arn:
            try:
                lines += medialive_lines(reader, targets.channel_arn, after)
            except Exception as error:
                notes.append("MediaLive has not written channel logs yet."
                             if "ResourceNotFound" in type(error).__name__ + str(error)
                             else f"Could not read MediaLive logs ({type(error).__name__}).")
    lines.sort(key=lambda line: line.at_ms)
    return {"lines": [line.to_dict() for line in lines],
            "after": max([after] + [line.at_ms for line in lines]), "notes": notes}
```
In `route`, GET:
```python
        if path == "/api/logs":
            tab = query.get("tab", ["all"])[0]
            if tab not in LOG_TABS:
                return _json(400, {"error": f"unknown log tab: {tab}"})
            after = int(query.get("after", ["0"])[0] or 0) or console.clock_ms() - LOOKBACK_MS
            return _json(200, _logs_payload(console, tab, after))
```
POST:
```python
    if path == "/api/source/start":
        refused = _refused(console, "source-start")
        if refused:
            return refused
        targets = console.targets()
        if not (targets.ingest_ip and targets.passphrase_secret_arn):
            return _json(409, {"error": "The ingest address or the passphrase is missing from the Terraform "
                                        "outputs. Run Deploy stack once to add them."})
        try:
            passphrase = console.read_passphrase(targets.passphrase_secret_arn)
        except Exception as error:  # never echo the error body: keep secrets out of responses on principle
            return _json(502, {"error": f"Could not read the SRT passphrase from Secrets Manager ({type(error).__name__})."})
        try:
            console.source.start(host=targets.ingest_ip, port=int(targets.ingest_port or 5000), passphrase=passphrase)
        except SourceBusy as error:
            return _json(409, {"error": "The test source is already running."})
        console.pipeline.forget()
        return _json(202, {"started": "source"})

    if path == "/api/source/stop":
        console.source.stop()
        console.pipeline.forget()
        return _json(200, {"stopped": "source"})
```
In the go-off-air branch, wrap the work:
```python
        def off_air(log) -> None:
            if console.source.running:
                console.source.stop()
                log("test source stopped")
            stop_workflow(console.mediaconnect, console.medialive, targets, log=log)
```
(and use `start_workflow` unchanged for go-live). In `serve`, wrap `httpd.serve_forever()` so the `finally` calls `console.source.stop()`.

In `cli.py`, build the console with `logs=session.client("logs")` and
```python
read_passphrase=lambda arn: session.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"],
```
and register `atexit.register(console.source.stop)` before `serve(...)`, so an exception path also stops FFmpeg.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools` → PASS.
```bash
git add tools/livectl/server.py tools/livectl/cli.py tools/tests/test_server.py
git commit -m "feat: serve per-resource logs and control the test source"
```

---

### Task 7: Log tabs and source controls on the page

**Files:**
- Modify: `tools/livectl/site/api.js`, `logs-panel.js`, `app.js`, `app.css`, `tools/tests/stubs.py`, `tools/tests/scenarios.py`, `tools/tests/test_ui.py`

**Interfaces (DOM contract additions):**
- `#tabs [data-tab]` for `all`, `srt`, `mediaconnect`, `medialive`, `mediapackage`, `cloudfront`, `jobs`.
- `#log .line` for each line; clicking a line with raw JSON toggles a `.raw` block under it.
- `#log .note` for notes.
- Selecting a node switches the tab: `srt_source→srt`, `mediaconnect_flow→mediaconnect`, `medialive_input|medialive_channel→medialive`, `mediapackage_channel→mediapackage`, `cloudfront_cdn→cloudfront`, `player→all`.

- [ ] **Step 1: Scenario additions**

In `stubs.py`, add:
```python
def logs_client(events: list[tuple[int, str]]) -> StubClient:
    """A CloudWatch Logs stub serving (timestamp, message) events from the events group."""

    def filter_log_events(logGroupName, startTime, **_):
        if logGroupName == "ElementalMediaLive":
            raise RuntimeError("ResourceNotFoundException: The specified log group does not exist.")
        return {"events": [{"timestamp": t, "logStreamName": "s", "message": m} for t, m in events if t >= startTime]}

    return StubClient(filter_log_events=filter_log_events)
```
In `scenarios.py`:
- add `"events_log_group": "/aws/events/live-sports-aws-demo"` and `"medialive_channel_arn": f"arn:aws:medialive:us-east-1:123456789012:channel:{CHANNEL_ID}"` to `OUTPUTS`;
- build every console with `logs=logs_client(EVENTS)`, where `EVENTS` holds three events one second apart ending now: a MediaConnect Flow Status Change `STANDBY → ACTIVE`, a MediaLive Channel State Change `RUNNING`, and a MediaConnect Source Health with `continuity_count_error: true`, each with the stub flow or channel ARN in `resources`;
- add a `FakeSource` (methods `status`, `lines`, `start`, `stop`, property `running`) and a scenario `source-running`: `dict(**ON, metrics=LIVE, playing=True, source="running")`, where `source="running"` makes `build` install a `FakeSource` whose status is `{"state": "running", ...}` and whose `lines(after)` returns `[(now_ms, "Output #0, mpegts, to 'srt://203.0.113.20:5000?mode=caller&passphrase=***'")]`;
- pass `source_status=console.source.status` to each scenario's `Pipeline`.

- [ ] **Step 2: Page**

`api.js`, add:
```js
export async function getLogs(tab, after) {
  const response = await fetch('api/logs?tab=' + tab + '&after=' + (after || 0), { cache: 'no-store' });
  if (!response.ok) throw new Error('logs answered ' + response.status);
  return response.json();
}
```
`logs-panel.js`, rewrite:
```js
// The right-hand panel: one tab per resource, plus Jobs. Only the visible tab is polled.
import { getLogs } from './api.js';
import { el } from './format.js';

const TABS = [
  ['all', 'All'], ['srt', 'SRT Source'], ['mediaconnect', 'MediaConnect'], ['medialive', 'MediaLive'],
  ['mediapackage', 'MediaPackage'], ['cloudfront', 'CloudFront'], ['jobs', 'Jobs'],
];
const NO_LOGS = {
  mediapackage: 'MediaPackage access logs are not enabled in this project. Its ingest rate and 5xx count are in the node details.',
  cloudfront: 'CloudFront access logs are not enabled in this project. Requests per minute and the 5xx rate are in the node details.',
};
export const TAB_FOR_NODE = {
  srt_source: 'srt', mediaconnect_flow: 'mediaconnect', medialive_input: 'medialive', medialive_channel: 'medialive',
  mediapackage_channel: 'mediapackage', cloudfront_cdn: 'cloudfront', player: 'all',
};
const LOG_POLL_MS = 5000;

export class LogsPanel {
  constructor(tabs, list) {
    this.tabs = tabs;
    this.list = list;
    this.active = 'all';
    this.buffers = {};       // tab -> { after, lines: [line], notes: [str] }
    this.jobText = '';
    this.jobKey = null;
    this.offset = 0;
    this.tabs.replaceChildren(...TABS.map(([id, label]) => el('button', {
      type: 'button', role: 'tab', 'data-tab': id, onclick: () => this.select(id),
    }, label)));
    this.select('all');
    setInterval(() => this.refresh(), LOG_POLL_MS);
  }

  select(id) {
    this.active = id;
    for (const tab of this.tabs.querySelectorAll('[role=tab]')) {
      tab.setAttribute('aria-selected', String(tab.dataset.tab === id));
    }
    this.draw();
    this.refresh();
  }

  async refresh() {
    const tab = this.active;
    if (tab === 'jobs' || NO_LOGS[tab]) return;
    const buffer = this.buffers[tab] || (this.buffers[tab] = { after: 0, lines: [], notes: [] });
    try {
      const data = await getLogs(tab, buffer.after);
      buffer.lines = buffer.lines.concat(data.lines).slice(-1000);
      buffer.after = data.after;
      buffer.notes = data.notes;
    } catch (error) {
      buffer.notes = ['Could not read logs: ' + error.message];
    }
    if (this.active === tab) this.draw();
  }

  draw() {
    const tab = this.active;
    if (tab === 'jobs') {
      this.list.replaceChildren(el('div', { class: 'line' }, this.jobText || 'No job has run since the console started.'));
    } else if (NO_LOGS[tab]) {
      this.list.replaceChildren(el('p', { class: 'note' }, NO_LOGS[tab]));
    } else {
      const buffer = this.buffers[tab] || { lines: [], notes: [] };
      this.list.replaceChildren(
        ...buffer.notes.map((note) => el('p', { class: 'note' }, note)),
        ...(buffer.lines.length ? buffer.lines.map((line) => this.line(line))
          : [el('p', { class: 'note' }, 'No events in the last 30 minutes.')]),
      );
    }
    this.list.scrollTop = this.list.scrollHeight;
  }

  line(line) {
    const node = el('div', { class: 'line' + (line.raw ? ' has-raw' : '') }, line.text);
    if (line.raw) {
      node.addEventListener('click', () => {
        const open = node.querySelector('.raw');
        if (open) open.remove();
        else node.append(el('pre', { class: 'raw' }, JSON.stringify(JSON.parse(line.raw), null, 2)));
      });
    }
    return node;
  }

  showJob(job) {
    if (!job) return;
    const key = job.name + '@' + job.started_at;
    if (key !== this.jobKey) {
      this.jobKey = key;
      this.jobText = '';
    }
    if (job.lines.length) this.jobText += job.lines.join('\n') + '\n';
    this.offset = job.offset;
    if (this.active === 'jobs') this.draw();
  }
}
```
In `index.html`, change `<pre class="log" id="log" tabindex="0"></pre>` to `<div class="log" id="log" tabindex="0" role="log"></div>`. In `app.css`, style `.log .line` (monospace, 12px, `white-space: pre-wrap`), `.log .has-raw { cursor: pointer }`, `.log .raw` (small, muted background). In `app.js`: when a node is selected, call `logs.select(TAB_FOR_NODE[id])`; when a job starts from a button, call `logs.select('jobs')` so its output is in view.

- [ ] **Step 3: UI tests**

In `test_ui.py`, update `EXPECTED`: add `"source-start"` to `on-air-no-source`, `on-air-playing`, `degraded` and `partly-on`; add `"source-running": ("On air · playing", ["scan", "go-off-air", "source-stop"])`. Then fix the tests that read `#log` for the job (teardown test): select the Jobs tab first, `page.locator('[data-tab="jobs"]').click()`. Add:
```python
def test_the_mediaconnect_tab_reads_events_as_sentences(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('[data-tab="mediaconnect"]').click()

    expect(page.locator("#log")).to_contain_text("MediaConnect · flow STANDBY → ACTIVE")


def test_the_srt_tab_shows_tr_101_290_flags_and_never_the_passphrase(open_scenario):
    page, _ = open_scenario("source-running")

    page.locator('[data-tab="srt"]').click()

    expect(page.locator("#log")).to_contain_text("TR 101 290: continuity_count_error")
    expect(page.locator("#log")).to_contain_text("passphrase=***")


def test_selecting_a_node_opens_its_log_tab(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('#chain button[data-node="medialive_channel"]').click()

    expect(page.locator('[data-tab="medialive"]')).to_have_attribute("aria-selected", "true")
    expect(page.locator("#log")).to_contain_text("MediaLive · channel RUNNING")
    expect(page.locator("#log")).to_contain_text("has not written channel logs yet")


def test_a_raw_event_opens_on_click(open_scenario):
    page, _ = open_scenario("off-air")
    page.locator('[data-tab="all"]').click()

    page.locator("#log .has-raw").first.click()

    expect(page.locator("#log .raw").first).to_contain_text('"detail-type"')


def test_mediapackage_says_plainly_that_it_has_no_access_logs(open_scenario):
    page, _ = open_scenario("off-air")

    page.locator('[data-tab="mediapackage"]').click()

    expect(page.locator("#log")).to_contain_text("access logs are not enabled")
```

- [ ] **Step 4: Verify and commit**

Run: `just test-ui && just test` → PASS, no JavaScript errors.
```bash
git add tools/livectl/site tools/tests
git commit -m "feat: add per-resource log tabs and test source controls to the console"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`, `AGENTS.md`, `justfile`, `docs/superpowers/specs/2026-09-26-control-center-design.md` (status)

- [ ] **Step 1: README**

Read `README.md` first. Rewrite its console section to cover, in this order: what the screen shows (header verdict and cost, the seven-node chain, player, node details, controls, endpoints, log tabs); the control names and what each runs (use the Controls table from the spec); that Tear down is refused on air and needs the typed word; the test source and its passphrase handling; `just ui-scenario` and `just test-ui`. Add to the limitations, next to the Terraform state one: *"While the test source runs, the SRT passphrase is visible in the local process list (`ps`), because FFmpeg takes it inside its SRT URL. `just send` has the same exposure."* Add this live checklist as its own section:

```markdown
### Verify the console live (costs money: about 1.74 USD/hour while on air)

Offline tests prove the console's logic, not AWS's answers. After `just up` (re-run it once so the new outputs
exist), open `just ui` and check:

1. Every node shows a state; none says "not in the Terraform outputs yet".
2. **Go live**: the verdict goes *Going live…* then *On air · no source*; the cost shows about $1.49 / h.
3. **Send test source**: within about two minutes the SRT node reads *connected*, the verdict *On air · playing*,
   and the player shows the burned-in clock.
4. The **MediaLive** tab shows channel state events (proves the EventBridge rule and the log resource policy) and
   encoder log lines (proves the `ElementalMediaLive` stream prefix is the channel ARN).
5. The **SRT Source** tab shows FFmpeg output with `passphrase=***`.
6. The **CloudFront** node shows requests per minute (proves the metric dimensions).
7. Stop the test source while on air: the channel node lists an input-loss alert (proves `list_alerts` with
   `StateFilter=SET`), and the player turns black.
8. Each *Open in the AWS console* link opens the right page.
9. **Go off air**: the source stops first, then channel and flow; the verdict reads *Off air* and the cost $0.00.
10. **Tear down**, then **Scan for leftovers**: nothing billable, and `ElementalMediaLive` listed as information.
```

- [ ] **Step 2: AGENTS.md, justfile, spec**

- `AGENTS.md`: change "MediaLive ~0.78" to "MediaLive ~1.20" in the money section; add `observability` to the module list; under Testing, mention `just test-ui` (Playwright, dev extra) and `tools/tests/scenarios.py`.
- `justfile`: recipe comments use the console's words: `start` → "Go live: start the flow, then the channel (about 2 minutes; billing starts here)", `stop` → "Go off air: stop the channel, then the flow", `check-clean` → "Scan for leftovers: fail if any billable resource was left behind", `up` → "Deploy stack…", `down` → "Tear down stack…".
- Spec: set `Status: Implemented; see the README live checklist`, and note under Log tabs that the MediaPackage and CloudFront tabs show a note and point at node metrics instead of charts (charts deferred).

- [ ] **Step 3: Verify and commit**

Run: `just test` → PASS. Read the README section once more as someone who has never seen the project.
```bash
git add README.md AGENTS.md justfile docs/superpowers/specs/2026-09-26-control-center-design.md
git commit -m "docs: describe the control center and add a live checklist"
```

---

## Done when

- `just test` and `just test-ui` pass with no AWS credentials.
- `git grep -n "SRT_PASSPHRASE\|passphrase" tools/livectl` shows the passphrase only in `source.py` (environment and redaction) and in `server.py`/`cli.py` (reading it); nowhere is it logged or returned.
- The live checklist exists in the README and is the user's to run.
