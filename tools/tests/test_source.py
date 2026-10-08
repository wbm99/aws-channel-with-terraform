import subprocess
import threading
import time
from pathlib import Path

import pytest

from livectl.source import SourceBusy, SourceProcess

SECRET = "Zq7aXk2m9PbR4tLc8vN1sW6yH3dJ5fG0"
BANNER = f"Output #0, mpegts, to 'srt://203.0.113.20:5000?mode=caller&passphrase={SECRET}&pbkeylen=32':"


class FakeProcess:
    """A child whose output is fed line by line, and which can ignore SIGTERM."""

    def __init__(self, lines, *, code=0, ignores_term=False):
        self._lines, self._code, self._ignores_term = lines, code, ignores_term
        self._done = threading.Event()
        self.signals = []
        self.stdout = self._feed()

    def _feed(self):
        for line in self._lines:
            yield line + "\n"
        self._done.wait(10)

    def poll(self):
        return self._code if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return self._code

    def terminate(self):
        self.signals.append("TERM")
        if not self._ignores_term:
            self._code = -15
            self._done.set()

    def kill(self):
        self.signals.append("KILL")
        self._code = -9
        self._done.set()

    def finish(self):
        self._done.set()


def launcher(process):
    def popen(args, **kwargs):
        popen.args, popen.kwargs = args, kwargs
        return process

    return popen


def settle(source, state, timeout=2.0):
    deadline = time.monotonic() + timeout
    while source.status()["state"] != state and time.monotonic() < deadline:
        time.sleep(0.01)
    assert source.status()["state"] == state


def test_the_passphrase_goes_in_the_environment_never_the_command_line():
    process = FakeProcess([])
    popen = launcher(process)
    source = SourceProcess(popen=popen, script="send-srt.sh")

    source.start(host="203.0.113.20", port=5000, passphrase=SECRET)
    process.finish()

    assert SECRET not in " ".join(popen.args)
    assert popen.kwargs["env"]["SRT_PASSPHRASE"] == SECRET
    assert popen.kwargs["env"]["SRT_HOST"] == "203.0.113.20"


def test_ffmpegs_banner_never_reaches_the_buffer_with_the_passphrase():
    process = FakeProcess([BANNER, "frame=   30 fps= 30 q=23.0 size=  512kB"])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")

    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "running")

    text = "\n".join(line for _, line in source.lines(0))
    assert SECRET not in text
    assert "passphrase=***" in text
    process.finish()


def test_progress_lines_update_the_status_instead_of_flooding_the_log():
    process = FakeProcess([f"frame= {n} fps=30" for n in range(100)])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")

    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "running")
    time.sleep(0.05)

    assert source.lines(0) == []
    assert source.status()["progress"].startswith("frame=")
    process.finish()


def test_a_second_start_is_refused_while_running():
    process = FakeProcess(["frame= 1"])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")
    source.start(host="h", port=5000, passphrase=SECRET)

    with pytest.raises(SourceBusy):
        source.start(host="h", port=5000, passphrase=SECRET)
    process.finish()


def test_stop_terminates_and_reports_stopped():
    process = FakeProcess(["frame= 1"])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")
    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "running")

    source.stop()

    assert process.signals == ["TERM"]
    settle(source, "stopped")


def test_stop_kills_a_child_that_ignores_sigterm():
    process = FakeProcess(["frame= 1"], ignores_term=True)
    source = SourceProcess(popen=launcher(process), script="send-srt.sh", grace=0.05)
    source.start(host="h", port=5000, passphrase=SECRET)

    source.stop()

    assert process.signals == ["TERM", "KILL"]


def test_a_crash_is_reported_with_its_last_line():
    process = FakeProcess(["Connection to srt://h:5000 failed: Input/output error"], code=1)
    source = SourceProcess(popen=launcher(process), script="send-srt.sh")

    source.start(host="h", port=5000, passphrase=SECRET)
    process.finish()
    settle(source, "exited")

    status = source.status()
    assert status["exit_code"] == 1
    assert "failed" in status["last_error"]


def test_stop_with_nothing_running_is_a_no_op():
    SourceProcess(popen=launcher(FakeProcess([])), script="send-srt.sh").stop()


def test_the_buffer_is_bounded():
    process = FakeProcess([f"line {n}" for n in range(50)])
    source = SourceProcess(popen=launcher(process), script="send-srt.sh", max_lines=10)
    source.start(host="h", port=5000, passphrase=SECRET)
    process.finish()
    settle(source, "exited")

    assert [line for _, line in source.lines(0)][-1] == "line 49"
    assert len(source.lines(0)) == 10


def test_the_pattern_goes_to_the_script_and_is_reported():
    process = FakeProcess(["frame= 1"])
    popen = launcher(process)
    source = SourceProcess(popen=popen, script="send-srt.sh")

    source.start(host="h", port=5000, passphrase=SECRET, pattern="smpte")

    assert popen.kwargs["env"]["PATTERN"] == "smpte"
    assert source.status()["pattern"] == "smpte"
    process.finish()


def test_an_unknown_pattern_is_refused_before_anything_starts():
    popen = launcher(FakeProcess([]))
    source = SourceProcess(popen=popen, script="send-srt.sh")

    with pytest.raises(ValueError, match="unknown pattern"):
        source.start(host="h", port=5000, passphrase=SECRET, pattern="rainbow")
    assert not hasattr(popen, "args")


def test_a_stopped_source_can_be_started_again_at_once():
    first, second = FakeProcess(["frame= 1"]), FakeProcess(["frame= 1"])
    processes = iter([first, second])
    source = SourceProcess(popen=lambda args, **kwargs: next(processes), script="send-srt.sh")
    source.start(host="h", port=5000, passphrase=SECRET)

    source.stop()
    source.start(host="h", port=5000, passphrase=SECRET, pattern="black")   # no SourceBusy

    assert source.status()["pattern"] == "black"
    second.finish()


def test_the_script_is_found_from_the_working_directory_not_the_install():
    from livectl.source import SCRIPT

    assert SCRIPT == Path("source/send-srt.sh")
    assert not SCRIPT.is_absolute()


# --- settings ----------------------------------------------------------------------------------------------

import dataclasses  # noqa: E402
import json  # noqa: E402

from livectl.source import CHOICES, SettingsError, SourceSettings  # noqa: E402


def test_defaults_match_the_spec():
    assert SourceSettings().to_dict() == {
        "pattern": "testcard", "size": "1920x1080", "fps": 30, "video_kbps": 6000, "gop_seconds": 2,
        "audio_codec": "aac", "audio_kbps": 128, "tone": "1000", "service_name": "livectl test source",
        "service_provider": "livectl", "program_number": 1, "latency_ms": 120,
    }


def test_merged_changes_only_the_given_keys():
    merged = SourceSettings().merged({"fps": 25})

    assert merged.fps == 25
    assert dataclasses.replace(merged, fps=30) == SourceSettings()


def _accepted_values():
    for choice in CHOICES:
        if choice["kind"] == "enum":
            yield from ((choice["key"], value["id"]) for value in choice["values"])
        elif choice["kind"] == "range":
            yield from ((choice["key"], choice["min"]), (choice["key"], choice["max"]))


@pytest.mark.parametrize("key,value", list(_accepted_values()))
def test_every_enum_value_and_both_range_ends_are_accepted(key, value):
    assert getattr(SourceSettings().merged({key: value}), key) == value


@pytest.mark.parametrize("key,value", [
    ("video_kbps", 499), ("video_kbps", 10001), ("program_number", 0), ("program_number", 65536),
    ("latency_ms", 19), ("fps", 24), ("size", "3840x2160"), ("audio_codec", "opus"),
    ("service_name", "x" * 61), ("service_name", ""), ("service_name", "a\nb"),
    ("video_kbps", "6000"), ("video_kbps", True), ("video_kbps", None), ("colour", "red"),
])
def test_bad_values_are_refused_naming_the_field(key, value):
    with pytest.raises(SettingsError) as raised:
        SourceSettings().merged({key: value})

    assert raised.value.field == key
    assert str(raised.value) == raised.value.message


def test_a_non_ascii_name_counts_characters():
    assert SourceSettings().merged({"service_name": "Jogo ão vivo"}).service_name == "Jogo ão vivo"
    SourceSettings().merged({"service_name": "é" * 60})
    with pytest.raises(SettingsError):
        SourceSettings().merged({"service_name": "é" * 61})


def test_to_env_names_the_script_variables():
    assert SourceSettings().merged({"video_kbps": 500, "tone": "silence"}).to_env() == {
        "PATTERN": "testcard", "SIZE": "1920x1080", "FPS": "30", "BITRATE": "500k", "GOP_SECONDS": "2",
        "AUDIO_CODEC": "aac", "AUDIO_BITRATE": "128k", "TONE": "silence", "SERVICE_NAME": "livectl test source",
        "SERVICE_PROVIDER": "livectl", "PROGRAM_NUMBER": "1", "SRT_LATENCY_MS": "120",
    }


def test_choices_follow_the_fields_and_serialise():
    assert [c["key"] for c in CHOICES] == [f.name for f in dataclasses.fields(SourceSettings)]
    json.dumps(CHOICES)
