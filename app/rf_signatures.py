"""RF signature fingerprinting: classifies an RF detection's signal
characteristics (center frequency, channel bandwidth, whether it's
frequency-hopping) against a small library of publicly documented drone
control/video link signatures, instead of trusting a single flat RF
"confidence" number.

This matches how real counter-drone RF sensors actually work: RF signal
intelligence systems classify frequency band, channel bandwidth, and
hopping behavior against known signature libraries for common link types
(DJI OcuSync/Lightbridge, analog FPV video, Wi-Fi-based FPV/control) --
not by decoding encrypted proprietary protocol content. This module does
exactly that: pattern matching against public, well-documented RF
envelope characteristics, nothing more. It cannot decode a link's
payload, extract telemetry, or identify a specific aircraft -- only that
its RF envelope is consistent with a known type of control/video link.

Values below are drawn from published consumer/hobbyist RF specifications
(ISM/license-free 2.4 GHz and 5.8 GHz band usage) and are approximate
band/bandwidth windows, not exact per-model specs -- tune SIGNATURES to
your own RF sensor's measured characteristics if you have one.
"""

from __future__ import annotations

from dataclasses import dataclass


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


SIGNATURES: tuple[RfSignature, ...] = (
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


@dataclass(frozen=True)
class RfMatchResult:
    signature: RfSignature | None
    confidence: float  # 0.0 if no match


def match_rf_signature(
    center_frequency_mhz: float | None,
    bandwidth_mhz: float | None,
    frequency_hopping: bool | None = None,
) -> RfMatchResult:
    """Match against the signature library in order; the first signature
    whose band, bandwidth, and (if the signature cares) hopping behavior
    all match wins. Missing frequency/bandwidth data can't be matched.
    """
    if center_frequency_mhz is None or bandwidth_mhz is None:
        return RfMatchResult(signature=None, confidence=0.0)

    for signature in SIGNATURES:
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
