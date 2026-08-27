"""app.adapters.dump1090_bridge's real ADS-B emitter category enrichment
-- the piece that lets the dashboard render distinct symbols per aircraft
type (rotorcraft/glider/light/heavy/UAV/...) instead of one generic
airplane glyph for every AIRCRAFT-classified track. See
app/models.py's Track.aircraft_category docstring for the real ICAO
category codes this is built from.
"""

from app.adapters.dump1090_bridge import CategoryLookup, parse_aircraft_categories


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
