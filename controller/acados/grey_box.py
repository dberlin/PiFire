"""Owned, deterministic Python interface to the generated grey-box solver."""

from __future__ import annotations

import ctypes
import math
from numbers import Real
import sys
import threading
from types import TracebackType
from typing import Any, Self
import weakref

import numpy as np
import numpy.typing as npt

from . import _ffi
from .contracts import (
    GREY_STATE_SIZE,
    GreyBoxMPCConfig,
    GreyBoxSolve,
    SolverDiagnostics,
    SolverError,
)

_STATUS_NAMES = {
    _ffi.STATUS_INVALID_ARGUMENT: "invalid argument",
    _ffi.STATUS_STRUCT_SIZE_MISMATCH: "ABI structure size mismatch",
    _ffi.STATUS_ALLOCATION_FAILURE: "native allocation failure",
    _ffi.STATUS_BACKEND_FAILURE: "acados backend failure",
    _ffi.STATUS_INVALID_SOLUTION: "invalid solver result",
}


def _finite_scalar(value: Real, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite real number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    return normalized


def _bounded_load(value: Real, name: str) -> float:
    normalized = _finite_scalar(value, name)
    if not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")
    return normalized


def _state_array(state: npt.ArrayLike) -> npt.NDArray[np.float64]:
    try:
        normalized = np.asarray(state, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError("state must be a numeric array with shape (10,)") from error
    if normalized.shape != (GREY_STATE_SIZE,):
        raise ValueError(f"state must have shape ({GREY_STATE_SIZE},)")
    if not np.isfinite(normalized).all():
        raise ValueError("state must contain only finite values")
    return np.ascontiguousarray(normalized)


def _native_config(config: GreyBoxMPCConfig) -> _ffi.GreyConfig:
    return _ffi.GreyConfig(
        struct_size=ctypes.sizeof(_ffi.GreyConfig),
        horizon_steps=config.horizon_steps,
        C_c=config.C_c,
        h_amb=config.h_amb,
        T_amb=config.T_amb,
        theta=config.theta,
        K_Q=config.K_Q,
        sigma=config.sigma,
        temperature_weight=config.temperature_weight,
        terminal_weight=config.terminal_weight,
        move_weight=config.move_weight,
        residual_weight=config.residual_weight,
        max_iterations=config.max_iterations,
    )


def _bounded_native_float(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        return sys.float_info.max
    return normalized


def _diagnostics(native: _ffi.GreyDiagnostics) -> SolverDiagnostics:
    return SolverDiagnostics(
        status=int(native.status),
        backend_status=int(native.backend_status),
        iterations=max(0, int(native.iterations)),
        solve_time_s=_bounded_native_float(native.solve_time_s),
        objective=(
            float(native.objective)
            if math.isfinite(float(native.objective))
            else 0.0
        ),
        kkt_residual=_bounded_native_float(native.kkt_residual),
        constraint_residual=_bounded_native_float(native.constraint_residual),
        warm_started=bool(native.warm_started),
    )

def _valid_native_diagnostics(native: _ffi.GreyDiagnostics) -> bool:
    finite_nonnegative = (
        float(native.solve_time_s),
        float(native.kkt_residual),
        float(native.constraint_residual),
    )
    return (
        int(native.struct_size) == ctypes.sizeof(_ffi.GreyDiagnostics)
        and int(native.iterations) >= 0
        and int(native.warm_started) in (0, 1)
        and math.isfinite(float(native.objective))
        and all(math.isfinite(value) and value >= 0.0 for value in finite_nonnegative)
    )


def _status_detail(status: int) -> str:
    return _STATUS_NAMES.get(status, f"unknown native status {status}")


class AcadosGreyBoxMPC:
    """One isolated native grey-box MPC handle with deterministic ownership."""

    def __init__(self, config: GreyBoxMPCConfig | None = None) -> None:
        resolved = GreyBoxMPCConfig() if config is None else config
        if not isinstance(resolved, GreyBoxMPCConfig):
            raise ValueError("config must be a GreyBoxMPCConfig")
        status, handle = _ffi.create(_native_config(resolved))
        if status != _ffi.STATUS_SUCCESS or not handle:
            if handle:
                _ffi.destroy(handle)
            raise RuntimeError(
                "Failed to create grey-box solver: " + _status_detail(status)
            )
        self.config = resolved
        self._handle: Any = handle
        self._lock = threading.Lock()
        self._finalizer = weakref.finalize(self, _ffi.destroy, handle)

    @property
    def closed(self) -> bool:
        return not self._finalizer.alive

    def _require_open(self) -> Any:
        if self.closed:
            raise RuntimeError("AcadosGreyBoxMPC is closed")
        return self._handle

    def solve(
        self,
        state: npt.ArrayLike,
        *,
        setpoint_c: float | int,
        q_previous: float | int,
        equilibrium_q: float | int,
    ) -> GreyBoxSolve:
        state_values = _state_array(state)
        setpoint = _finite_scalar(setpoint_c, "setpoint_c")
        previous = _bounded_load(q_previous, "q_previous")
        equilibrium = _bounded_load(equilibrium_q, "equilibrium_q")

        native_input = _ffi.GreySolveInput(
            struct_size=ctypes.sizeof(_ffi.GreySolveInput),
            setpoint_c=setpoint,
            q_previous=previous,
            equilibrium_q=equilibrium,
        )
        native_input.state[:] = state_values
        native_output = _ffi.GreySolveOutput(
            struct_size=ctypes.sizeof(_ffi.GreySolveOutput)
        )

        with self._lock:
            handle = self._require_open()
            status = _ffi.solve(handle, native_input, native_output)
        diagnostics_are_valid = _valid_native_diagnostics(native_output.diagnostics)
        diagnostics = _diagnostics(native_output.diagnostics)
        if not diagnostics_are_valid:
            raise SolverError(
                "Native grey-box diagnostics must match ABI v2 and be finite",
                diagnostics,
            )
        if diagnostics.status != status:
            raise SolverError(
                "Native grey-box status does not match structured diagnostics",
                diagnostics,
            )
        if status != _ffi.STATUS_SUCCESS:
            raise SolverError(
                "Grey-box solve failed: " + _status_detail(status),
                diagnostics,
            )

        sequence_length = int(native_output.sequence_length)
        if sequence_length != self.config.horizon_steps:
            raise SolverError(
                "Native sequence_length does not match the configured horizon: "
                f"expected {self.config.horizon_steps}, found {sequence_length}",
                diagnostics,
            )

        sequence_q = np.ctypeslib.as_array(native_output.sequence_q)[:sequence_length]
        sequence_residual = np.ctypeslib.as_array(
            native_output.sequence_residual
        )[:sequence_length]
        try:
            return GreyBoxSolve(
                sequence_q=sequence_q,
                sequence_residual=sequence_residual,
                objective=float(native_output.objective),
                diagnostics=diagnostics,
            )
        except ValueError as error:
            raise SolverError(
                f"Native grey-box result must be finite and valid: {error}",
                diagnostics,
            ) from error

    def reset(self) -> None:
        with self._lock:
            handle = self._require_open()
            status = _ffi.reset(handle)
        if status != _ffi.STATUS_SUCCESS:
            raise RuntimeError(
                "Failed to reset grey-box solver: " + _status_detail(status)
            )

    def close(self) -> None:
        with self._lock:
            if self._finalizer.alive:
                self._finalizer()

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
