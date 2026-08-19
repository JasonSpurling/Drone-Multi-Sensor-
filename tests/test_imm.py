import math

import pytest

from app.imm import CRUISE, MANEUVER, IMMFilter


def test_mode_probabilities_always_sum_to_one():
    imm = IMMFilter(x=0.0, y=0.0)
    for i in range(20):
        imm.predict(dt_s=1.0)
        imm.update(zx=float(i), zy=0.0, measurement_variance=25.0)
        assert sum(imm.mode_probabilities) == pytest.approx(1.0, abs=1e-9)


def test_predict_noop_for_nonpositive_dt():
    imm = IMMFilter(x=1.0, y=2.0, vx=3.0, vy=4.0)
    before = imm.mode_probabilities[:]
    imm.predict(dt_s=0.0)
    imm.predict(dt_s=-1.0)
    assert imm.mode_probabilities == before


def test_combined_state_tracks_constant_velocity_motion():
    imm = IMMFilter(x=0.0, y=0.0, position_variance=50.0**2, velocity_variance=40.0**2)
    true_vx = 15.0
    for step in range(1, 15):
        imm.predict(dt_s=1.0)
        imm.update(zx=true_vx * step, zy=0.0, measurement_variance=25.0)
    assert imm.x == pytest.approx(true_vx * 14, rel=0.05)
    assert imm.vx == pytest.approx(true_vx, abs=3.0)


def test_cruise_mode_dominates_steady_straight_line_motion():
    # Long run of smooth, noise-free constant-velocity motion: the CRUISE
    # (low process noise) mode should end up far more probable than
    # MANEUVER, since it explains the data with tighter, more confident
    # predictions.
    imm = IMMFilter(x=0.0, y=0.0, position_variance=50.0**2, velocity_variance=40.0**2)
    for step in range(1, 30):
        imm.predict(dt_s=1.0)
        imm.update(zx=10.0 * step, zy=0.0, measurement_variance=9.0)
    assert imm.mode_probabilities[CRUISE] > imm.mode_probabilities[MANEUVER]
    assert imm.mode_probabilities[CRUISE] > 0.7


def test_maneuver_mode_gains_probability_after_a_sharp_turn():
    # Fly straight for a while (CRUISE should dominate), then make a sharp
    # turn -- a sudden large heading/velocity change that a pure
    # constant-velocity prediction badly mispredicts. MANEUVER's higher
    # process noise should explain that surprise far better, so its
    # probability should rise sharply right after the turn.
    imm = IMMFilter(x=0.0, y=0.0, position_variance=50.0**2, velocity_variance=40.0**2)
    for step in range(1, 15):
        imm.predict(dt_s=1.0)
        imm.update(zx=10.0 * step, zy=0.0, measurement_variance=9.0)
    maneuver_probability_before_turn = imm.mode_probabilities[MANEUVER]
    assert maneuver_probability_before_turn < 0.1  # CRUISE clearly dominant while flying straight

    # Sharp 90-degree turn: from moving in +x to moving in +y, same speed.
    x0, y0 = imm.x, imm.y
    peak_maneuver_probability = maneuver_probability_before_turn
    for step in range(1, 5):
        imm.predict(dt_s=1.0)
        imm.update(zx=x0, zy=y0 + 10.0 * step, measurement_variance=9.0)
        peak_maneuver_probability = max(peak_maneuver_probability, imm.mode_probabilities[MANEUVER])

    assert peak_maneuver_probability > maneuver_probability_before_turn + 0.5


def test_position_uncertainty_is_positive_and_finite():
    imm = IMMFilter(x=0.0, y=0.0)
    imm.predict(dt_s=1.0)
    uncertainty = imm.position_uncertainty_m()
    assert uncertainty > 0
    assert not math.isnan(uncertainty) and not math.isinf(uncertainty)


def test_mahalanobis_sq_zero_at_predicted_state():
    imm = IMMFilter(x=10.0, y=20.0, vx=0.0, vy=0.0)
    imm.predict(dt_s=1.0)
    assert imm.mahalanobis_sq(imm.x, imm.y, measurement_variance=25.0) == pytest.approx(0.0, abs=1e-6)


def test_mahalanobis_sq_grows_with_distance():
    imm = IMMFilter(x=0.0, y=0.0, position_variance=25.0, velocity_variance=1.0)
    imm.predict(dt_s=1.0)
    near = imm.mahalanobis_sq(5.0, 0.0, measurement_variance=25.0)
    far = imm.mahalanobis_sq(500.0, 0.0, measurement_variance=25.0)
    assert far > near
