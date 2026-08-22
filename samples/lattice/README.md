# Lattice sample apps

Standalone sample programs demonstrating Anduril Lattice SDK concepts,
matching [Anduril's own "Sample apps" developer
docs](https://developer.anduril.com) page. Distinct from this repo's main
Lattice integration (`app/adapters/lattice.py` / `lattice_bridge.py`,
documented in the top-level README's "Downstream C2 integration"
section), which publishes *this drone tracker's own fused tracks* to
Lattice as Entities. Everything under this directory is independent of
the rest of the codebase -- none of it touches `app/`'s database, API, or
domain model (Track/Detection/Classification), and none of these scripts
are required to run the main app.

**Setup, common to all of these:**

```bash
pip install -r requirements-lattice.txt
export LATTICE_ENDPOINT=lattice-your_env_id.env.sandboxes.developer.anduril.com
export LATTICE_CLIENT_ID=...
export LATTICE_CLIENT_SECRET=...
export SANDBOXES_TOKEN=...          # only needed for Lattice Sandboxes
```

None of these have been exercised against a live Lattice environment in
this repo's own development -- there's no live Lattice Sandbox or
credentials available here to test against. Each script is verified
instead the same way `app/adapters/lattice_bridge.py` already is: real
field/method-name/type correctness checked against the actual installed
`anduril-lattice-sdk` package (constructing its real pydantic models,
not guessed-at shapes), with the SDK-independent logic behind each script
unit-tested directly. See each script's own docstring for specifics.

## Objects CLI (`objects_cli.py`)

A command-line interface for Lattice's [Objects
API](https://developer.anduril.com/reference/rest/objects/list-objects) --
upload, download, get metadata, list (with prefix filtering), and delete.
General-purpose file management, unconnected to entities/tracks:

```bash
python samples/lattice/objects_cli.py upload local/file.png uploaded/file.png
python samples/lattice/objects_cli.py list --prefix uploaded/
python samples/lattice/objects_cli.py download uploaded/file.png local/out.png
python samples/lattice/objects_cli.py metadata uploaded/file.png
python samples/lattice/objects_cli.py delete uploaded/file.png
```

TTL (time-to-live) on upload: the Objects API reference documents this,
but the exact query/header parameter isn't reproduced here without a live
endpoint to confirm the wire format against. `--extra-query key=value`
(repeatable) passes arbitrary extra query parameters through to any
operation once you've confirmed the real parameter name from that
reference.

## Entity visualizer (`entity_visualizer/`)

A simple web app rendering every entity in a Lattice environment on a
map -- `server.py` holds a `stream_entities` call open in a background
thread, maintaining an in-memory cache served as GeoJSON at
`GET /api/entities`; `static/index.html` is a Leaflet map polling that
endpoint. Different from this app's own `app/static/dashboard.html`
(which shows this app's own tracks from its own database, not Lattice):

```bash
python samples/lattice/entity_visualizer/server.py
# then open http://127.0.0.1:8090
```

## Integrate maritime AIS position data (`ais/`)

Publishes AIS (Automatic Identification System) vessel position reports
to Lattice as Entities, modeling each vessel with a periodically-updated
location -- see `ais.py`'s docstring for the (decoded, not raw NMEA)
record format, and `sample_vessels.jsonl` for a small bundled fixture
with fabricated coordinates/MMSIs to try this against without a real AIS
feed:

```bash
python samples/lattice/ais/ais_publisher.py samples/lattice/ais/sample_vessels.jsonl
python samples/lattice/ais/ais_publisher.py samples/lattice/ais/sample_vessels.jsonl --loop --interval 30
```

## Task an asset (`orbit_task/`)

Demonstrates defining and operating on a custom `Orbit` task -- circle
around a fixed point at a given radius/altitude for a duration. The
schema is documented in `orbit.proto` (for registering with a real
environment's Schema Registry); the Python client code in `spec.py`
doesn't need it compiled via protoc, since Lattice's REST/JSON transport
represents `google.protobuf.Any` as a plain `{"@type": ..., ...fields}`
object -- see that file's header comment.

Three programs, run together, demonstrate an automated tasking scenario:

```bash
# 1. A taskable asset, listening for Orbit tasks assigned to it:
python samples/lattice/orbit_task/orbit_asset.py --asset-id orbit-asset-1 \
    --start-lat 51.5 --start-lon -0.1

# 2. Watches the COP and auto-creates an Orbit task for each new
#    point-of-interest, assigned to the asset above:
python samples/lattice/orbit_task/auto_tasker.py --asset-id orbit-asset-1

# 3. Publishes a point-of-interest -- triggers auto_tasker.py, which
#    tasks orbit_asset.py to go orbit it:
python samples/lattice/orbit_task/publish_poi.py --lat 51.51 --lon -0.09 \
    --name "Unidentified contact"
```

**This is a simulation, clearly marked as one**: `orbit_asset.py` has no
real vehicle behind it -- it reports the real task status lifecycle
(`STATUS_EXECUTING` -> `STATUS_DONE_OK`, or `STATUS_DONE_NOT_OK` if
cancelled mid-orbit) and republishes its own entity position along the
simulated circle, but nothing physically moves. This is exactly the kind
of demo Anduril's own sample apps are -- see the top-level README's
"Downstream C2 integration" section for why the *main* drone tracker
(`app/adapters/lattice_bridge.py`) deliberately does **not** do this:
bolting a fake `Orbit` call onto the real tracking pipeline, with no real
commandable asset behind it, would misrepresent what that pipeline
actually does. A standalone, explicitly-a-simulation sample script under
`samples/` has no such risk -- it's not claiming to be anything else.
