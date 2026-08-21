"""RF signature fingerprinting: classifies an RF detection's signal
characteristics (center frequency, channel bandwidth, whether it's
frequency-hopping) against a library of drone control/video link
signatures, instead of trusting a single flat RF "confidence" number.

This matches how real counter-drone RF sensors actually work: RF signal
intelligence systems classify frequency band, channel bandwidth, and
hopping behavior against known signature libraries for common link types
(DJI OcuSync/Lightbridge, analog FPV video, Wi-Fi-based FPV/control) --
not by decoding encrypted proprietary protocol content. This module does
exactly that: pattern matching against RF envelope characteristics,
nothing more. It cannot decode a link's payload, extract telemetry, or
identify a specific aircraft -- only that its RF envelope is consistent
with a known type of control/video link.

BUILT_IN_SIGNATURES below are drawn from published consumer/hobbyist RF
specifications (ISM/license-free 2.4 GHz and 5.8 GHz band usage) and are
approximate band/bandwidth windows *by protocol family*, not exact
per-model specs -- this module has no access to a real, verified signal-
capture dataset or per-model FCC equipment-authorization data, and
deliberately does not fabricate specific-looking numbers it can't back
up. A confidently wrong per-model signature is worse than an honestly
approximate family-level one in a system people make security decisions
from.

To get real per-model fidelity, supply your own signatures -- from actual
captured/verified RF samples, a licensed RF signature library, or FCC ID
equipment-authorization filings you've looked up yourself -- via a JSON
file at DRONE_RF_SIGNATURES_PATH (see load_operator_signatures below for
the schema). Operator-supplied signatures are tried before the built-in
ones, so a verified per-model entry takes priority over the approximate
family-level fallback for the same frequency/bandwidth window.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import RF_SIGNATURES_PATH

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RfSignature:
    name: str
    label: str
    freq_bands_mhz: tuple[tuple[float, float], ...]  # inclusive (low, high) ranges
    bandwidth_mhz: tuple[float, float]  # inclusive (min, max)
    frequency_hopping: bool | None  # None = not a distinguishing factor for this signature
    # Confidence this signature, if matched, contributes toward "this is a
    # drone control/video link" -- signal-shape evidence, independent of
    # (and not a replacement for) the sensor's own reported confidence.
    drone_link_confidence: float
    # Where this signature's numbers came from -- shown in logs/exports so
    # a reviewer can tell an approximate built-in from a verified operator
    # entry at a glance, not left to be assumed. Free text, not an enum:
    # operators will describe provenance in their own terms ("captured
    # 2026-03 from unit S/N ...", "FCC ID 2AJZ5-P9E filing").
    source: str = "approximate public spec (protocol family, not per-model)"


BUILT_IN_SIGNATURES: tuple[RfSignature, ...] = (
    RfSignature(
        name="dji_ocusync",
        label="DJI OcuSync (1/2/3)",
        freq_bands_mhz=((2400.0, 2483.5), (5725.0, 5850.0)),
        bandwidth_mhz=(8.0, 20.0),
        frequency_hopping=True,
        drone_link_confidence=0.9,
    ),
    RfSignature(
        name="dji_lightbridge",
        label="DJI Lightbridge",
        freq_bands_mhz=((2400.0, 2483.5), (5725.0, 5850.0)),
        bandwidth_mhz=(10.0, 20.0),
        frequency_hopping=True,
        drone_link_confidence=0.85,
    ),
    RfSignature(
        name="analog_fpv",
        label="Analog FPV video (FM, 5.8 GHz)",
        freq_bands_mhz=((5645.0, 5945.0),),
        bandwidth_mhz=(15.0, 30.0),
        frequency_hopping=False,
        drone_link_confidence=0.75,
    ),
    RfSignature(
        name="wifi_fpv",
        label="Wi-Fi-based FPV/control link",
        freq_bands_mhz=((2400.0, 2483.5), (5150.0, 5850.0)),
        bandwidth_mhz=(18.0, 22.0),
        frequency_hopping=False,
        # Lower than the others: standard Wi-Fi channels are also used by
        # a huge number of non-drone devices, so a match here is much
        # weaker evidence on its own.
        drone_link_confidence=0.5,
    ),
)

# Backward-compatible alias -- app/fusion.py and earlier tests referred to
# the built-in table as SIGNATURES before operator-supplied signatures
# existed.
SIGNATURES = BUILT_IN_SIGNATURES


def load_operator_signatures(path: Path | None = RF_SIGNATURES_PATH) -> tuple[RfSignature, ...]:
    """Loads operator-supplied RF signatures from a JSON file: a list of
    objects with the same fields as RfSignature (freq_bands_mhz as a list
    of [low, high] pairs). Returns () if path is None (the default -- no
    file configured) or the file doesn't exist, the same "warn and
    continue" behavior as app/zones.py's load_zones_from_file, since a
    missing optional signature file shouldn't prevent the app from
    starting.
    """
    if path is None:
        return ()
    if not path.exists():
        logger.warning("RF signatures file not found: %s", path)
        return ()

    signatures = []
    for raw in json.loads(path.read_text()):
        signatures.append(
            RfSignature(
                name=raw["name"],
                label=raw["label"],
                freq_bands_mhz=tuple((band[0], band[1]) for band in raw["freq_bands_mhz"]),
                bandwidth_mhz=(raw["bandwidth_mhz"][0], raw["bandwidth_mhz"][1]),
                frequency_hopping=raw.get("frequency_hopping"),
                drone_link_confidence=raw["drone_link_confidence"],
                source=raw.get("source", "operator-supplied (see DRONE_RF_SIGNATURES_PATH)"),
            )
        )
        logger.info("Loaded RF signature '%s' from %s", signatures[-1].name, path)
    return tuple(signatures)


@dataclass(frozen=True)
class RfMatchResult:
    signature: RfSignature | None
    confidence: float  # 0.0 if no match


def match_rf_signature(
    center_frequency_mhz: float | None,
    bandwidth_mhz: float | None,
    frequency_hopping: bool | None = None,
    signatures: tuple[RfSignature, ...] | None = None,
) -> RfMatchResult:
    """Match against a signature library in order; the first signature
    whose band, bandwidth, and (if the signature cares) hopping behavior
    all match wins. Missing frequency/bandwidth data can't be matched.

    `signatures` defaults to operator-supplied signatures (if
    DRONE_RF_SIGNATURES_PATH is configured) followed by BUILT_IN_SIGNATURES
    -- a verified per-model operator entry is tried, and so can win, before
    the approximate family-level fallback for the same frequency/bandwidth
    window. Pass an explicit tuple (as the existing test suite does) to
    pin the match against a known table regardless of environment.
    """
    if center_frequency_mhz is None or bandwidth_mhz is None:
        return RfMatchResult(signature=None, confidence=0.0)

    if signatures is None:
        signatures = load_operator_signatures() + BUILT_IN_SIGNATURES

    for signature in signatures:
        in_band = any(low <= center_frequency_mhz <= high for low, high in signature.freq_bands_mhz)
        if not in_band:
            continue
        bandwidth_low, bandwidth_high = signature.bandwidth_mhz
        if not (bandwidth_low <= bandwidth_mhz <= bandwidth_high):
            continue
        if (
            signature.frequency_hopping is not None
            and frequency_hopping is not None
            and signature.frequency_hopping != frequency_hopping
        ):
            continue
        return RfMatchResult(signature=signature, confidence=signature.drone_link_confidence)

    return RfMatchResult(signature=None, confidence=0.0)
