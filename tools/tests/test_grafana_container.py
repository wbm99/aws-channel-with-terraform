"""Start the real Grafana service from compose.yaml, with no AWS credentials, and check it provisions the data source
and the dashboard. Needs Docker (skipped without it); the first run pulls the image."""

import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

import pytest

from test_grafana_files import ROOT


def _docker_answers() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


pytestmark = pytest.mark.skipif(not _docker_answers(), reason="needs Docker")
PASSWORD = "smoke"


def test_grafana_starts_and_provisions(tmp_path):
    # The real compose.yaml, as the current user, a fresh volume, an empty ~/.aws stand-in, a port Docker picks.
    (tmp_path / ".aws").mkdir()
    env = {**os.environ, "HOME": str(tmp_path), "GRAFANA_PORT": "0", "UID": str(os.getuid()),
           "GID": str(os.getgid()), "GRAFANA_ADMIN_PASSWORD": PASSWORD}
    project = f"livectl-grafana-test-{os.getpid()}"

    def compose(*args: str) -> str:
        done = subprocess.run(["docker", "compose", "-f", str(ROOT / "compose.yaml"), "-p", project, *args],
                              env=env, capture_output=True, text=True, timeout=600)
        assert done.returncode == 0, done.stderr
        return done.stdout + done.stderr

    try:
        compose("up", "-d", "grafana")
        url = "http://" + compose("port", "grafana", "3000").strip().splitlines()[-1]
        wait_for_health(url)

        assert get(url + "/api/datasources/uid/cloudwatch")["type"] == "cloudwatch"
        board = get(url + "/api/dashboards/uid/mediaconnect-source")
        assert board["meta"]["folderTitle"] == "Live pipeline"
        assert board["dashboard"]["title"] == "MediaConnect source"
        # Any error counts: with the provisioning folder mounted over the image's, a missing subfolder logs one.
        assert [line for line in compose("logs", "grafana").splitlines() if "level=error" in line] == []
    finally:
        subprocess.run(["docker", "compose", "-f", str(ROOT / "compose.yaml"), "-p", project, "down", "-v"],
                       env=env, capture_output=True, timeout=300)


def get(url: str) -> dict:
    auth = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": "Basic " + auth})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def wait_for_health(url: str, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=5) as response:
                if json.load(response).get("database") == "ok":
                    return
        except (urllib.error.URLError, ConnectionError, ValueError):
            pass
        assert time.monotonic() < deadline, "Grafana did not become healthy"
        time.sleep(1)
