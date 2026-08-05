"""Common condensed linear MPC for every affine bake-off model arm."""

from __future__ import annotations

from collections.abc import Mapping

from dataclasses import dataclass
from math import isfinite

import numpy as np
import numpy.typing as npt
from scipy.optimize import lsq_linear

from .contracts import AffinePrediction

FloatVector = npt.NDArray[np.float64]
_CANDIDATE_HORIZONS_S = (600, 800, 1000)
_EPSILON = np.finfo(np.float64).eps


@dataclass(frozen=True, slots=True)
class MPCWeights:
    """Quadratic temperature, terminal, and move penalties."""

    temperature: float = 1.0
    terminal: float = 4.0
    move: float = 0.05

    def __post_init__(self) -> None:
        for name, value in (
            ("temperature", self.temperature),
            ("terminal", self.terminal),
            ("move", self.move),
        ):
            if not isfinite(value) or value < 0.0:
                raise ValueError(f"{name} weight must be finite and non-negative")
        if self.temperature == 0.0 and self.terminal == 0.0 and self.move == 0.0:
            raise ValueError("at least one MPC weight must be positive")


@dataclass(frozen=True, slots=True)
class MPCConfig:
    """The frozen selected validation horizon and numerical controller settings."""

    horizon_s: int
    frame_s: int
    weights: MPCWeights = MPCWeights()
    max_iterations: int = 10_000
    tolerance: float = 1e-7

    def __post_init__(self) -> None:
        if self.horizon_s not in _CANDIDATE_HORIZONS_S:
            raise ValueError("horizon_s must be one of 600, 800, or 1000 seconds")
        if self.frame_s != 20:
            raise ValueError("frame_s must be exactly 20 seconds")
        if self.horizon_s % self.frame_s:
            raise ValueError("horizon_s must be divisible by frame_s")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        if not isfinite(self.tolerance) or self.tolerance <= 0.0:
            raise ValueError("tolerance must be finite and positive")

    @property
    def horizon_steps(self) -> int:
        """Return the model horizon implied by the selected twenty-second frame."""
        return self.horizon_s // self.frame_s



def select_validation_horizon(validation_scores: Mapping[int, float]) -> int:
    """Select a frozen candidate using validation control scores only.

    A shorter horizon wins when its score is within one percent of the
    validation best, preventing a statistically immaterial score difference
    from increasing the controller's solve horizon.
    """
    if set(validation_scores) != set(_CANDIDATE_HORIZONS_S):
        raise ValueError("validation scores must contain exactly 600, 800, and 1000 seconds")
    scores = {horizon_s: float(score) for horizon_s, score in validation_scores.items()}
    if not all(isfinite(score) and score >= 0.0 for score in scores.values()):
        raise ValueError("validation scores must be finite and non-negative")
    best = min(scores.values())
    threshold = best * 1.01
    return next(
        horizon_s
        for horizon_s in _CANDIDATE_HORIZONS_S
        if scores[horizon_s] < threshold or scores[horizon_s] == best
    )

@dataclass(frozen=True, slots=True)
class SolveResult:
    """A deterministic box-QP solution and its projected-gradient certificate."""

    x: FloatVector
    objective: float
    iterations: int
    kkt_residual: float
    predicted_c: FloatVector | None = None
    hessian_condition: float | None = None
    def __post_init__(self) -> None:
        x = np.array(self.x, dtype=np.float64, copy=True)
        if x.ndim != 1:
            raise ValueError("x must have shape (N,)")
        if not np.isfinite(x).all():
            raise ValueError("x must be finite")
        x.flags.writeable = False
        object.__setattr__(self, "x", x)
        if not isfinite(self.objective) or not isfinite(self.kkt_residual):
            raise ValueError("objective and kkt_residual must be finite")
        if self.predicted_c is not None:
            predicted = np.array(self.predicted_c, dtype=np.float64, copy=True)
            if predicted.shape != x.shape:
                raise ValueError("predicted_c must have shape (N,)")
            if not np.isfinite(predicted).all():
                raise ValueError("predicted_c must be finite")
            predicted.flags.writeable = False
            object.__setattr__(self, "predicted_c", predicted)
        if self.hessian_condition is not None and (
            not isfinite(self.hessian_condition) or self.hessian_condition < 1.0
        ):
            raise ValueError("hessian_condition must be finite and at least one")

    @property
    def sequence_q(self) -> FloatVector:
        """Expose the optimized duty sequence under the controller-facing name."""
        return self.x


def _difference_matrix(steps: int) -> FloatVector:
    difference = np.eye(steps, dtype=np.float64)
    if steps > 1:
        difference[np.arange(1, steps), np.arange(steps - 1)] = -1.0
    return difference


def condense_cost(
    prediction: AffinePrediction,
    setpoint_c: float,
    q_previous: float,
    weights: MPCWeights,
) -> tuple[FloatVector, FloatVector]:
    """Return ``H, f`` for ``0.5 q' H q + f' q`` from an affine prediction."""
    if not isfinite(setpoint_c):
        raise ValueError("setpoint_c must be finite")
    if not isfinite(q_previous) or not 0.0 <= q_previous <= 1.0:
        raise ValueError("q_previous must be within [0, 1]")

    free = prediction.free_output_c
    response = prediction.input_response_c
    steps = free.size
    error_weights = np.full(steps, weights.temperature, dtype=np.float64)
    error_weights[-1] += weights.terminal
    difference = _difference_matrix(steps)
    previous_move = np.zeros(steps, dtype=np.float64)
    previous_move[0] = q_previous
    error = free - setpoint_c

    hessian = 2.0 * (
        response.T @ (error_weights[:, np.newaxis] * response)
        + weights.move * (difference.T @ difference)
    )
    hessian = 0.5 * (hessian + hessian.T)
    linear = 2.0 * (
        response.T @ (error_weights * error)
        - weights.move * (difference.T @ previous_move)
    )
    return hessian, linear


def _objective(hessian: FloatVector, linear: FloatVector, q: FloatVector) -> float:
    return float(0.5 * q @ hessian @ q + linear @ q)


def _validate_qp(
    hessian: npt.ArrayLike,
    linear: npt.ArrayLike,
    lower: npt.ArrayLike,
    upper: npt.ArrayLike,
    warm_start: npt.ArrayLike,
) -> tuple[FloatVector, FloatVector, FloatVector, FloatVector, FloatVector]:
    H = np.asarray(hessian, dtype=np.float64)
    f = np.asarray(linear, dtype=np.float64)
    lo = np.asarray(lower, dtype=np.float64)
    hi = np.asarray(upper, dtype=np.float64)
    start = np.asarray(warm_start, dtype=np.float64)
    if H.ndim != 2 or H.shape[0] != H.shape[1]:
        raise ValueError("H must be square")
    if any(vector.shape != (H.shape[0],) for vector in (f, lo, hi, start)):
        raise ValueError("QP vectors must match H")
    if not all(np.isfinite(values).all() for values in (H, f, lo, hi, start)):
        raise ValueError("QP inputs must be finite")
    if np.any(lo > hi):
        raise ValueError("lower bounds must not exceed upper bounds")
    if not np.allclose(H, H.T, rtol=0.0, atol=1e-12):
        raise ValueError("H must be symmetric")
    return H, f, lo, hi, start


def _original_coordinate_kkt_residual(
    hessian: FloatVector,
    linear: FloatVector,
    x: FloatVector,
    lower: FloatVector,
    upper: FloatVector,
) -> float:
    """Return the infinity-norm box KKT residual in the caller's coordinates."""
    gradient = hessian @ x + linear
    bound_epsilon = 64.0 * _EPSILON * np.maximum(
        1.0, np.maximum(np.abs(lower), np.abs(upper))
    )
    projected = gradient.copy()
    at_lower = x <= lower + bound_epsilon
    at_upper = x >= upper - bound_epsilon
    projected[at_lower] = np.minimum(projected[at_lower], 0.0)
    projected[at_upper] = np.maximum(projected[at_upper], 0.0)
    return float(np.max(np.abs(projected))) if projected.size else 0.0


def _clip_roundoff_box_point(
    x: FloatVector, lower: FloatVector, upper: FloatVector
) -> FloatVector | None:
    """Clip only machine-roundoff bound drift, rejecting every substantive violation."""
    if not np.isfinite(x).all():
        return None
    clipped = np.clip(x, lower, upper)
    bound_epsilon = 64.0 * _EPSILON * np.maximum(
        1.0, np.maximum(np.abs(lower), np.abs(upper))
    )
    if np.any(np.abs(x - clipped) > bound_epsilon):
        return None
    return clipped

def _scaled_newton_step(
    scaled_hessian: FloatVector, gradient: FloatVector, free: npt.NDArray[np.bool_]
) -> FloatVector:
    """Solve the free Newton system with deterministic SVD refinement."""
    step = np.zeros(gradient.size, dtype=np.float64)
    reduced = scaled_hessian[np.ix_(free, free)]
    reduced_gradient = gradient[free]
    solution = -np.linalg.lstsq(reduced, reduced_gradient, rcond=None)[0]
    for _ in range(3):
        correction = -np.linalg.lstsq(
            reduced, reduced @ solution + reduced_gradient, rcond=None
        )[0]
        solution += correction
    step[free] = solution
    return step



def _bvls_box_qp(
    hessian: FloatVector,
    linear: FloatVector,
    lower: FloatVector,
    upper: FloatVector,
    max_iterations: int,
) -> tuple[FloatVector, int] | None:
    """Solve free coordinates as bounded least squares after exact-bound elimination."""
    fixed = lower == upper
    x = lower.copy()
    free = ~fixed
    if not np.any(free):
        return x, 0
    reduced_hessian = hessian[np.ix_(free, free)]
    reduced_linear = linear[free] + hessian[np.ix_(free, fixed)] @ x[fixed]
    values, vectors = np.linalg.eigh(reduced_hessian)
    if float(values[0]) <= _EPSILON:
        return None
    root = np.sqrt(values)[:, np.newaxis] * vectors.T
    target = -np.linalg.solve(root.T, reduced_linear)
    result = lsq_linear(
        root,
        target,
        bounds=(lower[free], upper[free]),
        method="bvls",
        tol=1e-14,
        max_iter=max_iterations,
    )
    x[free] = np.asarray(result.x, dtype=np.float64)
    return x, int(result.nit)

def projected_gradient_qp(
    H: npt.ArrayLike,
    f: npt.ArrayLike,
    lower: npt.ArrayLike,
    upper: npt.ArrayLike,
    warm_start: npt.ArrayLike,
    *,
    max_iterations: int = 10_000,
    tolerance: float = 1e-7,
) -> SolveResult:
    """Solve a PSD box QP by diagonally scaled primal active-set iterations."""
    hessian, linear, lo, hi, start = _validate_qp(H, f, lower, upper, warm_start)
    if max_iterations < 1:
        raise ValueError("max_iterations must be positive")
    if not isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError("tolerance must be finite and positive")

    eigenvalues = np.linalg.eigvalsh(hessian)
    spectral_scale = max(1.0, float(abs(eigenvalues[-1])))
    semidefinite_tolerance = 64.0 * _EPSILON * spectral_scale
    if float(eigenvalues[0]) < -semidefinite_tolerance:
        raise ValueError("H must be positive semidefinite")
    condition = (
        float(eigenvalues[-1] / eigenvalues[0])
        if float(eigenvalues[0]) > semidefinite_tolerance
        else None
    )
    if np.all(lo == hi):
        return SolveResult(
            lo,
            _objective(hessian, linear, lo),
            0,
            0.0,
            hessian_condition=condition,
        )
    if np.count_nonzero(hessian) == 0:
        x = np.where(linear > 0.0, lo, np.where(linear < 0.0, hi, np.clip(start, lo, hi)))
        return SolveResult(x, float(linear @ x), 0, 0.0, hessian_condition=condition)
    iterations_used = 0
    reserved_bvls_iterations = linear.size + 1
    bvls_budget = max_iterations - reserved_bvls_iterations
    bvls_result = (
        _bvls_box_qp(hessian, linear, lo, hi, bvls_budget)
        if bvls_budget > 0
        else None
    )
    if bvls_result is not None:
        x, bvls_iterations = bvls_result
        iterations_used = 1 + max(linear.size, bvls_iterations)
        exact_box_x = _clip_roundoff_box_point(x, lo, hi)
        if exact_box_x is not None:
            residual = _original_coordinate_kkt_residual(
                hessian, linear, exact_box_x, lo, hi
            )
            if np.isfinite(residual) and (
                residual <= tolerance or iterations_used >= max_iterations
            ):
                return SolveResult(
                    exact_box_x,
                    _objective(hessian, linear, exact_box_x),
                    iterations_used,
                    residual,
                    hessian_condition=condition,
                )

    scale = np.sqrt(np.maximum(np.diag(hessian), _EPSILON))
    scaled_hessian = hessian / np.outer(scale, scale)
    scaled_linear = linear / scale
    scaled_lower = lo * scale
    scaled_upper = hi * scale
    y = np.clip(start, lo, hi) * scale
    bound_epsilon = 64.0 * _EPSILON * np.maximum(
        1.0, np.maximum(np.abs(scaled_lower), np.abs(scaled_upper))
    )

    for iteration in range(1, max_iterations - iterations_used + 1):
        x = np.clip(y / scale, lo, hi)
        residual = _original_coordinate_kkt_residual(hessian, linear, x, lo, hi)
        if residual <= tolerance:
            return SolveResult(
                x,
                _objective(hessian, linear, x),
                iterations_used + iteration - 1,
                residual,
                hessian_condition=condition,
            )

        gradient = scaled_hessian @ y + scaled_linear
        at_lower = y <= scaled_lower + bound_epsilon
        at_upper = y >= scaled_upper - bound_epsilon
        free = ~(at_lower | at_upper)
        original_gradient = hessian @ x + linear
        release = (at_lower & (original_gradient < -tolerance)) | (
            at_upper & (original_gradient > tolerance)
        )
        free |= release
        if not np.any(free):
            violation = np.where(
                at_lower,
                np.maximum(-original_gradient, 0.0),
                np.maximum(original_gradient, 0.0),
            )
            free[int(np.argmax(violation))] = True

        step = _scaled_newton_step(scaled_hessian, gradient, free)
        if float(gradient @ step) >= 0.0:
            step.fill(0.0)
            step[free] = -gradient[free]

        alpha = 1.0
        rising = step > 0.0
        falling = step < 0.0
        if np.any(rising):
            alpha = min(alpha, float(np.min((scaled_upper[rising] - y[rising]) / step[rising])))
        if np.any(falling):
            alpha = min(alpha, float(np.min((scaled_lower[falling] - y[falling]) / step[falling])))
        if alpha <= _EPSILON:
            y = np.clip(y, scaled_lower, scaled_upper)
        else:
            y = np.clip(y + alpha * step, scaled_lower, scaled_upper)

    x = np.clip(y / scale, lo, hi)
    if not np.isfinite(x).all():
        x = np.clip(start, lo, hi)
    residual = _original_coordinate_kkt_residual(hessian, linear, x, lo, hi)
    if not isfinite(residual):
        raise FloatingPointError("box QP fallback produced a non-finite KKT certificate")
    return SolveResult(
        x,
        _objective(hessian, linear, x),
        max_iterations,
        residual,
        hessian_condition=condition,
    )


class LinearMPC:
    """Warm-started common controller consuming one model arm's affine map."""

    def __init__(self, config: MPCConfig) -> None:
        self._config = config
        self._warm_start: FloatVector | None = None
        self._q_previous = 0.0

    def solve(
        self,
        prediction: AffinePrediction,
        *,
        setpoint_c: float,
        q_previous: float | None = None,
    ) -> SolveResult:
        """Optimize exactly the configured horizon and retain a shifted warm start."""
        if prediction.free_output_c.size != self._config.horizon_steps:
            raise ValueError("prediction length must equal the selected horizon")
        previous = self._q_previous if q_previous is None else q_previous
        H, f = condense_cost(prediction, setpoint_c, previous, self._config.weights)
        if self._warm_start is None:
            warm_start = np.full(self._config.horizon_steps, previous, dtype=np.float64)
        else:
            warm_start = self._warm_start
        result = projected_gradient_qp(
            H,
            f,
            np.zeros(self._config.horizon_steps),
            np.ones(self._config.horizon_steps),
            warm_start,
            max_iterations=self._config.max_iterations,
            tolerance=self._config.tolerance,
        )
        self._warm_start = np.concatenate((result.x[1:], result.x[-1:]))
        self._q_previous = float(result.x[0])
        predicted = prediction.free_output_c + prediction.input_response_c @ result.x
        return SolveResult(
            result.x,
            result.objective,
            result.iterations,
            result.kkt_residual,
            predicted,
            result.hessian_condition,
        )
