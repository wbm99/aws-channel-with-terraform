import pytest

from livectl.cache import TtlCache


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def counter():
    def load():
        load.calls += 1
        return load.calls

    load.calls = 0
    return load


def test_a_fresh_value_is_served_without_loading_again():
    clock, load = FakeClock(), counter()
    cache = TtlCache(clock)

    assert cache.get("k", 5, load) == 1
    clock.now += 4.9
    assert cache.get("k", 5, load) == 1
    assert load.calls == 1


def test_an_expired_value_is_loaded_again():
    clock, load = FakeClock(), counter()
    cache = TtlCache(clock)
    cache.get("k", 5, load)

    clock.now += 5
    assert cache.get("k", 5, load) == 2


def test_a_failed_load_is_not_cached():
    cache = TtlCache(FakeClock())

    def boom():
        raise RuntimeError("throttled")

    with pytest.raises(RuntimeError):
        cache.get("k", 60, boom)
    assert cache.get("k", 60, lambda: "ok") == "ok"


def test_age_reports_how_old_the_value_is():
    clock = FakeClock()
    cache = TtlCache(clock)
    assert cache.age("k") is None

    cache.get("k", 60, lambda: 1)
    clock.now += 42
    assert cache.age("k") == 42


def test_clear_forgets_everything():
    clock, load = FakeClock(), counter()
    cache = TtlCache(clock)
    cache.get("k", 60, load)

    cache.clear()

    assert cache.get("k", 60, load) == 2
