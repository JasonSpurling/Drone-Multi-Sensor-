"""A minimal in-memory token-bucket rate limiter, keyed per sensor_id.

This is a backstop against a malfunctioning or malicious sensor flooding
POST /api/detections, not a general-purpose limiter: state is in-process
(resets on restart, isn't shared across multiple app processes/replicas).
A multi-process deployment behind PostgreSQL would need this pushed into
something shared (Redis, or a Postgres-backed bucket) to hold across
replicas -- fine for the single-process deployment this app targets.
"""

from __future__ import annotations

import threading
import time

from app.config import (
    GLOBAL_RATE_LIMIT_BURST,
    GLOBAL_RATE_LIMIT_PER_SECOND,
    RATE_LIMIT_BURST,
    RATE_LIMIT_PER_SECOND,
)


class _TokenBucket:
    def __init__(self, rate_per_second: float, burst: float, now: float) -> None:
        self.rate_per_second = rate_per_second
        self.burst = burst
        self.tokens = burst
        self.last_refill = now

    def allow(self, now: float) -> bool:
        elapsed = max(0.0, now - self.last_refill)
        self.last_refill = now
        self.tokens = min(self.burst, self.tokens + elapsed * self.rate_per_second)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    def __init__(self, rate_per_second: float, burst: float) -> None:
        self.rate_per_second = rate_per_second
        self.burst = burst
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _TokenBucket(self.rate_per_second, self.burst, now)
                self._buckets[key] = bucket
            return bucket.allow(now)

    def retry_after(self, key: str, now: float | None = None) -> float:
        """Seconds until `key` would next have a token available -- a pure
        peek (doesn't consume a token or otherwise mutate state) for
        populating a 429 response's Retry-After header, called right after
        allow() has already returned False for the same key/tick.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                return 0.0
            elapsed = max(0.0, now - bucket.last_refill)
            tokens = min(bucket.burst, bucket.tokens + elapsed * bucket.rate_per_second)
            if tokens >= 1.0:
                return 0.0
            return (1.0 - tokens) / bucket.rate_per_second


detection_rate_limiter = RateLimiter(RATE_LIMIT_PER_SECOND, RATE_LIMIT_BURST)

# Keyed by str(site_id) rather than sensor_id -- see app/config.py's
# GLOBAL_RATE_LIMIT_PER_SECOND docstring for why this exists alongside
# (not instead of) the per-sensor limiter above.
site_detection_rate_limiter = RateLimiter(GLOBAL_RATE_LIMIT_PER_SECOND, GLOBAL_RATE_LIMIT_BURST)
