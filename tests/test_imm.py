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


def test_mahalanobis_sq_uses_the_more_permissive_model_not_the_stricter_one():
    # The whole point of an IMM gate: right after a real maneuver, a
    # detection that fits MANEUVER's (wide) covariance but not CRUISE's
    # (still-tight, hasn't caught up yet) covariance must still gate in --
    # rejecting it because CRUISE alone would reject it defeats the
    # feature (see app/imm.py's mahalanobis_sq docstring: "never gets
    # tighter than either individual model would allow").
    imm = IMMFilter(x=0.0, y=0.0, position_variance=50.0**2, velocity_variance=40.0**2)
    for step in range(1, 15):
        imm.predict(dt_s=1.0)
        imm.update(zx=10.0 * step, zy=0.0, measurement_variance=9.0)
    x0, y0 = imm.x, imm.y
    for step in range(1, 5):
        imm.predict(dt_s=1.0)
        imm.update(zx=x0, zy=y0 + 10.0 * step, measurement_variance=9.0)

    imm.predict(dt_s=1.0)
    # 20m off the combined predicted position -- offset chosen so CRUISE's
    # own (tight) covariance alone would reject it while MANEUVER's own
    # (wide) covariance alone would accept it.
    zx, zy = imm.x + 20.0, imm.y
    combined = imm.mahalanobis_sq(zx, zy, measurement_variance=9.0)

    # Mirror mahalanobis_sq()'s own math exactly (innovation against the
    # *combined* state, using each model's own covariance) so this asserts
    # the fix (min, not max) rather than a slightly different quantity.
    from app.imm import _inv2x2

    state, _ = imm._combined()
    y_innovation = [zx - state[0], zy - state[1]]

    def _distance_sq(model):
        _, s_cov = model.innovation(zx, zy, 9.0)
        s_inv = _inv2x2(s_cov)
        return sum(y_innovation[i] * s_inv[i][j] * y_innovation[j] for i in range(2) for j in range(2))

    cruise_distance = _distance_sq(imm.models[CRUISE])
    maneuver_distance = _distance_sq(imm.models[MANEUVER])
    assert maneuver_distance < cruise_distance  # sanity: this scenario actually distinguishes the two
    assert combined == pytest.approx(maneuver_distance)  # the permissive (min) one, not max


def test_inv2x2_rejects_a_singular_matrix():
    from app.imm import _inv2x2

    with pytest.raises(ValueError, match="singular"):
        _inv2x2([[1.0, 2.0], [2.0, 4.0]])  # second row is a multiple of the first -> determinant 0


def test_combined_state_cache_is_invalidated_by_predict_and_update():
    imm = IMMFilter(x=0.0, y=0.0, vx=5.0, vy=0.0)
    x_before = imm.x
    imm.predict(dt_s=1.0)
    assert imm.x != x_before  # predict() must not return a stale cached x

    x_after_predict = imm.x
    imm.update(zx=x_after_predict + 50.0, zy=0.0, measurement_variance=25.0)
    assert imm.x != x_after_predict  # update() must not return a stale cached x
