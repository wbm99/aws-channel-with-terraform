"""Named states of the whole console, for browser tests and for looking at the page by hand.

Each scenario runs the real server, routing, verdict and action rules against stub AWS clients, so the page can be
driven through every state without an AWS account. Run one with:  python tools/tests/scenarios.py on-air-playing
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from livectl.jobs import JobRunner  # noqa: E402
from livectl.pipeline import Pipeline  # noqa: E402
from livectl.server import Console, make_server  # noqa: E402
from stubs import CHANNEL_ID, FLOW_ARN, logs_client, stub_aws  # noqa: E402

CHANNEL_ARN = f"arn:aws:medialive:us-east-1:123456789012:channel:{CHANNEL_ID}"

OUTPUTS = {
    "flow_arn": FLOW_ARN,
    "medialive_channel_id": CHANNEL_ID,
    "medialive_input_id": "4412345",
    "mediapackage_channel_group": "live-sports-aws-demo",
    "mediapackage_channel": "live-sports-aws-demo",
    "mediapackage_endpoint": "live-sports-aws-demo-hls",
    "distribution_id": "E2EXAMPLE",
    "cdn_manifest_url": "https://d1.example.net/out/v1/live/index.m3u8",
    # Port 9 refuses connections at once, so the player frame fails fast and offline.
    "player_url": "http://127.0.0.1:9/",
    "ingest_ip": "203.0.113.20",
    "ingest_port": 5000,
    "passphrase_secret_arn": "arn:aws:secretsmanager:us-east-1:123456789012:secret:live-sports-aws-demo-srt-AbC",
    "events_log_group": "/aws/events/live-sports-aws-demo",
    "medialive_channel_arn": CHANNEL_ARN,
}
LIVE = {"src_connected": 1.0, "src_bitrate": 6_000_000.0, "src_not_recovered": 0.0, "src_cc_errors": 0.0,
        "ml_alerts": 0.0, "ml_fps": 30.0, "mp_ingress_bytes": 74_000_000.0, "cf_requests": 42.0}
ON = dict(flow="ACTIVE", channel="RUNNING")

SCENARIOS: dict[str, dict] = {
    "not-deployed": dict(deployed=False),
    "deploying": dict(deployed=False, job="deploy"),
    "off-air": dict(),
    "going-live": dict(job="go-live"),
    "on-air-no-source": dict(**ON, metrics=dict(LIVE, src_connected=0.0, src_bitrate=None), health="DISCONNECTED"),
    "on-air-playing": dict(**ON, metrics=LIVE, playing=True),
    "degraded": dict(**ON, metrics=dict(LIVE, src_not_recovered=12.0), playing=True),
    "partly-on": dict(flow="ACTIVE"),
    "probe-error": dict(fail=("describe_flow", "describe_channel")),
    "source-running": dict(**ON, metrics=LIVE, playing=True, source="running"),
    # The live run of 2026-09-27: the source has just stopped, MediaConnect has said so, CloudWatch has not yet.
    "source-dropped": dict(**ON, metrics=LIVE, playing=True, health="DISCONNECTED"),
}


def recent_events(health: str = "CONNECTED") -> list[tuple[int, str]]:
    """Three events a person would see around going live, the last one a second ago."""
    now = int(time.time() * 1000)

    def event(source, kind, detail, resource):
        return json.dumps({"source": source, "detail-type": kind, "detail": detail, "resources": [resource]})

    return [
        (now - 3000, event("aws.mediaconnect", "MediaConnect Flow Status Change",
                           {"previousStatus": "STANDBY", "currentStatus": "ACTIVE"}, FLOW_ARN)),
        (now - 2000, event("aws.medialive", "MediaLive Channel State Change", {"state": "RUNNING"}, CHANNEL_ARN)),
        (now - 1000, event("aws.mediaconnect", "MediaConnect Source Health", {"unhealthy": True, "current": {
            "state": health, "tr101": {"ts_sync_loss": False, "continuity_count_error": True}}}, FLOW_ARN)),
    ]


class FakeSource:
    """Stands in for SourceProcess in every scenario, so no browser test can start a real FFmpeg.

    It starts, stops and switches pattern like the real one, records each call, and when running shows the banner
    FFmpeg prints, already redacted.
    """

    def __init__(self, running: bool = False, pattern: str = "testcard") -> None:
        self.running = running
        self.pattern = pattern
        self.calls: list[tuple] = []

    def status(self) -> dict:
        return {"state": "running" if self.running else "stopped", "exit_code": None, "last_error": None,
                "progress": "frame= 900 fps=30" if self.running else None, "pattern": self.pattern}

    def lines(self, after_ms: int) -> list[tuple[int, str]]:
        if not self.running:
            return []
        at = int(time.time() * 1000) - 5000
        banner = "Output #0, mpegts, to 'srt://203.0.113.20:5000?mode=caller&passphrase=***&pbkeylen=32':"
        return [(at, banner)] if at > after_ms else []

    def start(self, *, pattern: str = "testcard", **_) -> None:
        self.calls.append(("start", pattern))
        self.running, self.pattern = True, pattern

    def stop(self) -> None:
        self.calls.append(("stop",))
        self.running = False


def growing_playlist():
    state = {"seq": 500}

    def fetch(url: str) -> str:
        if url.endswith("/index.m3u8"):
            return "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5500000\nindex_1.m3u8\n"
        state["seq"] += 1
        return f"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:{state['seq']}\n"

    return fetch


def no_playlist(url: str) -> str:
    raise OSError("HTTP Error 404: Not Found")


@dataclass
class Scenario:
    console: Console
    commands: list = field(default_factory=list)
    release: threading.Event = field(default_factory=threading.Event)

    def close(self) -> None:
        self.release.set()


def build(name: str) -> Scenario:
    spec = dict(SCENARIOS[name])
    deployed = spec.pop("deployed", True)
    job = spec.pop("job", None)
    playing = spec.pop("playing", False)
    source = spec.pop("source", None)
    health = spec.pop("health", "CONNECTED")
    clients = stub_aws(**spec)
    commands: list = []

    def runner(args):
        return json.dumps({k: {"value": v} for k, v in OUTPUTS.items()} if deployed else {})

    def command(args):
        commands.append(list(args))
        return lambda log: log("$ " + " ".join(args))

    console = Console(**clients, jobs=JobRunner(), runner=runner, command=command,
                      logs=logs_client(recent_events(health)))
    console.source = FakeSource(running=source == "running")
    console.read_passphrase = lambda arn: "0123456789abcdef0123456789abcdef"
    console.pipeline = Pipeline(**clients, fetch=growing_playlist() if playing else no_playlist,
                                source_status=console.source.status, logs=console.logs)
    scenario = Scenario(console=console, commands=commands)
    if job:
        # Output shaped like the real thing, so progress can be measured: halfway through in both cases.
        output = {
            "deploy": ["$ terraform -chdir=envs/demo apply -auto-approve -input=false -no-color",
                       "Plan: 4 to add, 0 to change, 0 to destroy.",
                       "module.player.aws_s3_bucket.this: Creation complete after 1s [id=player]",
                       "module.ingest.aws_iam_role.flow: Creation complete after 1s [id=flow]",
                       "module.delivery.aws_cloudfront_distribution.this: Still creating... [00m10s elapsed]"],
            "go-live": ["starting the MediaConnect flow", "MediaConnect flow STARTING", "MediaConnect flow ACTIVE",
                        "starting the MediaLive channel", "MediaLive channel STARTING"],
        }.get(job, [f"{job} in progress…"])
        console.jobs.submit(job, lambda log: ([log(line) for line in output], scenario.release.wait(120)))
    return scenario


def main(argv: list[str]) -> int:
    name = argv[1] if len(argv) > 1 else "on-air-playing"
    port = int(argv[2]) if len(argv) > 2 else 8766
    if name not in SCENARIOS:
        print("scenarios: " + ", ".join(SCENARIOS), file=sys.stderr)
        return 2
    scenario = build(name)
    with make_server(scenario.console, port=port) as httpd:
        print(f"scenario {name} on http://127.0.0.1:{port}/  (Ctrl-C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            scenario.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
