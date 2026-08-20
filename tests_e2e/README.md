# Browser-driven dashboard tests

Unlike `tests/` (unit + API tests against `TestClient`'s in-process ASGI
transport, no browser involved), these actually launch the dashboard
(`app/static/dashboard.html`) in a real Chromium via
[Playwright](https://playwright.dev/python/) and click around in it --
select a track, drag the Playback scrubber, toggle map layers -- the way
someone testing the feature manually would.

## Running locally

```bash
pip install -r requirements-e2e.txt
playwright install chromium
python -m pytest tests_e2e/ -v
```

`conftest.py`'s `live_server` fixture starts the real app as a subprocess
(not `TestClient` -- Playwright needs an actual socket to connect to)
against a fresh temporary SQLite database per test, and tears it down
after. Leaflet's CDN requests (`unpkg.com`) are redirected to the copy
vendored in `tests_e2e/vendor/leaflet/` so the suite doesn't depend on a
third-party CDN being reachable, fast, or unrate-limited in CI -- the
dashboard's own behavior doesn't depend on which copy of Leaflet 1.9.4 it
loads. Map *tiles* (background imagery) still come from the real internet
and aren't asserted on directly; `test_dashboard_loads_with_no_console_errors`
specifically excludes bare network-failure console messages so a slow/
rate-limited tile CDN can't make this suite flaky for reasons unrelated to
whether the dashboard actually works.

If you need to point at a pre-vendored Chromium build at a nonstandard
path (e.g. a sandboxed environment with restricted network egress), set
`PLAYWRIGHT_CHROMIUM_PATH` to its executable -- unset by default, so real
CI (which runs `playwright install chromium` fresh) is unaffected.

## Updating the vendored Leaflet copy

If `app/static/dashboard.html`'s pinned Leaflet version
(`<link>`/`<script>` tags near the top) ever changes, update
`tests_e2e/vendor/leaflet/` to match:

```bash
npm install leaflet@<version> --prefix /tmp/leaflet_install
cp /tmp/leaflet_install/node_modules/leaflet/dist/leaflet.js tests_e2e/vendor/leaflet/
cp /tmp/leaflet_install/node_modules/leaflet/dist/leaflet.css tests_e2e/vendor/leaflet/
cp /tmp/leaflet_install/node_modules/leaflet/dist/images/*.png tests_e2e/vendor/leaflet/images/
```
