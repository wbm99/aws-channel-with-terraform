"""`just ui` starts Grafana before the console and stops it after, and `just grafana-up` never blocks the console.
Docker and livectl are fakes (LIVECTL_DOCKER, LIVECTL) that record how they were called."""

import os
import shutil
import subprocess
import textwrap

import pytest

from test_grafana_files import ROOT

pytestmark = pytest.mark.skipif(not shutil.which("just"), reason="needs just")


def fake(path, body):
    path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    path.chmod(0o755)
    return path


@pytest.fixture
def run(tmp_path):
    """run(recipe, docker=..., up_code=0, console_code=0): `just recipe` with a fake docker and a fake livectl."""
    calls, args = tmp_path / "docker-calls.txt", tmp_path / "livectl-args.txt"
    docker = fake(tmp_path / "docker", f"""
        echo "$*" >> {calls}
        case "$*" in
          "compose up -d grafana") [[ "${{FAKE_UP_CODE:-0}}" == 0 ]] || {{ echo "Error: port is already allocated" >&2; exit "$FAKE_UP_CODE"; }} ;;
          "compose port grafana 3000") [[ -n "${{FAKE_NO_PORT:-}}" ]] || echo "127.0.0.1:3999" ;;
          "compose ps -q --status running grafana") echo "${{FAKE_RUNNING:-}}" ;;
        esac
    """)
    livectl = fake(tmp_path / "livectl", f"""
        echo "$*" > {args}
        exit "${{FAKE_CONSOLE_CODE:-0}}"
    """)

    def run_(recipe, *, docker_present=True, up_code=0, console_code=0, running="", no_port=""):
        env = {**os.environ, "LIVECTL": str(livectl), "FAKE_UP_CODE": str(up_code), "FAKE_RUNNING": running,
               "FAKE_NO_PORT": no_port,
               "FAKE_CONSOLE_CODE": str(console_code),
               "LIVECTL_DOCKER": str(docker) if docker_present else "no-such-docker-here"}
        result = subprocess.run(["just", recipe], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        result.docker_calls = calls.read_text().splitlines() if calls.exists() else []
        result.livectl_args = args.read_text().strip() if args.exists() else None
        return result

    return run_


def test_grafana_up_skips_when_docker_is_missing(run):
    result = run("grafana-up", docker_present=False)

    assert result.returncode == 0 and "Grafana skipped: docker not found" in result.stdout


def test_grafana_up_skips_when_compose_fails(run):
    result = run("grafana-up", up_code=1)

    assert result.returncode == 0
    assert result.stdout.strip() == "Grafana skipped: Error: port is already allocated"


def test_grafana_up_prints_the_dashboard_url(run):
    result = run("grafana-up")

    assert result.stdout.strip().splitlines()[-1] == "http://127.0.0.1:3999/d/mediaconnect-source"


def test_ui_passes_the_published_grafana_url(run):
    result = run("ui")

    assert "--grafana-url http://127.0.0.1:3999/d/mediaconnect-source" in result.livectl_args


def test_ui_without_grafana_passes_no_url(run):
    result = run("ui", docker_present=False)

    assert result.returncode == 0
    assert result.livectl_args == "ui --port 8765"


def test_ui_stops_grafana_when_the_console_exits(run):
    result = run("ui", console_code=3)

    assert "compose up -d grafana" in result.docker_calls
    assert result.docker_calls[-1] == "compose stop grafana"
    assert result.returncode == 3          # the console's exit code survives


def test_ui_with_a_failed_grafana_still_starts_the_console(run):
    result = run("ui", up_code=1)

    assert result.livectl_args == "ui --port 8765"


def test_ui_leaves_a_grafana_it_did_not_start_running(run):
    result = run("ui", running="0123abcd")       # already up, from `docker compose up`

    assert "--grafana-url http://127.0.0.1:3999/d/mediaconnect-source" in result.livectl_args
    assert "compose stop grafana" not in result.docker_calls


def test_grafana_up_skips_without_a_published_port(run):
    result = run("grafana-up", no_port="1")

    assert result.returncode == 0 and result.stdout.strip() == "Grafana skipped: Docker published no port for it"


def test_grafana_up_says_it_is_starting_before_a_possible_image_pull(run):
    result = run("grafana-up")

    assert "starting Grafana" in result.stderr
