"""Certified deterministic condensed linear MPC for scalar combustion duty."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from numbers import Integral, Real

import numpy as np
import numpy.typing as npt
from scipy.optimize import lsq_linear

from .contracts import AffinePrediction, FloatArray

_EPSILON = np.finfo(np.float64).eps


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _duty(value: object, name: str) -> float:
    result = _finite(value, name)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be within [0, 1]")
    return result


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return int(value)


@dataclass(frozen=True, slots=True)
class LinearMPCConfig:
    """Immutable horizon, objective, and certificate parameters for :class:`LinearMPC`."""

    horizon_steps: int
    temperature_weight: float = 1.0
    terminal_weight: float = 4.0
    move_weight: float = 0.05
    max_iterations: int = 10_000
    tolerance: float = 1e-7

    def __post_init__(self) -> None:
        object.__setattr__(self, "horizon_steps", _nonnegative_int(self.horizon_steps, "horizon_steps"))
        for name in ("temperature_weight", "terminal_weight", "move_weight"):
            value = _finite(getattr(self, name), name)
            if value < 0.0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        if self.temperature_weight == self.terminal_weight == self.move_weight == 0.0:
            raise ValueError("at least one MPC weight must be positive")
        max_iterations = _nonnegative_int(self.max_iterations, "max_iterations")
        if max_iterations == 0:
            raise ValueError("max_iterations must be positive")
        object.__setattr__(self, "max_iterations", max_iterations)
        tolerance = _finite(self.tolerance, "tolerance")
        if tolerance <= 0.0:
            raise ValueError("tolerance must be positive")
        object.__setattr__(self, "tolerance", tolerance)


@dataclass(frozen=True, slots=True)
class LinearSolve:
    """An immutable certified scalar-duty MPC solution."""

    sequence_q: FloatArray
    objective: float
    kkt_residual: float
    iterations: int
    hessian_condition: float

    def __post_init__(self) -> None:
        sequence = np.array(self.sequence_q, dtype=np.float64, copy=True)
        if sequence.ndim != 1 or not np.isfinite(sequence).all():
            raise ValueError("sequence_q must be a finite one-dimensional array")
        if np.any(sequence < 0.0) or np.any(sequence > 1.0):
            raise ValueError("sequence_q must be within [0, 1]")
        sequence.setflags(write=False)
        object.__setattr__(self, "sequence_q", sequence)
        object.__setattr__(self, "objective", _finite(self.objective, "objective"))
        object.__setattr__(self, "kkt_residual", _finite(self.kkt_residual, "kkt_residual"))
        object.__setattr__(self, "iterations", _nonnegative_int(self.iterations, "iterations"))
        condition = _finite(self.hessian_condition, "hessian_condition")
        if condition < 1.0:
            raise ValueError("hessian_condition must be at least one")
        object.__setattr__(self, "hessian_condition", condition)


@dataclass(frozen=True, slots=True)
class BoxQPSolve:
    """Internal bounded-QP result, which may carry a non-finite condition number."""

    x: FloatArray
    objective: float
    iterations: int
    kkt_residual: float
    hessian_condition: float


def _difference_matrix(steps: int) -> FloatArray:
    difference = np.eye(steps, dtype=np.float64)
    if steps > 1:
        difference[np.arange(1, steps), np.arange(steps - 1)] = -1.0
    return difference


def condense_cost(
    prediction: AffinePrediction,
    setpoint_c: float,
    q_previous: float,
    config: LinearMPCConfig,
) -> tuple[FloatArray, FloatArray]:
    """Condense the tracking and move objective into ``0.5 q' H q + f' q``."""
    setpoint = _finite(setpoint_c, "setpoint_c")
    previous = _duty(q_previous, "q_previous")
    steps = prediction.free_output_c.size
    if steps != config.horizon_steps:
        raise ValueError("prediction length must equal the configured horizon")
    if steps == 0:
        return np.empty((0, 0), dtype=np.float64), np.empty(0, dtype=np.float64)
    error_weights = np.full(steps, config.temperature_weight, dtype=np.float64)
    error_weights[-1] += config.terminal_weight
    difference = _difference_matrix(steps)
    previous_move = np.zeros(steps, dtype=np.float64)
    previous_move[0] = previous
    response = prediction.input_response_c
    error = prediction.free_output_c - setpoint
    hessian = 2.0 * (
        response.T @ (error_weights[:, np.newaxis] * response) + config.move_weight * (difference.T @ difference)
    )
    hessian = 0.5 * (hessian + hessian.T)
    linear = 2.0 * (response.T @ (error_weights * error) - config.move_weight * (difference.T @ previous_move))
    return hessian, linear


def _objective(hessian: FloatArray, linear: FloatArray, q: FloatArray) -> float:
    return float(0.5 * q @ hessian @ q + linear @ q)


def _validate_qp(
    hessian: npt.ArrayLike,
    linear: npt.ArrayLike,
    lower: npt.ArrayLike,
    upper: npt.ArrayLike,
    warm_start: npt.ArrayLike,
) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray]:
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


def _kkt_residual(
    hessian: FloatArray,
    linear: FloatArray,
    x: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
) -> float:
    gradient = hessian @ x + linear
    bound_epsilon = 64.0 * _EPSILON * np.maximum(1.0, np.maximum(np.abs(lower), np.abs(upper)))
    projected = gradient.copy()
    at_lower = x <= lower + bound_epsilon
    at_upper = x >= upper - bound_epsilon
    projected[at_lower] = np.minimum(projected[at_lower], 0.0)
    projected[at_upper] = np.maximum(projected[at_upper], 0.0)
    return float(np.max(np.abs(projected))) if projected.size else 0.0


def _clip_roundoff_box_point(x: FloatArray, lower: FloatArray, upper: FloatArray) -> FloatArray | None:
    if not np.isfinite(x).all():
        return None
    clipped = np.clip(x, lower, upper)
    bound_epsilon = 64.0 * _EPSILON * np.maximum(1.0, np.maximum(np.abs(lower), np.abs(upper)))
    return clipped if not np.any(np.abs(x - clipped) > bound_epsilon) else None


def _bvls_box_qp(
    hessian: FloatArray,
    linear: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
    max_iterations: int,
) -> tuple[FloatArray, int] | None:
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
) -> BoxQPSolve:
    """Solve a PSD box QP with a deterministic BVLS-first active-set method."""
    hessian, linear, lo, hi, start = _validate_qp(H, f, lower, upper, warm_start)
    if max_iterations < 1:
        raise ValueError("max_iterations must be positive")
    if not isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError("tolerance must be finite and positive")
    if not linear.size:
        return BoxQPSolve(np.empty(0), 0.0, 0, 0.0, 1.0)
    eigenvalues = np.linalg.eigvalsh(hessian)
    spectral_scale = max(1.0, float(abs(eigenvalues[-1])))
    semidefinite_tolerance = 64.0 * _EPSILON * spectral_scale
    if float(eigenvalues[0]) < -semidefinite_tolerance:
        raise ValueError("H must be positive semidefinite")
    condition = (
        float(eigenvalues[-1] / eigenvalues[0]) if float(eigenvalues[0]) > semidefinite_tolerance else float("inf")
    )
    if np.all(lo == hi):
        return BoxQPSolve(lo, _objective(hessian, linear, lo), 0, 0.0, condition)
    if np.count_nonzero(hessian) == 0:
        x = np.where(linear > 0.0, lo, np.where(linear < 0.0, hi, np.clip(start, lo, hi)))
        return BoxQPSolve(x, float(linear @ x), 0, 0.0, condition)
    iterations_used = 0
    reserved_bvls_iterations = linear.size + 1
    bvls_budget = max_iterations - reserved_bvls_iterations
    bvls_result = _bvls_box_qp(hessian, linear, lo, hi, bvls_budget) if bvls_budget > 0 else None
    if bvls_result is not None:
        x, bvls_iterations = bvls_result
        iterations_used = 1 + max(linear.size, bvls_iterations)
        exact_box_x = _clip_roundoff_box_point(x, lo, hi)
        if exact_box_x is not None:
            residual = _kkt_residual(hessian, linear, exact_box_x, lo, hi)
            if np.isfinite(residual) and (residual <= tolerance or iterations_used >= max_iterations):
                return BoxQPSolve(
                    exact_box_x,
                    _objective(hessian, linear, exact_box_x),
                    iterations_used,
                    residual,
                    condition,
                )
    scale = np.sqrt(np.maximum(np.diag(hessian), _EPSILON))
    scaled_hessian = hessian / np.outer(scale, scale)
    scaled_linear = linear / scale
    scaled_lower = lo * scale
    scaled_upper = hi * scale
    y = np.clip(start, lo, hi) * scale
    bound_epsilon = 64.0 * _EPSILON * np.maximum(1.0, np.maximum(np.abs(scaled_lower), np.abs(scaled_upper)))
    for iteration in range(1, max_iterations - iterations_used + 1):
        x = np.clip(y / scale, lo, hi)
        residual = _kkt_residual(hessian, linear, x, lo, hi)
        if residual <= tolerance:
            return BoxQPSolve(x, _objective(hessian, linear, x), iterations_used + iteration - 1, residual, condition)
        gradient = scaled_hessian @ y + scaled_linear
        at_lower = y <= scaled_lower + bound_epsilon
        at_upper = y >= scaled_upper - bound_epsilon
        free = ~(at_lower | at_upper)
        original_gradient = hessian @ x + linear
        free |= (at_lower & (original_gradient < -tolerance)) | (at_upper & (original_gradient > tolerance))
        if not np.any(free):
            violation = np.where(at_lower, np.maximum(-original_gradient, 0.0), np.maximum(original_gradient, 0.0))
            free[int(np.argmax(violation))] = True
        step = np.zeros(gradient.size, dtype=np.float64)
        reduced = scaled_hessian[np.ix_(free, free)]
        reduced_gradient = gradient[free]
        solution = -np.linalg.lstsq(reduced, reduced_gradient, rcond=None)[0]
        for _ in range(3):
            solution += -np.linalg.lstsq(reduced, reduced @ solution + reduced_gradient, rcond=None)[0]
        step[free] = solution
        if float(gradient @ step) >= 0.0:
            step.fill(0.0)
            step[free] = -gradient[free]
        alpha = 1.0
        rising = step > 0.0
        falling = step < 0.0
        if np.any(rising):
            alpha = min(
                alpha,
                float(np.min((scaled_upper[rising] - y[rising]) / step[rising])),
            )
        if np.any(falling):
            alpha = min(
                alpha,
                float(np.min((scaled_lower[falling] - y[falling]) / step[falling])),
            )
        y = np.clip(y if alpha <= _EPSILON else y + alpha * step, scaled_lower, scaled_upper)
    x = np.clip(y / scale, lo, hi)
    residual = _kkt_residual(hessian, linear, x, lo, hi)
    if not isfinite(residual):
        raise FloatingPointError("box QP fallback produced a non-finite KKT certificate")
    return BoxQPSolve(x, _objective(hessian, linear, x), max_iterations, residual, condition)


class LinearMPC:
    """Warm-started scalar-duty MPC that refuses non-certified numerical results."""

    def __init__(self, config: LinearMPCConfig) -> None:
        self._config = config
        self._warm_start: FloatArray | None = None

    @property
    def config(self) -> LinearMPCConfig:
        """Return the immutable policy configuration."""
        return self._config

    def solve(
        self,
        prediction: AffinePrediction,
        *,
        setpoint_c: float,
        q_previous: float,
        equilibrium_q: float,
    ) -> LinearSolve:
        """Optimize one affine horizon with an equilibrium-seeded warm start."""
        previous = _duty(q_previous, "q_previous")
        equilibrium = _duty(equilibrium_q, "equilibrium_q")
        hessian, linear = condense_cost(prediction, setpoint_c, previous, self._config)
        if not self._config.horizon_steps:
            return LinearSolve(np.empty(0), 0.0, 0.0, 0, 1.0)
        warm_start = (
            np.full(self._config.horizon_steps, equilibrium, dtype=np.float64)
            if self._warm_start is None
            else self._warm_start
        )
        result = projected_gradient_qp(
            hessian,
            linear,
            np.zeros(self._config.horizon_steps),
            np.ones(self._config.horizon_steps),
            warm_start,
            max_iterations=self._config.max_iterations,
            tolerance=self._config.tolerance,
        )
        if (
            not np.isfinite(result.x).all()
            or not isfinite(result.objective)
            or not isfinite(result.hessian_condition)
            or not isfinite(result.kkt_residual)
            or result.kkt_residual > self._config.tolerance
        ):
            raise FloatingPointError("linear MPC solver did not produce a certified finite result")
        solve = LinearSolve(
            result.x,
            result.objective,
            result.kkt_residual,
            result.iterations,
            result.hessian_condition,
        )
        self._warm_start = np.concatenate((solve.sequence_q[1:], solve.sequence_q[-1:]))
        self._warm_start.setflags(write=False)
        return solve
