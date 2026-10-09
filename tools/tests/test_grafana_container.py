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
        detail = get(url + "/api/dashboards/uid/mediaconnect-detail")
        assert detail["meta"]["folderTitle"] == "Live pipeline"
        assert detail["dashboard"]["title"] == "MediaConnect source: transport stream & SRT"
        # DNS rebinding: a page whose own name resolves to 127.0.0.1 must not reach Grafana, which starts with
        # admin/admin and holds working AWS credentials. Grafana redirects any other Host to 127.0.0.1.
        port = int(url.rsplit(":", 1)[1])
        status, location = foreign_host("GET", port, "/login")
        assert status in (301, 302) and location.startswith("http://127.0.0.1"), (status, location)
        status, _ = foreign_host("POST", port, "/login", b'{"user": "admin", "password": "smoke"}')
        assert status != 200
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


def foreign_host(method: str, port: int, path: str, body: bytes = None) -> tuple:
    """A request as a rebound page would send it: to 127.0.0.1, under another name. Redirects are not followed."""
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path, body=body, headers={"Host": f"rebound.example:{port}",
                                                       "Content-Type": "application/json"})
        response = conn.getresponse()
        return response.status, response.getheader("Location") or ""
    finally:
        conn.close()
