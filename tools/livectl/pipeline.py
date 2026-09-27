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
from livectl.logs import SOURCE_PATTERN, LogReader, clock_label, latest_source_state
from livectl.manifest import Fetch, ManifestCheck, ManifestWatcher, http_fetch
from livectl.metrics import fetch_metrics
from livectl.targets import Targets

OK, WARN, BAD, OFF, UNKNOWN = "ok", "warn", "bad", "off", "unknown"
STATE_TTL, MANIFEST_TTL, METRICS_TTL = 5.0, 4.0, 60.0
# While AWS does not answer (the network dropped), show what it last said for up to this long, and say so.
STALE_FOR = 600.0
# Source Health events come only on change, so the newest may be hours old while the source stays connected.
SOURCE_EVENTS_LOOKBACK_MS = 12 * 60 * 60 * 1000

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


SOURCE_UP = {"CONNECTED", "RECEIVING"}


def source_node(flow_status: Optional[str], metrics: dict, process: Optional[dict], at: str,
                event: Optional[tuple[str, int]] = None) -> NodeStatus:
    """Connected or not comes from the newest MediaConnect Source Health event when there is one (seconds behind),
    else from the SourceConnected metric (minutes behind). The figures always come from CloudWatch."""
    picked = {k: metrics.get(k) for k in ("src_connected", "src_bitrate", "src_rtt", "src_not_recovered", "src_cc_errors")}
    details = {"test source": (process or {}).get("state", "not started from this console")}
    if event:
        connected: Optional[float] = 1.0 if event[0] in SOURCE_UP else 0.0
        details["state from"] = f"MediaConnect event at {clock_label(event[1])} UTC"
    else:
        connected = picked["src_connected"]
        details["state from"] = "CloudWatch metric (one to three minutes behind)"
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


def package_node(targets: Targets, metrics: dict, channel_running: bool, at: str,
                 source_lost: bool = False) -> NodeStatus:
    ingress = metrics.get("mp_ingress_bytes")
    mbps = None if ingress is None else ingress * 8 / 60
    # The newest IngressBytes datapoint can be minutes old; with the channel stopped nothing is arriving, whatever it says.
    if mbps and channel_running and source_lost:
        # With no input, MediaLive keeps encoding its input-loss slate (black), and MediaPackage really receives it.
        health, summary, state = WARN, f"receiving the input-loss slate · {_mbps(mbps)}", "RECEIVING"
    elif mbps and channel_running:
        health, summary, state = OK, f"receiving · {_mbps(mbps)}", "RECEIVING"
    elif channel_running:
        health, summary, state = WARN, "no ingest from MediaLive yet", "WAITING"
    else:
        health, summary, state = OFF, "waiting for MediaLive", "IDLE"
    details = {"Channel group": targets.channel_group, "Channel": targets.mediapackage_channel,
               "Endpoint": targets.mediapackage_endpoint}
    return _node("mediapackage_channel", at, state=state, health=health, summary=summary,
                 metrics={"mp_ingress_bytes": ingress, "mp_egress_bytes": metrics.get("mp_egress_bytes"),
                          "mp_egress_requests": metrics.get("mp_egress_requests"),
                          "mp_egress_5xx": metrics.get("mp_egress_5xx")},
                 details=details)


def cdn_node(distribution: dict, metrics: dict, at: str) -> NodeStatus:
    status = distribution["Status"]
    enabled = distribution.get("DistributionConfig", {}).get("Enabled", True)
    error_rate = metrics.get("cf_5xx_rate")
    if not enabled:  # Terraform disables a distribution before deleting it
        health, summary = OFF, "disabled"
    elif status != "Deployed":
        health, summary = WARN, "deploying changes"
    elif error_rate is not None and error_rate > 5:
        health, summary = WARN, f"{error_rate:.0f}% 5xx"
    else:
        requests = metrics.get("cf_requests")
        health, summary = OK, "deployed" + (f" · {requests:.0f} req/min" if requests else "")
    url = f"https://us-east-1.console.aws.amazon.com/cloudfront/v4/home#/distributions/{distribution.get('Id')}"
    return _node("cloudfront_cdn", at, state=status, health=health, summary=summary,
                 metrics={"cf_requests": metrics.get("cf_requests"),
                          "cf_bytes_downloaded": metrics.get("cf_bytes_downloaded"),
                          "cf_4xx_rate": metrics.get("cf_4xx_rate"), "cf_5xx_rate": error_rate},
                 details={"Distribution ID": distribution.get("Id"), "Domain": distribution.get("DomainName")},
                 console_url=url)


def player_node(check: Optional[ManifestCheck], channel_running: bool, manifest_url: Optional[str],
                at: str, source_lost: bool = False) -> NodeStatus:
    details = {"Manifest": manifest_url}
    if not channel_running:
        return _node("player", at, state=None, health=OFF, summary="nothing to play", details=details)
    if check is None:
        return unknown("player", at, f"cdn_manifest_url {REDEPLOY_HINT}")
    if check.advancing and source_lost:
        return _node("player", at, state="PLAYING", health=WARN, summary="playing the input-loss slate (no source)",
                     details=details)
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
                 source_status: Callable[[], Optional[dict]] = lambda: None, logs=None) -> None:
        self._mediaconnect, self._medialive = mediaconnect, medialive
        self._mediapackagev2, self._cloudfront, self._cloudwatch = mediapackagev2, cloudfront, cloudwatch
        self._region, self._now, self._source_status = region, now, source_status
        self._logs = logs
        self._cache = TtlCache(clock)
        self._watcher = ManifestWatcher(fetch, clock)

    def forget(self) -> None:
        """Drop cached state, for example after a job changed it."""
        self._cache.clear()

    def metrics_age(self) -> Optional[float]:
        return self._cache.age("metrics")

    def unreachable(self) -> Optional[str]:
        """A sentence for the page when some of what it shows is older than it looks, else None."""
        problems = self._cache.problems()
        if not problems:
            return None
        error, age = max(problems.values(), key=lambda item: item[1])
        return f"AWS is not answering ({error}). Showing what it said {age:.0f} s ago."

    def _read(self, key: str, ttl: float, load: Callable[[], Any]) -> tuple[Any, Optional[str]]:
        try:
            return self._cache.get(key, ttl, load, stale_for=STALE_FOR), None
        except Exception as error:  # a denied or throttled call becomes one unknown node
            return None, f"{type(error).__name__}: {error}"

    def _source_event(self, targets: Targets) -> Optional[tuple[str, int]]:
        since = int(self._now().timestamp() * 1000) - SOURCE_EVENTS_LOOKBACK_MS
        found = LogReader(self._logs).events(targets.events_log_group, after_ms=since, pattern=SOURCE_PATTERN)
        return latest_source_state([(at, message) for at, _, message in found], targets.flow_arn)

    def nodes(self, targets: Targets) -> list[NodeStatus]:
        at = self._now().isoformat(timespec="seconds")
        metrics, metrics_error = self._read(
            "metrics", METRICS_TTL, lambda: fetch_metrics(self._cloudwatch, targets, self._now()))
        metrics = metrics or {}

        flow, flow_error = self._read(
            "flow", STATE_TTL, lambda: self._mediaconnect.describe_flow(FlowArn=targets.flow_arn)["Flow"])
        flow_status = flow["Status"] if flow else None

        event = None
        if flow_status == "ACTIVE" and self._logs is not None and targets.events_log_group:
            event, _ = self._read("source-events", STATE_TTL, lambda: self._source_event(targets))
        source = source_node(flow_status, metrics, self._source_status(), at, event)
        if metrics_error:
            source = NodeStatus(**{**source.to_dict(), "error": f"metrics: {metrics_error}"})
        source_lost = source.health == BAD  # known to be disconnected, not merely unknown
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
                         else package_node(targets, metrics, channel_running, at, source_lost))
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
        nodes.append(player_node(check, channel_running, targets.manifest_url, at, source_lost))
        return nodes
