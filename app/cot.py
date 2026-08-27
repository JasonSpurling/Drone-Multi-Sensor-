"""Cursor on Target (CoT) output: the open XML event format the TAK
ecosystem (ATAK/WinTAK/iTAK, FreeTAKServer, and a wide public-safety and
military common-operating-picture user base -- TAK is also used by
wildland firefighting, search and rescue, and other civil agencies, not
exclusively the military) uses to share a common tactical picture.
Nothing in this app talked to that world before; this lets a track feed
into one.

Builds the XML directly with the standard library rather than taking on
`pytak` (the standard Python CoT/TAK client library) as a runtime
dependency for what's a handful of well-defined XML attributes -- but the
schema below (event version/type/uid/how/time/start/stale attributes; a
point child with lat/lon/hae/ce/le; a detail child with an optional
contact callsign) was verified against pytak's actual current source
(`cot_event()`/`serialize_cot()` in `pytak.functions`) during development,
not assumed from memory, and the output here was cross-checked to match
what pytak itself generates for the same inputs.

This only generates a CoT event and (see app/cot_publisher.py) sends it
outbound over plain UDP -- not a full TAK Server client (TLS mutual-auth
enrollment, packaged data transfer, etc.), which is real additional scope
a deployment with an actual TLS-secured TAK Server would need to add on
top of this. Plain UDP CoT broadcast is the simplest, most universally
supported transport (what ATAK's own default UDP input listens for).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

from app.models import Classification, Track

_COT_VAL_UNKNOWN = "9999999.0"  # CoT's own convention for "not specified"
_W3C_XML_DATETIME = "%Y-%m-%dT%H:%M:%S.%fZ"

# CoT's affiliation-domain type code takes the form "a-<affiliation>-<domain>".
# Domain "A" is Air. Affiliation is deliberately never "h" (hostile) here --
# that's a positive-identification declaration an operator makes, not
# something a sensor-fusion pipeline should auto-assert; an unclassified or
# suspicious drone reports as "u" (unknown) -- the correct default for
# "needs eyes on it," not a unilateral targeting declaration.
_AFFILIATION_BY_CLASSIFICATION = {
    Classification.FRIENDLY: "f",
    Classification.AIRCRAFT: "f",  # cooperative ADS-B transponder
    Classification.DRONE: "u",
    Classification.BIRD: "n",
    Classification.UNKNOWN: "u",
}


def classification_to_cot_type(classification: Classification) -> str:
    affiliation = _AFFILIATION_BY_CLASSIFICATION.get(classification, "u")
    return f"a-{affiliation}-A"


def _cot_time(offset_seconds: float = 0.0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).strftime(_W3C_XML_DATETIME)


def build_cot_xml(track: Track, stale_seconds: float = 60.0) -> bytes | None:
    """Returns a serialized CoT event for this track, or None if it has
    no resolved position to report. `stale_seconds` should roughly match
    how often you're actually re-sending updates for a live track -- a
    TAK client greys out/drops a track once its event goes stale.
    """
    if track.latitude is None or track.longitude is None:
        return None

    event = ET.Element(
        "event",
        {
            "version": "2.0",
            "type": classification_to_cot_type(track.classification),
            "uid": f"drone-multi-sensor.{track.track_uid}",
            "how": "m-g",
            "time": _cot_time(),
            "start": _cot_time(),
            "stale": _cot_time(stale_seconds),
        },
    )
    ET.SubElement(
        event,
        "point",
        {
            "lat": str(track.latitude),
            "lon": str(track.longitude),
            "hae": str(track.altitude_m) if track.altitude_m is not None else _COT_VAL_UNKNOWN,
            "ce": str(track.position_uncertainty_m) if track.position_uncertainty_m is not None else _COT_VAL_UNKNOWN,
            "le": _COT_VAL_UNKNOWN,
        },
    )
    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact", {"callsign": track.track_uid[:8]})

    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(event)
