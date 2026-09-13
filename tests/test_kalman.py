import pytest

from app.kalman import ConstantVelocityKalmanFilter, _inv2x2


def test_predict_advances_position_by_velocity_times_dt():
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0, vx=10.0, vy=5.0)
    kf.predict(dt_s=2.0, process_noise_accel_variance=1.0)
    assert kf.x == pytest.approx(20.0)
    assert kf.y == pytest.approx(10.0)
    # velocity is unchanged by a constant-velocity prediction
    assert kf.vx == pytest.approx(10.0)
    assert kf.vy == pytest.approx(5.0)


def test_predict_grows_uncertainty():
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0, position_variance=10.0, velocity_variance=10.0)
    before = kf.position_uncertainty_m()
    kf.predict(dt_s=5.0, process_noise_accel_variance=4.0)
    after = kf.position_uncertainty_m()
    assert after > before


def test_predict_noop_for_nonpositive_dt():
    kf = ConstantVelocityKalmanFilter(x=1.0, y=2.0, vx=3.0, vy=4.0)
    state_before = list(kf.state)
    kf.predict(dt_s=0.0, process_noise_accel_variance=4.0)
    kf.predict(dt_s=-1.0, process_noise_accel_variance=4.0)
    assert kf.state == state_before


def test_update_pulls_state_toward_measurement():
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0, position_variance=100.0**2, velocity_variance=1.0)
    kf.update(zx=50.0, zy=0.0, measurement_variance=5.0**2)
    # Measurement is far more certain than the prior, so the filter should
    # move close to it (not stay at 0, not overshoot past it).
    assert 0.0 < kf.x < 50.0
    assert kf.x == pytest.approx(50.0, abs=5.0)


def test_update_shrinks_uncertainty():
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0, position_variance=100.0**2, velocity_variance=1.0)
    before = kf.position_uncertainty_m()
    kf.update(zx=1.0, zy=1.0, measurement_variance=5.0**2)
    after = kf.position_uncertainty_m()
    assert after < before


def test_filter_converges_on_constant_velocity_track():
    # Simulate a track moving at a fixed 20 m/s east and feed it noise-free
    # measurements every second; after a few updates the filter's velocity
    # estimate should converge close to the true 20 m/s.
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0, position_variance=50.0**2, velocity_variance=40.0**2)
    true_vx = 20.0
    for step in range(1, 11):
        kf.predict(dt_s=1.0, process_noise_accel_variance=1.0)
        kf.update(zx=true_vx * step, zy=0.0, measurement_variance=5.0**2)
    assert kf.vx == pytest.approx(true_vx, abs=2.0)
    assert kf.vy == pytest.approx(0.0, abs=2.0)


def test_mahalanobis_sq_zero_at_predicted_state():
    kf = ConstantVelocityKalmanFilter(x=10.0, y=20.0)
    assert kf.mahalanobis_sq(10.0, 20.0, measurement_variance=25.0) == pytest.approx(0.0)


def test_mahalanobis_sq_grows_with_distance():
    kf = ConstantVelocityKalmanFilter(x=0.0, y=0.0, position_variance=25.0, velocity_variance=1.0)
    near = kf.mahalanobis_sq(5.0, 0.0, measurement_variance=25.0)
    far = kf.mahalanobis_sq(500.0, 0.0, measurement_variance=25.0)
    assert far > near


def test_inv2x2_rejects_a_singular_matrix():
    with pytest.raises(ValueError, match="singular"):
        _inv2x2([[1.0, 2.0], [2.0, 4.0]])  # second row is a multiple of the first -> determinant 0
