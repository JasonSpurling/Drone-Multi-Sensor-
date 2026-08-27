"""A small constant-velocity Kalman filter for 2D track state, plus the
minimal dense-matrix arithmetic it needs. No numpy dependency -- state is
always 4x4 or smaller, so plain nested lists are fast enough and keep the
project's dependency footprint small.

State vector: [x_m, y_m, vx_mps, vy_mps] in a local tangent-plane frame
(see app.geo). Measurements are position-only (x, y); velocity is inferred
purely from how position moves between updates.
"""

from __future__ import annotations

Matrix = list[list[float]]
Vector = list[float]


def _zeros(rows: int, cols: int) -> Matrix:
    return [[0.0] * cols for _ in range(rows)]


def _identity(n: int) -> Matrix:
    m = _zeros(n, n)
    for i in range(n):
        m[i][i] = 1.0
    return m


def _mat_mul(a: Matrix, b: Matrix) -> Matrix:
    rows, inner, cols = len(a), len(b), len(b[0])
    result = _zeros(rows, cols)
    for i in range(rows):
        for k in range(inner):
            a_ik = a[i][k]
            if a_ik == 0.0:
                continue
            for j in range(cols):
                result[i][j] += a_ik * b[k][j]
    return result


def _mat_vec_mul(a: Matrix, v: Vector) -> Vector:
    return [sum(a[i][j] * v[j] for j in range(len(v))) for i in range(len(a))]


def _transpose(a: Matrix) -> Matrix:
    return [[a[i][j] for i in range(len(a))] for j in range(len(a[0]))]


def _mat_add(a: Matrix, b: Matrix) -> Matrix:
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def _mat_sub(a: Matrix, b: Matrix) -> Matrix:
    return [[a[i][j] - b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def _vec_add(a: Vector, b: Vector) -> Vector:
    return [a[i] + b[i] for i in range(len(a))]


def _inv2x2(m: Matrix) -> Matrix:
    a, b = m[0]
    c, d = m[1]
    det = a * d - b * c
    if det == 0:
        raise ValueError("singular innovation covariance -- cannot invert")
    inv_det = 1.0 / det
    return [[d * inv_det, -b * inv_det], [-c * inv_det, a * inv_det]]


class ConstantVelocityKalmanFilter:
    """4-state (x, y, vx, vy) constant-velocity filter with position-only
    measurement updates.
    """

    def __init__(
        self,
        x: float,
        y: float,
        vx: float = 0.0,
        vy: float = 0.0,
        position_variance: float = 100.0**2,
        velocity_variance: float = 40.0**2,
        covariance: Matrix | None = None,
    ) -> None:
        self.state: Vector = [x, y, vx, vy]
        self.covariance: Matrix = covariance or [
            [position_variance, 0.0, 0.0, 0.0],
            [0.0, position_variance, 0.0, 0.0],
            [0.0, 0.0, velocity_variance, 0.0],
            [0.0, 0.0, 0.0, velocity_variance],
        ]

    @property
    def x(self) -> float:
        return self.state[0]

    @property
    def y(self) -> float:
        return self.state[1]

    @property
    def vx(self) -> float:
        return self.state[2]

    @property
    def vy(self) -> float:
        return self.state[3]

    def predict(self, dt_s: float, process_noise_accel_variance: float) -> None:
        """Advance the filter dt_s seconds under a constant-velocity model.
        No-op for dt_s <= 0 (out-of-order or duplicate-timestamp input).
        """
        if dt_s <= 0:
            return

        f_transition: Matrix = [
            [1.0, 0.0, dt_s, 0.0],
            [0.0, 1.0, 0.0, dt_s],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]

        # Discrete white-noise-acceleration process noise: an unmodeled
        # acceleration of variance q is assumed constant over the step,
        # inducing correlated position/velocity noise growth.
        q = process_noise_accel_variance
        dt2, dt3 = dt_s * dt_s, dt_s * dt_s * dt_s
        q_pos, q_cross, q_vel = q * dt3 / 3.0, q * dt2 / 2.0, q * dt_s
        process_noise: Matrix = [
            [q_pos, 0.0, q_cross, 0.0],
            [0.0, q_pos, 0.0, q_cross],
            [q_cross, 0.0, q_vel, 0.0],
            [0.0, q_cross, 0.0, q_vel],
        ]

        self.state = _mat_vec_mul(f_transition, self.state)
        predicted_covariance = _mat_mul(_mat_mul(f_transition, self.covariance), _transpose(f_transition))
        self.covariance = _mat_add(predicted_covariance, process_noise)

    def innovation(self, zx: float, zy: float, measurement_variance: float) -> tuple[Vector, Matrix]:
        """Return (innovation, innovation_covariance) for a candidate
        position measurement without mutating filter state -- used to gate
        candidate detections before committing to an update.
        """
        h_observe: Matrix = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
        r_measurement: Matrix = [[measurement_variance, 0.0], [0.0, measurement_variance]]
        y_innovation = [zx - self.state[0], zy - self.state[1]]
        ph_t = _mat_mul(self.covariance, _transpose(h_observe))
        s_innovation_cov = _mat_add(_mat_mul(h_observe, ph_t), r_measurement)
        return y_innovation, s_innovation_cov

    def mahalanobis_sq(self, zx: float, zy: float, measurement_variance: float) -> float:
        y_innovation, s_innovation_cov = self.innovation(zx, zy, measurement_variance)
        s_inv = _inv2x2(s_innovation_cov)
        return sum(
            y_innovation[i] * s_inv[i][j] * y_innovation[j] for i in range(2) for j in range(2)
        )

    def update(self, zx: float, zy: float, measurement_variance: float) -> None:
        h_observe: Matrix = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]]
        y_innovation, s_innovation_cov = self.innovation(zx, zy, measurement_variance)
        ph_t = _mat_mul(self.covariance, _transpose(h_observe))
        s_inv = _inv2x2(s_innovation_cov)
        kalman_gain = _mat_mul(ph_t, s_inv)

        self.state = _vec_add(self.state, _mat_vec_mul(kalman_gain, y_innovation))
        i_minus_kh = _mat_sub(_identity(4), _mat_mul(kalman_gain, h_observe))
        self.covariance = _mat_mul(i_minus_kh, self.covariance)

    def position_uncertainty_m(self) -> float:
        """1-sigma radial position uncertainty, from the trace of the
        position block of the covariance matrix.
        """
        return (self.covariance[0][0] + self.covariance[1][1]) ** 0.5
