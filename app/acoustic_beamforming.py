"""Delay-and-sum steered-response-power beamforming: estimates the
compass bearing (`azimuth_deg`, clockwise from the array's own zero
reference -- the same azimuth_deg/range_m convention every other
azimuth-reporting sensor in this app uses, see app/georeference.py) a
sound arrived from, using a small microphone array, instead of the single
flat confidence number acoustic detections have used until now
(app/classification.py just thresholds it into drone/bird/unknown).

This is the same fundamental idea as RF direction-finding -- multiple
receive points, arrival-time differences reveal direction -- applied to
sound instead of radio. Sound travels far slower than RF (343 m/s vs.
~3*10^8 m/s), so the time-of-arrival differences that would need
sub-nanosecond timing resolution for RF are on the order of milliseconds
for microphones spaced tens of centimeters apart -- resolvable with an
ordinary multi-channel USB audio interface (a small array like a
ReSpeaker 4-mic board), no specialized RF hardware needed.

A microphone array alone can't measure RANGE to the source, only bearing
-- the same fundamental limitation a single RF direction-finder has
without a second station to triangulate against. See
app/adapters/acoustic_array_bridge.py for how that's handled honestly (an
assumed, operator-tunable default range, the same compromise
app/adapters/camera_motion.py makes for the identical reason).

Pure numpy math, verified against synthetic signals with a known true
bearing (tests/test_acoustic_beamforming.py) rather than any external
reference implementation -- delay-and-sum steered-response-power is
well-established, unambiguous DSP, unlike the reverse-engineered/
undocumented protocols elsewhere in this app's RF adapters that needed an
external authoritative source to build against correctly. That
verification caught a real sign bug during development (a first version
of steering_delay_s had mics closer to the source hearing the wavefront
*later*, silently flipping every estimate 180 degrees) -- a synthetic
test that generates ground truth using this module's own delay function
would never have caught that, since it'd be circular; the real test
independently places a point source and computes each mic's exact
Euclidean propagation delay from scratch.

**Accuracy is a function of array size and signal bandwidth**, not just
azimuth_resolution_deg: with a compact ~6cm array (ReSpeaker-scale) and a
band-limited source resembling real rotor/propeller acoustic signatures
(low-frequency-dominated), empirical error against synthetic ground truth
was under 15 degrees; a larger ~30cm array brought that under 5 degrees.
A full-bandwidth white-noise source degrades accuracy well beyond that on
the larger array specifically (classic spatial aliasing once mic spacing
exceeds roughly half the shortest wavelength present) -- band-pass
filtering audio to the few-hundred-Hz range typical of rotor noise before
beamforming, not just using raw broadband audio, is the difference
between a working DOA estimate and a badly aliased one.
"""

from __future__ import annotations

import numpy as np

# Speed of sound in dry air at ~20 degrees C. Real deployments in very hot,
# cold, humid, or high-altitude conditions will see a few percent
# deviation from this -- small compared to azimuth_resolution_deg's own
# discretization error for a typical small array, so not accounted for
# here; a precise deployment could make this configurable per-sensor.
SPEED_OF_SOUND_MPS = 343.0


def steering_delay_s(mic_position_m: tuple[float, float], azimuth_deg: float) -> float:
    """Time delay (seconds, relative to the array's geometric center) a
    plane wave arriving from `azimuth_deg` (clockwise from north/the
    array's own boresight, standard compass convention) would reach a mic
    at `mic_position_m` (x=east, y=north, meters, relative to the array
    center) -- negative if that mic hears the wavefront before the
    center does (i.e. it sits closer to the source).
    """
    azimuth_rad = np.radians(azimuth_deg)
    # Unit vector pointing FROM the array center TOWARD the source.
    direction = np.array([np.sin(azimuth_rad), np.cos(azimuth_rad)])
    projection = float(np.dot(mic_position_m, direction))
    # A mic positioned toward the source (positive projection) is closer
    # to it and so hears the wavefront *earlier* -- negative delay.
    return -projection / SPEED_OF_SOUND_MPS


def _steered_power(
    channels: np.ndarray, mic_positions_m: list[tuple[float, float]], sample_rate_hz: float, azimuth_deg: float
) -> float:
    """Delay-compensate every channel for a hypothetical source at
    `azimuth_deg` and sum them; a source actually at that azimuth adds
    constructively (high power), one elsewhere partially cancels (lower
    power) -- the basis of steered-response-power beamforming.
    """
    n_mics = channels.shape[0]
    summed = np.zeros(channels.shape[1])
    for i in range(n_mics):
        delay_s = steering_delay_s(mic_positions_m[i], azimuth_deg)
        shift_samples = int(round(delay_s * sample_rate_hz))
        # A source at `azimuth_deg` reaches this mic `delay_s` earlier/later
        # than the array center; rolling the *opposite* direction realigns
        # it back to the center's timeline before summing.
        summed += np.roll(channels[i], -shift_samples)
    return float(np.sum(summed**2))


def estimate_bearing(
    channels: np.ndarray,
    mic_positions_m: list[tuple[float, float]],
    sample_rate_hz: float,
    azimuth_resolution_deg: float = 2.0,
) -> tuple[float, float]:
    """`channels` is a (n_mics, n_samples) array of synchronized audio
    samples from every microphone in the array. Returns
    (azimuth_deg, confidence): azimuth_deg is the 0-360 compass bearing
    (clockwise from the array's zero reference) with the highest steered
    power, confidence is that peak's power normalized against the mean
    power across every scanned azimuth (1.0 = no direction stood out at
    all; higher = a sharper, more confident peak). This is a measure of
    how well-resolved the BEARING estimate is, not a judgment of whether
    the sound source is actually a drone -- that's a separate question
    (see app/adapters/acoustic_array_bridge.py).
    """
    if channels.ndim != 2:
        raise ValueError("channels must be a 2D (n_mics, n_samples) array")
    if channels.shape[0] != len(mic_positions_m):
        raise ValueError(
            f"channels has {channels.shape[0]} mic rows but mic_positions_m has {len(mic_positions_m)} positions"
        )
    if channels.shape[0] < 2:
        raise ValueError("bearing estimation needs at least 2 microphones")

    azimuths = np.arange(0.0, 360.0, azimuth_resolution_deg)
    powers = np.array(
        [_steered_power(channels, mic_positions_m, sample_rate_hz, az) for az in azimuths]
    )

    peak_index = int(np.argmax(powers))
    mean_power = float(np.mean(powers))
    confidence = float(powers[peak_index]) / mean_power if mean_power > 0 else 0.0

    return float(azimuths[peak_index]), confidence
