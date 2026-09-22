import io
import json
import threading

import boto3

from conftest import make_workflow
from livectl.jobs import FAILED, SUCCEEDED, JobRunner
from livectl.server import Console, _Handler, _Server, route

OUTPUTS = {
    "flow_arn": {"value": "arn:aws:mediaconnect:us-east-1:123456789012:flow:1-abc:demo"},
    "medialive_channel_id": {"value": "7387208"},
    "player_url": {"value": "https://example.cloudfront.net/"},
    "ingest_ip": {"value": "203.0.113.20"},
    "ingest_port": {"value": 5000},
}


def not_deployed(args):
    """A terraform runner for a stack that does not exist yet."""
    return "{}"


def counting_runner():
    """A terraform runner that returns outputs and remembers how often it was asked."""

    def runner(args):
        runner.calls.append(list(args))
        return json.dumps(OUTPUTS)

    runner.calls = []
    return runner


def make_console(**overrides) -> Console:
    """A console wired to moto clients. Terraform is never run unless a test asks for it."""
    kwargs = dict(
        mediaconnect=boto3.client("mediaconnect"),
        medialive=boto3.client("medialive"),
        cloudwatch=boto3.client("cloudwatch"),
        mediapackagev2=boto3.client("mediapackagev2"),
        cloudfront=boto3.client("cloudfront"),
        jobs=JobRunner(),
        runner=not_deployed,
    )
    kwargs.update(overrides)
    return Console(**kwargs)


def deployed_console(**overrides) -> Console:
    targets = make_workflow()
    return make_console(flow_arn=targets.flow_arn, channel_id=targets.channel_id, **overrides)


def call(console, method, path, query=None, body=None):
    status, content_type, payload = route(method, path, query or {}, body or {}, console)
    if content_type.startswith("application/json"):
        return status, json.loads(payload)
    return status, payload


def recording_command(calls):
    """A stand-in for command_job that records the argv and runs nothing."""

    def factory(args):
        calls.append(list(args))
        return lambda log: log("$ " + " ".join(args))

    return factory


# --- static files -------------------------------------------------------------


def test_the_page_is_served_at_the_root(aws):
    status, payload = call(make_console(), "GET", "/")

    assert status == 200
    assert payload.startswith(b"<!doctype html>")


def test_the_font_is_served_from_disk_not_a_cdn(aws):
    status, content_type, payload = route("GET", "/fonts/InterVariable-subset.woff2", {}, {}, make_console())

    assert (status, content_type) == (200, "font/woff2")
    assert payload[:4] == b"wOF2"


def test_a_path_outside_the_site_directory_is_refused(aws):
    status, payload = call(make_console(), "GET", "/fonts/../../../../etc/passwd")

    assert status == 404


def test_an_unknown_path_is_a_readable_404(aws):
    status, payload = call(make_console(), "GET", "/nope")

    assert status == 404 and "not found" in payload["error"]


def test_an_unsupported_method_is_405(aws):
    status, payload = call(make_console(), "DELETE", "/api/status")

    assert status == 405


# --- status -------------------------------------------------------------------


def test_status_says_not_deployed_instead_of_failing(aws):
    status, payload = call(make_console(), "GET", "/api/status")

    assert status == 200
    assert payload["deployed"] is False
    assert "could not determine" in payload["error"]
    assert payload["job"] is None


def test_status_reports_flow_channel_and_endpoints(aws):
    status, payload = call(deployed_console(), "GET", "/api/status")

    assert status == 200
    assert payload["deployed"] is True
    assert (payload["flow"], payload["channel"]) == ("STANDBY", "IDLE")
    assert payload["error"] is None


# --- start and stop -----------------------------------------------------------


def test_start_brings_the_pipeline_up_and_reports_it(aws):
    console = deployed_console()

    status, payload = call(console, "POST", "/api/start")
    console.jobs.wait(10)

    assert (status, payload) == (202, {"started": "start"})
    assert console.jobs.summary()["state"] == SUCCEEDED
    assert call(console, "GET", "/api/status")[1]["channel"] == "RUNNING"


def test_stop_brings_the_pipeline_back_down(aws):
    console = deployed_console()
    call(console, "POST", "/api/start")
    console.jobs.wait(10)

    call(console, "POST", "/api/stop")
    console.jobs.wait(10)

    assert call(console, "GET", "/api/status")[1]["flow"] == "STANDBY"


def test_start_without_a_deployed_stack_is_a_readable_400(aws):
    status, payload = call(make_console(), "POST", "/api/start")

    assert status == 400 and "could not determine" in payload["error"]


def test_stop_without_a_deployed_stack_is_a_readable_400(aws):
    status, payload = call(make_console(), "POST", "/api/stop")

    assert status == 400 and "could not determine" in payload["error"]


def test_check_clean_needs_no_stack_so_it_still_runs_after_a_destroy(aws):
    """The page keeps this button enabled when nothing is deployed; this is why that is safe."""
    console = make_console()

    status, _ = call(console, "POST", "/api/check-clean")
    console.jobs.wait(10)

    assert status == 202
    assert console.jobs.summary()["state"] == SUCCEEDED


def test_a_second_job_is_refused_with_409(aws):
    console = deployed_console()
    release = threading.Event()
    console.jobs.submit("first", lambda log: release.wait(5))

    status, payload = call(console, "POST", "/api/start")
    release.set()
    console.jobs.wait(5)

    assert status == 409 and "first is still running" in payload["error"]


# --- terraform ----------------------------------------------------------------


def test_apply_runs_terraform_with_auto_approve_and_no_prompts(aws):
    calls = []
    console = make_console(command=recording_command(calls))

    status, payload = call(console, "POST", "/api/apply")
    console.jobs.wait(5)

    assert status == 202
    assert calls == [["terraform", "-chdir=envs/demo", "apply", "-auto-approve", "-input=false"]]


def test_destroy_is_refused_without_the_confirmation_token(aws):
    calls = []
    console = make_console(command=recording_command(calls))

    status, payload = call(console, "POST", "/api/destroy")

    assert status == 400
    assert "confirm" in payload["error"]
    assert calls == [], "terraform must not run without confirmation"


def test_destroy_is_refused_when_the_token_is_wrong(aws):
    calls = []
    console = make_console(command=recording_command(calls))

    status, _ = call(console, "POST", "/api/destroy", body={"confirm": "yes"})

    assert status == 400 and calls == []


def test_destroy_runs_once_the_token_matches(aws):
    calls = []
    console = make_console(command=recording_command(calls))

    status, _ = call(console, "POST", "/api/destroy", body={"confirm": "destroy"})
    console.jobs.wait(5)

    assert status == 202
    assert calls == [["terraform", "-chdir=envs/demo", "destroy", "-auto-approve", "-input=false"]]


def test_outputs_are_cached_between_polls_but_re_read_after_terraform(aws):
    runner = counting_runner()
    console = make_console(runner=runner, command=recording_command([]))

    call(console, "GET", "/api/status")
    call(console, "GET", "/api/status")
    assert len(runner.calls) == 1, "polling twice a second must not shell out to terraform each time"

    call(console, "POST", "/api/apply")
    console.jobs.wait(5)
    call(console, "GET", "/api/status")

    assert len(runner.calls) == 2, "the stack changed, so the outputs must be read again"


# --- check-clean --------------------------------------------------------------


def test_check_clean_fails_while_a_billable_resource_exists(aws):
    console = deployed_console()

    call(console, "POST", "/api/check-clean")
    console.jobs.wait(10)

    summary = console.jobs.summary()
    assert summary["state"] == FAILED
    assert any("MediaLive channel" in line for line in summary["lines"])


def test_check_clean_passes_when_nothing_is_left(aws):
    console = make_console()

    call(console, "POST", "/api/check-clean")
    console.jobs.wait(10)

    summary = console.jobs.summary()
    assert summary["state"] == SUCCEEDED
    assert summary["lines"] == ["clean: nothing left"]


# --- a client that hangs up ----------------------------------------------------


class BrokenWFile:
    """A socket whose peer has gone away."""

    def write(self, data):
        raise BrokenPipeError(32, "Broken pipe")

    def flush(self):
        pass


def make_handler(console, wfile):
    """A handler wired up by hand, so no socket is needed to exercise _dispatch."""
    handler = _Handler.__new__(_Handler)
    handler.console = console
    handler.path = "/api/status"
    handler.headers = {}
    handler.rfile = io.BytesIO(b"")
    handler.wfile = wfile
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /api/status HTTP/1.1"
    handler.close_connection = False
    handler._headers_buffer = []
    return handler


def test_a_browser_that_hangs_up_mid_response_is_not_an_error(aws):
    handler = make_handler(make_console(), BrokenWFile())

    handler._dispatch("GET")  # a reload during a poll must not raise

    assert handler.close_connection is True


def test_a_disconnected_client_prints_no_traceback(capsys):
    server = _Server.__new__(_Server)

    try:
        raise BrokenPipeError(32, "Broken pipe")
    except BrokenPipeError:
        server.handle_error(None, ("127.0.0.1", 51964))

    assert capsys.readouterr().err == ""


def test_an_unexpected_error_is_still_reported(capsys):
    server = _Server.__new__(_Server)

    try:
        raise RuntimeError("something actually broke")
    except RuntimeError:
        server.handle_error(None, ("127.0.0.1", 51964))

    assert "something actually broke" in capsys.readouterr().err
