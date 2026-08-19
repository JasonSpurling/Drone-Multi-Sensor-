"""Interacting Multiple Model (IMM) estimator: blends two constant-
velocity Kalman filters (app.kalman) that share the same state structure
but differ in process noise -- a CRUISE mode (low process noise, smooths
sensor jitter on straight/level flight) and a MANEUVER mode (high process
noise, tracks sharp turns and sudden acceleration without lagging behind
them). Mode probabilities update every cycle from how well each mode's
prediction matched the actual detection, so the filter automatically
leans on whichever mode fits the object's current behavior -- a plain
single-model constant-velocity filter treats a sharp turn as measurement
noise and lags behind it; this doesn't.

This is a restricted/simplified IMM (same state-transition structure,
different Q) rather than a full mixed-order IMM (e.g. CV + a coordinated-
turn model with its own turn-rate state). That's a well-established,
standard technique for exactly this maneuvering-target problem, and it
avoids the added complexity of blending genuinely different state spaces
that a full CV+CT IMM would need.
"""

from __future__ import annotations

import math

from app.kalman import ConstantVelocityKalmanFilter, Matrix, Vector

CRUISE = 0
MANEUVER = 1

# Markov mode-transition matrix P[i][j] = P(mode j at k | mode i at k-1).
# High self-transition probability keeps mode probabilities from
# chattering detection-to-detection, while still letting a real maneuver
# pull probability mass into MANEUVER within a few updates.
_MODE_TRANSITION: Matrix = [
    [0.95, 0.05],
    [0.15, 0.85],
]


def _vec_scale(v: Vector, s: float) -> Vector:
    return [c * s for c in v]


def _vec_add(a: Vector, b: Vector) -> Vector:
    return [a[i] + b[i] for i in range(len(a))]


def _mat_scale(m: Matrix, s: float) -> Matrix:
    return [[c * s for c in row] for row in m]


def _mat_add(a: Matrix, b: Matrix) -> Matrix:
    return [[a[i][j] + b[i][j] for j in range(len(a[0]))] for i in range(len(a))]


def _outer(v: Vector) -> Matrix:
    n = len(v)
    return [[v[i] * v[j] for j in range(n)] for i in range(n)]


def _gaussian_likelihood(mahalanobis_sq: float, det_s: float) -> float:
    """2D Gaussian likelihood of an innovation, given its squared
    Mahalanobis distance and the determinant of its innovation covariance.
    """
    det_s = max(det_s, 1e-12)
    return math.exp(-0.5 * mahalanobis_sq) / (2.0 * math.pi * math.sqrt(det_s))


class IMMFilter:
    def __init__(
        self,
        x: float,
        y: float,
        vx: float = 0.0,
        vy: float = 0.0,
        position_variance: float = 100.0**2,
        velocity_variance: float = 100.0**2,
        cruise_process_noise: float = 1.0,
        maneuver_process_noise: float = 25.0,
        mode_probabilities: Vector | None = None,
        models: list[ConstantVelocityKalmanFilter] | None = None,
    ) -> None:
        self.cruise_process_noise = cruise_process_noise
        self.maneuver_process_noise = maneuver_process_noise
        self.mode_probabilities: Vector = list(mode_probabilities) if mode_probabilities else [0.9, 0.1]
        if models is not None:
            self.models = list(models)
        else:
            self.models = [
                ConstantVelocityKalmanFilter(x, y, vx, vy, position_variance, velocity_variance),
                ConstantVelocityKalmanFilter(x, y, vx, vy, position_variance, velocity_variance),
            ]
        # Predicted (mixed) mode probabilities from the most recent predict()
        # call -- used as the prior when update() computes the Bayesian
        # mode-probability update from each model's measurement likelihood.
        self._predicted_mode_probabilities: Vector = list(self.mode_probabilities)

    # --- combined (reported) state, from a probability-weighted mixture ---

    def _combined(self) -> tuple[Vector, Matrix]:
        probs = self.mode_probabilities
        state = [0.0, 0.0, 0.0, 0.0]
        for j, model in enumerate(self.models):
            state = _vec_add(state, _vec_scale(model.state, probs[j]))
        covariance = [[0.0] * 4 for _ in range(4)]
        for j, model in enumerate(self.models):
            diff = [model.state[i] - state[i] for i in range(4)]
            spread = _mat_add(model.covariance, _outer(diff))
            covariance = _mat_add(covariance, _mat_scale(spread, probs[j]))
        return state, covariance

    @property
    def x(self) -> float:
        return self._combined()[0][0]

    @property
    def y(self) -> float:
        return self._combined()[0][1]

    @property
    def vx(self) -> float:
        return self._combined()[0][2]

    @property
    def vy(self) -> float:
        return self._combined()[0][3]

    @property
    def covariance(self) -> Matrix:
        return self._combined()[1]

    @property
    def maneuver_probability(self) -> float:
        return self.mode_probabilities[MANEUVER]

    def position_uncertainty_m(self) -> float:
        covariance = self.covariance
        return (covariance[0][0] + covariance[1][1]) ** 0.5

    # --- IMM cycle: mix -> predict, then (separately) update -> combine ---

    def _mix(self) -> tuple[list[Vector], list[Matrix]]:
        """Standard IMM mixing step: blend each model's state/covariance
        into a mixed initial condition for every target mode, weighted by
        the Markov transition probabilities and last cycle's mode
        probabilities.
        """
        n = len(self.models)
        normalizers = [
            sum(_MODE_TRANSITION[i][j] * self.mode_probabilities[i] for i in range(n)) or 1e-12
            for j in range(n)
        ]
        mix_weights = [
            [_MODE_TRANSITION[i][j] * self.mode_probabilities[i] / normalizers[j] for i in range(n)]
            for j in range(n)
        ]
        self._predicted_mode_probabilities = normalizers

        mixed_states: list[Vector] = []
        mixed_covariances: list[Matrix] = []
        for j in range(n):
            state = [0.0, 0.0, 0.0, 0.0]
            for i in range(n):
                state = _vec_add(state, _vec_scale(self.models[i].state, mix_weights[j][i]))
            covariance = [[0.0] * 4 for _ in range(4)]
            for i in range(n):
                diff = [self.models[i].state[k] - state[k] for k in range(4)]
                spread = _mat_add(self.models[i].covariance, _outer(diff))
                covariance = _mat_add(covariance, _mat_scale(spread, mix_weights[j][i]))
            mixed_states.append(state)
            mixed_covariances.append(covariance)
        return mixed_states, mixed_covariances

    def predict(self, dt_s: float) -> None:
        if dt_s <= 0:
            return
        mixed_states, mixed_covariances = self._mix()
        for j, model in enumerate(self.models):
            model.state = mixed_states[j]
            model.covariance = mixed_covariances[j]
        self.models[CRUISE].predict(dt_s, self.cruise_process_noise)
        self.models[MANEUVER].predict(dt_s, self.maneuver_process_noise)

    def mahalanobis_sq(self, zx: float, zy: float, measurement_variance: float) -> float:
        """Gate against the combined (mixture) predicted state, using
        whichever model's innovation covariance is larger (more
        conservative -- a true mixture-of-Gaussians Mahalanobis distance
        has no closed form, so this trades a little precision for a gate
        that never gets tighter than either individual model would allow).
        """
        state, _ = self._combined()
        y_innovation = [zx - state[0], zy - state[1]]
        worst = 0.0
        for model in self.models:
            _, s_innovation_cov = model.innovation(zx, zy, measurement_variance)
            s_inv = _inv2x2(s_innovation_cov)
            distance_sq = sum(
                y_innovation[i] * s_inv[i][j] * y_innovation[j] for i in range(2) for j in range(2)
            )
            worst = max(worst, distance_sq)
        return worst

    def update(self, zx: float, zy: float, measurement_variance: float) -> None:
        likelihoods = []
        for model in self.models:
            innovation, s_innovation_cov = model.innovation(zx, zy, measurement_variance)
            det_s = s_innovation_cov[0][0] * s_innovation_cov[1][1] - s_innovation_cov[0][1] * s_innovation_cov[1][0]
            mahalanobis_sq = sum(
                innovation[i] * _inv2x2(s_innovation_cov)[i][j] * innovation[j]
                for i in range(2) for j in range(2)
            )
            likelihoods.append(_gaussian_likelihood(mahalanobis_sq, det_s))
            model.update(zx, zy, measurement_variance)

        unnormalized = [self._predicted_mode_probabilities[j] * likelihoods[j] for j in range(len(self.models))]
        total = sum(unnormalized) or 1e-12
        self.mode_probabilities = [u / total for u in unnormalized]


def _inv2x2(m: Matrix) -> Matrix:
    a, b = m[0]
    c, d = m[1]
    det = a * d - b * c
    if det == 0:
        raise ValueError("singular innovation covariance -- cannot invert")
    inv_det = 1.0 / det
    return [[d * inv_det, -b * inv_det], [-c * inv_det, a * inv_det]]
