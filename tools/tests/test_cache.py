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


# --- losing the network (live run, 2026-09-27) --------------------------------------------------------

import threading  # noqa: E402
import time  # noqa: E402


def failing(message="EndpointConnectionError: Could not connect"):
    def load():
        load.calls += 1
        raise ConnectionError(message)

    load.calls = 0
    return load


def test_a_failed_refresh_serves_the_last_good_value_and_records_the_problem():
    clock = FakeClock()
    cache = TtlCache(clock)
    cache.get("k", 5, lambda: "good", stale_for=600)
    clock.now += 10

    assert cache.get("k", 5, failing(), stale_for=600) == "good"
    problem = cache.problems()["k"]
    assert "Could not connect" in problem[0] and problem[1] == 10


def test_a_value_too_old_to_trust_is_not_served():
    clock = FakeClock()
    cache = TtlCache(clock)
    cache.get("k", 5, lambda: "good", stale_for=60)
    clock.now += 61

    with pytest.raises(ConnectionError):
        cache.get("k", 5, failing(), stale_for=60)


def test_after_a_failure_the_network_is_not_retried_until_the_ttl_passes():
    clock = FakeClock()
    cache = TtlCache(clock)
    cache.get("k", 5, lambda: "good", stale_for=600)
    clock.now += 10
    load = failing()

    cache.get("k", 5, load, stale_for=600)
    clock.now += 2
    cache.get("k", 5, load, stale_for=600)

    assert load.calls == 1, "each retry would block a poll for the whole connect timeout"


def test_a_success_clears_the_problem():
    clock = FakeClock()
    cache = TtlCache(clock)
    cache.get("k", 5, lambda: "good", stale_for=600)
    clock.now += 10
    cache.get("k", 5, failing(), stale_for=600)
    clock.now += 10

    assert cache.get("k", 5, lambda: "back", stale_for=600) == "back"
    assert cache.problems() == {}


def test_only_one_thread_loads_a_key_the_others_reuse_its_result():
    """Without this, every two-second poll started another blocked AWS call while the network was down."""
    cache = TtlCache()
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.3)
        return "value"

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get("k", 5, slow))) for _ in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert calls == [1] and results == ["value"] * 5
