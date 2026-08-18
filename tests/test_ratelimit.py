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
