import io
import json
import threading

import pytest
import boto3

from conftest import make_workflow
from livectl.jobs import FAILED, SUCCEEDED, JobRunner
from livectl.server import Console, _Handler, _Server, make_server, route

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


def call(console, method, path, query=None, body=None, host="127.0.0.1:8765"):
    status, content_type, payload = route(method, path, query or {}, body or {}, console, host=host)
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


def test_check_clean_needs_no_stack_so_it_still_runs_after_a_destroy(aws):
    """The page keeps this button enabled when nothing is deployed; this is why that is safe."""
    console = make_console()

    status, _ = call(console, "POST", "/api/scan")
    console.jobs.wait(10)

    assert status == 202
    assert console.jobs.summary()["state"] == SUCCEEDED


def test_a_second_job_is_refused_with_409(aws):
    console = deployed_console()
    release = threading.Event()
    console.jobs.submit("first", lambda log: release.wait(5))

    status, payload = call(console, "POST", "/api/go-live")
    release.set()
    console.jobs.wait(5)

    assert status == 409 and "first is still running" in payload["error"]


def test_outputs_are_cached_between_polls_but_re_read_after_terraform(aws):
    runner = counting_runner()
    console = make_console(runner=runner, command=recording_command([]))

    call(console, "GET", "/api/pipeline")
    call(console, "GET", "/api/pipeline")
    assert len(runner.calls) == 1, "polling twice a second must not shell out to terraform each time"

    call(console, "POST", "/api/deploy")
    console.jobs.wait(5)
    call(console, "GET", "/api/pipeline")

    assert len(runner.calls) == 2, "the stack changed, so the outputs must be read again"


# --- scan for leftovers -------------------------------------------------------


def test_check_clean_fails_while_a_billable_resource_exists(aws):
    console = deployed_console()

    call(console, "POST", "/api/scan")
    console.jobs.wait(10)

    summary = console.jobs.summary()
    assert summary["state"] == FAILED
    assert any("MediaLive channel" in line for line in summary["lines"])


def test_check_clean_passes_when_nothing_is_left(aws):
    console = make_console()

    call(console, "POST", "/api/scan")
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
    handler.path = "/api/pipeline"
    handler.headers = {}
    handler.rfile = io.BytesIO(b"")
    handler.wfile = wfile
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /api/pipeline HTTP/1.1"
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


# --- control center ------------------------------------------------------------

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
    assert payload["actions"]["source-start"] is None, "on air, the test source can be sent"


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
    assert calls == [["terraform", "-chdir=envs/demo", "destroy", "-auto-approve", "-input=false", "-no-color"]]


def test_deploy_runs_terraform_apply(aws):
    calls = []
    console = make_console(command=recording_command(calls))

    status, _ = call(console, "POST", "/api/deploy")
    console.jobs.wait(5)

    assert status == 202
    assert calls == [["terraform", "-chdir=envs/demo", "apply", "-auto-approve", "-input=false", "-no-color"]]


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


def test_scripts_and_styles_are_served_with_their_types(aws):
    for name, kind in (("app.js", "text/javascript"), ("app.css", "text/css")):
        status, content_type, _ = route("GET", "/" + name, {}, {}, make_console())
        assert status == 200 and content_type.startswith(kind), name


def test_the_old_routes_are_gone(aws):
    for path in ("/api/start", "/api/stop", "/api/apply", "/api/check-clean"):
        assert call(make_console(), "POST", path)[0] == 404, path
    assert call(make_console(), "GET", "/api/status")[0] == 404


def test_make_server_refuses_a_network_interface(aws):
    import pytest
    from livectl.server import ConsoleError, make_server

    with pytest.raises(ConsoleError):
        make_server(make_console(), host="0.0.0.0", port=0)


def test_a_container_may_bind_every_interface(aws):
    from livectl.server import make_server

    server = make_server(make_console(), host="0.0.0.0", port=0, container=True)
    server.server_close()


def test_a_container_still_refuses_a_specific_network_address(aws):
    import pytest
    from livectl.server import ConsoleError, make_server

    with pytest.raises(ConsoleError):
        make_server(make_console(), host="192.168.1.5", port=0, container=True)


# --- logs and the test source --------------------------------------------------

from livectl.source import SourceProcess
from test_source import SECRET, FakeProcess, launcher


def source_console(flow="ACTIVE", channel="RUNNING", process=None, passphrase=SECRET):
    console = stub_console(flow=flow, channel=channel)
    console.source = SourceProcess(popen=launcher(process or FakeProcess(["frame= 1"])), script="send-srt.sh")
    console.read_passphrase = lambda arn: passphrase
    console.forget_targets()
    return console


def test_source_start_is_refused_while_the_flow_is_off(aws):
    status, payload = call(source_console(flow="STANDBY", channel="IDLE"), "POST", "/api/source/start")

    assert status == 409 and "go live first" in payload["error"]


def test_source_start_reads_the_passphrase_and_never_returns_it(aws):
    console = source_console()
    console.runner = lambda args: json.dumps(dict(OUTPUTS, passphrase_secret_arn={"value": "arn:secret"}))
    console.forget_targets()

    status, payload = call(console, "POST", "/api/source/start")
    _, pipeline = call(console, "GET", "/api/pipeline")

    assert status == 202
    assert SECRET not in json.dumps(payload) + json.dumps(pipeline)
    assert pipeline["source"]["state"] in ("starting", "running")
    console.source.stop()


def test_a_passphrase_that_cannot_be_read_is_a_readable_error(aws):
    console = source_console()
    console.runner = lambda args: json.dumps(dict(OUTPUTS, passphrase_secret_arn={"value": "arn:secret"}))
    console.forget_targets()

    def denied(arn):
        raise RuntimeError("AccessDeniedException")

    console.read_passphrase = denied
    status, payload = call(console, "POST", "/api/source/start")

    assert status == 502 and "Secrets Manager" in payload["error"]


def test_go_off_air_stops_the_source_first(aws):
    process = FakeProcess(["frame= 1"])
    console = deployed_console()
    console.source = SourceProcess(popen=launcher(process), script="send-srt.sh")
    console.source.start(host="h", port=5000, passphrase=SECRET)
    call(console, "POST", "/api/go-live")
    console.jobs.wait(10)

    call(console, "POST", "/api/go-off-air")
    console.jobs.wait(10)

    assert process.signals == ["TERM"]
    assert "test source stopped" in console.jobs.summary()["lines"][0]


def test_logs_without_a_stack_say_so(aws):
    status, payload = call(make_console(), "GET", "/api/logs", query={"tab": ["all"]})

    assert status == 200 and payload["lines"] == []
    assert "Nothing is deployed" in payload["notes"][0]


def test_an_unknown_tab_is_a_400(aws):
    assert call(make_console(), "GET", "/api/logs", query={"tab": ["nope"]})[0] == 400


def test_the_srt_tab_includes_the_redacted_ffmpeg_output(aws):
    from test_source import BANNER, settle

    process = FakeProcess([BANNER, "frame= 1"])
    console = source_console(process=process)
    console.source.start(host="h", port=5000, passphrase=SECRET)
    settle(console.source, "running")

    _, payload = call(console, "GET", "/api/logs", query={"tab": ["srt"], "after": ["1"]})

    texts = [line["text"] for line in payload["lines"]]
    assert any("passphrase=***" in t for t in texts)
    assert SECRET not in json.dumps(payload)
    process.finish()


def test_the_payload_names_the_next_step(aws):
    assert call(make_console(), "GET", "/api/pipeline")[1]["next"] == "deploy"
    assert call(stub_console(), "GET", "/api/pipeline")[1]["next"] == "go-live"
    assert call(stub_console(flow="ACTIVE", channel="RUNNING"), "GET", "/api/pipeline")[1]["next"] == "source-start"


def test_the_job_summary_says_how_far_terraform_has_got(aws):
    console = make_console(command=lambda args: lambda log: [
        log("Plan: 2 to add, 0 to change, 0 to destroy."),
        log("module.player.aws_s3_bucket.this: Creation complete after 1s [id=b]"),
    ])
    release = threading.Event()
    original = console.command

    def slow(args):
        work = original(args)
        return lambda log: (work(log), release.wait(5))

    console.command = slow
    call(console, "POST", "/api/deploy")
    import time
    time.sleep(0.2)

    progress = call(console, "GET", "/api/pipeline")[1]["job"]["progress"]
    release.set()
    console.jobs.wait(5)

    assert progress == {"done": 1, "total": 2, "percent": 50, "label": "1 of 2 resources"}


# --- source settings -------------------------------------------------------------------------------------

from livectl.source import CHOICES, SourceSettings  # noqa: E402


def sending_console(*processes):
    """A console on air whose source launches the given fake processes in turn, recording each launch's env."""
    console = source_console()
    queue, launches = iter(processes), []

    def popen(args, **kwargs):
        launches.append(kwargs["env"])
        return next(queue)

    console.source = SourceProcess(popen=popen, script="send-srt.sh")
    console.runner = lambda args: json.dumps(dict(OUTPUTS, passphrase_secret_arn={"value": "arn:secret"}))
    console.forget_targets()
    return console, launches


def test_the_payload_carries_the_settings_and_their_choices(aws):
    payload = call(make_console(), "GET", "/api/pipeline")[1]

    assert payload["source_settings"]["current"] == SourceSettings().to_dict()
    assert payload["source_settings"]["choices"] == CHOICES
    assert payload["source_settings"]["defaults"] == SourceSettings().to_dict()
    assert "patterns" not in payload


def test_settings_saved_while_stopped_start_nothing(aws):
    console, launches = sending_console()

    status, payload = call(console, "POST", "/api/source/settings", body={"fps": 25})

    assert (status, payload["saved"]) == (200, True) and payload["current"]["fps"] == 25
    assert console.settings.fps == 25 and launches == []


def test_settings_applied_while_running_restart_once_with_the_merged_settings(aws):
    first, second = FakeProcess(["frame= 1"]), FakeProcess(["frame= 1"])
    console, launches = sending_console(first, second)
    call(console, "POST", "/api/source/start")

    status, payload = call(console, "POST", "/api/source/settings", body={"fps": 50, "service_name": "Match 1"})

    assert status == 202 and payload["restarted"] is True
    assert len(launches) == 2 and first.signals == ["TERM"]
    assert (launches[1]["FPS"], launches[1]["SERVICE_NAME"], launches[1]["PATTERN"]) == ("50", "Match 1", "testcard")
    second.finish()


def test_settings_applied_after_the_source_exited_only_save_them(aws):
    from test_source import settle

    crashed = FakeProcess(["Connection failed"], code=1)
    console, launches = sending_console(crashed, FakeProcess(["frame= 1"]))
    call(console, "POST", "/api/source/start")
    crashed.finish()
    settle(console.source, "exited")

    status, _ = call(console, "POST", "/api/source/settings", body={"pattern": "smpte"})
    assert status == 200 and len(launches) == 1

    call(console, "POST", "/api/source/start")
    assert launches[1]["PATTERN"] == "smpte"
    console.source.stop()


def test_a_bad_setting_names_its_field(aws):
    console, _ = sending_console()

    status, payload = call(console, "POST", "/api/source/settings", body={"video_kbps": 20000})

    assert status == 400 and payload["field"] == "video_kbps" and "10000" in payload["error"]
    assert console.settings == SourceSettings()


def test_a_settings_body_that_is_not_an_object_is_a_400(aws):
    console, _ = sending_console()

    assert call(console, "POST", "/api/source/settings", body=["fps"])[0] == 400


def test_start_uses_the_saved_settings_and_ignores_its_body(aws):
    console, launches = sending_console(FakeProcess(["frame= 1"]))
    call(console, "POST", "/api/source/settings", body={"pattern": "pal"})

    status, _ = call(console, "POST", "/api/source/start", body={"pattern": "black"})

    assert status == 202 and launches[0]["PATTERN"] == "pal"
    console.source.stop()


def test_the_old_pattern_route_is_gone(aws):
    assert call(source_console(), "POST", "/api/source/pattern", body={"pattern": "black"})[0] == 404


def received_console(**state):
    console = stub_console(**state)
    console.mediaconnect = stub_aws(**state)["mediaconnect"]
    return console


def test_received_is_idle_while_the_flow_is_off(aws):
    status, payload = call(received_console(flow="STANDBY"), "GET", "/api/source/received")

    assert status == 200 and payload == {"state": "idle", "note": "The flow is not active."}


def test_received_reports_the_programs(aws):
    from stubs import source_metadata

    payload = call(received_console(flow="ACTIVE", metadata=source_metadata(name="Match 1")), "GET",
                   "/api/source/received")[1]

    assert payload["state"] == "ok" and payload["programs"][0]["name"] == "Match 1"


def test_received_reports_an_error_without_its_body(aws):
    payload = call(received_console(flow="ACTIVE", fail=("describe_flow_source_metadata",)), "GET",
                   "/api/source/received")[1]

    assert payload["state"] == "error" and "RuntimeError" in payload["note"]
    assert "stubbed failure" not in json.dumps(payload)


# --- identity --------------------------------------------------------------------

from botocore.exceptions import ClientError
from livectl.identity import IDENTITY_TTL
from stubs import StubClient, sts_client


def test_the_pipeline_payload_carries_the_identity(aws):
    _, payload = call(make_console(sts=sts_client("ok")), "GET", "/api/pipeline")

    assert payload["identity"]["kind"] == "ok"
    assert payload["identity"]["label"] == "acting as role LiveOps (william) · account 123456789012 · us-east-1"


def test_without_credentials_the_chain_is_not_polled(aws):
    runner = counting_runner()
    _, payload = call(make_console(sts=sts_client("no-credentials"), runner=runner), "GET", "/api/pipeline")

    assert payload["identity"]["kind"] == "no-credentials"
    assert payload["verdict"] == {"text": "No AWS credentials", "health": "bad"}
    assert payload["deployed"] is False and payload["nodes"] == []
    assert all(message for name, message in payload["actions"].items() if name != "source-stop")
    assert runner.calls == []  # terraform output reads the S3 state, which needs credentials too


def test_an_expired_session_recovers_after_login_without_a_restart(aws):
    logged_in = {"yes": False}

    def get_caller_identity():
        if not logged_in["yes"]:
            raise ClientError({"Error": {"Code": "ExpiredToken", "Message": "expired"}}, "GetCallerIdentity")
        return {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/LiveOps/william"}

    now = [1000.0]
    console = make_console(sts=StubClient(get_caller_identity=get_caller_identity), clock=lambda: now[0])

    assert call(console, "GET", "/api/pipeline")[1]["identity"]["kind"] == "expired"
    logged_in["yes"] = True
    assert call(console, "GET", "/api/pipeline")[1]["identity"]["kind"] == "expired"  # cached, not re-asked
    now[0] += IDENTITY_TTL
    assert call(console, "GET", "/api/pipeline")[1]["identity"]["kind"] == "ok"


def test_a_console_without_sts_behaves_as_before(aws):
    _, payload = call(make_console(), "GET", "/api/pipeline")

    assert payload["identity"] is None
    assert payload["verdict"]["text"] == "Not deployed"


def test_post_deploy_is_refused_without_credentials(aws):
    calls = []
    console = make_console(sts=sts_client("no-credentials"), command=recording_command(calls))

    status, payload = call(console, "POST", "/api/deploy")

    assert status == 403 and "No AWS credentials" in payload["error"]
    assert calls == []


def test_a_launch_that_fails_is_a_readable_error_and_does_not_block_the_next_start(aws):
    console, _ = sending_console()

    def missing(args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    console.source._popen = missing
    status, payload = call(console, "POST", "/api/source/start")

    assert status == 500 and "could not start" in payload["error"].lower()
    assert call(console, "GET", "/api/pipeline")[1]["source"]["state"] == "exited"


# --- an encoder of your own -----------------------------------------------------

def connection_console():
    console = source_console()
    console.runner = lambda args: json.dumps(dict(OUTPUTS, passphrase_secret_arn={"value": "arn:secret"}))
    console.forget_targets()
    return console


def test_the_connection_carries_the_passphrase_and_the_saved_latency_in_one_url(aws):
    console = connection_console()
    call(console, "POST", "/api/source/settings", body={"latency_ms": 250})

    status, payload = call(console, "POST", "/api/source/connection")

    assert status == 200
    ingest = console.targets().ingest_ip
    assert payload["address"] == f"srt://{ingest}:5000"
    assert payload["passphrase"] == SECRET and payload["latency_ms"] == 250 and payload["pbkeylen"] == 32
    assert payload["url"] == (f"srt://{ingest}:5000?mode=caller&passphrase={SECRET}&pbkeylen=32&pkt_size=1316"
                              "&latency=250000")


def test_the_passphrase_never_reaches_the_poll_or_the_logs(aws):
    console = connection_console()
    call(console, "POST", "/api/source/connection")

    _, pipeline = call(console, "GET", "/api/pipeline")
    _, logs = call(console, "GET", "/api/logs", query={"tab": ["all"]})

    assert SECRET not in json.dumps(pipeline) + json.dumps(logs)


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST", "[::1]:8765"])
def test_the_connection_is_answered_to_loopback_names(aws, host):
    status, _ = call(connection_console(), "POST", "/api/source/connection", host=host)

    assert status == 200


@pytest.mark.parametrize("host", ["rebound.example:8765", "192.168.1.20:8765", "127.0.0.1.nip.io", ""])
def test_the_connection_is_refused_to_any_other_host(aws, host):
    status, payload = call(connection_console(), "POST", "/api/source/connection", host=host)

    assert status == 403 and SECRET not in json.dumps(payload)


def test_the_connection_needs_the_ingest_outputs(aws):
    console = source_console()
    console.runner = lambda args: json.dumps({k: v for k, v in OUTPUTS.items() if k != "ingest_ip"})
    console.forget_targets()

    status, payload = call(console, "POST", "/api/source/connection")

    assert status == 409 and "Deploy stack" in payload["error"]


def test_the_served_console_checks_the_host_header_it_received(aws):
    import http.client

    server = make_server(connection_console(), port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        answers = {}
        for host in ("127.0.0.1", "rebound.example"):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
            conn.request("POST", "/api/source/connection", body=b"{}", headers={"Host": host})
            answers[host] = conn.getresponse().status
            conn.close()
    finally:
        server.shutdown()
        server.server_close()

    assert answers == {"127.0.0.1": 200, "rebound.example": 403}


# --- the Grafana link ----------------------------------------------------------

def test_the_grafana_link_is_an_endpoint_when_given(aws):
    console = stub_console()
    console.grafana_url = "http://127.0.0.1:3000/d/mediaconnect-source"

    _, payload = call(console, "GET", "/api/pipeline")

    assert payload["endpoints"]["grafana"] == "http://127.0.0.1:3000/d/mediaconnect-source"


def test_no_grafana_link_without_one(aws):
    _, payload = call(stub_console(), "GET", "/api/pipeline")

    assert payload["endpoints"]["grafana"] is None


def test_livectl_ui_takes_a_grafana_url():
    from livectl.cli import build_parser

    args = build_parser().parse_args(["ui", "--grafana-url", "http://127.0.0.1:3999/d/mediaconnect-source"])

    assert args.grafana_url == "http://127.0.0.1:3999/d/mediaconnect-source"
    assert build_parser().parse_args(["ui"]).grafana_url is None
