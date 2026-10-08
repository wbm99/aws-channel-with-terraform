"""Serve the livectl operations console on the loopback interface.

Routing is a plain function, `route`, that takes the request and a `Console` holding the AWS
clients and the job runner. The HTTP class around it is a thin shell, so the tests exercise the
real routing with moto clients and a fake process launcher, without opening a socket.

The console can destroy infrastructure, so it binds to loopback only and never listens on a
network interface. The one exception is a container (`--container`): Docker delivers published ports to the
container's own network interface, never to its loopback, so there it binds 0.0.0.0 and the guarantee moves to
compose.yaml, which publishes the port on the host's 127.0.0.1 only.
"""

from __future__ import annotations

import json
import re
import sys
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from livectl.actions import Situation, next_action, refusals
from livectl.cache import TtlCache
from livectl.clean import find_informational, find_leftovers
from livectl.control import start as start_workflow
from livectl.control import stop as stop_workflow
from livectl.identity import IDENTITY_TTL, Identity, whoami
from livectl.jobs import RUNNING, JobBusy, JobRunner, Work, command_job
from livectl.logs import LOG_TABS, LOOKBACK_MS, LogLine, LogReader, event_lines, medialive_lines
from livectl.pipeline import REDEPLOY_HINT, Pipeline
from livectl.progress import job_progress
from livectl.source import DEFAULT_PATTERN, PATTERNS, SourceBusy, SourceProcess, SourceSettings, now_ms

PATTERN_IDS = [key for key, _ in PATTERNS]
from livectl.targets import NotDeployed, Runner, TargetError, Targets, resolve_targets, run_command
from livectl.verdict import hourly_rate, verdict

SITE = Path(__file__).parent / "site"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CONTAINER_HOST = "0.0.0.0"  # allowed only with container=True; see the module docstring
DESTROY_TOKEN = "destroy"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}
SITE_FILE = re.compile(r"/[a-z][a-z-]*\.(js|css)")

Response = tuple[int, str, bytes]


class ConsoleError(RuntimeError):
    """Raised when the console cannot be served."""


def _no_passphrase(arn: str) -> str:
    raise RuntimeError("no Secrets Manager client configured")


@dataclass
class Console:
    """Everything a request needs. Clients and launchers are injected, as everywhere in livectl."""

    mediaconnect: Any
    medialive: Any
    cloudwatch: Any
    mediapackagev2: Any
    cloudfront: Any
    jobs: JobRunner = field(default_factory=JobRunner)
    tf_dir: str = "envs/demo"
    prefix: str = "live-sports-aws"
    runner: Runner = run_command
    command: Callable[[Sequence[str]], Work] = command_job
    flow_arn: Optional[str] = None
    channel_id: Optional[str] = None
    region: str = "us-east-1"
    logs: Any = None
    source: SourceProcess = field(default_factory=SourceProcess)
    read_passphrase: Callable[[str], str] = _no_passphrase
    clock_ms: Callable[[], int] = now_ms
    pipeline: Optional[Pipeline] = None
    sts: Any = None  # None: identity is not checked (tests that do not care)
    profile: Optional[str] = None
    clock: Callable[[], float] = time.monotonic
    _targets: Optional[Targets] = None

    def __post_init__(self) -> None:
        self._identity_cache = TtlCache(self.clock)
        if self.pipeline is None:
            self.pipeline = Pipeline(
                mediaconnect=self.mediaconnect, medialive=self.medialive, mediapackagev2=self.mediapackagev2,
                cloudfront=self.cloudfront, cloudwatch=self.cloudwatch, region=self.region,
                source_status=self.source.status, logs=self.logs,
            )

    def targets(self) -> Targets:
        """Terraform outputs, read once and cached until an apply or destroy invalidates them."""
        if self._targets is None:
            self._targets = resolve_targets(self.flow_arn, self.channel_id, self.tf_dir, self.runner)
        return self._targets

    def identity(self) -> Optional[Identity]:
        """Who the console acts as, re-checked every IDENTITY_TTL so an `aws sso login` is picked up unprompted."""
        if self.sts is None:
            return None
        return self._identity_cache.get("identity", IDENTITY_TTL, lambda: whoami(self.sts, self.region, self.profile))

    def forget_targets(self) -> None:
        self._targets = None

    def terraform(self, *args: str) -> Sequence[str]:
        # -input=false so Terraform can never wait on a prompt nobody can see from a browser;
        # -no-color because the output is read in a page, not a terminal.
        return ["terraform", f"-chdir={self.tf_dir}", *args, "-input=false", "-no-color"]


def _json(status: int, payload: dict) -> Response:
    return status, "application/json; charset=utf-8", json.dumps(payload).encode()


def _static(name: str) -> Response:
    target = (SITE / name).resolve()
    if not target.is_file() or SITE.resolve() not in target.parents:
        return _json(404, {"error": f"not found: {name}"})
    return 200, CONTENT_TYPES.get(target.suffix, "application/octet-stream"), target.read_bytes()


def _check_clean_work(console: Console) -> Work:
    def work(log) -> None:
        leftovers = find_leftovers(
            console.mediaconnect, console.medialive, console.mediapackagev2, console.cloudfront, console.prefix
        )
        for line in leftovers:
            log(line)
        if console.logs is not None:
            for line in find_informational(console.logs, console.prefix):
                log("info: " + line)
        if leftovers:
            raise RuntimeError(f"{len(leftovers)} billable resource(s) still exist")
        log("clean: nothing left")

    return work


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


def _snapshot(console: Console, offset: int = 0, job_key: Optional[str] = None) -> tuple[dict, Situation]:
    """Everything the page shows, plus the situation the action rules judge."""
    job = console.jobs.summary(offset, job_key)
    if job:
        job["progress"] = job_progress(job["name"], console.jobs.lines(), job["state"])
    running = job["name"] if job and job["state"] == RUNNING else None
    identity = console.identity()
    shown = identity.to_dict() if identity else None
    if identity and not identity.usable:
        # One cause on screen instead of an unknown chain. Not even `terraform output`: it reads the S3 state.
        situation = Situation(deployed=False, job_running=running, flow_state=None, channel_state=None,
                              source_running=console.source.running, credentials=identity.message)
        payload = {"deployed": False, "note": None, "verdict": {"text": identity.verdict, "health": "bad"},
                   "rate": 0.0, "metrics_age": None, "nodes": [], "endpoints": {}, "job": job,
                   "source": console.source.status(), "identity": shown}
        return payload, situation
    note: Optional[str] = None
    try:
        targets: Optional[Targets] = console.targets()
    except NotDeployed:
        targets = None
    except TargetError as error:  # terraform missing, not initialised: worth showing verbatim
        targets, note = None, str(error)

    if targets is None:
        situation = Situation(deployed=False, job_running=running, flow_state=None, channel_state=None,
                              source_running=console.source.running)
        payload = {"deployed": False, "note": note, "verdict": asdict(verdict(None, job)), "rate": 0.0,
                   "metrics_age": None, "nodes": [], "endpoints": {}, "job": job,
                   "source": console.source.status(), "identity": shown}
        return payload, situation

    nodes = console.pipeline.nodes(targets)
    by_id = {node.id: node for node in nodes}
    situation = Situation(deployed=True, job_running=running, flow_state=by_id["mediaconnect_flow"].state,
                          channel_state=by_id["medialive_channel"].state, source_running=console.source.running)
    payload = {
        "deployed": True,
        "note": None,
        "verdict": asdict(verdict(by_id, job)),
        "rate": hourly_rate(by_id),
        "metrics_age": console.pipeline.metrics_age(),
        "aws_note": console.pipeline.unreachable(),
        "nodes": [node.to_dict() for node in nodes],
        "endpoints": {
            "ingest": f"srt://{targets.ingest_ip}:{targets.ingest_port}" if targets.ingest_ip else None,
            "player": targets.player_url,
            "manifest": targets.manifest_url,
            "passphrase_secret_arn": targets.passphrase_secret_arn,
        },
        "job": job,
        "source": console.source.status(),
        "identity": shown,
    }
    return payload, situation


# Actions the server has routes for; the page is only offered these.
ROUTED_ACTIONS = ("deploy", "teardown", "scan", "go-live", "go-off-air", "source-start", "source-stop")


def _pipeline_payload(console: Console, offset: int = 0, job_key: Optional[str] = None) -> dict:
    payload, situation = _snapshot(console, offset, job_key)
    payload["patterns"] = [{"id": key, "label": label} for key, label in PATTERNS]
    payload["actions"] = {name: (r.message if r else None)
                          for name, r in refusals(situation).items() if name in ROUTED_ACTIONS}
    source = next((n for n in payload["nodes"] if n["id"] == "srt_source"), None)
    connected = bool(source) and source["state"] == "CONNECTED"
    payload["next"] = next_action(situation, source_connected=connected)
    return payload


def _logs_payload(console: Console, tab: str, after: int) -> dict:
    lines: list[LogLine] = []
    notes: list[str] = []
    if tab == "srt":
        lines += [LogLine(at, "srt", f"FFmpeg · {text}") for at, text in console.source.lines(after)]
    try:
        targets: Optional[Targets] = console.targets()
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
                missing = "ResourceNotFound" in type(error).__name__ + str(error)
                notes.append("MediaLive has not written channel logs yet." if missing
                             else f"Could not read MediaLive logs ({type(error).__name__}).")
    lines.sort(key=lambda line: line.at_ms)
    return {"lines": [line.to_dict() for line in lines],
            "after": max([after] + [line.at_ms for line in lines]), "notes": notes}


def _start_source(console: Console, pattern: str, *, switching: bool) -> Response:
    """Start the test source on a pattern. Switching restarts FFmpeg: SRT reconnects, a few seconds of slate."""
    targets = console.targets()
    if not (targets.ingest_ip and targets.passphrase_secret_arn):
        return _json(409, {"error": "The ingest address or the passphrase is missing from the Terraform "
                                    "outputs. Run Deploy stack once to add them."})
    try:
        passphrase = console.read_passphrase(targets.passphrase_secret_arn)
    except Exception as error:  # never echo the error body: keep secrets out of responses on principle
        return _json(502, {"error": "Could not read the SRT passphrase from Secrets Manager "
                                    f"({type(error).__name__})."})
    if switching:
        console.source.stop()
    try:
        console.source.start(host=targets.ingest_ip, port=int(targets.ingest_port or 5000), passphrase=passphrase,
                             settings=SourceSettings().merged({"pattern": pattern}))
    except SourceBusy:
        return _json(409, {"error": "The test source is already running."})
    console.pipeline.forget()
    return _json(202, {"switched": pattern} if switching else {"started": "source"})


def _refused(console: Console, action: str) -> Optional[Response]:
    _, situation = _snapshot(console)
    refusal = refusals(situation)[action]
    return _json(refusal.status, {"error": refusal.message}) if refusal else None


def _submit(console: Console, name: str, work: Work) -> Response:
    try:
        console.jobs.submit(name, work)
    except JobBusy as error:
        return _json(409, {"error": str(error)})
    return _json(202, {"started": name})


def route(method: str, path: str, query: dict, body: dict, console: Console) -> Response:
    """Handle one request. Returns (status, content type, body)."""
    if method == "GET":
        if path in ("/", "/index.html"):
            return _static("index.html")
        if SITE_FILE.fullmatch(path):
            return _static(path.lstrip("/"))
        if path.startswith("/fonts/"):
            return _static(path.lstrip("/"))
        if path == "/api/logs":
            tab = query.get("tab", ["all"])[0]
            if tab not in LOG_TABS:
                return _json(400, {"error": f"unknown log tab: {tab}"})
            after = int(query.get("after", ["0"])[0] or 0) or console.clock_ms() - LOOKBACK_MS
            return _json(200, _logs_payload(console, tab, after))
        if path == "/api/pipeline":
            offset = int(query.get("offset", ["0"])[0] or 0)
            return _json(200, _pipeline_payload(console, offset, query.get("job", [None])[0]))
        return _json(404, {"error": f"not found: {path}"})

    if method != "POST":
        return _json(405, {"error": f"method not allowed: {method}"})

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

        def go_live(log) -> None:
            start_workflow(console.mediaconnect, console.medialive, targets, log=log)

        def off_air(log) -> None:
            if console.source.running:
                console.source.stop()
                log("test source stopped")
            stop_workflow(console.mediaconnect, console.medialive, targets, log=log)

        return _submit(console, name, _after_job(console, go_live if name == "go-live" else off_air))

    if path in ("/api/source/start", "/api/source/pattern"):
        pattern = body.get("pattern") or DEFAULT_PATTERN
        if pattern not in PATTERN_IDS:
            return _json(400, {"error": f"Unknown pattern {pattern!r}; choose one of {', '.join(PATTERN_IDS)}."})
        if path.endswith("start"):
            refused = _refused(console, "source-start")
            if refused:
                return refused
        elif not console.source.running:
            return _json(409, {"error": "The test source is not running; pick the pattern and press Send test source."})
        return _start_source(console, pattern, switching=path.endswith("pattern"))

    if path == "/api/source/stop":
        console.source.stop()
        console.pipeline.forget()
        return _json(200, {"stopped": "source"})

    return _json(404, {"error": f"not found: {path}"})


class _Handler(BaseHTTPRequestHandler):
    console: Console
    server_version = "livectl"

    def do_GET(self) -> None:  # noqa: N802 - the name is fixed by BaseHTTPRequestHandler
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def log_message(self, fmt: str, *args) -> None:
        return  # the job log is the interesting output; an access log would bury it

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {}

        status, content_type, payload = route(method, parsed.path, parse_qs(parsed.query), body, self.console)
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            # The browser reloaded or navigated away while a poll was in flight.
            self.close_connection = True


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that does not print a traceback when a client hangs up."""

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def allowed_hosts(container: bool = False) -> set[str]:
    return LOOPBACK | {CONTAINER_HOST} if container else LOOPBACK


def make_server(console: Console, *, host: str = "127.0.0.1", port: int = 8765,
                container: bool = False) -> "_Server":
    """A bound console server. Refuses any address that is not loopback, except 0.0.0.0 inside a container."""
    if host not in allowed_hosts(container):
        raise ConsoleError(f"the console binds to loopback only, not {host!r} (it can destroy infrastructure)")
    handler = type("ConsoleHandler", (_Handler,), {"console": console})
    try:
        return _Server((host, port), handler)
    except OSError as error:
        raise ConsoleError(f"cannot listen on {host}:{port}: {error.strerror or error}") from error


def serve(console: Console, *, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True,
          container: bool = False, url: Optional[str] = None) -> None:
    """Run the console until interrupted. `url` is what to announce when the address people use differs (Docker)."""
    with make_server(console, host=host, port=port, container=container) as httpd:
        url = url or f"http://{host}:{httpd.server_address[1]}/"
        print(f"livectl console on {url}  (Ctrl-C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
        finally:
            console.source.stop()  # never leave FFmpeg pushing into a flow nobody is watching
