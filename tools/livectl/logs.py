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
# The two event types that say whether an SRT source is connected right now, as a CloudWatch Logs filter pattern.
SOURCE_PATTERN = '?"MediaConnect Source Health" ?"MediaConnect Flow Status Change"'
MAX_PAGES = 5


@dataclass(frozen=True)
class LogLine:
    """One line for the page. The time is data (at_ms); the page prints it, so text carries none."""

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


def _health(detail: dict) -> tuple[str, str]:
    """State and flags of a Source/Flow Health event: 'disconnected, no bitrate' plus any TR 101 290 errors."""
    current = detail.get("current") or {}
    state = str(current.get("state") or "?").lower()
    if current.get("isBitrateZero"):
        state += ", no bitrate"
    flags = [name for name, on in (current.get("tr101") or {}).items() if on]
    return state, (f" · TR 101 290: {', '.join(flags)}" if flags else "")


def _mediaconnect(kind: str, detail: dict) -> tuple[str, str]:
    if kind == "MediaConnect Source Health":
        state, flags = _health(detail)
        return "srt", f"MediaConnect · source {state}{flags}"
    if kind == "MediaConnect Flow Health":
        state, flags = _health(detail)
        return "mediaconnect", f"MediaConnect · flow {state}{flags}"
    if kind == "MediaConnect Flow Status Change":
        return "mediaconnect", f"MediaConnect · flow {detail.get('previousStatus', '?')} → {detail.get('currentStatus', '?')}"
    if kind == "MediaConnect Alert" and "error-code" in detail:
        if detail.get("errored") is False:
            return "mediaconnect", f"MediaConnect · alert cleared {detail['error-code']}"
        return "mediaconnect", f"MediaConnect · alert {detail['error-code']}: {detail.get('error-message', '')}".rstrip(": ")
    if kind == "MediaConnect Output Health":
        return "mediaconnect", f"MediaConnect · output {str((detail.get('current') or {}).get('state', '?')).lower()}"
    if kind == "MediaConnect Flow Source Metadata Changed":
        return "srt", "MediaConnect · source metadata changed"
    if kind == "MediaConnect Flow Content Quality":
        return "mediaconnect", f"MediaConnect · content quality report ({len(detail.get('streams') or [])} streams)"
    return "mediaconnect", f"MediaConnect · {kind.removeprefix('MediaConnect ')}: {_brief(detail)}"


def _medialive(kind: str, detail: dict) -> str:
    if kind == "MediaLive Channel State Change" and "state" in detail:
        return f"MediaLive · channel {detail['state']}"
    if kind == "MediaLive Channel Alert" and "alert_type" in detail:
        if detail.get("alarm_state") == "CLEARED":
            return f"MediaLive · alert cleared: {detail['alert_type']}"
        message = f" ({detail['message']})" if detail.get("message") else ""
        return f"MediaLive · alert raised: {detail['alert_type']}{message}"
    if kind == "MediaLive Channel Input Change" and "active_input_attachment_name" in detail:
        return f"MediaLive · input switched to {detail['active_input_attachment_name']}"
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
    return LogLine(at_ms, tab, text, raw=message)


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

    def events(self, group: str, *, after_ms: int, stream_prefix: Optional[str] = None,
               pattern: Optional[str] = None) -> list[tuple[int, str, str]]:
        """(timestamp, stream, message) newer than after_ms, oldest first, at most a few pages per call."""
        request: dict = {"logGroupName": group, "startTime": after_ms + 1}
        if stream_prefix:
            request["logStreamNamePrefix"] = stream_prefix
        if pattern:
            request["filterPattern"] = pattern
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
    # Log stream names cannot contain ":", so MediaLive names its streams after the ARN with underscores.
    prefix = channel_arn.replace(":", "_")
    for at_ms, stream, message in reader.events(ELEMENTAL_GROUP, after_ms=after_ms, stream_prefix=prefix):
        kind = "as-run" if stream.endswith("_as_run") else "encoder"
        lines.append(LogLine(at_ms, "medialive", f"MediaLive {kind} · {message.strip()}"))
    return lines


def latest_source_state(events: list[tuple[int, str]], flow_arn: str) -> Optional[tuple[str, int]]:
    """(state, at_ms) of the newest Source Health event for this flow since it last became ACTIVE, else None.

    MediaConnect sends Source Health within about a second of a source connecting or dropping (seen live on
    2026-09-27), while the SourceConnected metric arrives minutes later. Events from before the flow's latest start
    belong to an earlier session and say nothing about now.
    """
    latest: Optional[tuple[str, int]] = None
    for at_ms, message in sorted(events):
        try:
            event = json.loads(message)
        except ValueError:
            continue
        if not any(r.startswith(flow_arn) for r in event.get("resources") or []):
            continue
        detail = event.get("detail") or {}
        kind = event.get("detail-type")
        if kind == "MediaConnect Flow Status Change" and detail.get("currentStatus") == "ACTIVE":
            latest = None
        elif kind == "MediaConnect Source Health":
            state = (detail.get("current") or {}).get("state")
            if state:
                latest = (str(state).upper(), at_ms)
    return latest
