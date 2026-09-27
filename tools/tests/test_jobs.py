import io
import threading

import pytest

from livectl.jobs import FAILED, SUCCEEDED, JobBusy, JobRunner, command_job


class FakeProcess:
    """Stands in for subprocess.Popen so no process is ever started in a test."""

    def __init__(self, lines, code=0):
        self.stdout = io.StringIO("".join(f"{line}\n" for line in lines))
        self._code = code

    def wait(self):
        return self._code


def fake_popen(lines, code=0):
    return lambda *args, **kwargs: FakeProcess(lines, code)


def test_a_job_records_its_lines_and_succeeds():
    runner = JobRunner()

    def work(log):
        log("one")
        log("two")

    runner.submit("demo", work)
    runner.wait(5)

    summary = runner.summary()
    assert summary["state"] == SUCCEEDED
    assert summary["lines"] == ["one", "two"]
    assert summary["finished_at"] and summary["error"] is None


def test_a_failing_job_is_recorded_rather_than_lost_in_the_thread():
    runner = JobRunner()

    def work(log):
        log("starting")
        raise RuntimeError("boom")

    runner.submit("demo", work)
    runner.wait(5)

    summary = runner.summary()
    assert summary["state"] == FAILED
    assert summary["error"] == "boom"
    assert summary["lines"] == ["starting", "error: boom"]


def test_only_one_job_runs_at_a_time():
    runner = JobRunner()
    release = threading.Event()
    runner.submit("first", lambda log: release.wait(5))

    with pytest.raises(JobBusy, match="first is still running"):
        runner.submit("second", lambda log: None)

    release.set()
    runner.wait(5)
    assert runner.summary()["name"] == "first"


def test_a_finished_job_frees_the_runner():
    runner = JobRunner()
    runner.submit("first", lambda log: None)
    runner.wait(5)

    runner.submit("second", lambda log: log("ok"))
    runner.wait(5)

    assert runner.summary()["name"] == "second"


def test_summary_returns_only_the_lines_after_the_offset():
    runner = JobRunner()
    runner.submit("demo", lambda log: [log("one"), log("two"), log("three")])
    runner.wait(5)

    assert runner.summary(offset=2)["lines"] == ["three"]
    assert runner.summary()["offset"] == 3


def test_no_job_yet_summarises_as_nothing():
    assert JobRunner().summary() is None


def test_command_job_echoes_the_command_then_every_output_line():
    captured = []

    command_job(["terraform", "apply"], popen=fake_popen(["Plan: 1", "Apply complete"]))(captured.append)

    assert captured == ["$ terraform apply", "Plan: 1", "Apply complete"]


def test_command_job_raises_on_a_non_zero_exit():
    work = command_job(["terraform", "apply"], popen=fake_popen(["Error: no"], code=1))

    with pytest.raises(RuntimeError, match="exited with status 1"):
        work(lambda line: None)


def test_command_job_strips_terminal_colour_codes():
    captured = []
    coloured = "\x1b[0m\x1b[1mmodule.delivery.data.aws_cloudfront_cache_policy.disabled: Reading...\x1b[0m\x1b[0m"

    command_job(["terraform", "apply"], popen=fake_popen([coloured, "  \x1b[32m+\x1b[0m create"]))(captured.append)

    assert captured[1:] == ["module.delivery.data.aws_cloudfront_cache_policy.disabled: Reading...", "  + create"]


def test_an_offset_from_another_job_starts_the_new_job_from_its_first_line():
    """Live: the page asked for the teardown's lines from the previous job's offset and missed the first ones."""
    runner = JobRunner(clock=lambda: "2026-09-27T01:00:00+00:00")
    runner.submit("go-off-air", lambda log: [log(f"line {n}") for n in range(9)])
    runner.wait(5)
    old = runner.summary()["key"]
    runner._clock = lambda: "2026-09-27T01:05:00+00:00"
    runner.submit("teardown", lambda log: [log("$ terraform destroy"), log("Refreshing state...")])
    runner.wait(5)

    summary = runner.summary(offset=9, job=old)

    assert summary["key"] == "teardown@2026-09-27T01:05:00+00:00"
    assert summary["start"] == 0
    assert summary["lines"] == ["$ terraform destroy", "Refreshing state..."]


def test_an_offset_for_the_same_job_is_honoured_and_reported():
    runner = JobRunner()
    runner.submit("scan", lambda log: [log("a"), log("b"), log("c")])
    runner.wait(5)
    key = runner.summary()["key"]

    summary = runner.summary(offset=2, job=key)

    assert (summary["start"], summary["lines"]) == (2, ["c"])


def test_every_job_line_has_the_time_it_was_written():
    ticks = iter(["2026-09-27T02:00:00+00:00", "2026-09-27T02:00:01+00:00", "2026-09-27T02:00:05+00:00",
                  "2026-09-27T02:00:09+00:00"])
    runner = JobRunner(clock=lambda: next(ticks))
    runner.submit("go-live", lambda log: [log("starting the MediaConnect flow"), log("MediaConnect flow ACTIVE")])
    runner.wait(5)

    summary = runner.summary()

    assert summary["stamps"] == ["2026-09-27T02:00:01+00:00", "2026-09-27T02:00:05+00:00"]
    assert runner.summary(offset=1, job=summary["key"])["stamps"] == ["2026-09-27T02:00:05+00:00"]
