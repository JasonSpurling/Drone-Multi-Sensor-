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

## Visual regression tests

`test_visual_regression.py` catches what the rest of this suite
structurally can't: a CSS change that leaves every selector, click target,
and ARIA attribute correct while quietly breaking the *layout* -- exactly
the shape of bug that shipped once already (`.rail-btn .rail-badge` was
positioned relative to the wrong ancestor because `.rail-btn` was missing
`position: relative`; every behavioral assertion about it still passed).

It screenshots a handful of small, static bits of UI chrome (the icon
rail with an active alert, the map-layer-toggle checkboxes) -- not the map
(real tile imagery, non-deterministic) and not the track list (live "Ns
ago" timestamps) -- and compares pixel-for-pixel against a baseline PNG in
`tests_e2e/visual_baselines/`, via `assert_visual_baseline`
(`conftest.py`). A small tolerance (0.5% of pixels, each needing to differ
by more than 24/255 in some channel) absorbs font-hinting/anti-aliasing
noise between runs without masking a real layout change.

**Updating a baseline** after an intentional UI change:

```bash
UPDATE_VISUAL_BASELINES=1 python -m pytest tests_e2e/test_visual_regression.py -v
```

This writes the new PNG(s) and skips (not passes) the test, so CI can't
silently accept an unreviewed baseline -- always `git diff`/open the PNG
in an image viewer to confirm it actually looks right before committing
it. A failing visual test also writes the actual screenshot and a diff
image to `tests_e2e/.visual_failures/` (gitignored) for the same kind of
review when a change is *not* expected.

These run in the same CI job as the rest of `tests_e2e/`, on the same
pinned Chromium version CI installs -- keeping browser and OS fixed is
what keeps the tolerance able to stay this tight without becoming flaky.

## Browser coverage

This suite only runs against Chromium -- a deliberate scope choice (one
browser to install and run in CI), not an oversight. It's worth naming
plainly, though: a dashboard CSS/layout bug specific to Firefox or Safari
would pass this suite and CI undetected. Nothing in the tests themselves
is Chromium-specific; to also run against Firefox or WebKit, change
`p.chromium.launch(...)` to `p.firefox.launch(...)`/`p.webkit.launch(...)`
in the `browser` fixture above (and `playwright install firefox`/`webkit`
instead of `chromium`).

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
