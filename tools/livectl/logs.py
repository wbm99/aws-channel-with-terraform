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
