"""scripts/load_test.py's error classification -- specifically that a 429
(the rate limiter correctly rejecting excess load) doesn't count as a
"hard error" the way a 5xx or a dropped connection does. This is what lets
CI's load-smoke job (.github/workflows/tests.yml) use --fail-on-errors to
catch a real concurrency bug without also failing every time the rate
limiter does its job under intentionally saturating load.
"""

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "load_test", Path(__file__).resolve().parent.parent / "scripts" / "load_test.py"
)
assert _SPEC is not None and _SPEC.loader is not None
load_test = importlib.util.module_from_spec(_SPEC)
sys.modules["load_test"] = load_test
_SPEC.loader.exec_module(load_test)


def test_a_201_is_neither_an_error_nor_a_hard_error():
    result = load_test._Result()
    result.record(0.01, ok=True, hard_error=False)
    assert result.errors == 0
    assert result.hard_errors == 0


def test_a_429_counts_as_an_error_but_not_a_hard_error():
    result = load_test._Result()
    result.record(0.01, ok=False, hard_error=False)
    assert result.errors == 1
    assert result.hard_errors == 0


def test_a_5xx_counts_as_both_an_error_and_a_hard_error():
    result = load_test._Result()
    result.record(0.01, ok=False, hard_error=True)
    assert result.errors == 1
    assert result.hard_errors == 1


def test_run_single_returns_zero_hard_errors_are_reflected_in_the_return_value(monkeypatch):
    """run_single/run_batch return hard_errors, not the (rate-limit-
    inclusive) errors count -- main()'s --fail-on-errors gate reads that
    return value directly.
    """

    class _FakeResponse:
        status_code = 429

    class _FakeSession:
        def __init__(self):
            self.headers = {}

        def post(self, *args, **kwargs):
            return _FakeResponse()

    monkeypatch.setattr(load_test.requests, "Session", _FakeSession)
    hard_errors = load_test.run_single("http://example.invalid", num_sensors=1, duration_s=0.05)
    assert hard_errors == 0
