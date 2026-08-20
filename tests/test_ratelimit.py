import pytest

from app.ratelimit import RateLimiter


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
