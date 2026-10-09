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


def test_the_settings_reach_the_script_environment():
    from livectl.source import SourceSettings

    process = FakeProcess(["frame= 1"])
    popen = launcher(process)
    source = SourceProcess(popen=popen, script="send-srt.sh")

    source.start(host="h", port=5000, passphrase=SECRET, settings=SourceSettings().merged({"fps": 50}))

    assert popen.kwargs["env"]["FPS"] == "50"
    assert popen.kwargs["env"]["PATTERN"] == "testcard"
    assert source.status()["settings"]["fps"] == 50
    process.finish()


def test_a_stopped_source_can_be_started_again_at_once():
    from livectl.source import SourceSettings

    first, second = FakeProcess(["frame= 1"]), FakeProcess(["frame= 1"])
    processes = iter([first, second])
    source = SourceProcess(popen=lambda args, **kwargs: next(processes), script="send-srt.sh")
    source.start(host="h", port=5000, passphrase=SECRET)

    source.stop()
    source.start(host="h", port=5000, passphrase=SECRET,  # no SourceBusy
                 settings=SourceSettings().merged({"pattern": "black"}))

    assert source.status()["settings"]["pattern"] == "black"
    second.finish()


class StubbornProcess(FakeProcess):
    """Ignores SIGTERM and whose SIGKILL does not end its output at once: its reader outlives stop()'s wait."""

    def __init__(self, lines):
        super().__init__(lines, ignores_term=True)

    def kill(self):
        self.signals.append("KILL")
        self._code = -9

    def wait(self, timeout=None):
        if self.signals and self.signals[-1] == "KILL":
            return -9  # the kernel reaped it; its pipe has not been drained yet
        return super().wait(timeout)


def test_a_late_reader_from_the_old_process_changes_nothing():
    newsecret = "N3wS3cr3tN3wS3cr3tN3wS3cr3tN3wS3"
    old = StubbornProcess(["frame= 1"])
    new = FakeProcess(["frame= 1", f"to 'srt://h:5000?passphrase={newsecret}'"])
    processes = iter([old, new])
    source = SourceProcess(popen=lambda args, **kwargs: next(processes), script="send-srt.sh", grace=0.05)
    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "running")
    source.stop()

    source.start(host="h", port=5000, passphrase=newsecret)
    settle(source, "running")
    old.finish()           # the old reader drains and ends only now
    time.sleep(0.1)

    status = source.status()
    assert status["state"] == "running" and status["exit_code"] is None
    text = "\n".join(line for _, line in source.lines(0))
    assert newsecret not in text and "passphrase=***" in text
    new.finish()


def test_two_restarts_at_once_run_one_after_the_other():
    live, most, launches = [0], [0], []
    lock = threading.Lock()

    class Counted(FakeProcess):
        def terminate(self):
            with lock:
                live[0] -= 1
            super().terminate()

    def popen(args, **kwargs):
        with lock:
            live[0] += 1
            most[0] = max(most[0], live[0])
            launches.append(kwargs["env"]["FPS"])
        time.sleep(0.02)  # widen the window two unserialised restarts would need to overlap
        return Counted(["frame= 1"])

    from livectl.source import SourceSettings

    source = SourceProcess(popen=popen, script="send-srt.sh")
    source.start(host="h", port=5000, passphrase=SECRET)
    threads = [threading.Thread(target=source.restart, kwargs=dict(
        host="h", port=5000, passphrase=SECRET, settings=SourceSettings().merged({"fps": fps}))) for fps in (25, 50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert len(launches) == 3 and most[0] == 1
    assert source.status()["settings"]["fps"] == int(launches[-1])
    source.stop()


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


def test_stop_during_a_restart_wins():
    """Stop pressed while an Apply is restarting: the source ends stopped, not running on the new settings."""
    from livectl.source import SourceSettings

    slow, fresh = FakeProcess(["frame= 1"], ignores_term=True), FakeProcess(["frame= 1"])
    processes = iter([slow, fresh])
    source = SourceProcess(popen=lambda args, **kwargs: next(processes), script="send-srt.sh", grace=0.3)
    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "running")
    restart = threading.Thread(target=source.restart, kwargs=dict(
        host="h", port=5000, passphrase=SECRET, settings=SourceSettings().merged({"fps": 25})))
    restart.start()
    time.sleep(0.05)       # the restart is waiting for the old FFmpeg to exit

    source.stop()          # pressed after Apply: waits for the restart, then stops what it started
    restart.join(5)

    assert source.status()["state"] == "stopped"
    assert fresh.signals == ["TERM"]


def test_a_launch_that_fails_does_not_leave_the_source_starting():
    def popen(args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "source/send-srt.sh")

    source = SourceProcess(popen=popen, script="source/send-srt.sh")

    with pytest.raises(OSError):
        source.start(host="h", port=5000, passphrase=SECRET)

    status = source.status()
    assert status["state"] == "exited" and "No such file" in status["last_error"]
    assert not source.running


REFUSED = ["[srt @ 0x5581] Connection setup failure: connection rejected",
           "[out#0/mpegts @ 0x5582] Error opening output srt://h:5000: Input/output error"]


def refused():
    process = FakeProcess(REFUSED, code=251)
    process.finish()
    return process


def test_a_restart_the_listener_refuses_is_retried_until_it_connects():
    """MediaConnect may still hold the old SRT connection right after an Apply; the new FFmpeg tries again."""
    from livectl.source import SourceSettings

    old, fresh = FakeProcess(["frame= 1"]), FakeProcess(["frame= 1"])
    processes = iter([old, refused(), refused(), fresh])
    source = SourceProcess(popen=lambda args, **kwargs: next(processes), script="send-srt.sh",
                           retry_delays=(0, 0, 0))
    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "running")

    source.restart(host="h", port=5000, passphrase=SECRET, settings=SourceSettings().merged({"fps": 25}))
    settle(source, "running")

    assert source.status()["settings"]["fps"] == 25
    assert [text for _, text in source.lines(0) if "trying again" in text] == [
        "SRT connection refused; trying again in 0 s (attempt 2 of 4)",
        "SRT connection refused; trying again in 0 s (attempt 3 of 4)",
    ]
    source.stop()
    assert fresh.signals == ["TERM"]


def test_a_source_refused_every_time_is_reported_exited_after_the_last_attempt():
    launches = []

    def popen(args, **kwargs):
        launches.append(args)
        return refused()

    source = SourceProcess(popen=popen, script="send-srt.sh", retry_delays=(0, 0, 0))
    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "exited")

    assert len(launches) == 4
    status = source.status()
    assert status["exit_code"] == 251 and "Input/output error" in status["last_error"]


def test_stop_during_the_pause_before_a_retry_cancels_it():
    launches = []

    def popen(args, **kwargs):
        launches.append(args)
        return refused()

    source = SourceProcess(popen=popen, script="send-srt.sh", retry_delays=(30, 30, 30))
    source.start(host="h", port=5000, passphrase=SECRET)
    deadline = time.monotonic() + 2
    while not any("trying again" in text for _, text in source.lines(0)) and time.monotonic() < deadline:
        time.sleep(0.01)

    started = time.monotonic()
    source.stop()

    assert time.monotonic() - started < 2       # the 30 s pause was cut short
    assert source.status()["state"] == "stopped" and not source.running
    time.sleep(0.05)
    assert len(launches) == 1


def test_a_failure_after_the_first_frame_is_not_retried():
    launches = []

    def popen(args, **kwargs):
        launches.append(args)
        process = FakeProcess(["frame= 1", *REFUSED], code=251)
        process.finish()
        return process

    source = SourceProcess(popen=popen, script="send-srt.sh", retry_delays=(0, 0, 0))
    source.start(host="h", port=5000, passphrase=SECRET)
    settle(source, "exited")

    assert len(launches) == 1
