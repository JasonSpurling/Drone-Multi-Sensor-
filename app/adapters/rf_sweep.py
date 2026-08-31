"""Parses hackrf_sweep's CSV output (a full-spectrum energy sweep from a
HackRF or compatible SDR) and does simple energy-threshold detection --
flags any frequency bin whose power exceeds the sweep's own noise floor
by --threshold-db, independent of what protocol (or none at all) is
actually transmitting there. This is deliberately NOT protocol decoding:
every other RF adapter in this package (dji_droneid_bridge.py,
astm_remote_id_*_bridge.py, mavlink_bridge.py) only sees a transmission
it already knows how to decode. This one instead catches an unknown or
non-cooperative RF source purely by its presence -- the same "energy
detection" first pass real counter-drone RF systems use before any
signal classification.

hackrf_sweep's CSV line format:
    date, time, hz_low, hz_high, hz_bin_width, num_samples, dB, dB, dB, ...
(verified against hackrf_sweep's actual documented output and
tesorrells/RF-Drone-Detection's reference parsing of it, not guessed).
A single sweep across a wide frequency range (e.g. the whole 2.4GHz ISM
band) is split across several lines -- every line sharing the same
(date, time) belongs to the same sweep cycle; app/adapters/rf_sweep_bridge.py
accumulates them into one complete sweep before running detection.

IMPORTANT LIMITATIONS -- this is the least specific sensor in this
package, deliberately:
  - No protocol identification. A flagged bin could be a drone control
    link, a WiFi AP, a microwave oven, or a cordless phone -- this can
    tell you SOMETHING is transmitting there above the noise floor, not
    what. app/rf_signatures.py's frequency/bandwidth/hopping matching
    (used by the protocol-specific RF adapters) doesn't apply here: this
    has no decoded signal's real occupied bandwidth to check against,
    only a single FFT bin's power reading.
  - No direction or range. An omnidirectional SDR sweep has neither --
    every detection is reported at the sensor's own --target-lat/
    --target-lon (the same honest compromise app/adapters/camera_motion.py
    makes for a monocular camera's identical limitation), not a
    triangulated position.
Best used as a coarse first-pass alert ("something is transmitting in a
drone-relevant band right now") that a human or a more specific sensor
then investigates, not as a standalone drone/not-drone classifier.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class SweepSegment:
    """One line of hackrf_sweep CSV output -- one frequency sub-range's
    power readings from a single sweep cycle.
    """

    timestamp_key: tuple[str, str]
    hz_low: int
    hz_bin_width: float
    powers_db: list[float]


def parse_sweep_line(line: str) -> SweepSegment | None:
    """None for a blank/malformed line (a header, a truncated line from a
    killed process, ...) rather than raising -- one bad line must not
    take down a long-running sweep listener.
    """
    fields = [f.strip() for f in line.strip().split(",")]
    if len(fields) < 7:
        return None
    try:
        date_str, time_str = fields[0], fields[1]
        hz_low = int(fields[2])
        hz_bin_width = float(fields[4])
        num_samples = int(fields[5])
        powers_db = [float(v) for v in fields[6 : 6 + num_samples]]
    except (ValueError, IndexError):
        return None
    if len(powers_db) != num_samples or not powers_db:
        return None
    return SweepSegment(
        timestamp_key=(date_str, time_str), hz_low=hz_low, hz_bin_width=hz_bin_width, powers_db=powers_db
    )


def bins_from_segment(segment: SweepSegment) -> list[tuple[float, float]]:
    """(frequency_hz, power_db) for every bin in this segment -- bin i's
    frequency is hz_low + i * hz_bin_width, the start of that bin (not
    its center), matching how hackrf_sweep itself and every reference
    parser of its output computes it.
    """
    return [(segment.hz_low + i * segment.hz_bin_width, power_db) for i, power_db in enumerate(segment.powers_db)]


def estimate_noise_floor_db(bins: list[tuple[float, float]]) -> float:
    """The sweep's own median power across every bin -- robust to a
    handful of genuinely occupied bins skewing a mean upward, and doesn't
    require a separately recorded quiet-band baseline (though
    app/adapters/rf_sweep_bridge.py's --baseline-csv still supports one,
    for a site with persistent RF activity a per-sweep median wouldn't
    correctly treat as "noise").
    """
    if not bins:
        return 0.0
    powers = sorted(power for _, power in bins)
    mid = len(powers) // 2
    if len(powers) % 2 == 0:
        return (powers[mid - 1] + powers[mid]) / 2.0
    return powers[mid]


def find_peaks(
    bins: list[tuple[float, float]], noise_floor_db: float, threshold_db: float
) -> list[tuple[float, float]]:
    """Every bin whose power exceeds noise_floor_db + threshold_db,
    sorted strongest first -- simple energy-threshold detection, the same
    first-pass approach real counter-drone RF systems use before any
    signal classification. Adjacent bins from one real transmission
    (which usually spans several bins, not exactly one) aren't merged
    here -- app/adapters/rf_sweep_bridge.py's --min-interval, bucketed by
    frequency, is what keeps one real emitter from posting a detection
    for every one of its bins on every single sweep.
    """
    threshold = noise_floor_db + threshold_db
    peaks = [(freq, power) for freq, power in bins if power >= threshold]
    return sorted(peaks, key=lambda pair: -pair[1])


def build_detection_payload(
    frequency_hz: float,
    power_db: float,
    noise_floor_db: float,
    sensor_id: str,
    target_lat: float,
    target_lon: float,
    confidence: float,
) -> dict:
    return {
        "sensor_id": sensor_id,
        "sensor_type": "rf",
        "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "latitude": target_lat,
        "longitude": target_lon,
        "confidence": confidence,
        "raw_data": {
            "protocol": "rf_energy_sweep",
            "center_frequency_mhz": frequency_hz / 1_000_000.0,
            "peak_power_db": power_db,
            "noise_floor_db": noise_floor_db,
            "margin_db": power_db - noise_floor_db,
        },
    }
