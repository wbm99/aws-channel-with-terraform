# livectl, FFmpeg Source, Cost Estimate and README Implementation Plan (Plan 4 of 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the project: a small Python package (`livectl`) to operate the live workflow, an FFmpeg source script with a working UTC burn-in clock, a persistent budget guardrail, a priced cost estimate, and a README that sells the project.

**Architecture:** `tools/livectl/` is a `boto3` + `argparse` package split into `targets` (which resources to act on), `control` (start/stop with waits), `status`, `clean` (leftover check) and `cli`. Target IDs come from `terraform output -json` by default and can be overridden by flags (`--flow-arn`, `--channel-id`). Tests use `pytest` with `moto`, so no AWS access is needed. `source/send-srt.sh` wraps one FFmpeg command. The budget moves from `envs/demo` to `bootstrap/` so it survives `terraform destroy`.

**Tech Stack:** Python >= 3.10, `boto3`, `pytest`, `moto` (>= 5); Bash, FFmpeg (libsrt); Terraform >= 1.11; AWS CLI v2 (Price List API).

**Spec:** `docs/superpowers/specs/2026-09-18-live-srt-pipeline-design.md`

## Global Constraints

- `livectl` commands: `start`, `stop`, `status`, `check-clean`. Flags `--flow-arn` and `--channel-id` override Terraform output; `--tf-dir` defaults to `envs/demo`; `--region` defaults to `us-east-1`.
- `livectl` must be idempotent (starting a running workflow or stopping a stopped one is a no-op) and must stop the MediaLive channel before the MediaConnect flow, and start the flow before the channel.
- No hard-coded resource IDs anywhere; secrets are never printed.
- Python style: type hints, small functions, no global state, stdlib plus `boto3` only at runtime.
- Tests need no AWS credentials: `moto` for boto3 calls, injected fakes for Terraform, time and sleeping.
- The virtualenv is created with `python3 -m virtualenv .venv` (this machine's Python has no `venv` module) and is gitignored.
- Prices in the cost estimate come from the AWS Price List API on the day of writing and are labelled as estimates; nothing is quoted from memory.
- Tests for Terraform stay in `<module>/tests/`.

## File Structure

```
tools/pyproject.toml
tools/livectl/{__init__.py,targets.py,control.py,status.py,clean.py,cli.py}
tools/tests/{conftest.py,test_targets.py,test_control.py,test_status.py,test_clean.py,test_cli.py}
source/send-srt.sh
scripts/price_lookup.py
bootstrap/{main.tf,variables.tf,tests/bootstrap.tftest.hcl}   (modify)
envs/demo/{main.tf,variables.tf}                             (modify: remove guardrails)
docs/cost-estimate.md
README.md
```

---

### Task 1: Package scaffold and target resolution

**Files:**
- Create: `tools/pyproject.toml`, `tools/livectl/__init__.py`, `tools/livectl/targets.py`, `tools/tests/conftest.py`, `tools/tests/test_targets.py`

**Interfaces:**
- Produces: `Targets` (frozen dataclass: `flow_arn: str`, `channel_id: str`, `player_url: str | None`, `ingest_ip: str | None`, `ingest_port: int | None`), `TargetError(RuntimeError)`, `run_command(args) -> str`, `read_terraform_outputs(tf_dir, runner) -> dict`, `resolve_targets(flow_arn, channel_id, tf_dir, runner) -> Targets`.

- [ ] **Step 1: Create the virtualenv and scaffold**

Run:
```bash
cd /home/wmacedo/terraform-test-project
pip install --user virtualenv && python3 -m virtualenv .venv
mkdir -p tools/livectl tools/tests
```
Write `tools/pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "livectl"
version = "0.1.0"
description = "Operate the live SRT to HLS demo pipeline: start, stop, status, check-clean."
requires-python = ">=3.10"
dependencies = ["boto3>=1.34"]

[project.optional-dependencies]
dev = ["pytest>=8", "moto>=5"]

[project.scripts]
livectl = "livectl.cli:main"

[tool.setuptools.packages.find]
include = ["livectl*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```
Write `tools/livectl/__init__.py`:
```python
"""livectl: operate the live SRT to HLS demo pipeline."""

__version__ = "0.1.0"
```
Then: `.venv/bin/pip install -q -e "tools[dev]"`.
Expected: install succeeds and `.venv/bin/python -c "import livectl, moto, boto3; print(livectl.__version__)"` prints `0.1.0`.

- [ ] **Step 2: Write the failing tests**

`tools/tests/conftest.py`:
```python
import boto3
import pytest
from moto import mock_aws

from livectl.targets import Targets

ROLE_ARN = "arn:aws:iam::123456789012:role/test-role"


@pytest.fixture(autouse=True)
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def aws():
    with mock_aws():
        yield


def make_workflow(name: str = "live-sports-aws-demo") -> Targets:
    """Create a MediaConnect flow and a MediaLive channel in moto and return their IDs."""
    mediaconnect = boto3.client("mediaconnect")
    medialive = boto3.client("medialive")
    flow = mediaconnect.create_flow(
        Name=name,
        Source={
            "Name": f"{name}-srt",
            "Protocol": "srt-listener",
            "IngestPort": 5000,
            "WhitelistCidr": "203.0.113.10/32",
        },
    )
    channel = medialive.create_channel(
        Name=name,
        ChannelClass="SINGLE_PIPELINE",
        RoleArn=ROLE_ARN,
        InputSpecification={"Codec": "AVC", "Resolution": "HD", "MaximumBitrate": "MAX_10_MBPS"},
        Destinations=[],
        EncoderSettings={
            "AudioDescriptions": [],
            "OutputGroups": [],
            "TimecodeConfig": {"Source": "EMBEDDED"},
            "VideoDescriptions": [],
        },
    )
    return Targets(flow_arn=flow["Flow"]["FlowArn"], channel_id=channel["Channel"]["Id"])
```

`tools/tests/test_targets.py`:
```python
import json

import pytest

from livectl.targets import TargetError, resolve_targets

OUTPUTS = {
    "flow_arn": {"value": "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:demo"},
    "medialive_channel_id": {"value": "7387208"},
    "player_url": {"value": "https://example.cloudfront.net/"},
    "ingest_ip": {"value": "203.0.113.20"},
    "ingest_port": {"value": 5000},
}


def fake_runner(payload):
    calls = []

    def runner(args):
        calls.append(list(args))
        return payload if isinstance(payload, str) else json.dumps(payload)

    runner.calls = calls
    return runner


def test_flags_skip_terraform_entirely():
    def must_not_run(args):
        raise AssertionError("terraform must not be called when both flags are given")

    targets = resolve_targets("arn:flow", "42", "envs/demo", must_not_run)

    assert targets.flow_arn == "arn:flow"
    assert targets.channel_id == "42"
    assert targets.player_url is None


def test_terraform_outputs_are_used_by_default():
    runner = fake_runner(OUTPUTS)

    targets = resolve_targets(None, None, "envs/demo", runner)

    assert runner.calls == [["terraform", "-chdir=envs/demo", "output", "-json"]]
    assert targets.flow_arn.endswith(":demo")
    assert targets.channel_id == "7387208"
    assert targets.player_url == "https://example.cloudfront.net/"
    assert (targets.ingest_ip, targets.ingest_port) == ("203.0.113.20", 5000)


def test_a_flag_overrides_the_matching_terraform_value():
    targets = resolve_targets(None, "999", "envs/demo", fake_runner(OUTPUTS))

    assert targets.channel_id == "999"
    assert targets.flow_arn.endswith(":demo")


def test_missing_outputs_raise_a_helpful_error():
    with pytest.raises(TargetError, match="flow ARN and channel ID"):
        resolve_targets(None, None, "envs/demo", fake_runner({}))


def test_invalid_json_raises():
    with pytest.raises(TargetError, match="valid JSON"):
        resolve_targets(None, None, "envs/demo", fake_runner("not json"))
```

- [ ] **Step 3: Run them to verify they fail**

Run: `.venv/bin/pytest tools/tests/test_targets.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'livectl.targets'`).

- [ ] **Step 4: Implement**

`tools/livectl/targets.py`:
```python
"""Resolve which AWS resources livectl operates on."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

Runner = Callable[[Sequence[str]], str]


class TargetError(RuntimeError):
    """Raised when the target resources cannot be determined."""


@dataclass(frozen=True)
class Targets:
    flow_arn: str
    channel_id: str
    player_url: Optional[str] = None
    ingest_ip: Optional[str] = None
    ingest_port: Optional[int] = None


def run_command(args: Sequence[str]) -> str:
    """Run a command and return its standard output."""
    try:
        result = subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise TargetError(f"command not found: {args[0]}") from error
    except subprocess.CalledProcessError as error:
        raise TargetError(f"{' '.join(args)} failed: {error.stderr.strip()}") from error
    return result.stdout


def read_terraform_outputs(tf_dir: str, runner: Runner = run_command) -> dict:
    """Return the Terraform outputs of tf_dir as a plain name-to-value dict."""
    raw = runner(["terraform", f"-chdir={tf_dir}", "output", "-json"])
    try:
        outputs = json.loads(raw)
    except json.JSONDecodeError as error:
        raise TargetError("terraform output did not return valid JSON") from error
    return {name: item["value"] for name, item in outputs.items()}


def resolve_targets(
    flow_arn: Optional[str],
    channel_id: Optional[str],
    tf_dir: str,
    runner: Runner = run_command,
) -> Targets:
    """Combine flags and Terraform outputs; flags win. Terraform is skipped if both flags are set."""
    outputs: dict = {}
    if not (flow_arn and channel_id):
        outputs = read_terraform_outputs(tf_dir, runner)

    flow = flow_arn or outputs.get("flow_arn")
    channel = channel_id or outputs.get("medialive_channel_id")
    missing = [label for label, value in (("flow ARN", flow), ("channel ID", channel)) if not value]
    if missing:
        raise TargetError(
            "could not determine " + " and ".join(missing)
            + "; pass --flow-arn/--channel-id or run against a deployed environment"
        )

    return Targets(
        flow_arn=flow,
        channel_id=str(channel),
        player_url=outputs.get("player_url"),
        ingest_ip=outputs.get("ingest_ip"),
        ingest_port=outputs.get("ingest_port"),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tools/tests/test_targets.py -q`
Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add tools/pyproject.toml tools/livectl tools/tests
git commit -m "feat: add livectl package scaffold and target resolution"
```

---

### Task 2: Start and stop

**Files:**
- Create: `tools/livectl/control.py`, `tools/tests/test_control.py`

**Interfaces:**
- Consumes: `Targets` from Task 1; `conftest.make_workflow` and the `aws` fixture.
- Produces: `WaitTimeout(RuntimeError)`, `wait_for(read_state, target, *, timeout, interval, sleep, clock, label)`, `flow_status(mc, arn) -> str`, `channel_state(ml, channel_id) -> str`, `start(mc, ml, targets, *, log, **wait_kwargs) -> None`, `stop(mc, ml, targets, *, log, **wait_kwargs) -> None`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_control.py`:
```python
import boto3
import pytest

from conftest import make_workflow
from livectl.control import WaitTimeout, channel_state, flow_status, start, stop, wait_for

NO_SLEEP = {"sleep": lambda seconds: None}


def test_wait_for_returns_when_target_is_reached():
    states = iter(["STARTING", "STARTING", "ACTIVE"])

    wait_for(lambda: next(states), "ACTIVE", label="flow", **NO_SLEEP)


def test_wait_for_times_out_and_reports_the_last_state():
    ticks = iter(range(0, 1000, 10))

    with pytest.raises(WaitTimeout, match=r"flow did not reach ACTIVE.*last state: STARTING"):
        wait_for(
            lambda: "STARTING",
            "ACTIVE",
            timeout=30,
            interval=1,
            label="flow",
            sleep=lambda seconds: None,
            clock=lambda: next(ticks),
        )


def test_start_brings_up_flow_then_channel(aws):
    targets = make_workflow()
    mc, ml = boto3.client("mediaconnect"), boto3.client("medialive")
    assert flow_status(mc, targets.flow_arn) == "STANDBY"
    assert channel_state(ml, targets.channel_id) == "IDLE"

    start(mc, ml, targets, **NO_SLEEP)

    assert flow_status(mc, targets.flow_arn) == "ACTIVE"
    assert channel_state(ml, targets.channel_id) == "RUNNING"


def test_stop_brings_down_channel_then_flow(aws):
    targets = make_workflow()
    mc, ml = boto3.client("mediaconnect"), boto3.client("medialive")
    start(mc, ml, targets, **NO_SLEEP)

    stop(mc, ml, targets, **NO_SLEEP)

    assert channel_state(ml, targets.channel_id) == "IDLE"
    assert flow_status(mc, targets.flow_arn) == "STANDBY"


def test_start_and_stop_are_idempotent(aws):
    targets = make_workflow()
    mc, ml = boto3.client("mediaconnect"), boto3.client("medialive")

    stop(mc, ml, targets, **NO_SLEEP)
    start(mc, ml, targets, **NO_SLEEP)
    start(mc, ml, targets, **NO_SLEEP)
    stop(mc, ml, targets, **NO_SLEEP)
    stop(mc, ml, targets, **NO_SLEEP)

    assert flow_status(mc, targets.flow_arn) == "STANDBY"


def test_start_logs_each_step(aws):
    targets = make_workflow()
    messages = []

    start(boto3.client("mediaconnect"), boto3.client("medialive"), targets, log=messages.append, **NO_SLEEP)

    assert messages == ["flow ACTIVE", "channel RUNNING"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tools/tests/test_control.py -q`
Expected: FAIL (`No module named 'livectl.control'`).

- [ ] **Step 3: Implement**

`tools/livectl/control.py`:
```python
"""Start and stop the live workflow, waiting for each state change."""

from __future__ import annotations

import time
from typing import Callable

from livectl.targets import Targets


class WaitTimeout(RuntimeError):
    """Raised when a resource does not reach the expected state in time."""


def wait_for(
    read_state: Callable[[], str],
    target: str,
    *,
    timeout: float = 600,
    interval: float = 5,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    label: str = "resource",
) -> None:
    """Poll read_state until it returns target or the timeout expires."""
    deadline = clock() + timeout
    while True:
        state = read_state()
        if state == target:
            return
        if clock() >= deadline:
            raise WaitTimeout(f"{label} did not reach {target} within {timeout:.0f}s (last state: {state})")
        sleep(interval)


def flow_status(mediaconnect, flow_arn: str) -> str:
    return mediaconnect.describe_flow(FlowArn=flow_arn)["Flow"]["Status"]


def channel_state(medialive, channel_id: str) -> str:
    return medialive.describe_channel(ChannelId=channel_id)["State"]


def _noop(message: str) -> None:
    return None


def start(mediaconnect, medialive, targets: Targets, *, log: Callable[[str], None] = _noop, **wait_kwargs) -> None:
    """Start the flow, wait for ACTIVE, then start the channel and wait for RUNNING."""
    if flow_status(mediaconnect, targets.flow_arn) == "STANDBY":
        mediaconnect.start_flow(FlowArn=targets.flow_arn)
    wait_for(lambda: flow_status(mediaconnect, targets.flow_arn), "ACTIVE", label="flow", **wait_kwargs)
    log("flow ACTIVE")

    if channel_state(medialive, targets.channel_id) == "IDLE":
        medialive.start_channel(ChannelId=targets.channel_id)
    wait_for(lambda: channel_state(medialive, targets.channel_id), "RUNNING", label="channel", **wait_kwargs)
    log("channel RUNNING")


def stop(mediaconnect, medialive, targets: Targets, *, log: Callable[[str], None] = _noop, **wait_kwargs) -> None:
    """Stop the channel, wait for IDLE, then stop the flow and wait for STANDBY."""
    if channel_state(medialive, targets.channel_id) == "RUNNING":
        medialive.stop_channel(ChannelId=targets.channel_id)
    wait_for(lambda: channel_state(medialive, targets.channel_id), "IDLE", label="channel", **wait_kwargs)
    log("channel IDLE")

    if flow_status(mediaconnect, targets.flow_arn) == "ACTIVE":
        mediaconnect.stop_flow(FlowArn=targets.flow_arn)
    wait_for(lambda: flow_status(mediaconnect, targets.flow_arn), "STANDBY", label="flow", **wait_kwargs)
    log("flow STANDBY")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tools/tests/test_control.py -q`
Expected: `6 passed`. (If a moto state name differs from the one in the tests, use the value printed in the failure and note it in the commit message.)

- [ ] **Step 5: Commit**

```bash
git add tools/livectl/control.py tools/tests/test_control.py
git commit -m "feat: add livectl start and stop with state waits"
```

---

### Task 3: Status and leftover check

**Files:**
- Create: `tools/livectl/status.py`, `tools/livectl/clean.py`, `tools/tests/test_status.py`, `tools/tests/test_clean.py`

**Interfaces:**
- Consumes: `Targets`, `flow_status`, `channel_state`, `make_workflow`.
- Produces: `source_connected(cloudwatch, flow_arn, now=None) -> bool | None`, `get_status(mediaconnect, medialive, cloudwatch, targets) -> dict` with keys `flow`, `channel`, `source_connected`, `ingest`, `player`; `find_leftovers(mediaconnect, medialive, mediapackagev2, cloudfront, prefix) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_status.py`:
```python
from datetime import datetime, timezone

import boto3

from conftest import make_workflow
from livectl.status import get_status, source_connected
from livectl.targets import Targets


def test_status_reports_states_and_endpoints(aws):
    base = make_workflow()
    targets = Targets(
        flow_arn=base.flow_arn,
        channel_id=base.channel_id,
        player_url="https://example.cloudfront.net/",
        ingest_ip="203.0.113.20",
        ingest_port=5000,
    )

    status = get_status(boto3.client("mediaconnect"), boto3.client("medialive"), boto3.client("cloudwatch"), targets)

    assert status["flow"] == "STANDBY"
    assert status["channel"] == "IDLE"
    assert status["source_connected"] is None
    assert status["ingest"] == "srt://203.0.113.20:5000"
    assert status["player"] == "https://example.cloudfront.net/"


def test_source_connected_reads_the_latest_datapoint(aws):
    targets = make_workflow()
    cloudwatch = boto3.client("cloudwatch")
    now = datetime.now(timezone.utc)
    cloudwatch.put_metric_data(
        Namespace="AWS/MediaConnect",
        MetricData=[
            {
                "MetricName": "SourceConnected",
                "Dimensions": [{"Name": "FlowARN", "Value": targets.flow_arn}],
                "Timestamp": now,
                "Value": 1.0,
            }
        ],
    )

    assert source_connected(cloudwatch, targets.flow_arn, now=now) is True
```

`tools/tests/test_clean.py`:
```python
import boto3

from conftest import make_workflow
from livectl.clean import find_leftovers


def clients():
    return (
        boto3.client("mediaconnect"),
        boto3.client("medialive"),
        boto3.client("mediapackagev2"),
        boto3.client("cloudfront"),
    )


def test_a_clean_account_reports_nothing(aws):
    assert find_leftovers(*clients(), prefix="live-sports-aws") == []


def test_leftover_flow_and_channel_are_reported(aws):
    make_workflow("live-sports-aws-demo")

    leftovers = find_leftovers(*clients(), prefix="live-sports-aws")

    assert len(leftovers) == 2
    assert any("MediaConnect flow live-sports-aws-demo" in item for item in leftovers)
    assert any("MediaLive channel live-sports-aws-demo" in item for item in leftovers)


def test_resources_with_other_names_are_ignored(aws):
    make_workflow("someone-elses-stream")

    assert find_leftovers(*clients(), prefix="live-sports-aws") == []


def test_deleted_resources_disappear(aws):
    targets = make_workflow("live-sports-aws-demo")
    mediaconnect, medialive, *_ = clients()
    medialive.delete_channel(ChannelId=targets.channel_id)
    mediaconnect.delete_flow(FlowArn=targets.flow_arn)

    assert find_leftovers(*clients(), prefix="live-sports-aws") == []


def test_mediapackage_channel_groups_are_reported(aws):
    boto3.client("mediapackagev2").create_channel_group(ChannelGroupName="live-sports-aws-demo")

    leftovers = find_leftovers(*clients(), prefix="live-sports-aws")

    assert leftovers == ["MediaPackage channel group live-sports-aws-demo"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tools/tests/test_status.py tools/tests/test_clean.py -q`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement**

`tools/livectl/status.py`:
```python
"""Report the state of the live workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from livectl.control import channel_state, flow_status
from livectl.targets import Targets


def source_connected(cloudwatch, flow_arn: str, now: Optional[datetime] = None) -> Optional[bool]:
    """True/False from the latest SourceConnected datapoint, or None if there is no recent data."""
    end = now or datetime.now(timezone.utc)
    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/MediaConnect",
        MetricName="SourceConnected",
        Dimensions=[{"Name": "FlowARN", "Value": flow_arn}],
        StartTime=end - timedelta(minutes=5),
        EndTime=end + timedelta(minutes=1),
        Period=60,
        Statistics=["Maximum"],
    )
    points = sorted(response["Datapoints"], key=lambda point: point["Timestamp"])
    if not points:
        return None
    return points[-1]["Maximum"] >= 1


def get_status(mediaconnect, medialive, cloudwatch, targets: Targets) -> dict:
    ingest = f"srt://{targets.ingest_ip}:{targets.ingest_port}" if targets.ingest_ip else None
    return {
        "flow": flow_status(mediaconnect, targets.flow_arn),
        "channel": channel_state(medialive, targets.channel_id),
        "source_connected": source_connected(cloudwatch, targets.flow_arn),
        "ingest": ingest,
        "player": targets.player_url,
    }
```

`tools/livectl/clean.py`:
```python
"""Find billable resources that were left behind."""

from __future__ import annotations


def _pages(client, operation: str, **kwargs):
    yield from client.get_paginator(operation).paginate(**kwargs)


def find_leftovers(mediaconnect, medialive, mediapackagev2, cloudfront, prefix: str) -> list[str]:
    """Return one line per resource whose name (or comment) starts with prefix."""
    found: list[str] = []

    for page in _pages(mediaconnect, "list_flows"):
        for flow in page.get("Flows", []):
            if flow["Name"].startswith(prefix):
                found.append(f"MediaConnect flow {flow['Name']} ({flow['Status']})")

    for page in _pages(medialive, "list_channels"):
        for channel in page.get("Channels", []):
            if channel["Name"].startswith(prefix):
                found.append(f"MediaLive channel {channel['Name']} ({channel['State']})")

    for page in _pages(medialive, "list_inputs"):
        for item in page.get("Inputs", []):
            if item["Name"].startswith(prefix):
                found.append(f"MediaLive input {item['Name']}")

    for page in _pages(mediapackagev2, "list_channel_groups"):
        for group in page.get("Items", []):
            if group["ChannelGroupName"].startswith(prefix):
                found.append(f"MediaPackage channel group {group['ChannelGroupName']}")

    for page in _pages(cloudfront, "list_distributions"):
        for distribution in page.get("DistributionList", {}).get("Items", []):
            if distribution.get("Comment", "").startswith(prefix):
                found.append(f"CloudFront distribution {distribution['Id']}")

    return found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tools/tests/test_status.py tools/tests/test_clean.py -q`
Expected: `7 passed`. If moto rejects an `AWS/`-prefixed custom namespace in `put_metric_data`, or lacks a paginator or list call, replace only that test's setup with a stubbed client (a small class returning the same response shape) and note it in the commit message.

- [ ] **Step 5: Commit**

```bash
git add tools/livectl/status.py tools/livectl/clean.py tools/tests/test_status.py tools/tests/test_clean.py
git commit -m "feat: add livectl status and leftover check"
```

---

### Task 4: Command-line interface

**Files:**
- Create: `tools/livectl/cli.py`, `tools/tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `build_parser() -> argparse.ArgumentParser`, `main(argv=None, *, runner=run_command) -> int` (exit codes: 0 ok, 1 leftovers found by `check-clean`, 2 error).

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_cli.py`:
```python
import boto3

from conftest import make_workflow
from livectl.cli import main


def flags(targets):
    return ["--flow-arn", targets.flow_arn, "--channel-id", targets.channel_id]


def test_status_prints_states(aws, capsys):
    targets = make_workflow()

    code = main(["status", *flags(targets)])

    out = capsys.readouterr().out
    assert code == 0
    assert "flow:" in out and "STANDBY" in out
    assert "channel:" in out and "IDLE" in out


def test_start_then_stop_round_trip(aws, capsys):
    targets = make_workflow()

    assert main(["start", *flags(targets), "--interval", "0"]) == 0
    assert "channel RUNNING" in capsys.readouterr().out
    assert boto3.client("medialive").describe_channel(ChannelId=targets.channel_id)["State"] == "RUNNING"

    assert main(["stop", *flags(targets), "--interval", "0"]) == 0
    assert "flow STANDBY" in capsys.readouterr().out


def test_check_clean_fails_when_resources_exist_and_passes_when_gone(aws, capsys):
    targets = make_workflow("live-sports-aws-demo")

    assert main(["check-clean"]) == 1
    assert "MediaLive channel live-sports-aws-demo" in capsys.readouterr().out

    boto3.client("medialive").delete_channel(ChannelId=targets.channel_id)
    boto3.client("mediaconnect").delete_flow(FlowArn=targets.flow_arn)

    assert main(["check-clean"]) == 0
    assert "clean" in capsys.readouterr().out


def test_missing_targets_exit_with_code_2(aws, capsys):
    code = main(["status"], runner=lambda args: "{}")

    assert code == 2
    assert "could not determine" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/pytest tools/tests/test_cli.py -q`
Expected: FAIL (`No module named 'livectl.cli'`).

- [ ] **Step 3: Implement**

`tools/livectl/cli.py`:
```python
"""Command-line entry point for livectl."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

import boto3

from livectl.clean import find_leftovers
from livectl.control import WaitTimeout, start, stop
from livectl.status import get_status
from livectl.targets import Runner, TargetError, resolve_targets, run_command


def _region_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    return parent


def _target_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--tf-dir", default="envs/demo", help="Terraform environment to read outputs from")
    parent.add_argument("--flow-arn", help="MediaConnect flow ARN (overrides Terraform output)")
    parent.add_argument("--channel-id", help="MediaLive channel ID (overrides Terraform output)")
    return parent


def _wait_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument("--timeout", type=float, default=600, help="seconds to wait for each state change")
    parent.add_argument("--interval", type=float, default=5, help="seconds between polls")
    return parent


def build_parser() -> argparse.ArgumentParser:
    region, target, wait = _region_parent(), _target_parent(), _wait_parent()
    parser = argparse.ArgumentParser(prog="livectl", description="Operate the live SRT to HLS demo pipeline.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("start", parents=[region, target, wait], help="start the flow, then the channel")
    commands.add_parser("stop", parents=[region, target, wait], help="stop the channel, then the flow")
    commands.add_parser("status", parents=[region, target], help="show flow, channel and source state")
    clean = commands.add_parser("check-clean", parents=[region], help="fail if billable resources are left")
    clean.add_argument("--prefix", default="live-sports-aws", help="name prefix of the resources to look for")
    return parser


def _print_status(status: dict) -> None:
    connected = {True: "connected", False: "not connected", None: "unknown"}[status["source_connected"]]
    print(f"flow:      {status['flow']}")
    print(f"channel:   {status['channel']}")
    print(f"source:    {connected}")
    if status["ingest"]:
        print(f"ingest:    {status['ingest']}")
    if status["player"]:
        print(f"player:    {status['player']}")


def main(argv: Optional[Sequence[str]] = None, *, runner: Runner = run_command) -> int:
    args = build_parser().parse_args(argv)
    session = boto3.Session(region_name=args.region)

    try:
        if args.command == "check-clean":
            leftovers = find_leftovers(
                session.client("mediaconnect"),
                session.client("medialive"),
                session.client("mediapackagev2"),
                session.client("cloudfront"),
                args.prefix,
            )
            if leftovers:
                print("Billable resources still exist:")
                for line in leftovers:
                    print(f"  {line}")
                return 1
            print("clean: nothing left")
            return 0

        targets = resolve_targets(args.flow_arn, args.channel_id, args.tf_dir, runner)
        mediaconnect, medialive = session.client("mediaconnect"), session.client("medialive")

        if args.command == "status":
            _print_status(get_status(mediaconnect, medialive, session.client("cloudwatch"), targets))
        elif args.command == "start":
            start(mediaconnect, medialive, targets, log=print, timeout=args.timeout, interval=args.interval)
        elif args.command == "stop":
            stop(mediaconnect, medialive, targets, log=print, timeout=args.timeout, interval=args.interval)
        return 0
    except (TargetError, WaitTimeout) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the whole Python suite**

Run: `.venv/bin/pytest -q`
Expected: `22 passed` (5 + 6 + 7 + 4). Also run `.venv/bin/livectl --help` and confirm the four subcommands are listed.

- [ ] **Step 5: Commit**

```bash
git add tools/livectl/cli.py tools/tests/test_cli.py
git commit -m "feat: add livectl command-line interface"
```

---

### Task 5: FFmpeg source script with a working UTC clock

**Files:**
- Create: `source/send-srt.sh`

**Interfaces:**
- Consumes: `SRT_HOST`, `SRT_PASSPHRASE` (required), `SRT_PORT` (default 5000), `SRT_LATENCY_MS` (optional), `SIZE` (default 1920x1080), `BITRATE` (default 6M), `--frame FILE` (render one frame locally, no network).
- Produces: a running SRT stream, or a PNG for local verification.

- [ ] **Step 1: Write the script**

`source/send-srt.sh`:
```bash
#!/usr/bin/env bash
# Push a test stream with a burned-in UTC clock to an SRT listener.
#
# Required environment:
#   SRT_HOST        ingest IP or hostname of the MediaConnect flow (see: livectl status)
#   SRT_PASSPHRASE  SRT passphrase (from Secrets Manager; never put it on the command line)
# Optional environment:
#   SRT_PORT        listener port (default 5000)
#   SRT_LATENCY_MS  SRT receiver latency in milliseconds; FFmpeg expects microseconds, converted here
#   SIZE            video size (default 1920x1080)
#   BITRATE         video bitrate (default 6M)
# Local check without any network:
#   source/send-srt.sh --frame /tmp/clock.png

set -euo pipefail

SIZE="${SIZE:-1920x1080}"
BITRATE="${BITRATE:-6M}"
FONT="${FONT:-/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf}"

if [[ ! -f "$FONT" ]]; then
  echo "error: font not found: $FONT (install fonts-dejavu-core or set FONT)" >&2
  exit 1
fi

# %T is HH:MM:SS, so the only colon that needs escaping is the one after gmtime.
CLOCK="drawtext=fontfile=${FONT}:text='%{gmtime\:%T}':fontsize=96:fontcolor=white:box=1:boxcolor=black@0.6:x=40:y=40"
SOURCE="testsrc2=size=${SIZE}:rate=30"

if [[ "${1:-}" == "--frame" ]]; then
  ffmpeg -v warning -y -f lavfi -i "$SOURCE" -vf "$CLOCK" -frames:v 1 "${2:?usage: --frame FILE}"
  echo "wrote ${2} (UTC now: $(date -u +%T))"
  exit 0
fi

: "${SRT_HOST:?set SRT_HOST}"
: "${SRT_PASSPHRASE:?set SRT_PASSPHRASE}"
SRT_PORT="${SRT_PORT:-5000}"

QUERY="mode=caller&passphrase=${SRT_PASSPHRASE}&pbkeylen=32&pkt_size=1316"
if [[ -n "${SRT_LATENCY_MS:-}" ]]; then
  QUERY="${QUERY}&latency=$((SRT_LATENCY_MS * 1000))"
fi

exec ffmpeg -re \
  -f lavfi -i "$SOURCE" \
  -f lavfi -i "sine=frequency=1000" \
  -vf "$CLOCK" \
  -c:v libx264 -preset veryfast -tune zerolatency \
  -b:v "$BITRATE" -maxrate "$BITRATE" -bufsize 12M -g 60 -keyint_min 60 \
  -c:a aac \
  -f mpegts "srt://${SRT_HOST}:${SRT_PORT}?${QUERY}"
```

- [ ] **Step 2: Make it executable and verify the clock renders locally**

Run:
```bash
chmod +x source/send-srt.sh
source/send-srt.sh --frame /tmp/clock.png
```
Expected: it prints `wrote /tmp/clock.png (UTC now: HH:MM:SS)` and FFmpeg prints no warnings (in particular no `Stray %`). Open the PNG (or view it with an image viewer): the large clock in the top-left must match the printed UTC time to within a second.

- [ ] **Step 3: Verify the required-variable checks**

Run: `env -u SRT_HOST -u SRT_PASSPHRASE source/send-srt.sh; echo "exit $?"`
Expected: `set SRT_HOST` on stderr and a non-zero exit code.

- [ ] **Step 4: Commit**

```bash
git add source/send-srt.sh
git commit -m "feat: add FFmpeg SRT source script with UTC burn-in clock"
```

---

### Task 6: Persistent budget guardrail

**Why:** the budget lives in `envs/demo`, so `terraform destroy` deletes it and the demo has no cost alerts between sessions. Move it to `bootstrap/`, which is never destroyed.

**Files:**
- Modify: `bootstrap/main.tf`, `bootstrap/variables.tf`, `bootstrap/tests/bootstrap.tftest.hcl`, `envs/demo/main.tf`, `envs/demo/variables.tf`, `envs/demo/terraform.tfvars.example`, `envs/demo/terraform.tfvars` (local, ignored)

**Interfaces:**
- Consumes: `modules/guardrails` (`project`, `limit_usd`, `alert_email`); new bootstrap variables `alert_email` (string), `budget_limit_usd` (number, default 25).

- [ ] **Step 1: Confirm nothing from the demo is deployed**

Run: `cd envs/demo && terraform state list | head`
Expected: no output (the environment was destroyed, so removing the `guardrails` module from it leaves nothing orphaned). If anything is listed, run `terraform destroy` first.

- [ ] **Step 2: Write the failing test**

In `bootstrap/tests/bootstrap.tftest.hcl`, add a `variables` block at the top and a new run:

```hcl
variables {
  alert_email = "me@example.com"
}

run "budget_alerts_are_persistent" {
  command = plan

  assert {
    condition     = length(module.guardrails.budget_name) > 0
    error_message = "Bootstrap must create the monthly budget."
  }
}
```
Run: `cd bootstrap && terraform init -backend=false -input=false && terraform test`
Expected: the new run FAILS (no `guardrails` module in bootstrap yet).

- [ ] **Step 3: Implement**

Append to `bootstrap/variables.tf`:
```hcl
variable "alert_email" {
  description = "Email address that receives budget alerts."
  type        = string
}

variable "budget_limit_usd" {
  description = "Monthly budget limit in USD."
  type        = number
  default     = 25
}
```
Append to `bootstrap/main.tf`:
```hcl
module "guardrails" {
  source = "../modules/guardrails"

  project     = var.project
  limit_usd   = var.budget_limit_usd
  alert_email = var.alert_email
}
```
In `envs/demo/main.tf` delete the `module "guardrails" { ... }` block. In `envs/demo/variables.tf` delete the `alert_email` and `budget_limit_usd` variables. In `envs/demo/terraform.tfvars.example` and the local `envs/demo/terraform.tfvars` delete the `alert_email` line. Create `bootstrap/terraform.tfvars` (gitignored) with `alert_email = "<your email>"` and add `alert_email = "you@example.com"` to a new `bootstrap/terraform.tfvars.example`.

- [ ] **Step 4: Run tests, validate and apply the budget**

Run:
```bash
cd bootstrap && terraform fmt -check && terraform validate && terraform test
cd ../envs/demo && terraform init -backend-config=backend.hcl -input=false && terraform validate
cd ../../bootstrap && terraform plan -out=tfplan
```
Expected: tests pass; the bootstrap plan adds exactly 1 resource (`module.guardrails.aws_budgets_budget.monthly`) and changes nothing else. Read the plan, then `terraform apply tfplan && rm -f tfplan`.
Expected: `Apply complete! Resources: 1 added`. Afterwards `aws budgets describe-budgets --account-id $(aws sts get-caller-identity --query Account --output text) --query 'Budgets[].BudgetName' --output text` prints `live-sports-aws-monthly`.

- [ ] **Step 5: Commit**

```bash
git add bootstrap envs/demo .gitignore
git commit -m "feat: move budget guardrail to bootstrap so it survives destroy"
```

---

### Task 7: Cost estimate from real prices

**Files:**
- Create: `scripts/price_lookup.py`, `docs/cost-estimate.md`

**Interfaces:**
- Produces: `scripts/price_lookup.py SERVICE_CODE REGEX` printing `(usagetype, operation, description, USD)` for matching on-demand price dimensions in us-east-1.

- [ ] **Step 1: Write the lookup script**

`scripts/price_lookup.py`:
```python
#!/usr/bin/env python3
"""Print on-demand prices from the AWS Price List API for one service in us-east-1.

Usage: scripts/price_lookup.py SERVICE_CODE REGEX
Example: scripts/price_lookup.py AWSElementalMediaLive "Single Pipeline HD AVC"
"""

import json
import re
import subprocess
import sys


def get_products(service_code: str):
    token = None
    while True:
        command = [
            "aws", "pricing", "get-products", "--region", "us-east-1",
            "--service-code", service_code,
            "--filters", "Type=TERM_MATCH,Field=regionCode,Value=us-east-1",
            "--max-results", "100", "--output", "json",
        ]
        if token:
            command += ["--starting-token", token]
        page = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
        yield from page.get("PriceList", [])
        token = page.get("NextToken")
        if not token:
            return


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    service_code, pattern = sys.argv[1], re.compile(sys.argv[2], re.IGNORECASE)
    rows = set()
    for item in get_products(service_code):
        product = json.loads(item) if isinstance(item, str) else item
        attributes = product["product"]["attributes"]
        for term in product["terms"].get("OnDemand", {}).values():
            for dimension in term["priceDimensions"].values():
                text = f"{dimension['description']} {attributes.get('usagetype', '')}"
                if pattern.search(text):
                    rows.add((attributes.get("usagetype"), attributes.get("operation"),
                              dimension["description"][:120], dimension["pricePerUnit"].get("USD")))
    for row in sorted(rows, key=lambda r: (str(r[0]), str(r[2]))):
        print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```
Run: `chmod +x scripts/price_lookup.py && scripts/price_lookup.py AWSElementalMediaLive "Single Pipeline HD AVC" | head -5`
Expected: tuples such as `('EML1-USE1-IN-AVC-HD-L10', 'CHANNEL_INPUT', '$0.1404/hour for Single Pipeline HD AVC inputs at <10mbps ...', '0.1404000000')`.

- [ ] **Step 2: Fetch the prices for every billed component**

Run each and keep the output:
```bash
scripts/price_lookup.py AWSElementalMediaLive "Single Pipeline (HD|SD) AVC (inputs|outputs)"
scripts/price_lookup.py AWSElementalMediaLive "Standard (HD|SD) AVC (inputs|outputs)"
scripts/price_lookup.py AWSElementalMediaConnect "flow|hour|data"
scripts/price_lookup.py AWSElementalMediaPackage "ingest|egress|origin|GB"
scripts/price_lookup.py AmazonCloudFront "Data Transfer Out|HTTPS"
```
Expected: non-empty lists for MediaLive and MediaConnect. If a service returns nothing for a pattern, run it with a broader pattern (for example `"."`) and read the descriptions to pick the right line items. If MediaPackage v2 is not under `AWSElementalMediaPackage`, run `aws pricing describe-services --region us-east-1 --query 'Services[?contains(ServiceCode, \`MediaPackage\`)].ServiceCode' --output text` to find its service code.

- [ ] **Step 3: Write `docs/cost-estimate.md`**

Structure (fill every number from the Step 2 output, and state the date the prices were fetched):

```markdown
# Cost estimate

Prices fetched from the AWS Price List API for us-east-1 on <date>. These are estimates: they exclude
taxes, free-tier credits and any data-transfer detail not listed. Nothing here is a quote.

## What runs while the demo is live
| Component | Billing unit | Unit price (USD) | Quantity per hour | Cost per hour |
|---|---|---|---|---|
| MediaConnect flow | hour | ... | 1 | ... |
| MediaConnect data out (to MediaLive) | GB | ... | source bitrate x 3600 / 8 / 1000 | ... |
| MediaLive input (single pipeline, HD AVC, <10 Mbps) | hour | ... | 1 | ... |
| MediaLive output: 1080p | hour | ... | 1 | ... |
| MediaLive output: 720p | hour | ... | 1 | ... |
| MediaLive output: 480p | hour | ... | 1 | ... |
| MediaPackage v2 ingest / egress | GB | ... | ... | ... |
| CloudFront data out and requests (one viewer) | GB / 10k requests | ... | ... | ... |
| **Total per hour** | | | | **...** |

## Session costs
| Session | Cost |
|---|---|
| 15-minute demo | total per hour x 0.25 |
| 1-hour demo | total per hour |
| Standard (two pipelines) channel class | state the MediaLive lines that double, from the Standard price list |

## Always-on costs (when nothing is running)
State each (Secrets Manager secrets, S3 state bucket, budget) with its price line or "no charge" only where the price list shows it.

## Guardrails
The $25 monthly budget (alerts at 50% and 80% actual, 100% forecast) lives in `bootstrap/` and survives `terraform destroy`; alerts lag by hours, so `livectl stop`, `terraform destroy` and `livectl check-clean` are the real controls.
```
Expected: every "..." replaced by a number or a computed expression with its source line; no cell left blank.

- [ ] **Step 4: Commit**

```bash
git add scripts/price_lookup.py docs/cost-estimate.md
git commit -m "docs: add cost estimate from AWS Price List API"
```

---

### Task 8: README and spec clean-up

**Files:**
- Create: `README.md`
- Modify: `docs/superpowers/specs/2026-09-18-live-srt-pipeline-design.md`

- [ ] **Step 1: Write `README.md`**

Sections, in this order (keep each short, with real commands and real numbers from the earlier tasks):
1. **Title and one-paragraph pitch**: a live SRT contribution to ABR HLS distribution on AWS, built with Terraform, operated with a Python CLI.
2. **Architecture**: a diagram (fenced ASCII or Mermaid) of `FFmpeg (SRT) -> MediaConnect -> MediaLive (1080p/720p/480p) -> MediaPackage v2 -> CloudFront -> hls.js player`, with one line per stage saying what it does. State that MediaLive segments and MediaPackage is the origin.
3. **What it demonstrates**: modules, remote state with native locking, mocked-provider tests, CDN authorization, thumbnails, budget guardrails, Python automation, provider-gap handling (`awscc` for MediaConnect and MediaPackage v2).
4. **Prerequisites**: Terraform >= 1.11, AWS CLI v2 with credentials, FFmpeg with libsrt and DejaVu font, Python >= 3.10 with `virtualenv`.
5. **Quick start**: bootstrap, backend and tfvars files, `terraform apply`, `livectl start`, `SRT_HOST=... SRT_PASSPHRASE=... source/send-srt.sh`, open the player URL, `livectl stop`, `terraform destroy`, `livectl check-clean`. Show how to read the passphrase from Secrets Manager without echoing it.
6. **Repository layout** (the actual tree).
7. **Testing**: `terraform test` per module and `pytest`, both offline.
8. **Costs**: link to `docs/cost-estimate.md` and quote its per-hour and 15-minute figures.
9. **Design notes and lessons learned**: the provider gaps found, that empty `hls_basic_put_settings {}` is dropped, that MediaConnect rejects `algorithm` with `srt-password`, that the burn-in clock needs `%{gmtime\:%T}`, and that the latency overlay is approximate.
10. **Known limitations**: single pipeline only in the demo, spike-permissive MediaLive role (`Resource = "*"`), TS/HLS only, approximate latency, public IP whitelist must be updated if it changes.

Expected: the README's commands are copy-pasteable and match the files in the repo.

- [ ] **Step 2: Bring the spec in line with what was built**

In the spec: update the architecture diagram's MediaPackage line to "MediaPackage v2 (HLS ingest, CDN authorization)"; change the repository layout so tests live under each module (`<module>/tests/`) and `tools/` shows `livectl/` and `tests/`; replace the "Python tooling" list with the four commands and the flag overrides; mark verification items 4-7 RESOLVED with one line each (item 4: `use_lockfile` works on Terraform 1.16.3 as installed; item 5: see `docs/cost-estimate.md`; item 6: state what the Standard price list shows, from Task 7; item 7: `moto` 5.x supports MediaLive, MediaConnect and MediaPackage v2 create/start/stop/describe/list).

- [ ] **Step 3: Final checks and commit**

Run:
```bash
terraform fmt -check -recursive .
for m in bootstrap modules/*; do (cd $m && terraform test -no-color | grep -E '^(Success|Failure)'); done
.venv/bin/pytest -q
git status --short
```
Expected: `fmt` clean, every module `Success!`, Python `22 passed`, and only intended files listed.

```bash
git add README.md docs
git commit -m "docs: add README and bring the spec in line with the build"
```

---

## Self-Review

**Spec coverage:** the four `livectl` commands with Terraform-output default and flag override (Tasks 1-4); FFmpeg source with a working UTC burn-in and optional latency in the right units (Task 5); cost awareness with real prices and a budget that survives destroy (Tasks 6-7); README and spec clean-up including the remaining verification items 4-7 (Task 8). Everything else in the spec was delivered in Plans 1-3.

**Placeholder scan:** none in code or commands. Task 7 Step 3 and Task 8 Step 1 are documents whose numbers and prose come from earlier tasks' output; each names its source and states the rule "no cell left blank".

**Type consistency:** `Targets` fields, `resolve_targets`, `start`/`stop`/`wait_for` keyword arguments (`timeout`, `interval`, `sleep`, `clock`, `label`), `get_status` keys and `find_leftovers` signature are used identically in modules, tests and the CLI.

**Known risks:** (1) moto's fakes may differ from real AWS in state names or paginator support; Task 3 has an explicit stub fallback, and the live commands were already proven with the AWS CLI in Plans 2-3. (2) The `SourceConnected` metric dimension is `FlowARN` (proven in Plan 1). (3) `livectl check-clean` prefix matching only sees resources named `live-sports-aws*`. (4) The MediaPackage v2 service code for pricing may differ; Task 7 Step 2 says how to find it.
