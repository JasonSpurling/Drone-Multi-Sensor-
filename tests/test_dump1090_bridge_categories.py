"""app.adapters.dump1090_bridge's real ADS-B emitter category enrichment
-- the piece that lets the dashboard render distinct symbols per aircraft
type (rotorcraft/glider/light/heavy/UAV/...) instead of one generic
airplane glyph for every AIRCRAFT-classified track. See
app/models.py's Track.aircraft_category docstring for the real ICAO
category codes this is built from.
"""

import json
import urllib.error

from app.adapters.dump1090_bridge import (
    CategoryLookup,
    fetch_aircraft_categories,
    main,
    parse_aircraft_categories,
    stream_lines,
)


def test_parse_aircraft_categories_extracts_hex_and_category():
    aircraft_json = {
        "aircraft": [
            {"hex": "4CA593", "category": "A3", "flight": "BAW123"},
            {"hex": "AABBCC", "category": "A7"},
        ]
    }
    assert parse_aircraft_categories(aircraft_json) == {"4ca593": "A3", "aabbcc": "A7"}


def test_parse_aircraft_categories_omits_entries_without_a_category():
    # Most GA aircraft with older transponders never report a category --
    # omitted, not given a fabricated default.
    aircraft_json = {"aircraft": [{"hex": "4CA593"}, {"hex": "AABBCC", "category": ""}]}
    assert parse_aircraft_categories(aircraft_json) == {}


def test_parse_aircraft_categories_handles_empty_or_missing_list():
    assert parse_aircraft_categories({}) == {}
    assert parse_aircraft_categories({"aircraft": []}) == {}


def test_category_lookup_returns_none_when_no_url_configured():
    lookup = CategoryLookup(url=None)
    assert lookup.get("4ca593") is None


def test_category_lookup_fetches_and_caches(monkeypatch):
    calls = []

    def fake_fetch(url, timeout=5.0):
        calls.append(url)
        return {"4ca593": "A3"}

    monkeypatch.setattr("app.adapters.dump1090_bridge.fetch_aircraft_categories", fake_fetch)
    lookup = CategoryLookup(url="http://example/aircraft.json", refresh_interval_s=999)

    assert lookup.get("4ca593") == "A3"
    assert lookup.get("4CA593") == "A3"  # case-insensitive
    assert lookup.get("unknownhex") is None
    assert len(calls) == 1  # second/third .get() reused the cached fetch, within refresh_interval_s


def test_category_lookup_refreshes_after_interval(monkeypatch):
    responses = [{"4ca593": "A3"}, {"4ca593": "A7"}]

    def fake_fetch(url, timeout=5.0):
        return responses.pop(0)

    monkeypatch.setattr("app.adapters.dump1090_bridge.fetch_aircraft_categories", fake_fetch)
    lookup = CategoryLookup(url="http://example/aircraft.json", refresh_interval_s=0)

    assert lookup.get("4ca593") == "A3"
    assert lookup.get("4ca593") == "A7"  # refresh_interval_s=0 -- every get() re-fetches


def test_category_lookup_degrades_gracefully_on_fetch_failure(monkeypatch):
    def failing_fetch(url, timeout=5.0):
        raise OSError("connection refused")

    monkeypatch.setattr("app.adapters.dump1090_bridge.fetch_aircraft_categories", failing_fetch)
    lookup = CategoryLookup(url="http://example/aircraft.json", refresh_interval_s=999)

    # Never raises out to the caller -- a bridge script reading a live SBS-1
    # stream must keep posting position detections even if this optional
    # enrichment endpoint is unreachable.
    assert lookup.get("4ca593") is None


def test_category_lookup_keeps_stale_data_after_a_failed_refresh(monkeypatch):
    state = {"should_fail": False}

    def sometimes_failing_fetch(url, timeout=5.0):
        if state["should_fail"]:
            raise OSError("connection refused")
        return {"4ca593": "A3"}

    monkeypatch.setattr("app.adapters.dump1090_bridge.fetch_aircraft_categories", sometimes_failing_fetch)
    lookup = CategoryLookup(url="http://example/aircraft.json", refresh_interval_s=0)

    assert lookup.get("4ca593") == "A3"
    state["should_fail"] = True
    # A transient failure on the next refresh shouldn't wipe out the last
    # known-good category data -- stale-but-real beats silently blank.
    assert lookup.get("4ca593") == "A3"


class _FakeSocket:
    """A minimal stand-in for socket.socket good enough for stream_lines():
    each call to recv() returns the next chunk queued at construction,
    then b"" (connection closed) once exhausted -- the same "may deliver
    partial lines, ends with an empty recv" contract a real TCP socket has.
    """

    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    def recv(self, bufsize: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_stream_lines_yields_one_line_per_newline():
    sock = _FakeSocket([b"MSG,3,foo\nMSG,3,bar\n"])
    assert list(stream_lines(sock)) == ["MSG,3,foo", "MSG,3,bar"]


def test_stream_lines_reassembles_a_line_split_across_chunks():
    # A real TCP stream has no message-boundary guarantee -- one SBS-1
    # line can arrive in two separate recv() calls.
    sock = _FakeSocket([b"MSG,3,f", b"oo\n"])
    assert list(stream_lines(sock)) == ["MSG,3,foo"]


def test_stream_lines_stops_on_empty_recv_without_yielding_a_trailing_partial():
    # A line with no trailing newline when the connection closes is an
    # incomplete message -- correctly dropped, not yielded half-formed.
    sock = _FakeSocket([b"MSG,3,foo\nMSG,3,incomplete"])
    assert list(stream_lines(sock)) == ["MSG,3,foo"]


AIRBORNE_POSITION_LINE = (
    "MSG,3,1,1,4CA593,1,2026/08/18,12:34:56.789,2026/08/18,12:34:56.789,,"
    "38000,,,51.4700,-0.4543,,,,,,0"
)


def test_main_posts_each_parsed_line_and_merges_category(monkeypatch):
    posted = []
    monkeypatch.setattr(
        "app.adapters.dump1090_bridge.socket.create_connection",
        lambda addr: _FakeSocket([(AIRBORNE_POSITION_LINE + "\n").encode()]),
    )
    monkeypatch.setattr(
        "app.adapters.dump1090_bridge.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload) or {"track_id": 1},
    )
    monkeypatch.setattr(
        "app.adapters.dump1090_bridge.CategoryLookup.get", lambda self, hex_ident: "A3"
    )
    monkeypatch.setattr(
        "sys.argv", ["dump1090_bridge", "--sbs-host", "127.0.0.1", "--sbs-port", "30003"]
    )

    main()

    assert len(posted) == 1
    assert posted[0]["raw_data"]["category"] == "A3"


def test_main_keeps_reading_after_a_post_failure(monkeypatch, capsys):
    lines = (AIRBORNE_POSITION_LINE + "\n") * 2
    posted = []

    def failing_then_ok(url, payload, api_key, max_retries, retry_backoff_s):
        if not posted:
            posted.append(payload)
            raise urllib.error.URLError("connection refused")
        posted.append(payload)
        return {"track_id": 1}

    monkeypatch.setattr(
        "app.adapters.dump1090_bridge.socket.create_connection",
        lambda addr: _FakeSocket([lines.encode()]),
    )
    monkeypatch.setattr("app.adapters.dump1090_bridge.post_detection", failing_then_ok)
    monkeypatch.setattr("app.adapters.dump1090_bridge.CategoryLookup.get", lambda self, hex_ident: None)
    monkeypatch.setattr(
        "sys.argv",
        ["dump1090_bridge", "--sbs-host", "127.0.0.1", "--sbs-port", "30003", "--no-category-lookup"],
    )

    main()

    # Both lines were processed -- a failed POST for the first one doesn't
    # abort the stream, it just gets logged and the loop moves on.
    assert len(posted) == 2
    assert "ERROR posting detection" in capsys.readouterr().out


def test_main_skips_lines_that_dont_parse_into_a_position_message(monkeypatch):
    # A non-airborne-position SBS-1 message type (e.g. an identification-
    # only MSG,1 line) parses to None -- silently skipped, never posted.
    identification_line = (
        "MSG,1,1,1,4CA593,1,2026/08/18,12:34:56.789,2026/08/18,12:34:56.789,"
        "RYR123,,,,,,,,,,,\n"
    )
    posted = []
    monkeypatch.setattr(
        "app.adapters.dump1090_bridge.socket.create_connection",
        lambda addr: _FakeSocket([identification_line.encode()]),
    )
    monkeypatch.setattr(
        "app.adapters.dump1090_bridge.post_detection",
        lambda url, payload, api_key, max_retries, retry_backoff_s: posted.append(payload),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["dump1090_bridge", "--sbs-host", "127.0.0.1", "--sbs-port", "30003", "--no-category-lookup"],
    )

    main()

    assert posted == []


class _FakeAircraftJsonResponse:
    def __init__(self, body: dict):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self._body).encode()


def test_fetch_aircraft_categories_parses_a_real_response(monkeypatch):
    monkeypatch.setattr(
        "app.adapters.dump1090_bridge.urllib.request.urlopen",
        lambda url, timeout=5.0: _FakeAircraftJsonResponse(
            {"aircraft": [{"hex": "4CA593", "category": "A3"}]}
        ),
    )
    assert fetch_aircraft_categories("http://example/aircraft.json") == {"4ca593": "A3"}
