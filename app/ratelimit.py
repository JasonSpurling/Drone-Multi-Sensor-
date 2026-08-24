"""A token-bucket rate limiter, keyed per sensor_id (and, for the
site-wide bucket, per str(site_id)).

This is a backstop against a malfunctioning or malicious sensor flooding
POST /api/detections, not a general-purpose limiter. Bucket state lives
in-process by default (fine for the common single-replica deployment,
resets on restart) -- but on PostgreSQL, RateLimiter transparently shares
bucket state through a `rate_limit_bucket` table instead, the same fix
app.cluster_lock applies to detection association: run two app replicas
behind a load balancer, and two in-process buckets for the same sensor_id
would each independently allow up to the configured rate, so the
*effective* limit becomes replicas x configured limit instead of the
configured limit -- silently defeating the whole backstop. On SQLite,
which this app treats as inherently single-process/single-deployment
anyway (see app.cluster_lock's docstring for the same reasoning), there's
no cross-process case to guard, so this stays the cheap in-process path.

The PostgreSQL path takes a `SELECT ... FOR UPDATE` row lock per key per
call (after an `INSERT ... ON CONFLICT DO NOTHING` that guarantees a row
exists to lock, closing the race where two replicas' very first request
for a brand-new key could otherwise both see "no bucket yet" and both
grant a token from the same initial burst) -- more round trips than the
in-process dict, but detection ingest already does several DB calls per
request, and correctness under concurrent replicas matters more here than
shaving one round trip off a rate-limit check. "Now" is read from the
database's own clock (clock_timestamp(), not each app replica's local
clock) so bucket math is never skewed by clock drift between hosts.
"""

from __future__ import annotations

import threading
import time

from sqlalchemy import text

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


def _refill(tokens: float, last_refill: float, now: float, rate_per_second: float, burst: float) -> float:
    elapsed = max(0.0, now - last_refill)
    return min(burst, tokens + elapsed * rate_per_second)


def _postgres_allow(key: str, rate_per_second: float, burst: float, now: float | None) -> bool:
    from app import db as db_module

    with db_module.engine.begin() as conn:
        if now is None:
            # psycopg2 returns EXTRACT(...)'s numeric result as a
            # decimal.Decimal, not a float -- left as-is, it can't be
            # subtracted from/compared with the plain floats the rest of
            # this module (and the in-process _TokenBucket path) uses.
            now = float(conn.execute(text("SELECT EXTRACT(EPOCH FROM clock_timestamp())")).scalar_one())
        # Guarantees a row exists to lock below, even on this key's very
        # first request -- without this, two replicas' simultaneous first
        # request for a brand-new key could each see "no bucket yet" and
        # each grant a token from the same initial burst before either
        # row exists to serialize against.
        conn.execute(
            text(
                "INSERT INTO rate_limit_bucket (key, tokens, last_refill) "
                "VALUES (:key, :burst, :now) ON CONFLICT (key) DO NOTHING"
            ),
            {"key": key, "burst": burst, "now": now},
        )
        row = conn.execute(
            text("SELECT tokens, last_refill FROM rate_limit_bucket WHERE key = :key FOR UPDATE"),
            {"key": key},
        ).mappings().one()
        tokens = _refill(row["tokens"], row["last_refill"], now, rate_per_second, burst)
        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0
        conn.execute(
            text("UPDATE rate_limit_bucket SET tokens = :tokens, last_refill = :now WHERE key = :key"),
            {"tokens": tokens, "now": now, "key": key},
        )
    return allowed


def _postgres_retry_after(key: str, rate_per_second: float, burst: float, now: float | None) -> float:
    """Pure peek, no row lock -- mirrors RateLimiter.retry_after's
    in-process semantics (doesn't consume a token or otherwise mutate
    state), called right after _postgres_allow already returned False for
    the same key/tick.
    """
    from app import db as db_module

    with db_module.engine.connect() as conn:
        if now is None:
            # psycopg2 returns EXTRACT(...)'s numeric result as a
            # decimal.Decimal, not a float -- left as-is, it can't be
            # subtracted from/compared with the plain floats the rest of
            # this module (and the in-process _TokenBucket path) uses.
            now = float(conn.execute(text("SELECT EXTRACT(EPOCH FROM clock_timestamp())")).scalar_one())
        row = conn.execute(
            text("SELECT tokens, last_refill FROM rate_limit_bucket WHERE key = :key"),
            {"key": key},
        ).mappings().fetchone()
    if row is None:
        return 0.0
    tokens = _refill(row["tokens"], row["last_refill"], now, rate_per_second, burst)
    if tokens >= 1.0:
        return 0.0
    return (1.0 - tokens) / rate_per_second


class RateLimiter:
    def __init__(self, rate_per_second: float, burst: float) -> None:
        self.rate_per_second = rate_per_second
        self.burst = burst
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = threading.Lock()

    def _use_postgres(self) -> bool:
        from app import db as db_module

        return db_module.engine.dialect.name == "postgresql"

    def allow(self, key: str, now: float | None = None) -> bool:
        if self._use_postgres():
            return _postgres_allow(key, self.rate_per_second, self.burst, now)

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
        if self._use_postgres():
            return _postgres_retry_after(key, self.rate_per_second, self.burst, now)

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
