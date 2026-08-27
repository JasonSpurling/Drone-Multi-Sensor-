import pytest

from app.ratelimit import RateLimiter, _postgres_allow, _postgres_retry_after


def test_allows_up_to_burst_immediately():
    limiter = RateLimiter(rate_per_second=1.0, burst=3.0)
    assert limiter.allow("sensor-1", now=0.0)
    assert limiter.allow("sensor-1", now=0.0)
    assert limiter.allow("sensor-1", now=0.0)
    assert limiter.allow("sensor-1", now=0.0) is False


def test_refills_over_time():
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    assert limiter.allow("sensor-1", now=0.0)
    assert limiter.allow("sensor-1", now=0.1) is False
    assert limiter.allow("sensor-1", now=1.1) is True


def test_buckets_are_independent_per_key():
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    assert limiter.allow("sensor-1", now=0.0)
    assert limiter.allow("sensor-1", now=0.0) is False
    assert limiter.allow("sensor-2", now=0.0) is True


def test_never_exceeds_burst_cap():
    limiter = RateLimiter(rate_per_second=100.0, burst=2.0)
    limiter.allow("sensor-1", now=0.0)
    # A huge elapsed time shouldn't accumulate unbounded tokens.
    assert limiter.allow("sensor-1", now=1000.0)
    assert limiter.allow("sensor-1", now=1000.0)
    assert limiter.allow("sensor-1", now=1000.0) is False


def test_retry_after_is_zero_for_an_unknown_key():
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    assert limiter.retry_after("never-seen", now=0.0) == 0.0


def test_retry_after_is_zero_when_a_token_is_available():
    limiter = RateLimiter(rate_per_second=1.0, burst=2.0)
    limiter.allow("sensor-1", now=0.0)  # drains one of two tokens -- one remains
    assert limiter.retry_after("sensor-1", now=0.0) == 0.0


def test_retry_after_matches_time_to_next_token():
    limiter = RateLimiter(rate_per_second=2.0, burst=1.0)
    assert limiter.allow("sensor-1", now=0.0)  # drains the single token
    assert limiter.allow("sensor-1", now=0.0) is False  # 0 tokens left
    # At rate 2/s, a full token takes 0.5s to refill.
    assert limiter.retry_after("sensor-1", now=0.0) == pytest.approx(0.5)


def test_retry_after_is_a_pure_peek():
    # Calling retry_after must not itself consume a token or change what
    # allow() would do next -- it's meant to be called after allow() has
    # already returned False, to describe that same rejected state.
    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    assert limiter.allow("sensor-1", now=0.0)
    assert limiter.allow("sensor-1", now=0.0) is False
    limiter.retry_after("sensor-1", now=0.0)
    limiter.retry_after("sensor-1", now=0.0)
    assert limiter.allow("sensor-1", now=1.0) is True  # unaffected by the peeks above


def test_uses_postgres_backed_buckets_when_the_engine_is_postgresql(isolated_db):
    # Regression test for the bug this exists to fix: on PostgreSQL,
    # RateLimiter must not fall back to its in-process dict -- that dict is
    # exactly what makes the effective limit `replicas x configured limit`
    # once more than one app process shares the same database.
    if isolated_db.dialect.name != "postgresql":
        pytest.skip("Requires DRONE_TEST_DATABASE_URL pointed at PostgreSQL")

    limiter = RateLimiter(rate_per_second=1.0, burst=1.0)
    assert limiter.allow("sensor-1") is True
    assert limiter._buckets == {}  # never touched the in-process path
    assert limiter.allow("sensor-1") is False  # the token was actually spent, in the DB


def test_postgres_backed_buckets_are_shared_across_separate_ratelimiter_instances(isolated_db):
    # The actual multi-replica scenario: two independent RateLimiter
    # objects (standing in for two app processes, each with their own
    # process-local instance but the same database) must share one
    # bucket's state, not each get their own `burst` tokens.
    if isolated_db.dialect.name != "postgresql":
        pytest.skip("Requires DRONE_TEST_DATABASE_URL pointed at PostgreSQL")

    replica_a = RateLimiter(rate_per_second=1.0, burst=1.0)
    replica_b = RateLimiter(rate_per_second=1.0, burst=1.0)

    assert replica_a.allow("sensor-1") is True  # spends the one shared token
    assert replica_b.allow("sensor-1") is False  # replica B sees it already spent


def test_postgres_allow_refills_over_time_using_an_explicit_now(isolated_db):
    if isolated_db.dialect.name != "postgresql":
        pytest.skip("Requires DRONE_TEST_DATABASE_URL pointed at PostgreSQL")

    key = "sensor-refill"
    assert _postgres_allow(key, rate_per_second=1.0, burst=1.0, now=0.0) is True
    assert _postgres_allow(key, rate_per_second=1.0, burst=1.0, now=0.1) is False
    assert _postgres_allow(key, rate_per_second=1.0, burst=1.0, now=1.1) is True


def test_postgres_retry_after_is_a_pure_peek(isolated_db):
    if isolated_db.dialect.name != "postgresql":
        pytest.skip("Requires DRONE_TEST_DATABASE_URL pointed at PostgreSQL")

    key = "sensor-peek"
    assert _postgres_allow(key, rate_per_second=1.0, burst=1.0, now=0.0) is True
    assert _postgres_allow(key, rate_per_second=1.0, burst=1.0, now=0.0) is False
    _postgres_retry_after(key, rate_per_second=1.0, burst=1.0, now=0.0)
    _postgres_retry_after(key, rate_per_second=1.0, burst=1.0, now=0.0)
    assert _postgres_allow(key, rate_per_second=1.0, burst=1.0, now=1.0) is True  # unaffected


def test_postgres_allow_survives_concurrent_first_requests_for_a_brand_new_key(isolated_db):
    # The narrow cold-start race this design closes: two "replicas"
    # racing to be the very first to touch a key must still only grant
    # `burst` tokens total, not `burst` tokens to each of them.
    if isolated_db.dialect.name != "postgresql":
        pytest.skip("Requires DRONE_TEST_DATABASE_URL pointed at PostgreSQL")
    import threading

    key = "sensor-cold-start-race"
    results: list[bool] = []
    results_lock = threading.Lock()

    def _attempt() -> None:
        allowed = _postgres_allow(key, rate_per_second=0.001, burst=1.0, now=0.0)
        with results_lock:
            results.append(allowed)

    threads = [threading.Thread(target=_attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 1  # exactly one of the 8 concurrent "first requests" wins
