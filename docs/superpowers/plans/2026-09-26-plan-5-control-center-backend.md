# Control Center Backend Implementation Plan (Plan 5 of 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the console a backend that knows the state of every resource in the chain: seven nodes, a truthful verdict, an hourly rate built from what is actually running, and one set of action rules shared by the page and the server. The old page keeps working until Plan 6 replaces it.

**Architecture:** New modules under `tools/livectl/`: `cache.py` (TTL cache with an injected clock), `metrics.py` (one `GetMetricData` call for every metric), `manifest.py` (fetch the HLS playlist through CloudFront and watch the media sequence), `pipeline.py` (pure node builders plus a `Pipeline` that reads AWS through the cache), `verdict.py` (verdict and hourly rate, no I/O) and `actions.py` (which actions are allowed and why not). `server.py` gains `GET /api/pipeline` and the renamed POST actions next to the old ones. Terraform gains the outputs the probes need.

**Tech Stack:** Python >= 3.10, boto3, pytest, moto; Terraform >= 1.11 with mocked providers.

**Spec:** `docs/superpowers/specs/2026-09-26-control-center-design.md`

**Branch:** `feat/control-center`

## Global Constraints

- boto3 is the only runtime dependency. `urllib.request` for the manifest fetch.
- Clients, clocks, fetchers and runners are parameters. Nothing in logic constructs a boto3 client.
- `from __future__ import annotations`, a module docstring on every file, frozen dataclasses for values.
- Every new Terraform variable and output has a `description`. Run `just fmt`.
- moto lacks `medialive.list_alerts` and `mediapackagev2.get_origin_endpoint`. Probe tests use `tools/tests/stubs.py`; route tests keep using moto.
- Every task ends with `just test` green and a commit (`feat:` / `test:` / `docs:`, lower case after the prefix).
- Nothing in this plan touches a real AWS account.

## File Structure

```
modules/encode/outputs.tf                     (modify: input_id)
modules/encode/tests/encode.tftest.hcl        (modify)
modules/package/outputs.tf                    (modify: channel group, channel, endpoint names)
modules/package/tests/package.tftest.hcl      (modify)
envs/demo/outputs.tf                          (modify: six outputs)
tools/livectl/targets.py                      (modify: more fields, NotDeployed)
tools/livectl/cache.py                        (create)
tools/livectl/metrics.py                      (create)
tools/livectl/manifest.py                     (create)
tools/livectl/pipeline.py                     (create)
tools/livectl/verdict.py                      (create)
tools/livectl/actions.py                      (create)
tools/livectl/server.py                       (modify)
tools/livectl/cli.py                          (modify: region)
tools/tests/stubs.py                          (create)
tools/tests/test_cache.py, test_metrics.py, test_manifest.py,
tools/tests/test_pipeline.py, test_verdict.py, test_actions.py   (create)
tools/tests/test_targets.py, test_server.py   (modify)
```

---

### Task 1: Terraform outputs for every probe

**Files:**
- Modify: `modules/encode/outputs.tf`, `modules/package/outputs.tf`, `envs/demo/outputs.tf`, `modules/package/tests/package.tftest.hcl`

**Interfaces:**
- Produces (envs/demo outputs): `medialive_input_id`, `medialive_channel_arn`, `mediapackage_channel_group`, `mediapackage_channel`, `mediapackage_endpoint`, `distribution_id`, `cdn_manifest_url`.

- [ ] **Step 1: Write the failing Terraform test**

Append to `modules/package/tests/package.tftest.hcl`:
```hcl
run "exposes_the_names_the_console_reads" {
  command = plan

  assert {
    condition     = output.channel_group_name == "live-demo" && output.channel_name == "live-demo"
    error_message = "Channel group and channel are both named after var.name."
  }

  assert {
    condition     = output.origin_endpoint_name == "live-demo-hls"
    error_message = "The HLS endpoint is named <name>-hls."
  }
}
```
Run: `terraform -chdir=modules/package test`
Expected: FAIL, `output.channel_group_name` is not declared.

- [ ] **Step 2: Add the module outputs**

Append to `modules/package/outputs.tf`:
```hcl
output "channel_group_name" {
  description = "Name of the MediaPackage v2 channel group."
  value       = awscc_mediapackagev2_channel_group.this.channel_group_name
}

output "channel_name" {
  description = "Name of the MediaPackage v2 channel."
  value       = awscc_mediapackagev2_channel.this.channel_name
}

output "origin_endpoint_name" {
  description = "Name of the HLS origin endpoint."
  value       = awscc_mediapackagev2_origin_endpoint.hls.origin_endpoint_name
}
```
If the plan-time assertion sees unknown values under the mock, change the three `value`s to the literal expressions the resources use (`var.name`, `var.name`, `"${var.name}-hls"`), which are known at plan time.

Append to `modules/encode/outputs.tf`:
```hcl
output "input_id" {
  description = "ID of the MediaLive input attached to the channel."
  value       = aws_medialive_input.this.id
}
```

Append to `envs/demo/outputs.tf`:
```hcl
output "medialive_input_id" {
  description = "ID of the MediaLive input."
  value       = module.encode.input_id
}

output "medialive_channel_arn" {
  description = "ARN of the MediaLive channel."
  value       = module.encode.channel_arn
}

output "mediapackage_channel_group" {
  description = "Name of the MediaPackage v2 channel group."
  value       = module.package.channel_group_name
}

output "mediapackage_channel" {
  description = "Name of the MediaPackage v2 channel."
  value       = module.package.channel_name
}

output "mediapackage_endpoint" {
  description = "Name of the MediaPackage v2 HLS origin endpoint."
  value       = module.package.origin_endpoint_name
}

output "distribution_id" {
  description = "ID of the CloudFront distribution."
  value       = module.delivery.distribution_id
}

output "cdn_manifest_url" {
  description = "HLS master manifest URL through CloudFront, as viewers fetch it."
  value       = "https://${module.delivery.distribution_domain_name}${module.package.hls_manifest_path}"
}
```

- [ ] **Step 3: Verify**

`envs/demo` has no test file, so `validate` is its check. It needs an initialised directory: if
`envs/demo/.terraform` already exists (it does wherever `just init` ran), use it as is; otherwise initialise without
the backend so the real remote state is never touched.
```bash
just fmt
terraform -chdir=modules/package test
terraform -chdir=modules/encode test
[ -d envs/demo/.terraform ] || terraform -chdir=envs/demo init -backend=false -input=false >/dev/null
terraform -chdir=envs/demo validate
```
Expected: both tests PASS and `Success! The configuration is valid.`

- [ ] **Step 4: Commit**

```bash
git add modules/encode/outputs.tf modules/package envs/demo/outputs.tf
git commit -m "feat: expose the resource ids the console probes"
```

---

### Task 2: Targets carry every id, and "not deployed" is its own error

**Files:**
- Modify: `tools/livectl/targets.py`, `tools/tests/test_targets.py`

**Interfaces:**
- Produces: `NotDeployed(TargetError)`; `Targets` gains optional `input_id`, `channel_arn`, `channel_group`, `mediapackage_channel`, `mediapackage_endpoint`, `distribution_id`, `manifest_url`, `passphrase_secret_arn`, `events_log_group` (all `Optional[str]`, default `None`).

- [ ] **Step 1: Write the failing tests**

Append to `tools/tests/test_targets.py`:
```python
import json

import pytest

from livectl.targets import NotDeployed, TargetError, resolve_targets

FULL_OUTPUTS = {
    "flow_arn": {"value": "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:demo"},
    "medialive_channel_id": {"value": "7387208"},
    "medialive_channel_arn": {"value": "arn:aws:medialive:us-east-1:123456789012:channel:7387208"},
    "medialive_input_id": {"value": "4412345"},
    "mediapackage_channel_group": {"value": "live-sports-aws-demo"},
    "mediapackage_channel": {"value": "live-sports-aws-demo"},
    "mediapackage_endpoint": {"value": "live-sports-aws-demo-hls"},
    "distribution_id": {"value": "E2EXAMPLE"},
    "cdn_manifest_url": {"value": "https://d1.cloudfront.net/out/v1/x/index.m3u8"},
    "passphrase_secret_arn": {"value": "arn:aws:secretsmanager:us-east-1:123456789012:secret:srt-AbC"},
}


def test_every_probe_id_is_read_from_the_outputs():
    targets = resolve_targets(None, None, "envs/demo", lambda args: json.dumps(FULL_OUTPUTS))

    assert targets.input_id == "4412345"
    assert targets.channel_arn.endswith(":channel:7387208")
    assert (targets.channel_group, targets.mediapackage_channel, targets.mediapackage_endpoint) == (
        "live-sports-aws-demo", "live-sports-aws-demo", "live-sports-aws-demo-hls")
    assert targets.distribution_id == "E2EXAMPLE"
    assert targets.manifest_url.endswith("index.m3u8")
    assert targets.passphrase_secret_arn.endswith("srt-AbC")


def test_older_state_without_the_new_outputs_still_resolves():
    old = {k: FULL_OUTPUTS[k] for k in ("flow_arn", "medialive_channel_id")}

    targets = resolve_targets(None, None, "envs/demo", lambda args: json.dumps(old))

    assert targets.input_id is None and targets.distribution_id is None


def test_an_empty_stack_is_reported_as_not_deployed():
    with pytest.raises(NotDeployed):
        resolve_targets(None, None, "envs/demo", lambda args: "{}")


def test_not_deployed_is_still_a_target_error_for_the_cli():
    assert issubclass(NotDeployed, TargetError)
```
Run: `.venv/bin/pytest -q tools/tests/test_targets.py`
Expected: FAIL, `ImportError: cannot import name 'NotDeployed'`.

- [ ] **Step 2: Implement**

In `tools/livectl/targets.py`, add after `TargetError`:
```python
class NotDeployed(TargetError):
    """Raised when Terraform has no outputs for the environment: nothing is deployed."""
```
Replace `Targets` with:
```python
@dataclass(frozen=True)
class Targets:
    flow_arn: str
    channel_id: str
    player_url: Optional[str] = None
    ingest_ip: Optional[str] = None
    ingest_port: Optional[int] = None
    input_id: Optional[str] = None
    channel_arn: Optional[str] = None
    channel_group: Optional[str] = None
    mediapackage_channel: Optional[str] = None
    mediapackage_endpoint: Optional[str] = None
    distribution_id: Optional[str] = None
    manifest_url: Optional[str] = None
    passphrase_secret_arn: Optional[str] = None
    events_log_group: Optional[str] = None
```
In `resolve_targets`, raise `NotDeployed` instead of `TargetError` for the "could not determine" case, and build the result with the new fields:
```python
    if missing:
        raise NotDeployed(
            "could not determine " + " and ".join(missing)
            + "; pass --flow-arn/--channel-id or run against a deployed environment"
        )

    def text(name: str) -> Optional[str]:
        value = outputs.get(name)
        return None if value is None else str(value)

    return Targets(
        flow_arn=flow,
        channel_id=str(channel),
        player_url=outputs.get("player_url"),
        ingest_ip=outputs.get("ingest_ip"),
        ingest_port=outputs.get("ingest_port"),
        input_id=text("medialive_input_id"),
        channel_arn=text("medialive_channel_arn"),
        channel_group=text("mediapackage_channel_group"),
        mediapackage_channel=text("mediapackage_channel"),
        mediapackage_endpoint=text("mediapackage_endpoint"),
        distribution_id=text("distribution_id"),
        manifest_url=text("cdn_manifest_url"),
        passphrase_secret_arn=text("passphrase_secret_arn"),
        events_log_group=text("events_log_group"),
    )
```
The CLI message stays as it is: the CLI prints it, the console will not (Task 8).

- [ ] **Step 3: Verify**

Run: `.venv/bin/pytest -q tools`
Expected: all PASS (existing tests catch `TargetError`, which `NotDeployed` still is).

- [ ] **Step 4: Commit**

```bash
git add tools/livectl/targets.py tools/tests/test_targets.py
git commit -m "feat: read every probe id from terraform and name the not-deployed case"
```

---

### Task 3: TTL cache

**Files:**
- Create: `tools/livectl/cache.py`, `tools/tests/test_cache.py`

**Interfaces:**
- Produces: `TtlCache(clock=time.monotonic)` with `get(key, ttl, load)`, `age(key) -> Optional[float]`, `clear()`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_cache.py`:
```python
import pytest

from livectl.cache import TtlCache


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def counter():
    def load():
        load.calls += 1
        return load.calls

    load.calls = 0
    return load


def test_a_fresh_value_is_served_without_loading_again():
    clock, load = FakeClock(), counter()
    cache = TtlCache(clock)

    assert cache.get("k", 5, load) == 1
    clock.now += 4.9
    assert cache.get("k", 5, load) == 1
    assert load.calls == 1


def test_an_expired_value_is_loaded_again():
    clock, load = FakeClock(), counter()
    cache = TtlCache(clock)
    cache.get("k", 5, load)

    clock.now += 5
    assert cache.get("k", 5, load) == 2


def test_a_failed_load_is_not_cached():
    cache = TtlCache(FakeClock())

    def boom():
        raise RuntimeError("throttled")

    with pytest.raises(RuntimeError):
        cache.get("k", 60, boom)
    assert cache.get("k", 60, lambda: "ok") == "ok"


def test_age_reports_how_old_the_value_is():
    clock = FakeClock()
    cache = TtlCache(clock)
    assert cache.age("k") is None

    cache.get("k", 60, lambda: 1)
    clock.now += 42
    assert cache.age("k") == 42


def test_clear_forgets_everything():
    clock, load = FakeClock(), counter()
    cache = TtlCache(clock)
    cache.get("k", 60, load)

    cache.clear()

    assert cache.get("k", 60, load) == 2
```
Run: `.venv/bin/pytest -q tools/tests/test_cache.py`
Expected: FAIL, `ModuleNotFoundError: No module named 'livectl.cache'`.

- [ ] **Step 2: Implement**

`tools/livectl/cache.py`:
```python
"""Remember slow or billed lookups for a short time, so a page that polls every two seconds stays cheap.

The browser asks for the whole pipeline every two seconds. AWS state changes on the order of seconds and
CloudWatch metrics once a minute, so each source is cached for its own interval instead.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable, Optional


class TtlCache:
    """A dict of values that expire. A load that raises is not stored, so the next call tries again."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[Hashable, tuple[float, Any]] = {}

    def get(self, key: Hashable, ttl: float, load: Callable[[], Any]) -> Any:
        now = self._clock()
        with self._lock:
            hit = self._items.get(key)
        if hit is not None and now - hit[0] < ttl:
            return hit[1]
        # Loading outside the lock: two threads may both load once, which is cheaper than serialising AWS calls.
        value = load()
        with self._lock:
            self._items[key] = (now, value)
        return value

    def age(self, key: Hashable) -> Optional[float]:
        with self._lock:
            hit = self._items.get(key)
        return None if hit is None else self._clock() - hit[0]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
```

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools/tests/test_cache.py` → PASS.
```bash
git add tools/livectl/cache.py tools/tests/test_cache.py
git commit -m "feat: add a ttl cache for pipeline lookups"
```

---

### Task 4: Stub clients for APIs moto does not implement

**Files:**
- Create: `tools/tests/stubs.py`

**Interfaces:**
- Produces: `StubClient(**responses)` (each keyword is an operation: a value, an exception instance, or a callable taking the request kwargs); `metric_response(values: dict[str, float | None]) -> dict`; `stub_aws(**state) -> dict[str, StubClient]` with keys `mediaconnect`, `medialive`, `mediapackagev2`, `cloudfront`, `cloudwatch`.

- [ ] **Step 1: Write the stubs**

`tools/tests/stubs.py`:
```python
"""Small stand-ins for AWS clients.

moto has no MediaLive alerts and no MediaPackage v2 origin endpoints, and the console's scenario fixtures need
exact states (a running channel with no source, a failing call) that are awkward to reach through moto. A stub
answers each operation from a dict and records every call.
"""

from __future__ import annotations

from typing import Any, Optional

FLOW_ARN = "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:live-sports-aws-demo"
CHANNEL_ID = "7387208"


class StubClient:
    def __init__(self, **responses: Any) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name: str):
        if name.startswith("_") or name not in self._responses:
            raise AttributeError(f"stub has no operation {name!r}")

        def operation(**kwargs):
            self.calls.append((name, kwargs))
            answer = self._responses[name]
            if isinstance(answer, Exception):
                raise answer
            return answer(**kwargs) if callable(answer) else answer

        return operation


def metric_response(values: dict[str, Optional[float]]) -> dict:
    """A GetMetricData response with one newest-first value per query id (None means no datapoints)."""
    return {
        "MetricDataResults": [
            {"Id": key, "Values": [] if value is None else [value], "Timestamps": [], "StatusCode": "Complete"}
            for key, value in values.items()
        ]
    }


def stub_aws(
    *,
    flow: str = "STANDBY",
    channel: str = "IDLE",
    input_state: str = "ATTACHED",
    alerts: tuple = (),
    metrics: Optional[dict] = None,
    distribution: str = "Deployed",
    fail: tuple = (),
) -> dict[str, StubClient]:
    """Clients describing one state of the whole chain. `fail` names operations that raise."""
    error = RuntimeError("AccessDeniedException: stubbed failure")

    def maybe(name: str, answer: Any) -> Any:
        return error if name in fail else answer

    return {
        "mediaconnect": StubClient(
            describe_flow=maybe("describe_flow", {"Flow": {
                "FlowArn": FLOW_ARN, "Name": "live-sports-aws-demo", "Status": flow,
                "Source": {"IngestIp": "203.0.113.20", "IngestPort": 5000, "Transport": {"Protocol": "srt-listener"}},
            }}),
        ),
        "medialive": StubClient(
            describe_input=maybe("describe_input", {"Id": "4412345", "State": input_state, "Type": "MEDIACONNECT"}),
            describe_channel=maybe("describe_channel", {
                "Id": CHANNEL_ID, "Arn": f"arn:aws:medialive:us-east-1:123456789012:channel:{CHANNEL_ID}",
                "State": channel, "PipelinesRunningCount": 1 if channel == "RUNNING" else 0,
            }),
            list_alerts=maybe("list_alerts", {"Alerts": [
                {"AlertType": kind, "Message": message, "State": "SET"} for kind, message in alerts
            ]}),
        ),
        "mediapackagev2": StubClient(
            get_channel=maybe("get_channel", {"Arn": "arn:aws:mediapackagev2:::channel", "ChannelName": "live-sports-aws-demo"}),
            get_origin_endpoint=maybe("get_origin_endpoint", {"OriginEndpointName": "live-sports-aws-demo-hls"}),
        ),
        "cloudfront": StubClient(
            get_distribution=maybe("get_distribution", {"Distribution": {
                "Id": "E2EXAMPLE", "Status": distribution, "DomainName": "d1.cloudfront.net",
                "DistributionConfig": {"Enabled": True},
            }}),
        ),
        "cloudwatch": StubClient(get_metric_data=maybe("get_metric_data", metric_response(metrics or {}))),
    }
```

- [ ] **Step 2: Commit**

```bash
git add tools/tests/stubs.py
git commit -m "test: add stub aws clients for probes moto cannot serve"
```

---

### Task 5: Metrics in one call

**Files:**
- Create: `tools/livectl/metrics.py`, `tools/tests/test_metrics.py`

**Interfaces:**
- Produces: `MetricSpec` (frozen: `id, namespace, name, dimensions: tuple[tuple[str, str], ...], stat`); `metric_specs(targets) -> list[MetricSpec]`; `fetch_metrics(cloudwatch, targets, now: datetime) -> dict[str, Optional[float]]`.
- Query ids used by later tasks: `src_connected`, `src_bitrate`, `src_rtt`, `src_not_recovered`, `src_cc_errors`, `ml_alerts`, `ml_fps`, `ml_input_loss`, `mp_ingress_bytes`, `mp_egress_5xx`, `cf_requests`, `cf_5xx_rate`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_metrics.py`:
```python
from datetime import datetime, timezone

from livectl.metrics import fetch_metrics, metric_specs
from livectl.targets import Targets
from stubs import CHANNEL_ID, FLOW_ARN, StubClient, metric_response

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
MINIMAL = Targets(flow_arn=FLOW_ARN, channel_id=CHANNEL_ID)
FULL = Targets(flow_arn=FLOW_ARN, channel_id=CHANNEL_ID, channel_group="g", mediapackage_channel="c",
               distribution_id="E2EXAMPLE")


def dims(spec):
    return dict(spec.dimensions)


def test_source_metrics_are_keyed_by_the_flow_arn():
    by_id = {s.id: s for s in metric_specs(MINIMAL)}

    assert by_id["src_connected"].name == "SourceConnected"
    assert by_id["src_connected"].namespace == "AWS/MediaConnect"
    assert dims(by_id["src_connected"]) == {"FlowARN": FLOW_ARN}


def test_channel_metrics_use_channel_id_and_pipeline_zero():
    spec = {s.id: s for s in metric_specs(MINIMAL)}["ml_alerts"]

    assert (spec.namespace, spec.name) == ("AWS/MediaLive", "ActiveAlerts")
    assert dims(spec) == {"ChannelId": CHANNEL_ID, "Pipeline": "0"}


def test_package_and_cdn_metrics_need_their_ids():
    assert not any(s.id.startswith(("mp_", "cf_")) for s in metric_specs(MINIMAL))

    by_id = {s.id: s for s in metric_specs(FULL)}
    assert dims(by_id["mp_ingress_bytes"]) == {"ChannelGroup": "g", "Channel": "c"}
    assert dims(by_id["mp_egress_5xx"])["StatusCode"] == "5xx"
    assert dims(by_id["cf_requests"]) == {"DistributionId": "E2EXAMPLE", "Region": "Global"}


def test_one_call_returns_the_newest_value_per_metric():
    cloudwatch = StubClient(get_metric_data=metric_response({"src_connected": 1.0, "ml_alerts": None}))

    values = fetch_metrics(cloudwatch, MINIMAL, NOW)

    assert len(cloudwatch.calls) == 1
    request = cloudwatch.calls[0][1]
    assert request["ScanBy"] == "TimestampDescending"
    assert request["EndTime"] == NOW
    assert values["src_connected"] == 1.0
    assert values["ml_alerts"] is None
    assert values["src_bitrate"] is None, "a metric missing from the response reads as no data"
```
Run: `.venv/bin/pytest -q tools/tests/test_metrics.py` → FAIL (no module).

- [ ] **Step 2: Implement**

`tools/livectl/metrics.py`:
```python
"""Read every CloudWatch metric the console shows in a single GetMetricData call.

Metric names and dimensions were checked against the AWS documentation on 2026-09-26, except the CloudFront
dimension set (`DistributionId`, `Region=Global`), which is on the live checklist in the README.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from livectl.targets import Targets

PERIOD_SECONDS = 60
LOOKBACK = timedelta(minutes=5)


@dataclass(frozen=True)
class MetricSpec:
    id: str
    namespace: str
    name: str
    dimensions: tuple[tuple[str, str], ...]
    stat: str


def metric_specs(targets: Targets) -> list[MetricSpec]:
    flow = (("FlowARN", targets.flow_arn),)
    channel = (("ChannelId", targets.channel_id), ("Pipeline", "0"))
    specs = [
        MetricSpec("src_connected", "AWS/MediaConnect", "SourceConnected", flow, "Maximum"),
        MetricSpec("src_bitrate", "AWS/MediaConnect", "SourceBitRate", flow, "Average"),
        MetricSpec("src_rtt", "AWS/MediaConnect", "SourceRoundTripTime", flow, "Average"),
        MetricSpec("src_not_recovered", "AWS/MediaConnect", "SourceNotRecoveredPackets", flow, "Sum"),
        MetricSpec("src_cc_errors", "AWS/MediaConnect", "SourceContinuityCounter", flow, "Sum"),
        MetricSpec("ml_alerts", "AWS/MediaLive", "ActiveAlerts", channel, "Maximum"),
        MetricSpec("ml_fps", "AWS/MediaLive", "InputVideoFrameRate", channel, "Maximum"),
        MetricSpec("ml_input_loss", "AWS/MediaLive", "InputLossSeconds", channel, "Sum"),
    ]
    if targets.channel_group and targets.mediapackage_channel:
        package = (("ChannelGroup", targets.channel_group), ("Channel", targets.mediapackage_channel))
        specs += [
            MetricSpec("mp_ingress_bytes", "AWS/MediaPackage", "IngressBytes", package, "Sum"),
            MetricSpec("mp_egress_5xx", "AWS/MediaPackage", "EgressRequestCount", package + (("StatusCode", "5xx"),), "Sum"),
        ]
    if targets.distribution_id:
        cdn = (("DistributionId", targets.distribution_id), ("Region", "Global"))
        specs += [
            MetricSpec("cf_requests", "AWS/CloudFront", "Requests", cdn, "Sum"),
            MetricSpec("cf_5xx_rate", "AWS/CloudFront", "5xxErrorRate", cdn, "Average"),
        ]
    return specs


def fetch_metrics(cloudwatch, targets: Targets, now: datetime) -> dict[str, Optional[float]]:
    """The newest one-minute value of each metric, or None where CloudWatch has no datapoint."""
    specs = metric_specs(targets)
    response = cloudwatch.get_metric_data(
        MetricDataQueries=[
            {
                "Id": spec.id,
                "MetricStat": {
                    "Metric": {
                        "Namespace": spec.namespace,
                        "MetricName": spec.name,
                        "Dimensions": [{"Name": name, "Value": value} for name, value in spec.dimensions],
                    },
                    "Period": PERIOD_SECONDS,
                    "Stat": spec.stat,
                },
                "ReturnData": True,
            }
            for spec in specs
        ],
        StartTime=now - LOOKBACK,
        EndTime=now,
        ScanBy="TimestampDescending",
    )
    values: dict[str, Optional[float]] = {spec.id: None for spec in specs}
    for result in response.get("MetricDataResults", []):
        if result.get("Values"):
            values[result["Id"]] = result["Values"][0]
    return values
```

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools/tests/test_metrics.py` → PASS.
```bash
git add tools/livectl/metrics.py tools/tests/test_metrics.py
git commit -m "feat: read every console metric in one getmetricdata call"
```

---

### Task 6: Manifest check

**Files:**
- Create: `tools/livectl/manifest.py`, `tools/tests/test_manifest.py`

**Interfaces:**
- Produces: `http_fetch(url, timeout=3.0) -> str`; `first_variant(master, base_url) -> Optional[str]`; `media_sequence(playlist) -> Optional[int]`; `ManifestCheck` (frozen: `sequence, advancing, seconds_since_change, error`); `ManifestWatcher(fetch=http_fetch, clock=time.monotonic, window=30.0).check(url) -> ManifestCheck`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_manifest.py`:
```python
from livectl.manifest import ManifestWatcher, first_variant, media_sequence

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=5500000,RESOLUTION=1920x1080
index_1.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=3300000,RESOLUTION=1280x720
index_2.m3u8
"""


def media(seq):
    return f"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:{seq}\n#EXTINF:6.0,\nseg_{seq}.ts\n"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


URL = "https://d1.cloudfront.net/out/v1/x/x/x-hls/index.m3u8"


def site(pages):
    def fetch(url):
        page = pages[url]
        if isinstance(page, Exception):
            raise page
        return page() if callable(page) else page

    return fetch


def test_the_first_variant_is_resolved_against_the_master_url():
    assert first_variant(MASTER, URL) == "https://d1.cloudfront.net/out/v1/x/x/x-hls/index_1.m3u8"


def test_media_sequence_is_read_from_a_media_playlist():
    assert media_sequence(media(1234)) == 1234
    assert media_sequence(MASTER) is None


def test_the_first_look_cannot_tell_whether_it_is_advancing():
    watcher = ManifestWatcher(site({URL: MASTER, URL.replace("index", "index_1"): media(10)}), Clock())

    check = watcher.check(URL)

    assert (check.sequence, check.advancing, check.error) == (10, False, None)


def test_a_growing_sequence_is_advancing_until_it_stalls_past_the_window():
    clock, seq = Clock(), {"n": 10}
    variant = URL.replace("index", "index_1")
    watcher = ManifestWatcher(site({URL: MASTER, variant: lambda: media(seq["n"])}), clock, window=30)
    watcher.check(URL)

    clock.now, seq["n"] = 6, 11
    assert watcher.check(URL).advancing is True

    clock.now = 36.5  # no new segment for 30.5 s
    assert watcher.check(URL).advancing is False


def test_a_fetch_error_is_reported_not_raised():
    watcher = ManifestWatcher(site({URL: OSError("HTTP Error 404: Not Found")}), Clock())

    check = watcher.check(URL)

    assert check.advancing is False and "404" in check.error
```
Run → FAIL (no module).

- [ ] **Step 2: Implement**

`tools/livectl/manifest.py`:
```python
"""Prove that video reaches viewers: read the HLS playlist through CloudFront and watch it advance.

Every other node says a resource is up. This one says segments are arriving where a viewer fetches them.
The master playlist is multivariant and has no media sequence, so the first variant playlist is read instead.
"""

from __future__ import annotations

import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urljoin

Fetch = Callable[[str], str]


def http_fetch(url: str, timeout: float = 3.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "livectl"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def first_variant(master: str, base_url: str) -> Optional[str]:
    lines = [line.strip() for line in master.splitlines()]
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            for following in lines[index + 1:]:
                if following and not following.startswith("#"):
                    return urljoin(base_url, following)
    return None


def media_sequence(playlist: str) -> Optional[int]:
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            return int(line.split(":", 1)[1].strip())
    return None


@dataclass(frozen=True)
class ManifestCheck:
    sequence: Optional[int]
    advancing: bool
    seconds_since_change: Optional[float]
    error: Optional[str]


class ManifestWatcher:
    """Remembers the last media sequence it saw, so each check can say whether the stream is moving."""

    def __init__(self, fetch: Fetch = http_fetch, clock: Callable[[], float] = time.monotonic,
                 window: float = 30.0) -> None:
        self._fetch = fetch
        self._clock = clock
        self._window = window
        self._url: Optional[str] = None
        self._sequence: Optional[int] = None
        self._changed_at: Optional[float] = None

    def check(self, url: str) -> ManifestCheck:
        if url != self._url:
            self._url, self._sequence, self._changed_at = url, None, None
        try:
            master = self._fetch(url)
            variant = first_variant(master, url)
            sequence = media_sequence(self._fetch(variant) if variant else master)
        except Exception as error:  # 404 before the first segment, DNS, timeouts: all are "not playing"
            return ManifestCheck(self._sequence, False, None, f"{type(error).__name__}: {error}")
        if sequence is None:
            return ManifestCheck(None, False, None, "the playlist has no #EXT-X-MEDIA-SEQUENCE")

        now = self._clock()
        if self._sequence is not None and sequence != self._sequence:
            self._changed_at = now
        self._sequence = sequence
        if self._changed_at is None:
            return ManifestCheck(sequence, False, None, None)
        since = now - self._changed_at
        return ManifestCheck(sequence, since <= self._window, since, None)
```

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools/tests/test_manifest.py` → PASS.
```bash
git add tools/livectl/manifest.py tools/tests/test_manifest.py
git commit -m "feat: check that the hls playlist advances through cloudfront"
```

---

### Task 7: Nodes and the Pipeline reader

**Files:**
- Create: `tools/livectl/pipeline.py`, `tools/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `TtlCache`, `fetch_metrics`, `ManifestWatcher`, `Targets`.
- Produces: health constants `OK, WARN, BAD, OFF, UNKNOWN`; `ORDER` (the seven node ids); `NodeStatus` (frozen, `to_dict()`); `Pipeline(*, mediaconnect, medialive, mediapackagev2, cloudfront, cloudwatch, region="us-east-1", fetch=http_fetch, clock=time.monotonic, now=utcnow, source_status=lambda: None)` with `nodes(targets) -> list[NodeStatus]`, `metrics_age() -> Optional[float]`, `forget()`.
- `source_status` returns `None` or a dict with at least `state` (`stopped`/`starting`/`running`/`exited`); Plan 7 supplies it.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_pipeline.py`:
```python
from datetime import datetime, timezone

from livectl.manifest import ManifestCheck
from livectl.pipeline import BAD, OFF, OK, ORDER, UNKNOWN, WARN, Pipeline
from livectl.targets import Targets
from stubs import CHANNEL_ID, FLOW_ARN, stub_aws

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
TARGETS = Targets(
    flow_arn=FLOW_ARN, channel_id=CHANNEL_ID, input_id="4412345", channel_group="live-sports-aws-demo",
    mediapackage_channel="live-sports-aws-demo", mediapackage_endpoint="live-sports-aws-demo-hls",
    distribution_id="E2EXAMPLE", manifest_url="https://d1.cloudfront.net/out/v1/x/index.m3u8",
)
LIVE_METRICS = {"src_connected": 1.0, "src_bitrate": 6_000_000.0, "src_not_recovered": 0.0,
                "src_cc_errors": 0.0, "ml_alerts": 0.0, "ml_fps": 30.0, "mp_ingress_bytes": 74_000_000.0}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def playing_fetch():
    """A CloudFront that serves a playlist whose sequence grows on every fetch of the variant."""
    state = {"seq": 100}

    def fetch(url):
        if url.endswith("index.m3u8"):
            return "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nindex_1.m3u8\n"
        state["seq"] += 1
        return f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{state['seq']}\n"

    return fetch


def make(clients, fetch=None, clock=None, source=None):
    return Pipeline(**clients, fetch=fetch or playing_fetch(), clock=clock or Clock(), now=lambda: NOW,
                    source_status=lambda: source)


def by_id(nodes):
    return {node.id: node for node in nodes}


def test_there_is_one_node_per_resource_in_chain_order():
    nodes = make(stub_aws()).nodes(TARGETS)

    assert [n.id for n in nodes] == list(ORDER)
    assert nodes[0].title == "SRT Input Source" and nodes[-1].title == "Player"


def test_off_air_everything_hourly_is_off():
    nodes = by_id(make(stub_aws()).nodes(TARGETS))

    assert nodes["mediaconnect_flow"].health == OFF
    assert nodes["medialive_channel"].health == OFF
    assert nodes["srt_source"].health == OFF
    assert nodes["player"].health == OFF
    assert nodes["medialive_input"].health == OK, "an attached input is healthy even while the channel is idle"
    assert nodes["cloudfront_cdn"].health == OK


def test_on_air_and_playing_is_ok_all_the_way_along():
    clock = Clock()
    pipeline = make(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=LIVE_METRICS), clock=clock)
    pipeline.nodes(TARGETS)
    clock.now += 5  # past the manifest TTL, so the second look sees the sequence move

    nodes = by_id(pipeline.nodes(TARGETS))

    assert {n.id: n.health for n in nodes.values()} == {i: OK for i in ORDER}
    assert "6.0 Mbps" in nodes["srt_source"].summary


def test_a_running_channel_without_a_source_is_bad_at_the_source():
    metrics = dict(LIVE_METRICS, src_connected=0.0)
    nodes = by_id(make(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=metrics)).nodes(TARGETS))

    assert nodes["srt_source"].health == BAD
    assert nodes["srt_source"].summary == "not connected"


def test_packet_loss_degrades_the_source():
    metrics = dict(LIVE_METRICS, src_not_recovered=12.0)
    nodes = by_id(make(stub_aws(flow="ACTIVE", channel="RUNNING", metrics=metrics)).nodes(TARGETS))

    assert nodes["srt_source"].health == WARN


def test_an_active_alert_degrades_the_channel_and_is_listed():
    clients = stub_aws(flow="ACTIVE", channel="RUNNING", metrics=LIVE_METRICS,
                       alerts=(("InputLoss", "Input video lost"),))
    node = by_id(make(clients).nodes(TARGETS))["medialive_channel"]

    assert node.health == WARN
    assert node.details["alerts"] == ["InputLoss: Input video lost"]
    assert clients["medialive"].calls[-1] == ("list_alerts", {"ChannelId": CHANNEL_ID, "StateFilter": "SET"})


def test_one_failing_call_marks_one_node_unknown_and_the_rest_still_render():
    nodes = by_id(make(stub_aws(fail=("describe_input",))).nodes(TARGETS))

    assert nodes["medialive_input"].health == UNKNOWN
    assert "AccessDenied" in nodes["medialive_input"].error
    assert nodes["mediaconnect_flow"].health == OFF


def test_missing_outputs_make_their_nodes_unknown_with_a_useful_hint():
    minimal = Targets(flow_arn=FLOW_ARN, channel_id=CHANNEL_ID)
    nodes = by_id(make(stub_aws()).nodes(minimal))

    assert nodes["medialive_input"].health == UNKNOWN
    assert "Deploy stack" in nodes["medialive_input"].error


def test_aws_is_read_at_most_once_per_ttl_however_often_the_page_polls():
    clients, clock = stub_aws(), Clock()
    pipeline = make(clients, clock=clock)

    for _ in range(3):
        pipeline.nodes(TARGETS)
        clock.now += 1

    assert len(clients["mediaconnect"].calls) == 1
    assert len(clients["cloudwatch"].calls) == 1


def test_the_player_is_not_fetched_while_the_channel_is_idle():
    calls = []
    make(stub_aws(), fetch=lambda url: calls.append(url) or "").nodes(TARGETS)

    assert calls == []


def test_the_test_source_state_shows_on_the_source_node():
    nodes = by_id(make(stub_aws(), source={"state": "running"}).nodes(TARGETS))

    assert nodes["srt_source"].details["test source"] == "running"


def test_nodes_serialise_to_plain_json_types():
    import json

    json.dumps([n.to_dict() for n in make(stub_aws()).nodes(TARGETS)])
```
Run → FAIL (no module).

- [ ] **Step 2: Implement**

`tools/livectl/pipeline.py`:
```python
"""The state of every resource in the chain, one node each, cheap enough to poll every two seconds.

Node builders are pure functions of AWS responses, so the rules for "healthy" read in one place and test without
clients. `Pipeline` does the reading, through a TTL cache, and turns any single failed call into one `unknown` node
instead of an error page.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import quote

from livectl.cache import TtlCache
from livectl.manifest import Fetch, ManifestCheck, ManifestWatcher, http_fetch
from livectl.metrics import fetch_metrics
from livectl.targets import Targets

OK, WARN, BAD, OFF, UNKNOWN = "ok", "warn", "bad", "off", "unknown"
STATE_TTL, MANIFEST_TTL, METRICS_TTL = 5.0, 4.0, 60.0

ORDER = (
    "srt_source", "mediaconnect_flow", "medialive_input", "medialive_channel",
    "mediapackage_channel", "cloudfront_cdn", "player",
)
TITLES = {
    "srt_source": "SRT Input Source",
    "mediaconnect_flow": "MediaConnect Flow",
    "medialive_input": "MediaLive Input",
    "medialive_channel": "MediaLive Channel",
    "mediapackage_channel": "MediaPackage Channel",
    "cloudfront_cdn": "CloudFront CDN",
    "player": "Player",
}
REDEPLOY_HINT = "not in the Terraform outputs yet; run Deploy stack once to add it"


@dataclass(frozen=True)
class NodeStatus:
    id: str
    title: str
    state: Optional[str]
    health: str
    summary: str
    checked_at: str
    metrics: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    console_url: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _node(node_id: str, at: str, **fields: Any) -> NodeStatus:
    return NodeStatus(id=node_id, title=TITLES[node_id], checked_at=at, **fields)


def unknown(node_id: str, at: str, error: str) -> NodeStatus:
    return _node(node_id, at, state=None, health=UNKNOWN, summary="could not read", error=error)


def _mbps(bits_per_second: Optional[float]) -> Optional[str]:
    return None if bits_per_second is None else f"{bits_per_second / 1e6:.1f} Mbps"


# --- node builders (pure) -------------------------------------------------------


def source_node(flow_status: Optional[str], metrics: dict, process: Optional[dict], at: str) -> NodeStatus:
    picked = {k: metrics.get(k) for k in ("src_connected", "src_bitrate", "src_rtt", "src_not_recovered", "src_cc_errors")}
    details = {"test source": (process or {}).get("state", "not started from this console")}
    connected = picked["src_connected"]
    if flow_status != "ACTIVE":
        health, summary = OFF, "flow not active"
    elif connected is None:
        health, summary = UNKNOWN, "waiting for metrics (about a minute)"
    elif connected < 1:
        health, summary = BAD, "not connected"
    elif (picked["src_not_recovered"] or 0) > 0 or (picked["src_cc_errors"] or 0) > 0:
        health, summary = WARN, "connected, losing packets"
    else:
        health, summary = OK, "connected" + (f" · {_mbps(picked['src_bitrate'])}" if picked["src_bitrate"] else "")
    state = None if connected is None else ("CONNECTED" if connected >= 1 else "DISCONNECTED")
    return _node("srt_source", at, state=state, health=health, summary=summary, metrics=picked, details=details)


def flow_node(flow: dict, region: str, at: str) -> NodeStatus:
    status = flow["Status"]
    source = flow.get("Source", {})
    health = {"ACTIVE": OK, "STANDBY": OFF, "ERROR": BAD}.get(status, WARN)
    summary = {"ACTIVE": "listening for SRT", "STANDBY": "stopped"}.get(status, status.lower())
    details = {
        "Flow ARN": flow.get("FlowArn"),
        "Protocol": source.get("Transport", {}).get("Protocol"),
        "Ingest": f"{source.get('IngestIp')}:{source.get('IngestPort')}" if source.get("IngestIp") else None,
    }
    url = f"https://{region}.console.aws.amazon.com/mediaconnect/home?region={region}#/flows/{quote(flow.get('FlowArn', ''), safe='')}"
    return _node("mediaconnect_flow", at, state=status, health=health, summary=summary, details=details, console_url=url)


def input_node(item: dict, at: str) -> NodeStatus:
    state = item["State"]
    health = {"ATTACHED": OK, "DETACHED": WARN}.get(state, WARN)
    summary = "attached to the channel" if state == "ATTACHED" else state.lower()
    return _node("medialive_input", at, state=state, health=health, summary=summary,
                 details={"Input ID": item.get("Id"), "Type": item.get("Type")})


def channel_node(channel: dict, alerts: Optional[list], alerts_error: Optional[str], metrics: dict,
                 region: str, at: str) -> NodeStatus:
    state = channel["State"]
    alert_lines = [f"{a.get('AlertType')}: {a.get('Message')}" for a in (alerts or [])]
    picked = {k: metrics.get(k) for k in ("ml_alerts", "ml_fps", "ml_input_loss")}
    if state == "RUNNING":
        health = WARN if alert_lines else OK
        fps = f" · input {picked['ml_fps']:.0f} fps" if picked["ml_fps"] else ""
        summary = (f"running · {len(alert_lines)} alert(s)" if alert_lines else "running") + fps
    elif state == "IDLE":
        health, summary = OFF, "idle"
    elif state.endswith("FAILED"):
        health, summary = BAD, state.lower()
    else:
        health, summary = WARN, state.lower()
    details = {"Channel ID": channel.get("Id"), "ARN": channel.get("Arn"),
               "alerts": alert_lines if alerts_error is None else f"unavailable: {alerts_error}"}
    url = f"https://{region}.console.aws.amazon.com/medialive/home?region={region}#/channels/{channel.get('Id')}"
    return _node("medialive_channel", at, state=state, health=health, summary=summary, metrics=picked,
                 details=details, console_url=url)


def package_node(targets: Targets, metrics: dict, channel_running: bool, at: str) -> NodeStatus:
    ingress = metrics.get("mp_ingress_bytes")
    mbps = None if ingress is None else ingress * 8 / 60
    if mbps:
        health, summary, state = OK, f"receiving · {_mbps(mbps)}", "RECEIVING"
    elif channel_running:
        health, summary, state = WARN, "no ingest from MediaLive yet", "WAITING"
    else:
        health, summary, state = OFF, "waiting for MediaLive", "IDLE"
    details = {"Channel group": targets.channel_group, "Channel": targets.mediapackage_channel,
               "Endpoint": targets.mediapackage_endpoint}
    return _node("mediapackage_channel", at, state=state, health=health, summary=summary,
                 metrics={"mp_ingress_bytes": ingress, "mp_egress_5xx": metrics.get("mp_egress_5xx")},
                 details=details)


def cdn_node(distribution: dict, metrics: dict, at: str) -> NodeStatus:
    status = distribution["Status"]
    enabled = distribution.get("DistributionConfig", {}).get("Enabled", True)
    error_rate = metrics.get("cf_5xx_rate")
    if not enabled:
        health, summary = BAD, "disabled"
    elif status != "Deployed":
        health, summary = WARN, "deploying changes"
    elif error_rate is not None and error_rate > 5:
        health, summary = WARN, f"{error_rate:.0f}% 5xx"
    else:
        requests = metrics.get("cf_requests")
        health, summary = OK, "deployed" + (f" · {requests:.0f} req/min" if requests else "")
    url = f"https://us-east-1.console.aws.amazon.com/cloudfront/v4/home#/distributions/{distribution.get('Id')}"
    return _node("cloudfront_cdn", at, state=status, health=health, summary=summary,
                 metrics={"cf_requests": metrics.get("cf_requests"), "cf_5xx_rate": error_rate},
                 details={"Distribution ID": distribution.get("Id"), "Domain": distribution.get("DomainName")},
                 console_url=url)


def player_node(check: Optional[ManifestCheck], channel_running: bool, manifest_url: Optional[str],
                at: str) -> NodeStatus:
    details = {"Manifest": manifest_url}
    if not channel_running:
        return _node("player", at, state=None, health=OFF, summary="nothing to play", details=details)
    if check is None:
        return unknown("player", at, f"cdn_manifest_url {REDEPLOY_HINT}")
    if check.advancing:
        return _node("player", at, state="PLAYING", health=OK, summary=f"playing · segment {check.sequence}",
                     details=details)
    summary = "no playlist yet" if check.error else "playlist not advancing"
    return _node("player", at, state="STALLED", health=WARN, summary=summary, details=details, error=check.error)


# --- reading AWS -----------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Pipeline:
    def __init__(self, *, mediaconnect, medialive, mediapackagev2, cloudfront, cloudwatch,
                 region: str = "us-east-1", fetch: Fetch = http_fetch,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] = _utcnow,
                 source_status: Callable[[], Optional[dict]] = lambda: None) -> None:
        self._mediaconnect, self._medialive = mediaconnect, medialive
        self._mediapackagev2, self._cloudfront, self._cloudwatch = mediapackagev2, cloudfront, cloudwatch
        self._region, self._now, self._source_status = region, now, source_status
        self._cache = TtlCache(clock)
        self._watcher = ManifestWatcher(fetch, clock)

    def forget(self) -> None:
        """Drop cached state, for example after a job changed it."""
        self._cache.clear()

    def metrics_age(self) -> Optional[float]:
        return self._cache.age("metrics")

    def _read(self, key: str, ttl: float, load: Callable[[], Any]) -> tuple[Any, Optional[str]]:
        try:
            return self._cache.get(key, ttl, load), None
        except Exception as error:  # a denied or throttled call becomes one unknown node
            return None, f"{type(error).__name__}: {error}"

    def nodes(self, targets: Targets) -> list[NodeStatus]:
        at = self._now().isoformat(timespec="seconds")
        metrics, metrics_error = self._read(
            "metrics", METRICS_TTL, lambda: fetch_metrics(self._cloudwatch, targets, self._now()))
        metrics = metrics or {}

        flow, flow_error = self._read(
            "flow", STATE_TTL, lambda: self._mediaconnect.describe_flow(FlowArn=targets.flow_arn)["Flow"])
        flow_status = flow["Status"] if flow else None

        source = source_node(flow_status, metrics, self._source_status(), at)
        if metrics_error:
            source = NodeStatus(**{**source.to_dict(), "error": f"metrics: {metrics_error}"})
        nodes = [source, flow_node(flow, self._region, at) if flow else unknown("mediaconnect_flow", at, flow_error)]

        if targets.input_id:
            item, error = self._read("input", STATE_TTL,
                                     lambda: self._medialive.describe_input(InputId=targets.input_id))
            nodes.append(input_node(item, at) if item else unknown("medialive_input", at, error))
        else:
            nodes.append(unknown("medialive_input", at, f"medialive_input_id {REDEPLOY_HINT}"))

        channel, channel_error = self._read(
            "channel", STATE_TTL, lambda: self._medialive.describe_channel(ChannelId=targets.channel_id))
        channel_running = bool(channel) and channel["State"] == "RUNNING"
        if channel:
            alerts, alerts_error = (None, None)
            if channel_running:
                alerts, alerts_error = self._read("alerts", STATE_TTL, lambda: self._medialive.list_alerts(
                    ChannelId=targets.channel_id, StateFilter="SET")["Alerts"])
            nodes.append(channel_node(channel, alerts, alerts_error, metrics, self._region, at))
        else:
            nodes.append(unknown("medialive_channel", at, channel_error))

        if targets.channel_group and targets.mediapackage_channel and targets.mediapackage_endpoint:
            _, error = self._read("package", STATE_TTL, lambda: (
                self._mediapackagev2.get_channel(ChannelGroupName=targets.channel_group,
                                                 ChannelName=targets.mediapackage_channel),
                self._mediapackagev2.get_origin_endpoint(ChannelGroupName=targets.channel_group,
                                                         ChannelName=targets.mediapackage_channel,
                                                         OriginEndpointName=targets.mediapackage_endpoint)))
            nodes.append(unknown("mediapackage_channel", at, error) if error
                         else package_node(targets, metrics, channel_running, at))
        else:
            nodes.append(unknown("mediapackage_channel", at, f"MediaPackage names {REDEPLOY_HINT}"))

        if targets.distribution_id:
            distribution, error = self._read("cdn", STATE_TTL, lambda: self._cloudfront.get_distribution(
                Id=targets.distribution_id)["Distribution"])
            nodes.append(cdn_node(distribution, metrics, at) if distribution else unknown("cloudfront_cdn", at, error))
        else:
            nodes.append(unknown("cloudfront_cdn", at, f"distribution_id {REDEPLOY_HINT}"))

        check = None
        if channel_running and targets.manifest_url:
            check, _ = self._read("manifest", MANIFEST_TTL, lambda: self._watcher.check(targets.manifest_url))
        nodes.append(player_node(check, channel_running, targets.manifest_url, at))
        return nodes
```

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools/tests/test_pipeline.py` → PASS.
```bash
git add tools/livectl/pipeline.py tools/tests/test_pipeline.py
git commit -m "feat: read every resource in the chain as its own node"
```

---

### Task 8: Verdict, hourly rate and action rules

**Files:**
- Create: `tools/livectl/verdict.py`, `tools/livectl/actions.py`, `tools/tests/test_verdict.py`, `tools/tests/test_actions.py`

**Interfaces:**
- Produces: `Verdict` (frozen: `text`, `health` where health adds `busy`); `verdict(nodes: Optional[dict[str, NodeStatus]], job: Optional[dict]) -> Verdict`; `RATES`; `hourly_rate(nodes: Optional[dict]) -> float`.
- Produces: `ACTIONS` tuple (`deploy`, `teardown`, `scan`, `go-live`, `go-off-air`, `source-start`, `source-stop`); `Refusal` (frozen: `status: int`, `message: str`); `Situation` (frozen: `deployed`, `job_running: Optional[str]`, `flow_state`, `channel_state`, `source_running=False`); `refusals(situation) -> dict[str, Optional[Refusal]]`.

- [ ] **Step 1: Write the failing tests**

`tools/tests/test_verdict.py`:
```python
import pytest

from livectl.pipeline import BAD, OFF, OK, ORDER, UNKNOWN, WARN, NodeStatus
from livectl.verdict import hourly_rate, verdict


def chain(**overrides):
    """Seven nodes, all off-air defaults, with (state, health) overrides by node id."""
    defaults = {
        "srt_source": (None, OFF), "mediaconnect_flow": ("STANDBY", OFF), "medialive_input": ("ATTACHED", OK),
        "medialive_channel": ("IDLE", OFF), "mediapackage_channel": ("IDLE", OFF),
        "cloudfront_cdn": ("Deployed", OK), "player": (None, OFF),
    }
    defaults.update(overrides)
    return {i: NodeStatus(id=i, title=i, state=s, health=h, summary="", checked_at="t")
            for i, (s, h) in ((i, defaults[i]) for i in ORDER)}


ON = dict(mediaconnect_flow=("ACTIVE", OK), medialive_channel=("RUNNING", OK))
RUNNING = {"name": "go-live", "state": "running"}


@pytest.mark.parametrize("nodes, job, text, health", [
    (None, None, "Not deployed", "off"),
    (None, {"name": "deploy", "state": "running"}, "Deploying…", "busy"),
    (chain(), {"name": "teardown", "state": "running"}, "Tearing down…", "busy"),
    (chain(), RUNNING, "Going live…", "busy"),
    (chain(**ON), {"name": "go-off-air", "state": "running"}, "Going off air…", "busy"),
    (chain(mediaconnect_flow=("ACTIVE", OK)), None, "Partly on", "bad"),
    (chain(medialive_channel=("RUNNING", OK)), None, "Partly on", "bad"),
    (chain(**ON, srt_source=("DISCONNECTED", BAD)), None, "On air · no source", "bad"),
    (chain(**ON, srt_source=("CONNECTED", WARN)), None, "On air · source degraded", "warn"),
    (chain(**{**ON, "srt_source": ("CONNECTED", OK), "medialive_channel": ("RUNNING", WARN)}), None,
     "On air · source degraded", "warn"),
    (chain(**ON, srt_source=("CONNECTED", OK), player=("STALLED", WARN)), None,
     "On air · not reaching viewers", "warn"),
    (chain(**ON, srt_source=("CONNECTED", OK), player=("PLAYING", OK)), None, "On air · playing", "ok"),
    (chain(), None, "Off air", "off"),
    (chain(mediaconnect_flow=(None, UNKNOWN), medialive_channel=(None, UNKNOWN)), None, "Unknown", "unknown"),
    (chain(), {"name": "scan", "state": "running"}, "Off air", "off"),
])
def test_verdict_table(nodes, job, text, health):
    result = verdict(nodes, job)

    assert (result.text, result.health) == (text, health)


def test_a_finished_job_does_not_hold_the_verdict():
    assert verdict(chain(), {"name": "go-live", "state": "succeeded"}).text == "Off air"


def test_nothing_hourly_bills_off_air():
    assert hourly_rate(chain()) == 0.0
    assert hourly_rate(None) == 0.0


def test_the_rate_adds_up_what_is_running():
    assert hourly_rate(chain(mediaconnect_flow=("ACTIVE", OK))) == pytest.approx(0.29)
    assert hourly_rate(chain(**ON)) == pytest.approx(1.485)
    assert hourly_rate(chain(**ON, mediapackage_channel=("RECEIVING", OK))) == pytest.approx(1.735)
```

`tools/tests/test_actions.py`:
```python
from livectl.actions import ACTIONS, Situation, refusals


def allowed(situation):
    return {name for name, refusal in refusals(situation).items() if refusal is None}


OFF_AIR = Situation(deployed=True, job_running=None, flow_state="STANDBY", channel_state="IDLE")
ON_AIR = Situation(deployed=True, job_running=None, flow_state="ACTIVE", channel_state="RUNNING")


def test_every_action_has_an_answer():
    assert set(refusals(OFF_AIR)) == set(ACTIONS)


def test_not_deployed_offers_deploy_teardown_and_scan():
    assert allowed(Situation(deployed=False, job_running=None, flow_state=None, channel_state=None)) == {
        "deploy", "teardown", "scan", "source-stop"}


def test_off_air_offers_going_live_but_not_going_off_air():
    assert allowed(OFF_AIR) == {"deploy", "teardown", "scan", "go-live", "source-stop"}


def test_on_air_refuses_deploy_and_teardown_and_offers_the_source():
    assert allowed(ON_AIR) == {"scan", "go-off-air", "source-start", "source-stop"}
    assert "Go off air first" in refusals(ON_AIR)["teardown"].message
    assert refusals(ON_AIR)["deploy"].status == 409


def test_partly_on_offers_both_directions():
    partly = Situation(deployed=True, job_running=None, flow_state="ACTIVE", channel_state="IDLE")

    assert {"go-live", "go-off-air"} <= allowed(partly)


def test_a_running_job_blocks_everything_but_stopping_the_source():
    busy = Situation(deployed=True, job_running="go-live", flow_state="STANDBY", channel_state="IDLE")

    assert allowed(busy) == {"source-stop"}
    assert refusals(busy)["deploy"].message == "go-live is still running"


def test_a_running_test_source_blocks_teardown():
    situation = Situation(deployed=True, job_running=None, flow_state="STANDBY", channel_state="IDLE",
                          source_running=True)

    assert "Stop the test source first" in refusals(situation)["teardown"].message


def test_the_source_needs_an_active_flow():
    assert "flow is not active" in refusals(OFF_AIR)["source-start"].message


def test_unknown_states_do_not_block_teardown():
    unknown = Situation(deployed=True, job_running=None, flow_state=None, channel_state=None)

    assert "teardown" in allowed(unknown)


def test_messages_never_leak_cli_flags():
    everything = [OFF_AIR, ON_AIR, Situation(deployed=False, job_running=None, flow_state=None, channel_state=None)]
    for situation in everything:
        for refusal in refusals(situation).values():
            assert refusal is None or "--" not in refusal.message
```
Run → FAIL (no modules).

- [ ] **Step 2: Implement**

`tools/livectl/verdict.py`:
```python
"""The one-line verdict for the whole pipeline and what it costs per hour right now. No I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from livectl.pipeline import BAD, OK, UNKNOWN, WARN, NodeStatus

# docs/cost-estimate.md, us-east-1, per hour while the resource is up.
RATES = {
    "mediaconnect_flow": 0.29,     # active flow 0.16 + 20 Mbps input 0.09 + output 0.04
    "medialive_channel": 1.195,    # HD input + 1080p + 720p + 480p outputs
    "mediapackage_channel": 0.25,  # billed per GB; this is the estimate at the demo bitrate
}
BUSY = {"deploy": "Deploying…", "teardown": "Tearing down…", "go-live": "Going live…", "go-off-air": "Going off air…"}


@dataclass(frozen=True)
class Verdict:
    text: str
    health: str  # ok | warn | bad | off | unknown | busy


def verdict(nodes: Optional[dict[str, NodeStatus]], job: Optional[dict]) -> Verdict:
    if job and job.get("state") == "running" and job.get("name") in BUSY:
        return Verdict(BUSY[job["name"]], "busy")
    if nodes is None:
        return Verdict("Not deployed", "off")

    flow, channel = nodes["mediaconnect_flow"], nodes["medialive_channel"]
    if flow.health == UNKNOWN and channel.health == UNKNOWN:
        return Verdict("Unknown", "unknown")
    flow_on, channel_on = flow.state == "ACTIVE", channel.state == "RUNNING"
    if flow_on != channel_on:
        return Verdict("Partly on", "bad")
    if not flow_on:
        return Verdict("Off air", "off")

    source = nodes["srt_source"].health
    if source == BAD:
        return Verdict("On air · no source", "bad")
    if source == WARN or channel.health == WARN:
        return Verdict("On air · source degraded", "warn")
    if nodes["player"].health == OK:
        return Verdict("On air · playing", "ok")
    return Verdict("On air · not reaching viewers", "warn")


def hourly_rate(nodes: Optional[dict[str, NodeStatus]]) -> float:
    if nodes is None:
        return 0.0
    rate = 0.0
    if nodes["mediaconnect_flow"].state == "ACTIVE":
        rate += RATES["mediaconnect_flow"]
    if nodes["medialive_channel"].state == "RUNNING":
        rate += RATES["medialive_channel"]
    if nodes["mediapackage_channel"].state == "RECEIVING":
        rate += RATES["mediapackage_channel"]
    return round(rate, 3)
```

`tools/livectl/actions.py`:
```python
"""Which actions the console allows right now, and why not.

The page hides what this refuses, and the POST handlers refuse with the same message, so the two cannot disagree.
Messages are written for the person at the page, never copied from the CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

ACTIONS = ("deploy", "teardown", "scan", "go-live", "go-off-air", "source-start", "source-stop")


@dataclass(frozen=True)
class Refusal:
    status: int
    message: str


@dataclass(frozen=True)
class Situation:
    deployed: bool
    job_running: Optional[str]
    flow_state: Optional[str]
    channel_state: Optional[str]
    source_running: bool = False


def _first(*refusals: Optional[Refusal]) -> Optional[Refusal]:
    return next((r for r in refusals if r is not None), None)


def refusals(s: Situation) -> dict[str, Optional[Refusal]]:
    busy = Refusal(409, f"{s.job_running} is still running") if s.job_running else None
    missing = None if s.deployed else Refusal(400, "Nothing is deployed yet. Use Deploy stack first.")
    on_air = s.flow_state == "ACTIVE" or s.channel_state == "RUNNING"
    fully_on = s.flow_state == "ACTIVE" and s.channel_state == "RUNNING"
    fully_off = s.flow_state == "STANDBY" and s.channel_state == "IDLE"
    off_air_first = Refusal(409, "Go off air first: the flow or the channel is still running.") if on_air else None

    return {
        "deploy": _first(busy, off_air_first and Refusal(
            409, "Go off air first: Terraform cannot update a running MediaLive channel.")),
        # A failed apply can leave billable resources without complete outputs, so teardown never needs them.
        "teardown": _first(busy, off_air_first,
                           Refusal(409, "Stop the test source first.") if s.source_running else None),
        "scan": busy,
        "go-live": _first(busy, missing, Refusal(409, "Already on air.") if fully_on else None),
        "go-off-air": _first(busy, missing, Refusal(409, "Already off air.") if fully_off else None),
        "source-start": _first(
            missing,
            Refusal(409, "The test source is already running.") if s.source_running else None,
            None if s.flow_state == "ACTIVE" else Refusal(409, "The flow is not active: go live first."),
        ),
        "source-stop": None,
    }
```
Note on `test_not_deployed_offers_deploy_teardown_and_scan`: with `deployed=False` and unknown states, go-live and go-off-air are refused by `missing`. Check it passes as written.

- [ ] **Step 3: Verify and commit**

Run: `.venv/bin/pytest -q tools/tests/test_verdict.py tools/tests/test_actions.py` → PASS.
```bash
git add tools/livectl/verdict.py tools/livectl/actions.py tools/tests/test_verdict.py tools/tests/test_actions.py
git commit -m "feat: add the pipeline verdict, hourly rate and action rules"
```

---

### Task 9: `/api/pipeline` and the renamed actions

**Files:**
- Modify: `tools/livectl/server.py`, `tools/livectl/cli.py`, `tools/tests/test_server.py`

**Interfaces:**
- Consumes: `Pipeline`, `verdict`, `hourly_rate`, `refusals`, `Situation`, `NotDeployed`.
- Produces: `Console` gains `region: str = "us-east-1"` and `pipeline: Optional[Pipeline] = None` (built in `__post_init__` from its clients). New routes: `GET /api/pipeline`; `POST /api/deploy`, `/api/teardown`, `/api/scan`, `/api/go-live`, `/api/go-off-air`. Every job clears the pipeline cache when it finishes; Terraform jobs also clear the targets. Old routes stay until Plan 6.
- `/api/pipeline` payload keys: `deployed`, `note`, `verdict {text, health}`, `rate`, `metrics_age`, `nodes [NodeStatus dicts]`, `endpoints {ingest, player, manifest, passphrase_secret_arn}`, `job`, `actions {name: null | message}`.

- [ ] **Step 1: Write the failing tests**

Append to `tools/tests/test_server.py`:
```python
from livectl.pipeline import Pipeline
from stubs import stub_aws


def stub_console(runner=None, **state):
    """A console whose pipeline reads stub clients: any chain state, no moto needed."""
    clients = stub_aws(**state)
    console = make_console(runner=runner or counting_runner(), command=recording_command([]))
    console.pipeline = Pipeline(**clients, fetch=lambda url: "", source_status=lambda: None)
    return console


def test_pipeline_when_nothing_is_deployed_says_so_in_plain_words(aws):
    status, payload = call(make_console(), "GET", "/api/pipeline")

    assert status == 200
    assert payload["deployed"] is False
    assert payload["verdict"] == {"text": "Not deployed", "health": "off"}
    assert payload["note"] is None, "the ordinary empty stack needs no error text"
    assert payload["actions"]["deploy"] is None
    assert "Deploy stack" in payload["actions"]["go-live"]


def test_pipeline_reports_seven_nodes_a_verdict_and_endpoints(aws):
    status, payload = call(stub_console(), "GET", "/api/pipeline")

    assert status == 200 and payload["deployed"] is True
    assert len(payload["nodes"]) == 7
    assert payload["verdict"]["text"] == "Off air"
    assert payload["rate"] == 0.0
    assert payload["endpoints"]["ingest"] == "srt://203.0.113.20:5000"
    assert payload["endpoints"]["player"] == "https://example.cloudfront.net/"


def test_pipeline_on_air_refuses_teardown_with_a_readable_reason(aws):
    _, payload = call(stub_console(flow="ACTIVE", channel="RUNNING"), "GET", "/api/pipeline")

    assert payload["actions"]["go-off-air"] is None
    assert payload["actions"]["teardown"].startswith("Go off air first")
    assert "source-start" not in payload["actions"], "no route for it until plan 7"


def test_teardown_is_refused_on_air_and_terraform_never_runs(aws):
    calls = []
    console = stub_console(flow="ACTIVE", channel="RUNNING")
    console.command = recording_command(calls)

    status, payload = call(console, "POST", "/api/teardown", body={"confirm": "destroy"})

    assert status == 409 and payload["error"].startswith("Go off air first")
    assert calls == []


def test_teardown_still_needs_the_typed_confirmation(aws):
    calls = []
    console = stub_console()
    console.command = recording_command(calls)

    status, _ = call(console, "POST", "/api/teardown", body={"confirm": "yes"})

    assert status == 400 and calls == []


def test_teardown_runs_terraform_destroy_off_air(aws):
    calls = []
    console = stub_console()
    console.command = recording_command(calls)

    status, payload = call(console, "POST", "/api/teardown", body={"confirm": "destroy"})
    console.jobs.wait(5)

    assert (status, payload) == (202, {"started": "teardown"})
    assert calls == [["terraform", "-chdir=envs/demo", "destroy", "-auto-approve", "-input=false"]]


def test_deploy_runs_terraform_apply(aws):
    calls = []
    console = make_console(command=recording_command(calls))

    status, _ = call(console, "POST", "/api/deploy")
    console.jobs.wait(5)

    assert status == 202
    assert calls == [["terraform", "-chdir=envs/demo", "apply", "-auto-approve", "-input=false"]]


def test_go_live_and_go_off_air_drive_the_real_workflow(aws):
    console = deployed_console()

    assert call(console, "POST", "/api/go-live")[0] == 202
    console.jobs.wait(10)
    assert console.jobs.summary()["state"] == SUCCEEDED

    assert call(console, "POST", "/api/go-off-air")[0] == 202
    console.jobs.wait(10)
    assert console.jobs.summary()["state"] == SUCCEEDED


def test_go_live_without_a_stack_speaks_to_the_person_not_the_cli(aws):
    status, payload = call(make_console(), "POST", "/api/go-live")

    assert status == 400
    assert "--flow-arn" not in payload["error"]
    assert "Deploy stack" in payload["error"]


def test_scan_is_the_old_check_clean(aws):
    console = make_console()

    assert call(console, "POST", "/api/scan")[0] == 202
    console.jobs.wait(10)
    assert console.jobs.summary()["lines"] == ["clean: nothing left"]


def test_a_finished_job_clears_the_cached_pipeline(aws):
    console = stub_console()
    call(console, "GET", "/api/pipeline")
    cleared = []
    console.pipeline.forget = lambda: cleared.append(True)

    call(console, "POST", "/api/scan")
    console.jobs.wait(10)

    assert cleared == [True]
```
Run: `.venv/bin/pytest -q tools/tests/test_server.py` → the new tests FAIL (404 on the new routes).

- [ ] **Step 2: Implement**

In `tools/livectl/server.py`:

1. Imports: add
```python
from dataclasses import asdict
from livectl.actions import Situation, refusals
from livectl.pipeline import Pipeline
from livectl.targets import NotDeployed
from livectl.verdict import hourly_rate, verdict
from livectl.jobs import RUNNING
```
2. `Console` fields: add `region: str = "us-east-1"` and `pipeline: Optional[Pipeline] = None`, and:
```python
    def __post_init__(self) -> None:
        if self.pipeline is None:
            self.pipeline = Pipeline(
                mediaconnect=self.mediaconnect, medialive=self.medialive, mediapackagev2=self.mediapackagev2,
                cloudfront=self.cloudfront, cloudwatch=self.cloudwatch, region=self.region,
            )
```
3. Replace `_forgetting_targets` with:
```python
def _after_job(console: Console, work: Work, *, stack_changed: bool = False) -> Work:
    """Whatever a job did, the cached state is stale once it ends; Terraform also changes the outputs."""

    def wrapped(log) -> None:
        try:
            work(log)
        finally:
            if stack_changed:
                console.forget_targets()
            console.pipeline.forget()

    return wrapped
```
and update the old `/api/apply` and `/api/destroy` branch to call `_after_job(console, work, stack_changed=True)`.

4. Add the pipeline snapshot and the rules:
```python
def _snapshot(console: Console, offset: int = 0) -> tuple[dict, Situation]:
    """Everything the page shows, plus the situation the action rules judge."""
    job = console.jobs.summary(offset)
    running = job["name"] if job and job["state"] == RUNNING else None
    try:
        targets = console.targets()
    except NotDeployed:
        targets, note = None, None
    except TargetError as error:  # terraform missing, not initialised: worth showing verbatim
        targets, note = None, str(error)

    if targets is None:
        situation = Situation(deployed=False, job_running=running, flow_state=None, channel_state=None)
        payload = {"deployed": False, "note": note, "verdict": asdict(verdict(None, job)), "rate": 0.0,
                   "metrics_age": None, "nodes": [], "endpoints": {}, "job": job}
        return payload, situation

    nodes = console.pipeline.nodes(targets)
    by_id = {node.id: node for node in nodes}
    situation = Situation(deployed=True, job_running=running, flow_state=by_id["mediaconnect_flow"].state,
                          channel_state=by_id["medialive_channel"].state)
    payload = {
        "deployed": True,
        "note": None,
        "verdict": asdict(verdict(by_id, job)),
        "rate": hourly_rate(by_id),
        "metrics_age": console.pipeline.metrics_age(),
        "nodes": [node.to_dict() for node in nodes],
        "endpoints": {
            "ingest": f"srt://{targets.ingest_ip}:{targets.ingest_port}" if targets.ingest_ip else None,
            "player": targets.player_url,
            "manifest": targets.manifest_url,
            "passphrase_secret_arn": targets.passphrase_secret_arn,
        },
        "job": job,
    }
    return payload, situation


# Actions the server has routes for. Plan 7 adds the test source; until then the page must not offer it.
ROUTED_ACTIONS = ("deploy", "teardown", "scan", "go-live", "go-off-air")


def _pipeline_payload(console: Console, offset: int = 0) -> dict:
    payload, situation = _snapshot(console, offset)
    payload["actions"] = {name: (r.message if r else None)
                          for name, r in refusals(situation).items() if name in ROUTED_ACTIONS}
    return payload


def _refused(console: Console, action: str) -> Optional[Response]:
    _, situation = _snapshot(console)
    refusal = refusals(situation)[action]
    return _json(refusal.status, {"error": refusal.message}) if refusal else None
```
5. In `route`, GET branch: add
```python
        if path == "/api/pipeline":
            offset = int(query.get("offset", ["0"])[0] or 0)
            return _json(200, _pipeline_payload(console, offset))
```
POST branch, before the old routes:
```python
    if path == "/api/deploy":
        return _refused(console, "deploy") or _submit(console, "deploy", _after_job(
            console, console.command(console.terraform("apply", "-auto-approve")), stack_changed=True))

    if path == "/api/teardown":
        if body.get("confirm") != DESTROY_TOKEN:
            return _json(400, {"error": 'Type "destroy" to confirm the teardown.'})
        return _refused(console, "teardown") or _submit(console, "teardown", _after_job(
            console, console.command(console.terraform("destroy", "-auto-approve")), stack_changed=True))

    if path == "/api/scan":
        return _refused(console, "scan") or _submit(console, "scan", _after_job(console, _check_clean_work(console)))

    if path in ("/api/go-live", "/api/go-off-air"):
        name = path.rsplit("/", 1)[1]
        refused = _refused(console, name)
        if refused:
            return refused
        targets = console.targets()
        action = start_workflow if name == "go-live" else stop_workflow
        return _submit(console, name, _after_job(
            console, lambda log: action(console.mediaconnect, console.medialive, targets, log=log)))
```
6. `cli.py`: pass `region=args.region` when building `Console`.

- [ ] **Step 3: Verify**

Run: `.venv/bin/pytest -q tools` → all PASS, old tests included.
Run: `just test` → all PASS.

- [ ] **Step 4: Commit**

```bash
git add tools/livectl/server.py tools/livectl/cli.py tools/tests/test_server.py
git commit -m "feat: serve the whole pipeline and the renamed actions"
```

---

## Done when

- `just test` passes with no AWS credentials.
- `curl -s 127.0.0.1:8765/api/pipeline | python3 -m json.tool` against `livectl ui --no-browser` on an undeployed checkout shows `"verdict": {"text": "Not deployed", ...}` and no CLI flags in any message.
- The old page still loads and its buttons still work (they call the old routes).
