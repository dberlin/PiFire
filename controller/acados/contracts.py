"""Immutable contracts for the grey-box acados MPC boundary."""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral, Real

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]

GREY_HORIZON_MIN = 5
GREY_HORIZON_CAPACITY = 24
GREY_DELAY_STATES = 8
GREY_STATE_SIZE = 10
GREY_TIMESTEP_S = 25.0
GREY_MIN_DELAY_S = GREY_TIMESTEP_S
GREY_MAX_ITERATIONS = 10


def _finite_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a finite real number")
    normalized = float(value)
    if not np.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError(f"{name} must be an integer")
    return int(value)


def _nonnegative(value: object, name: str) -> float:
    normalized = _finite_float(value, name)
    if normalized < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return normalized


def _positive(value: object, name: str) -> float:
    normalized = _finite_float(value, name)
    if normalized <= 0.0:
        raise ValueError(f"{name} must be positive")
    return normalized


def _fixed_integer(value: object, expected: int, name: str) -> int:
    normalized = _integer(value, name)
    if normalized != expected:
        raise ValueError(f"{name} must be {expected}")
    return normalized


def _fixed_float(value: object, expected: float, name: str) -> float:
    normalized = _finite_float(value, name)
    if normalized != expected:
        raise ValueError(f"{name} must be {expected}")
    return normalized


def _owned_sequence(values: npt.ArrayLike, name: str) -> FloatArray:
    try:
        array = np.array(values, dtype=np.float64, order="C", copy=True)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a numeric sequence") from error
    if array.ndim != 1:
        raise ValueError(f"{name} must be a one-dimensional sequence")
    if not np.isfinite(array).all():
        raise ValueError(f"{name} must contain only finite values")
    array.setflags(write=False)
    return array


@dataclass(frozen=True, slots=True)
class GreyBoxMPCConfig:
    """Physical model, objective, and fixed generated model dimensions."""

    C_c: float = 320.0
    h_amb: float = 0.5
    T_amb: float = 20.0
    theta: float = 50.0
    K_Q: float = 350.0
    sigma: float = 1.4e-9
    horizon_steps: int = GREY_HORIZON_CAPACITY
    delay_states: int = GREY_DELAY_STATES
    state_size: int = GREY_STATE_SIZE
    timestep_s: float = GREY_TIMESTEP_S
    temperature_weight: float = 1.0
    terminal_weight: float = 1.0
    move_weight: float = 0.1
    residual_weight: float = 0.0
    max_iterations: int = GREY_MAX_ITERATIONS

    def __post_init__(self) -> None:
        object.__setattr__(self, "C_c", _positive(self.C_c, "C_c"))
        object.__setattr__(self, "h_amb", _nonnegative(self.h_amb, "h_amb"))
        object.__setattr__(self, "T_amb", _finite_float(self.T_amb, "T_amb"))
        theta = _finite_float(self.theta, "theta")
        if theta < GREY_MIN_DELAY_S:
            raise ValueError(f"theta must be at least {GREY_MIN_DELAY_S}")
        object.__setattr__(self, "theta", theta)
        object.__setattr__(self, "K_Q", _positive(self.K_Q, "K_Q"))
        object.__setattr__(self, "sigma", _nonnegative(self.sigma, "sigma"))

        horizon_steps = _integer(self.horizon_steps, "horizon_steps")
        if not GREY_HORIZON_MIN <= horizon_steps <= GREY_HORIZON_CAPACITY:
            raise ValueError(f"horizon_steps must be between {GREY_HORIZON_MIN} and {GREY_HORIZON_CAPACITY}")
        object.__setattr__(self, "horizon_steps", horizon_steps)
        object.__setattr__(
            self,
            "delay_states",
            _fixed_integer(self.delay_states, GREY_DELAY_STATES, "delay_states"),
        )
        object.__setattr__(
            self,
            "state_size",
            _fixed_integer(self.state_size, GREY_STATE_SIZE, "state_size"),
        )
        object.__setattr__(
            self,
            "timestep_s",
            _fixed_float(self.timestep_s, GREY_TIMESTEP_S, "timestep_s"),
        )

        weight_names = (
            "temperature_weight",
            "terminal_weight",
            "move_weight",
            "residual_weight",
        )
        for name in weight_names:
            object.__setattr__(self, name, _nonnegative(getattr(self, name), name))
        if not any(getattr(self, name) > 0.0 for name in weight_names):
            raise ValueError("at least one MPC weight must be positive")

        max_iterations = _integer(self.max_iterations, "max_iterations")
        if not 1 <= max_iterations <= GREY_MAX_ITERATIONS:
            raise ValueError(f"max_iterations must be between 1 and {GREY_MAX_ITERATIONS}")
        object.__setattr__(self, "max_iterations", max_iterations)


@dataclass(frozen=True, slots=True)
class SolverDiagnostics:
    """Bounded, backend-independent diagnostics for one solver attempt."""

    status: int
    backend_status: int
    iterations: int
    solve_time_s: float
    objective: float
    kkt_residual: float
    constraint_residual: float
    warm_started: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _integer(self.status, "status"))
        object.__setattr__(
            self,
            "backend_status",
            _integer(self.backend_status, "backend_status"),
        )
        iterations = _integer(self.iterations, "iterations")
        if iterations < 0:
            raise ValueError("iterations must be non-negative")
        object.__setattr__(self, "iterations", iterations)
        object.__setattr__(
            self,
            "solve_time_s",
            _nonnegative(self.solve_time_s, "solve_time_s"),
        )
        object.__setattr__(
            self,
            "objective",
            _finite_float(self.objective, "objective"),
        )
        object.__setattr__(
            self,
            "kkt_residual",
            _nonnegative(self.kkt_residual, "kkt_residual"),
        )
        object.__setattr__(
            self,
            "constraint_residual",
            _nonnegative(self.constraint_residual, "constraint_residual"),
        )
        if not isinstance(self.warm_started, bool):
            raise TypeError("warm_started must be a bool")


@dataclass(frozen=True, slots=True)
class GreyBoxSolve:
    """Immutable result containing exactly the configured prediction horizon."""

    sequence_q: FloatArray
    sequence_residual: FloatArray
    objective: float
    diagnostics: SolverDiagnostics

    def __post_init__(self) -> None:
        sequence_q = _owned_sequence(self.sequence_q, "sequence_q")
        sequence_residual = _owned_sequence(
            self.sequence_residual,
            "sequence_residual",
        )
        if sequence_q.size != sequence_residual.size:
            raise ValueError("sequence arrays must have matching lengths")
        if not GREY_HORIZON_MIN <= sequence_q.size <= GREY_HORIZON_CAPACITY:
            raise ValueError(f"sequence length must be between {GREY_HORIZON_MIN} and {GREY_HORIZON_CAPACITY}")
        if not isinstance(self.diagnostics, SolverDiagnostics):
            raise TypeError("diagnostics must be SolverDiagnostics")
        object.__setattr__(self, "sequence_q", sequence_q)
        object.__setattr__(self, "sequence_residual", sequence_residual)
        object.__setattr__(
            self,
            "objective",
            _finite_float(self.objective, "objective"),
        )


class SolverError(RuntimeError):
    """A solver failure that retains structured diagnostics."""

    __slots__ = ("diagnostics",)

    def __init__(self, message: str, diagnostics: SolverDiagnostics) -> None:
        if not isinstance(diagnostics, SolverDiagnostics):
            raise TypeError("diagnostics must be SolverDiagnostics")
        super().__init__(message)
        self.diagnostics = diagnostics
