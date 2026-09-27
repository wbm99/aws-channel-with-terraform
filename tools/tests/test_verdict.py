import pytest

from livectl.pipeline import BAD, OFF, OK, ORDER, UNKNOWN, WARN, NodeStatus
from livectl.verdict import hourly_rate, verdict


def chain(**overrides):
    """Seven nodes, all off-air defaults, with (state, health) overrides by node id."""
    defaults = {
        "srt_source": (None, OFF), "mediaconnect_flow": ("STANDBY", OFF), "medialive_input": ("ATTACHED", OK),
        "medialive_channel": ("IDLE", OFF), "mediapackage_channel": ("IDLE", OFF),
        "cloudfront_cdn": ("Deployed", OK), "player": (None, OFF),
    }
    defaults.update(overrides)
    return {i: NodeStatus(id=i, title=i, state=s, health=h, summary="", checked_at="t")
            for i, (s, h) in ((i, defaults[i]) for i in ORDER)}


ON = dict(mediaconnect_flow=("ACTIVE", OK), medialive_channel=("RUNNING", OK))
RUNNING = {"name": "go-live", "state": "running"}


@pytest.mark.parametrize("nodes, job, text, health", [
    (None, None, "Not deployed", "off"),
    (None, {"name": "deploy", "state": "running"}, "Deploying…", "busy"),
    (chain(), {"name": "teardown", "state": "running"}, "Tearing down…", "busy"),
    (chain(), RUNNING, "Going live…", "busy"),
    (chain(**ON), {"name": "go-off-air", "state": "running"}, "Going off air…", "busy"),
    (chain(mediaconnect_flow=("ACTIVE", OK)), None, "Partly on", "bad"),
    (chain(medialive_channel=("RUNNING", OK)), None, "Partly on", "bad"),
    (chain(**ON, srt_source=("DISCONNECTED", BAD)), None, "On air · no source", "bad"),
    (chain(**ON, srt_source=("CONNECTED", WARN)), None, "On air · source degraded", "warn"),
    (chain(**{**ON, "srt_source": ("CONNECTED", OK), "medialive_channel": ("RUNNING", WARN)}), None,
     "On air · source degraded", "warn"),
    (chain(**ON, srt_source=("CONNECTED", OK), player=("STALLED", WARN)), None,
     "On air · not reaching viewers", "warn"),
    (chain(**ON, srt_source=("CONNECTED", OK), player=("PLAYING", OK)), None, "On air · playing", "ok"),
    (chain(), None, "Off air", "off"),
    (chain(mediaconnect_flow=(None, UNKNOWN), medialive_channel=(None, UNKNOWN)), None, "Unknown", "unknown"),
    (chain(), {"name": "scan", "state": "running"}, "Off air", "off"),
])
def test_verdict_table(nodes, job, text, health):
    result = verdict(nodes, job)

    assert (result.text, result.health) == (text, health)


def test_a_finished_job_does_not_hold_the_verdict():
    assert verdict(chain(), {"name": "go-live", "state": "succeeded"}).text == "Off air"


def test_nothing_hourly_bills_off_air():
    assert hourly_rate(chain()) == 0.0
    assert hourly_rate(None) == 0.0


def test_the_rate_adds_up_what_is_running():
    assert hourly_rate(chain(mediaconnect_flow=("ACTIVE", OK))) == pytest.approx(0.29)
    assert hourly_rate(chain(**ON)) == pytest.approx(1.485)
    assert hourly_rate(chain(**ON, mediapackage_channel=("RECEIVING", OK))) == pytest.approx(1.735)
