from __future__ import annotations

import ctypes
import gc
import hashlib
import json
import shutil
import threading
import weakref
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pytest

import controller.acados.grey_box as grey_box_module
import controller.acados._library as library_module
from controller.acados import (
    AcadosGreyBoxMPC,
    GreyBoxMPCConfig,
    SolverDiagnostics,
    SolverError,
    _ffi,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def built_native_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    built_library = PROJECT_ROOT / "build" / "acados-configure" / "native-output" / library_module._library_filename()
    if not built_library.is_file():
        pytest.fail(
            f"Built native library is missing at {built_library}. "
            "Run `cmake --build build/acados-configure -j2 "
            "--target acados_pifire` before this focused gate.",
            pytrace=False,
        )

    package = tmp_path / "checkout" / "controller" / "acados"
    package.mkdir(parents=True)
    with built_library.open("rb") as stream:
        library_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    build_digest = hashlib.sha256(library_digest.encode("ascii")).hexdigest()
    release = package.parent / "_native" / "releases" / build_digest
    release.mkdir(parents=True)
    library_path = release / library_module._library_filename()
    shutil.copy2(built_library, library_path)
    (release / "build-manifest.json").write_text(
        json.dumps(
            {
                "build_digest": build_digest,
                "library_sha256": library_digest,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (release.parent.parent / "current").symlink_to(release, target_is_directory=True)

    monkeypatch.setattr(library_module, "__file__", str(package / "_library.py"))
    _ffi.load_grey_api.cache_clear()
    library_module.load_native.cache_clear()
    yield library_path
    _ffi.load_grey_api.cache_clear()
    library_module.load_native.cache_clear()


def _state(temperature_c: float = 100.0, disturbance: float = 0.0) -> np.ndarray:
    return np.array([0.2] * 8 + [temperature_c, disturbance], dtype=np.float64)


def _solve(solver: AcadosGreyBoxMPC, state: np.ndarray | None = None):
    return solver.solve(
        _state() if state is None else state,
        setpoint_c=120.0,
        q_previous=0.2,
        equilibrium_q=0.25,
    )


def _native_config(horizon_steps: int) -> _ffi.GreyConfig:
    config = _ffi.GreyConfig()
    config.struct_size = ctypes.sizeof(_ffi.GreyConfig)
    config.horizon_steps = horizon_steps
    config.C_c = 320.0
    config.h_amb = 0.5
    config.T_amb = 20.0
    config.theta = 50.0
    config.K_Q = 350.0
    config.sigma = 0.0
    config.temperature_weight = 1.0
    config.terminal_weight = 1.0
    config.move_weight = 0.1
    config.residual_weight = 0.0
    config.max_iterations = 10
    return config


def _native_input(
    *,
    state: np.ndarray | None = None,
    setpoint_c: float = 120.0,
    q_previous: float = 0.2,
    equilibrium_q: float = 0.25,
) -> _ffi.GreySolveInput:
    solve_input = _ffi.GreySolveInput()
    solve_input.struct_size = ctypes.sizeof(_ffi.GreySolveInput)
    solve_input.state[:] = _state() if state is None else state
    solve_input.setpoint_c = setpoint_c
    solve_input.q_previous = q_previous
    solve_input.equilibrium_q = equilibrium_q
    return solve_input


def _sentinel_output() -> _ffi.GreySolveOutput:
    output = _ffi.GreySolveOutput()
    output.struct_size = ctypes.sizeof(_ffi.GreySolveOutput)
    output.sequence_length = 23
    output.sequence_q[:] = [7.0] * 24
    output.sequence_residual[:] = [-7.0] * 24
    output.objective = 7.0
    return output


def _populate_successful_output(
    output: _ffi.GreySolveOutput,
    *,
    horizon_steps: int,
    q: float = 0.25,
    residual: float = 0.0,
) -> None:
    output.sequence_length = horizon_steps
    output.sequence_q[:] = [q] * 24
    output.sequence_residual[:] = [residual] * 24
    output.objective = 1.0
    output.diagnostics.struct_size = ctypes.sizeof(_ffi.GreyDiagnostics)
    output.diagnostics.status = _ffi.STATUS_SUCCESS
    output.diagnostics.backend_status = 0
    output.diagnostics.iterations = 1
    output.diagnostics.solve_time_s = 0.001
    output.diagnostics.objective = 1.0
    output.diagnostics.kkt_residual = 0.0
    output.diagnostics.constraint_residual = 0.0
    output.diagnostics.warm_started = 0


def _advance_physics(
    state: np.ndarray,
    residual: float,
    equilibrium_q: float,
    config: GreyBoxMPCConfig,
) -> np.ndarray:
    def rhs(current: np.ndarray) -> np.ndarray:
        q_total = equilibrium_q + residual
        delay_time_constant = config.theta / 8.0
        derivatives = np.empty(10, dtype=np.float64)
        derivatives[0] = (q_total - current[0]) / delay_time_constant
        for index in range(1, 8):
            derivatives[index] = (current[index - 1] - current[index]) / delay_time_constant
        derivatives[8] = (
            config.K_Q * current[7]
            - config.h_amb * (current[8] - config.T_amb)
            - config.sigma * ((current[8] + 273.15) ** 4 - (config.T_amb + 273.15) ** 4)
            + current[9]
        ) / config.C_c
        derivatives[9] = 0.0
        return derivatives

    current = state.copy()
    step = 25.0 / 8.0
    for _ in range(8):
        k1 = rhs(current)
        k2 = rhs(current + 0.5 * step * k1)
        k3 = rhs(current + 0.5 * step * k2)
        k4 = rhs(current + step * k3)
        current += step * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
    return current


def _unit_scaled_objective(
    config: GreyBoxMPCConfig,
    state: np.ndarray,
    residuals: np.ndarray,
    *,
    setpoint_c: float,
    q_previous: float,
    equilibrium_q: float,
) -> float:
    current = state.copy()
    previous_residual = q_previous - equilibrium_q
    running = 0.0
    for residual in residuals:
        running += config.temperature_weight * (current[8] - setpoint_c) ** 2
        running += config.move_weight * (float(residual) - previous_residual) ** 2
        running += config.residual_weight * float(residual) ** 2
        current = _advance_physics(
            current,
            float(residual),
            equilibrium_q,
            config,
        )
        previous_residual = float(residual)
    terminal = config.terminal_weight * (current[8] - setpoint_c) ** 2
    # acados NONLINEAR_LS uses 0.5 * y.T @ W @ y.
    return 0.5 * (running + terminal)


def test_ctypes_layouts_statuses_and_prototypes_match_grey_only_abi_v2(
    built_native_release: Path,
) -> None:
    assert _ffi.GREY_HORIZON_CAPACITY == 24
    assert (
        _ffi.STATUS_SUCCESS,
        _ffi.STATUS_INVALID_ARGUMENT,
        _ffi.STATUS_STRUCT_SIZE_MISMATCH,
        _ffi.STATUS_ALLOCATION_FAILURE,
        _ffi.STATUS_BACKEND_FAILURE,
        _ffi.STATUS_INVALID_SOLUTION,
    ) == (0, 1, 2, 3, 4, 5)
    assert _ffi.GreyConfig._fields_ == [
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
    assert _ffi.GreySolveInput._fields_ == [
        ("struct_size", ctypes.c_uint32),
        ("state", ctypes.c_double * 10),
        ("setpoint_c", ctypes.c_double),
        ("q_previous", ctypes.c_double),
        ("equilibrium_q", ctypes.c_double),
    ]
    assert _ffi.GreyDiagnostics._fields_ == [
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
    assert _ffi.GreySolveOutput._fields_ == [
        ("struct_size", ctypes.c_uint32),
        ("sequence_length", ctypes.c_uint32),
        ("sequence_q", ctypes.c_double * 24),
        ("sequence_residual", ctypes.c_double * 24),
        ("objective", ctypes.c_double),
        ("diagnostics", _ffi.GreyDiagnostics),
    ]

    library = _ffi.load_grey_api()
    handle_pointer = ctypes.POINTER(_ffi.GreyHandle)
    assert library.acados_pifire_grey_create.argtypes == [
        ctypes.POINTER(_ffi.GreyConfig),
        ctypes.POINTER(handle_pointer),
    ]
    assert library.acados_pifire_grey_create.restype is ctypes.c_int32
    assert library.acados_pifire_grey_solve.argtypes == [
        handle_pointer,
        ctypes.POINTER(_ffi.GreySolveInput),
        ctypes.POINTER(_ffi.GreySolveOutput),
    ]
    assert library.acados_pifire_grey_solve.restype is ctypes.c_int32
    assert library.acados_pifire_grey_reset.argtypes == [handle_pointer]
    assert library.acados_pifire_grey_reset.restype is ctypes.c_int32
    assert library.acados_pifire_grey_destroy.argtypes == [handle_pointer]
    assert library.acados_pifire_grey_destroy.restype is None


@pytest.mark.parametrize("horizon_steps", [4, 25])
def test_native_create_rejects_unsupported_horizon_without_returning_a_handle(
    horizon_steps: int,
    built_native_release: Path,
) -> None:
    status, handle = _ffi.create(_native_config(horizon_steps))

    assert status == _ffi.STATUS_INVALID_ARGUMENT
    assert not handle


def test_native_struct_size_guards_reject_mismatched_callers(
    built_native_release: Path,
) -> None:
    bad_config = _native_config(5)
    bad_config.struct_size -= 1
    status, handle = _ffi.create(bad_config)
    assert status == _ffi.STATUS_STRUCT_SIZE_MISMATCH
    assert not handle

    status, handle = _ffi.create(_native_config(5))
    assert status == _ffi.STATUS_SUCCESS
    try:
        bad_input = _native_input()
        bad_input.struct_size -= 1
        assert _ffi.solve(handle, bad_input, _sentinel_output()) == _ffi.STATUS_STRUCT_SIZE_MISMATCH

        bad_output = _sentinel_output()
        bad_output.struct_size -= 1
        assert _ffi.solve(handle, _native_input(), bad_output) == _ffi.STATUS_STRUCT_SIZE_MISMATCH
    finally:
        _ffi.destroy(handle)


@pytest.mark.parametrize(
    ("invalid_argument", "expected_status"),
    [
        ("null-handle", _ffi.STATUS_INVALID_ARGUMENT),
        ("null-input", _ffi.STATUS_INVALID_ARGUMENT),
        ("mismatched-input", _ffi.STATUS_STRUCT_SIZE_MISMATCH),
    ],
)
def test_native_guard_failures_fully_zero_valid_output(
    invalid_argument: str,
    expected_status: int,
    built_native_release: Path,
) -> None:
    status, handle = _ffi.create(_native_config(5))
    assert status == _ffi.STATUS_SUCCESS
    output = _sentinel_output()
    solve_input = _native_input()
    if invalid_argument == "mismatched-input":
        solve_input.struct_size -= 1

    try:
        library = _ffi.load_grey_api()
        status = library.acados_pifire_grey_solve(
            None if invalid_argument == "null-handle" else handle,
            None if invalid_argument == "null-input" else ctypes.byref(solve_input),
            ctypes.byref(output),
        )
    finally:
        _ffi.destroy(handle)

    assert status == expected_status
    assert output.sequence_length == 0
    np.testing.assert_array_equal(output.sequence_q, np.zeros(24))
    np.testing.assert_array_equal(output.sequence_residual, np.zeros(24))
    assert output.objective == 0.0
    assert output.diagnostics.status == expected_status


@pytest.mark.parametrize("horizon_steps", [5, 24])
def test_native_solve_sets_selected_length_and_zeroes_unused_capacity(
    horizon_steps: int,
    built_native_release: Path,
) -> None:
    status, handle = _ffi.create(_native_config(horizon_steps))
    assert status == _ffi.STATUS_SUCCESS
    output = _sentinel_output()
    try:
        status = _ffi.solve(handle, _native_input(), output)
    finally:
        _ffi.destroy(handle)

    q = np.ctypeslib.as_array(output.sequence_q)
    residual = np.ctypeslib.as_array(output.sequence_residual)
    assert status == _ffi.STATUS_SUCCESS
    assert output.sequence_length == horizon_steps
    assert np.isfinite(q[:horizon_steps]).all()
    assert np.isfinite(residual[:horizon_steps]).all()
    np.testing.assert_array_equal(q[horizon_steps:], np.zeros(24 - horizon_steps))
    np.testing.assert_array_equal(residual[horizon_steps:], np.zeros(24 - horizon_steps))
    assert np.isfinite(output.objective)
    assert output.diagnostics.status == _ffi.STATUS_SUCCESS


def test_native_failed_solve_zeroes_entire_output_capacity(
    built_native_release: Path,
) -> None:
    status, handle = _ffi.create(_native_config(5))
    assert status == _ffi.STATUS_SUCCESS
    output = _sentinel_output()
    try:
        status = _ffi.solve(
            handle,
            _native_input(setpoint_c=np.nan),
            output,
        )
    finally:
        _ffi.destroy(handle)

    assert status == _ffi.STATUS_INVALID_ARGUMENT
    assert output.sequence_length == 0
    np.testing.assert_array_equal(output.sequence_q, np.zeros(24))
    np.testing.assert_array_equal(output.sequence_residual, np.zeros(24))
    assert output.objective == 0.0
    assert output.diagnostics.status == _ffi.STATUS_INVALID_ARGUMENT


@pytest.mark.parametrize("horizon_steps", [5, 24])
def test_solver_uses_selected_horizon_for_cold_warm_and_reset_solves(
    horizon_steps: int,
    built_native_release: Path,
) -> None:
    solver = AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=horizon_steps, sigma=0.0))
    try:
        cold = _solve(solver)
        warm = _solve(solver)
        solver.reset()
        reset = _solve(solver)
    finally:
        solver.close()

    for result in (cold, warm, reset):
        assert result.sequence_q.shape == (horizon_steps,)
        assert result.sequence_residual.shape == (horizon_steps,)
        assert np.isfinite(result.sequence_q).all()
        assert np.isfinite(result.sequence_residual).all()
        assert np.all((0.0 <= result.sequence_q) & (result.sequence_q <= 1.0))
    assert cold.diagnostics.warm_started is False
    assert warm.diagnostics.warm_started is True
    assert reset.diagnostics.warm_started is False


@pytest.mark.parametrize("horizon_steps", [5, 24])
def test_runtime_discretization_keeps_fixed_map_and_unit_running_cost_scaling(
    horizon_steps: int,
    built_native_release: Path,
) -> None:
    config = GreyBoxMPCConfig(
        horizon_steps=horizon_steps,
        K_Q=350.0,
        h_amb=0.5,
        sigma=0.0,
        temperature_weight=0.0,
        terminal_weight=2.0,
        move_weight=1.0,
        residual_weight=1.0,
    )
    state = _state(temperature_c=100.0)
    setpoint_c = 120.0
    q_previous = 0.8
    equilibrium_q = 0.2

    with AcadosGreyBoxMPC(config) as solver:
        result = solver.solve(
            state,
            setpoint_c=setpoint_c,
            q_previous=q_previous,
            equilibrium_q=equilibrium_q,
        )

    expected = _unit_scaled_objective(
        config,
        state,
        result.sequence_residual,
        setpoint_c=setpoint_c,
        q_previous=q_previous,
        equilibrium_q=equilibrium_q,
    )
    assert result.objective == pytest.approx(expected, rel=1e-8, abs=1e-8)
    assert result.diagnostics.objective == result.objective


@pytest.mark.parametrize(
    ("horizon_steps", "expected"),
    [(4, ValueError), (25, ValueError), (5.0, TypeError), (True, TypeError)],
)
def test_invalid_horizons_are_rejected_before_native_create(
    horizon_steps: object, expected: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    native_create_called = False

    def unexpected_create(config: object) -> tuple[int, object]:
        nonlocal native_create_called
        native_create_called = True
        raise AssertionError("native create must not be called")

    monkeypatch.setattr(_ffi, "create", unexpected_create)

    with pytest.raises(expected, match="horizon_steps"):
        AcadosGreyBoxMPC(
            GreyBoxMPCConfig(horizon_steps=horizon_steps)  # type: ignore[arg-type]
        )

    assert native_create_called is False


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (np.array([1.01] + [0.2] * 7 + [100.0, 0.0]), "delay state"),
        (np.array([-0.01] + [0.2] * 7 + [100.0, 0.0]), "delay state"),
        (np.array([0.2] * 8 + [-273.16, 0.0]), "temperature"),
    ],
)
def test_state_contract_rejects_nonphysical_estimates_before_native_call(
    state: np.ndarray,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        grey_box_module._state_array(state)


def test_invalid_solve_inputs_are_rejected_before_ffi(
    monkeypatch: pytest.MonkeyPatch,
    built_native_release: Path,
) -> None:
    solver = AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=5, sigma=0.0))

    def unexpected_solve(*args: object) -> int:
        raise AssertionError("native solve must not be called")

    monkeypatch.setattr(_ffi, "solve", unexpected_solve)
    invalid_cases = [
        (np.zeros(9), 120.0, 0.2, 0.25, "shape"),
        (np.r_[_state()[:-1], np.nan], 120.0, 0.2, 0.25, "finite"),
        (np.array([1.01] + [0.2] * 7 + [100.0, 0.0]), 120.0, 0.2, 0.25, "delay state"),
        (np.array([-0.01] + [0.2] * 7 + [100.0, 0.0]), 120.0, 0.2, 0.25, "delay state"),
        (np.array([0.2] * 8 + [-273.16, 0.0]), 120.0, 0.2, 0.25, "temperature"),
        (_state(), np.nan, 0.2, 0.25, "setpoint_c"),
        (_state(), 120.0, -0.01, 0.25, "q_previous"),
        (_state(), 120.0, 0.2, 1.01, "equilibrium_q"),
    ]
    try:
        for state, setpoint, previous, equilibrium, message in invalid_cases:
            with pytest.raises(ValueError, match=message):
                solver.solve(
                    state,
                    setpoint_c=setpoint,
                    q_previous=previous,
                    equilibrium_q=equilibrium,
                )
    finally:
        solver.close()


def test_wrapper_rejects_native_sequence_length_that_differs_from_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_handle = ctypes.pointer(_ffi.GreyHandle())
    monkeypatch.setattr(
        _ffi,
        "create",
        lambda config: (_ffi.STATUS_SUCCESS, fake_handle),
    )
    monkeypatch.setattr(_ffi, "destroy", lambda handle: None)

    def solve_with_wrong_length(
        handle: object,
        solve_input: _ffi.GreySolveInput,
        output: _ffi.GreySolveOutput,
    ) -> int:
        _populate_successful_output(output, horizon_steps=4)
        return _ffi.STATUS_SUCCESS

    monkeypatch.setattr(_ffi, "solve", solve_with_wrong_length)
    solver = AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=5))
    try:
        with pytest.raises(SolverError, match="sequence_length|horizon") as raised:
            _solve(solver)
    finally:
        solver.close()

    assert isinstance(raised.value.diagnostics, SolverDiagnostics)


def test_wrapper_copies_only_selected_prefix_and_ignores_native_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_handle = ctypes.pointer(_ffi.GreyHandle())
    monkeypatch.setattr(
        _ffi,
        "create",
        lambda config: (_ffi.STATUS_SUCCESS, fake_handle),
    )
    monkeypatch.setattr(_ffi, "destroy", lambda handle: None)

    def solve_with_unused_tail(
        handle: object,
        solve_input: _ffi.GreySolveInput,
        output: _ffi.GreySolveOutput,
    ) -> int:
        _populate_successful_output(output, horizon_steps=5)
        output.sequence_q[5:] = [np.nan] * 19
        output.sequence_residual[5:] = [np.inf] * 19
        return _ffi.STATUS_SUCCESS

    monkeypatch.setattr(_ffi, "solve", solve_with_unused_tail)
    solver = AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=5))
    try:
        result = _solve(solver)
    finally:
        solver.close()

    np.testing.assert_array_equal(result.sequence_q, np.full(5, 0.25))
    np.testing.assert_array_equal(result.sequence_residual, np.zeros(5))
    assert result.sequence_q.flags.owndata
    assert result.sequence_residual.flags.owndata
    assert not result.sequence_q.flags.writeable
    assert not result.sequence_residual.flags.writeable


def test_wrapper_rejects_non_finite_success_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_handle = ctypes.pointer(_ffi.GreyHandle())
    monkeypatch.setattr(
        _ffi,
        "create",
        lambda config: (_ffi.STATUS_SUCCESS, fake_handle),
    )
    monkeypatch.setattr(_ffi, "destroy", lambda handle: None)

    def solve_with_nan(
        handle: object,
        solve_input: _ffi.GreySolveInput,
        output: _ffi.GreySolveOutput,
    ) -> int:
        _populate_successful_output(output, horizon_steps=5)
        output.sequence_q[2] = np.nan
        return _ffi.STATUS_SUCCESS

    monkeypatch.setattr(_ffi, "solve", solve_with_nan)
    solver = AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=5))
    try:
        with pytest.raises(SolverError, match="finite") as raised:
            _solve(solver)
    finally:
        solver.close()

    assert isinstance(raised.value.diagnostics, SolverDiagnostics)


@pytest.mark.parametrize("corruption", ["sequence", "objective"])
def test_wrapper_rejects_internally_inconsistent_success_output(
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    fake_handle = ctypes.pointer(_ffi.GreyHandle())
    monkeypatch.setattr(
        _ffi,
        "create",
        lambda config: (_ffi.STATUS_SUCCESS, fake_handle),
    )
    monkeypatch.setattr(_ffi, "destroy", lambda handle: None)

    def solve_with_inconsistency(
        handle: object,
        solve_input: _ffi.GreySolveInput,
        output: _ffi.GreySolveOutput,
    ) -> int:
        _populate_successful_output(output, horizon_steps=5)
        if corruption == "sequence":
            output.sequence_q[0] = 0.4
        else:
            output.diagnostics.objective = 2.0
        return _ffi.STATUS_SUCCESS

    monkeypatch.setattr(_ffi, "solve", solve_with_inconsistency)
    solver = AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=5))
    try:
        with pytest.raises(SolverError, match="inconsistent"):
            _solve(solver)
    finally:
        solver.close()


def test_failed_solve_restores_exact_successful_primal_and_dual_warm_iterate(
    built_native_release: Path,
) -> None:
    config = GreyBoxMPCConfig(horizon_steps=5, sigma=1.4e-9, max_iterations=10)
    failed_handle = AcadosGreyBoxMPC(config)
    control_handle = AcadosGreyBoxMPC(config)
    try:
        failed_prime = _solve(failed_handle)
        control_prime = _solve(control_handle)
        np.testing.assert_array_equal(
            failed_prime.sequence_residual,
            control_prime.sequence_residual,
        )

        explosive = _state(temperature_c=1e100, disturbance=1e100)
        with pytest.raises(SolverError) as failure:
            _solve(failed_handle, explosive)
        assert failure.value.diagnostics.status != _ffi.STATUS_SUCCESS

        recovery_state = _state(temperature_c=105.0, disturbance=2.0)
        failed_recovery = failed_handle.solve(
            recovery_state,
            setpoint_c=125.0,
            q_previous=0.3,
            equilibrium_q=0.25,
        )
        control_recovery = control_handle.solve(
            recovery_state,
            setpoint_c=125.0,
            q_previous=0.3,
            equilibrium_q=0.25,
        )

        assert failed_recovery.diagnostics.warm_started
        assert control_recovery.diagnostics.warm_started
        np.testing.assert_allclose(
            failed_recovery.sequence_q,
            control_recovery.sequence_q,
            rtol=0.0,
            atol=1e-9,
        )
        np.testing.assert_allclose(
            failed_recovery.sequence_residual,
            control_recovery.sequence_residual,
            rtol=0.0,
            atol=1e-9,
        )
        assert failed_recovery.diagnostics.iterations == (control_recovery.diagnostics.iterations)
        assert failed_recovery.objective == pytest.approx(
            control_recovery.objective,
            rel=0.0,
            abs=1e-9,
        )
        assert failed_recovery.diagnostics.kkt_residual == pytest.approx(
            control_recovery.diagnostics.kkt_residual,
            rel=0.0,
            abs=1e-9,
        )
    finally:
        failed_handle.close()
        control_handle.close()


def test_separate_handle_locks_allow_concurrent_native_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    barrier = threading.Barrier(2)
    handles: list[Any] = []
    handle_horizons: dict[int, tuple[int, float]] = {}

    def fake_create(config: _ffi.GreyConfig) -> tuple[int, Any]:
        handle = ctypes.pointer(_ffi.GreyHandle())
        handles.append(handle)
        marker = 0.2 if len(handles) == 1 else 0.8
        handle_horizons[ctypes.addressof(handle.contents)] = (
            int(config.horizon_steps),
            marker,
        )
        return _ffi.STATUS_SUCCESS, handle

    def concurrent_solve(
        handle: Any,
        solve_input: _ffi.GreySolveInput,
        output: _ffi.GreySolveOutput,
    ) -> int:
        barrier.wait(timeout=2.0)
        horizon_steps, marker = handle_horizons[ctypes.addressof(handle.contents)]
        _populate_successful_output(
            output,
            horizon_steps=horizon_steps,
            q=marker,
            residual=marker - 0.25,
        )
        return _ffi.STATUS_SUCCESS

    monkeypatch.setattr(_ffi, "create", fake_create)
    monkeypatch.setattr(_ffi, "solve", concurrent_solve)
    monkeypatch.setattr(_ffi, "destroy", lambda handle: None)

    first = AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=5))
    second = AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=24))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(_solve, first, _state(90.0))
            second_future = pool.submit(_solve, second, _state(180.0))
            first_result = first_future.result()
            second_result = second_future.result()
    finally:
        first.close()
        second.close()

    assert first_result.sequence_q.shape == (5,)
    assert second_result.sequence_q.shape == (24,)
    np.testing.assert_array_equal(first_result.sequence_q, np.full(5, 0.2))
    np.testing.assert_array_equal(second_result.sequence_q, np.full(24, 0.8))


def test_context_manager_closes_deterministically_and_close_is_idempotent(
    built_native_release: Path,
) -> None:
    with AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=5, sigma=0.0)) as solver:
        result = _solve(solver)
        assert result.sequence_q.flags.owndata
        assert result.sequence_residual.flags.owndata

    solver.close()
    with pytest.raises(RuntimeError, match="closed"):
        _solve(solver)
    with pytest.raises(RuntimeError, match="closed"):
        solver.reset()


def test_finalizer_destroys_exactly_one_leaked_native_handle(
    monkeypatch: pytest.MonkeyPatch,
    built_native_release: Path,
) -> None:
    destroyed: list[object] = []
    original_destroy = _ffi.destroy

    def recording_destroy(handle: object) -> None:
        destroyed.append(handle)
        original_destroy(handle)

    monkeypatch.setattr(_ffi, "destroy", recording_destroy)
    solver = AcadosGreyBoxMPC(GreyBoxMPCConfig(horizon_steps=5, sigma=0.0))
    reference = weakref.ref(solver)
    del solver
    gc.collect()

    assert reference() is None
    assert len(destroyed) == 1
