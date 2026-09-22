"""Serve the livectl operations console on the loopback interface.

Routing is a plain function, `route`, that takes the request and a `Console` holding the AWS
clients and the job runner. The HTTP class around it is a thin shell, so the tests exercise the
real routing with moto clients and a fake process launcher, without opening a socket.

The console can destroy infrastructure, so it binds to loopback only and never listens on a
network interface.
"""

from __future__ import annotations

import json
import sys
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from livectl.clean import find_leftovers
from livectl.control import start as start_workflow
from livectl.control import stop as stop_workflow
from livectl.jobs import JobBusy, JobRunner, Work, command_job
from livectl.status import get_status
from livectl.targets import Runner, TargetError, Targets, resolve_targets, run_command

SITE = Path(__file__).parent / "site"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
DESTROY_TOKEN = "destroy"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8"}

Response = tuple[int, str, bytes]


class ConsoleError(RuntimeError):
    """Raised when the console cannot be served."""


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
    _targets: Optional[Targets] = None

    def targets(self) -> Targets:
        """Terraform outputs, read once and cached until an apply or destroy invalidates them."""
        if self._targets is None:
            self._targets = resolve_targets(self.flow_arn, self.channel_id, self.tf_dir, self.runner)
        return self._targets

    def forget_targets(self) -> None:
        self._targets = None

    def terraform(self, *args: str) -> Sequence[str]:
        # -input=false so Terraform can never wait on a prompt nobody can see from a browser.
        return ["terraform", f"-chdir={self.tf_dir}", *args, "-input=false"]


def _json(status: int, payload: dict) -> Response:
    return status, "application/json; charset=utf-8", json.dumps(payload).encode()


def _static(name: str) -> Response:
    target = (SITE / name).resolve()
    if not target.is_file() or SITE.resolve() not in target.parents:
        return _json(404, {"error": f"not found: {name}"})
    return 200, CONTENT_TYPES.get(target.suffix, "application/octet-stream"), target.read_bytes()


def _status_payload(console: Console, offset: int = 0) -> dict:
    job = console.jobs.summary(offset)
    try:
        targets = console.targets()
    except TargetError as error:
        return {"deployed": False, "error": str(error), "job": job}

    payload: dict = {
        "deployed": True,
        "error": None,
        "job": job,
        "ingest": f"srt://{targets.ingest_ip}:{targets.ingest_port}" if targets.ingest_ip else None,
        "player": targets.player_url,
    }
    try:
        payload.update(get_status(console.mediaconnect, console.medialive, console.cloudwatch, targets))
    except Exception as error:  # missing credentials, a deleted resource: report it, do not 500
        payload["error"] = f"{type(error).__name__}: {error}"
    return payload


def _check_clean_work(console: Console) -> Work:
    def work(log) -> None:
        leftovers = find_leftovers(
            console.mediaconnect, console.medialive, console.mediapackagev2, console.cloudfront, console.prefix
        )
        for line in leftovers:
            log(line)
        if leftovers:
            raise RuntimeError(f"{len(leftovers)} billable resource(s) still exist")
        log("clean: nothing left")

    return work


def _forgetting_targets(console: Console, work: Work) -> Work:
    """Terraform changed the stack, so the cached outputs must not survive the job."""

    def wrapped(log) -> None:
        try:
            work(log)
        finally:
            console.forget_targets()

    return wrapped


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
        if path.startswith("/fonts/"):
            return _static(path.lstrip("/"))
        if path == "/api/status":
            offset = int(query.get("offset", ["0"])[0] or 0)
            return _json(200, _status_payload(console, offset))
        return _json(404, {"error": f"not found: {path}"})

    if method != "POST":
        return _json(405, {"error": f"method not allowed: {method}"})

    if path == "/api/check-clean":
        return _submit(console, "check-clean", _check_clean_work(console))

    if path in ("/api/apply", "/api/destroy"):
        action = path.rsplit("/", 1)[1]
        if action == "destroy" and body.get("confirm") != DESTROY_TOKEN:
            return _json(400, {"error": f'destroy requires {{"confirm": "{DESTROY_TOKEN}"}}'})
        work = console.command(console.terraform(action, "-auto-approve"))
        return _submit(console, action, _forgetting_targets(console, work))

    if path in ("/api/start", "/api/stop"):
        try:
            targets = console.targets()
        except TargetError as error:
            return _json(400, {"error": str(error)})
        action = start_workflow if path.endswith("start") else stop_workflow
        name = path.rsplit("/", 1)[1]
        return _submit(
            console,
            name,
            lambda log: action(console.mediaconnect, console.medialive, targets, log=log),
        )

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


def serve(console: Console, *, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    """Run the console until interrupted. Refuses any address that is not loopback."""
    if host not in LOOPBACK:
        raise ConsoleError(f"the console binds to loopback only, not {host!r} (it can destroy infrastructure)")

    handler = type("ConsoleHandler", (_Handler,), {"console": console})
    try:
        server = _Server((host, port), handler)
    except OSError as error:
        raise ConsoleError(f"cannot listen on {host}:{port}: {error.strerror or error}") from error

    with server as httpd:
        url = f"http://{host}:{port}/"
        print(f"livectl console on {url}  (Ctrl-C to stop)")
        if open_browser:
            webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
