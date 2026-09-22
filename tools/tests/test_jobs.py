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
