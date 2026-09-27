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
