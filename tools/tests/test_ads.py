from livectl.ads import AdBreaks
from livectl.manifest import DateRange, ManifestCheck


class Clocks:
    """A monotonic clock and a wall clock that move together."""

    def __init__(self):
        self.now = 100.0

    def mono(self):
        return self.now

    def ms(self):
        return int(1_760_184_000_000 + self.now * 1000)


def check(*ids):
    return ManifestCheck(500, True, 1.0, None, tuple(DateRange(i, "2026-10-11T12:00:00Z", 30.0) for i in ids))


def test_nothing_cued_has_no_status():
    clocks = Clocks()
    breaks = AdBreaks(clocks.mono, clocks.ms)
    assert breaks.status() is None
    assert breaks.seconds_left() is None


def test_a_new_daterange_marks_the_cue_seen():
    clocks = Clocks()
    breaks = AdBreaks(clocks.mono, clocks.ms)
    breaks.cued(42, 30, check("1"))
    clocks.now += 6
    breaks.observe(check("1", "2"))
    status = breaks.status()
    assert status["state"] == "seen"
    assert status["seen_at_ms"] - status["cued_at_ms"] == 6000
    assert status["event_id"] == 42 and status["seconds"] == 30


def test_an_old_daterange_does_not_count():
    clocks = Clocks()
    breaks = AdBreaks(clocks.mono, clocks.ms)
    breaks.cued(42, 30, check("1"))
    breaks.observe(check("1"))
    assert breaks.status()["state"] == "waiting"


def test_after_sixty_seconds_unseen_is_reported():
    clocks = Clocks()
    breaks = AdBreaks(clocks.mono, clocks.ms)
    breaks.cued(42, 30, None)
    clocks.now += 61
    breaks.observe(None)
    assert breaks.status()["state"] == "not_seen"


def test_a_marker_seen_once_stays_seen():
    clocks = Clocks()
    breaks = AdBreaks(clocks.mono, clocks.ms)
    breaks.cued(42, 30, check())
    breaks.observe(check("9"))
    clocks.now += 120
    breaks.observe(check())  # the break has left the playlist window
    assert breaks.status()["state"] == "seen"


def test_seconds_left_counts_down_then_clears():
    clocks = Clocks()
    breaks = AdBreaks(clocks.mono, clocks.ms)
    breaks.cued(42, 30, None)
    clocks.now += 10
    assert breaks.seconds_left() == 20.0
    assert breaks.status()["seconds_left"] == 20.0
    clocks.now += 25
    assert breaks.seconds_left() is None
