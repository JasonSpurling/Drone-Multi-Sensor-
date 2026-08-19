import json

from app.airspace.faa_notam import fetch_notams

SAMPLE_RESPONSE = {
    "items": [
        {
            "properties": {
                "coreNOTAMData": {
                    "notam": {
                        "number": "A1234/26",
                        "text": "UAS OPERATIONS PROHIBITED WI 1NM RADIUS",
                        "traditionalMessage": "!UAS A1234/26 ...",
                        "classification": "DOM",
                        "effectiveStart": "2026-08-01T00:00:00.000Z",
                        "effectiveEnd": "2026-09-01T00:00:00.000Z",
                        "icaoLocation": "EGLL",
                    }
                }
            }
        },
        {"properties": {}},  # malformed/unexpected entry -- must not crash the whole fetch
    ]
}


def test_fetch_notams_sends_credentials_as_headers(monkeypatch):
    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(SAMPLE_RESPONSE).encode()

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    notams = fetch_notams("my-client-id", "my-client-secret", lat=51.5, lon=-0.1, radius_nm=50)

    assert captured["headers"]["client_id"] == "my-client-id"
    assert captured["headers"]["client_secret"] == "my-client-secret"
    assert "locationLatitude=51.5" in captured["url"]
    assert "locationRadius=50" in captured["url"]
    assert "responseFormat=geoJson" in captured["url"]

    assert len(notams) == 1
    assert notams[0]["number"] == "A1234/26"
    assert notams[0]["icao_location"] == "EGLL"


def test_malformed_items_are_skipped_not_fatal(monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(SAMPLE_RESPONSE).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: _FakeResponse())

    notams = fetch_notams("id", "secret", lat=51.5, lon=-0.1)
    assert len(notams) == 1  # the malformed second item didn't crash or appear


def test_empty_items_returns_empty_list(monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"items": []}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: _FakeResponse())
    assert fetch_notams("id", "secret", lat=51.5, lon=-0.1) == []
