"""_RequestMetricsMiddleware (app/main.py) -- generic HTTP-layer counters/
histogram, distinct from the domain-specific ones in app/metrics.py.
Verifies both that a normal request is counted under its route *template*
(not raw path) and that an unmatched route doesn't leak an
attacker-controlled label into the metrics endpoint.

Prometheus's default registry (what these counters/histograms are
registered into) is a single module-level global that persists for the
whole pytest session -- unlike isolated_db, there's no per-test reset for
it. So every assertion here reads a specific metric line's own *value*
(via _metric_value) rather than asserting a whole line's exact text or
that some other line is absent -- a sibling test (or a completely
unrelated test file, also importing app.main) incrementing the same
counter for the same route earlier in the session is expected, not a bug.
"""

import re

from fastapi.testclient import TestClient

from app.main import app


def _metrics_text(client: TestClient) -> str:
    return client.get("/api/metrics").text


def _metric_value(body: str, metric_line_prefix: str) -> float:
    """The numeric value of one Prometheus exposition line, given its
    "name{labels}" prefix exactly as it appears in the output (before the
    trailing " <value>"). 0.0 if that series hasn't been observed at all
    yet in this process.
    """
    match = re.search(rf"^{re.escape(metric_line_prefix)} ([\d.eE+-]+)$", body, re.MULTILINE)
    return float(match.group(1)) if match else 0.0


def test_a_request_is_counted_under_its_route_template(isolated_db):
    with TestClient(app) as client:
        client.get("/api/health")
        body = _metrics_text(client)

    assert 'drone_http_requests_total{method="GET",path="/health",status="200"}' in body


def test_a_request_with_a_path_parameter_is_counted_under_the_template_not_the_raw_value(isolated_db):
    with TestClient(app) as client:
        client.get("/api/tracks/12345")
        body = _metrics_text(client)

    # The template, with {track_id} still a placeholder, not "12345" --
    # otherwise every distinct track id ever requested would mint a new
    # Prometheus time series, which is exactly the cardinality blowup
    # labeling by raw path would cause.
    assert "path=\"/tracks/{track_id}\"" in body
    assert 'path="/tracks/12345"' not in body


def test_an_unmatched_route_is_labeled_unmatched_not_its_raw_probed_path(isolated_db):
    with TestClient(app) as client:
        client.get("/api/this-route-does-not-exist")
        body = _metrics_text(client)

    assert 'path="unmatched"' in body
    assert "this-route-does-not-exist" not in body


def test_request_latency_histogram_records_a_sample_for_a_known_route(isolated_db):
    with TestClient(app) as client:
        client.get("/api/health")
        body = _metrics_text(client)

    assert 'drone_http_request_duration_seconds_count{method="GET",path="/health"}' in body


def test_an_unhandled_exception_is_counted_before_propagating(isolated_db, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("app.api.tracks.list_tracks", _boom)

    with TestClient(app, raise_server_exceptions=False) as client:
        before = _metric_value(
            _metrics_text(client), 'drone_http_exceptions_total{method="GET",path="/tracks"}'
        )
        before_200s = _metric_value(
            _metrics_text(client),
            'drone_http_requests_total{method="GET",path="/tracks",status="200"}',
        )

        response = client.get("/api/tracks")
        assert response.status_code == 500

        body = _metrics_text(client)
        after = _metric_value(body, 'drone_http_exceptions_total{method="GET",path="/tracks"}')
        after_200s = _metric_value(
            body, 'drone_http_requests_total{method="GET",path="/tracks",status="200"}'
        )

    assert after == before + 1
    # The failed request must not also show up as a normal 200 in
    # http_requests_total -- it never got that far.
    assert after_200s == before_200s
