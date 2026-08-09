"""Exact ctypes declarations for the grey-only acados-pifire ABI v2."""

from __future__ import annotations

import ctypes
from functools import cache
from typing import Any

from ._library import load_native

GREY_STATE_SIZE = 10
GREY_HORIZON_CAPACITY = 24

STATUS_SUCCESS = 0
STATUS_INVALID_ARGUMENT = 1
STATUS_STRUCT_SIZE_MISMATCH = 2
STATUS_ALLOCATION_FAILURE = 3
STATUS_BACKEND_FAILURE = 4
STATUS_INVALID_SOLUTION = 5


class GreyHandle(ctypes.Structure):
    """Incomplete native handle owned by the C library."""


GreyHandlePointer = ctypes.POINTER(GreyHandle)


class GreyConfig(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("horizon_steps", ctypes.c_uint32),
        ("C_c", ctypes.c_double),
        ("h_amb", ctypes.c_double),
        ("T_amb", ctypes.c_double),
        ("theta", ctypes.c_double),
        ("K_Q", ctypes.c_double),
        ("sigma", ctypes.c_double),
        ("temperature_weight", ctypes.c_double),
        ("terminal_weight", ctypes.c_double),
        ("move_weight", ctypes.c_double),
        ("residual_weight", ctypes.c_double),
        ("max_iterations", ctypes.c_int32),
    ]


class GreySolveInput(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("state", ctypes.c_double * GREY_STATE_SIZE),
        ("setpoint_c", ctypes.c_double),
        ("q_previous", ctypes.c_double),
        ("equilibrium_q", ctypes.c_double),
    ]


class GreyDiagnostics(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("status", ctypes.c_int32),
        ("backend_status", ctypes.c_int32),
        ("iterations", ctypes.c_int32),
        ("solve_time_s", ctypes.c_double),
        ("objective", ctypes.c_double),
        ("kkt_residual", ctypes.c_double),
        ("constraint_residual", ctypes.c_double),
        ("warm_started", ctypes.c_int32),
    ]


class GreySolveOutput(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32),
        ("sequence_length", ctypes.c_uint32),
        ("sequence_q", ctypes.c_double * GREY_HORIZON_CAPACITY),
        ("sequence_residual", ctypes.c_double * GREY_HORIZON_CAPACITY),
        ("objective", ctypes.c_double),
        ("diagnostics", GreyDiagnostics),
    ]


@cache
def load_grey_api() -> ctypes.CDLL:
    """Resolve grey symbols only after the loader has validated ABI v2."""
    library = load_native()
    library.acados_pifire_grey_create.argtypes = [
        ctypes.POINTER(GreyConfig),
        ctypes.POINTER(GreyHandlePointer),
    ]
    library.acados_pifire_grey_create.restype = ctypes.c_int32
    library.acados_pifire_grey_solve.argtypes = [
        GreyHandlePointer,
        ctypes.POINTER(GreySolveInput),
        ctypes.POINTER(GreySolveOutput),
    ]
    library.acados_pifire_grey_solve.restype = ctypes.c_int32
    library.acados_pifire_grey_reset.argtypes = [GreyHandlePointer]
    library.acados_pifire_grey_reset.restype = ctypes.c_int32
    library.acados_pifire_grey_destroy.argtypes = [GreyHandlePointer]
    library.acados_pifire_grey_destroy.restype = None
    return library


def create(config: GreyConfig) -> tuple[int, Any]:
    handle = GreyHandlePointer()
    status = load_grey_api().acados_pifire_grey_create(
        ctypes.byref(config),
        ctypes.byref(handle),
    )
    return int(status), handle


def solve(
    handle: Any,
    solve_input: GreySolveInput,
    output: GreySolveOutput,
) -> int:
    return int(
        load_grey_api().acados_pifire_grey_solve(
            handle,
            ctypes.byref(solve_input),
            ctypes.byref(output),
        )
    )


def reset(handle: Any) -> int:
    return int(load_grey_api().acados_pifire_grey_reset(handle))


def destroy(handle: Any) -> None:
    load_grey_api().acados_pifire_grey_destroy(handle)
