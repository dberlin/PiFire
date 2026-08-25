from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from controller.acados import (
    GreyBoxMPCConfig,
    GreyBoxSolve,
    SolverDiagnostics,
    SolverError,
)


def _diagnostics() -> SolverDiagnostics:
    return SolverDiagnostics(
        status=0,
        backend_status=0,
        iterations=3,
        solve_time_s=0.01,
        objective=2.5,
        kkt_residual=1e-7,
        constraint_residual=0.0,
        warm_started=False,
    )


@pytest.mark.parametrize("horizon_steps", [5, 24])
def test_grey_config_accepts_supported_integer_horizon_boundaries(
    horizon_steps: int,
) -> None:
    config = GreyBoxMPCConfig(horizon_steps=horizon_steps)

    assert config.horizon_steps == horizon_steps
    assert config.delay_states == 8
    assert config.state_size == 10
    assert config.timestep_s == 25.0


@pytest.mark.parametrize(
    ("horizon_steps", "expected"),
    [(4, ValueError), (25, ValueError), (5.0, TypeError), (True, TypeError)],
)
def test_grey_config_rejects_out_of_range_or_non_integer_horizons(
    horizon_steps: object,
    expected: type[Exception],
) -> None:
    with pytest.raises(expected, match="horizon_steps"):
        GreyBoxMPCConfig(horizon_steps=horizon_steps)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    [
        "C_c",
        "h_amb",
        "T_amb",
        "theta",
        "K_Q",
        "sigma",
        "temperature_weight",
        "terminal_weight",
        "move_weight",
        "residual_weight",
    ],
)
@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_grey_config_rejects_non_finite_scalars(field: str, value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        GreyBoxMPCConfig(**{field: value})


@pytest.mark.parametrize("field", ["C_c", "temperature_weight", "max_iterations"])
def test_grey_config_rejects_booleans_as_numbers(field: str) -> None:
    with pytest.raises(TypeError, match=field):
        GreyBoxMPCConfig(**{field: True})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("C_c", 0.0),
        ("h_amb", -0.01),
        ("theta", 0.0),
        ("K_Q", 0.0),
        ("sigma", -0.01),
        ("temperature_weight", -0.01),
        ("terminal_weight", -0.01),
        ("move_weight", -0.01),
        ("residual_weight", -0.01),
        ("max_iterations", 0),
    ],
)
def test_grey_config_rejects_invalid_physical_and_solver_values(
    field: str,
    value: float,
) -> None:
    with pytest.raises(ValueError, match=field):
        GreyBoxMPCConfig(**{field: value})


def test_grey_config_requires_at_least_one_positive_objective_weight() -> None:
    with pytest.raises(ValueError, match="at least one"):
        GreyBoxMPCConfig(
            temperature_weight=0.0,
            terminal_weight=0.0,
            move_weight=0.0,
            residual_weight=0.0,
        )


@pytest.mark.parametrize("horizon_steps", [5, 24])
def test_grey_results_own_read_only_arrays_of_the_selected_length(
    horizon_steps: int,
) -> None:
    q = np.linspace(0.0, 1.0, horizon_steps, dtype=np.float32)
    residual = np.linspace(-0.5, 0.5, horizon_steps, dtype=np.float32)
    expected_q = q.astype(np.float64)
    expected_residual = residual.astype(np.float64)

    result = GreyBoxSolve(q, residual, 1.5, _diagnostics())
    q[:] = -1.0
    residual[:] = -1.0

    np.testing.assert_array_equal(result.sequence_q, expected_q)
    np.testing.assert_array_equal(result.sequence_residual, expected_residual)
    assert result.sequence_q.shape == (horizon_steps,)
    assert result.sequence_residual.shape == (horizon_steps,)
    assert result.sequence_q.dtype == np.float64
    assert result.sequence_residual.dtype == np.float64
    assert result.sequence_q.flags.owndata
    assert result.sequence_residual.flags.owndata
    assert result.sequence_q.flags.c_contiguous
    assert result.sequence_residual.flags.c_contiguous
    assert not result.sequence_q.flags.writeable
    assert not result.sequence_residual.flags.writeable
    with pytest.raises(ValueError):
        result.sequence_q[0] = 1.0
    with pytest.raises(FrozenInstanceError):
        result.objective = 0.0


@pytest.mark.parametrize(
    ("q_length", "residual_length"),
    [(4, 4), (25, 25), (5, 6), (6, 5)],
)
def test_grey_results_reject_unsupported_or_mismatched_sequence_lengths(
    q_length: int,
    residual_length: int,
) -> None:
    with pytest.raises(ValueError, match="sequence"):
        GreyBoxSolve(
            np.zeros(q_length),
            np.zeros(residual_length),
            0.0,
            _diagnostics(),
        )


@pytest.mark.parametrize(
    ("sequence_q", "sequence_residual", "objective"),
    [
        (np.r_[np.zeros(4), np.nan], np.zeros(5), 0.0),
        (np.zeros(5), np.r_[np.zeros(4), np.inf], 0.0),
        (np.zeros(5), np.zeros(5), np.nan),
    ],
)
def test_grey_results_reject_non_finite_native_values(
    sequence_q: np.ndarray,
    sequence_residual: np.ndarray,
    objective: float,
) -> None:
    with pytest.raises(ValueError, match="finite"):
        GreyBoxSolve(sequence_q, sequence_residual, objective, _diagnostics())


def test_solver_diagnostics_are_structured_immutable_and_finite() -> None:
    details = _diagnostics()

    assert details.status == 0
    assert details.backend_status == 0
    assert details.iterations == 3
    assert details.solve_time_s == 0.01
    assert details.objective == 2.5
    assert details.kkt_residual == 1e-7
    assert details.constraint_residual == 0.0
    assert details.warm_started is False
    with pytest.raises(FrozenInstanceError):
        details.status = 1


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("status", True, TypeError),
        ("backend_status", True, TypeError),
        ("iterations", -1, ValueError),
        ("solve_time_s", -0.01, ValueError),
        ("objective", np.nan, ValueError),
        ("kkt_residual", np.inf, ValueError),
        ("constraint_residual", -0.01, ValueError),
        ("warm_started", 0, TypeError),
    ],
)
def test_solver_diagnostics_reject_invalid_fields(field: str, value: object, expected: type[Exception]) -> None:
    values: dict[str, object] = {
        "status": 0,
        "backend_status": 0,
        "iterations": 0,
        "solve_time_s": 0.0,
        "objective": 0.0,
        "kkt_residual": 0.0,
        "constraint_residual": 0.0,
        "warm_started": False,
    }
    values[field] = value

    with pytest.raises(expected, match=field):
        SolverDiagnostics(**values)  # type: ignore[arg-type]


def test_solver_error_retains_structured_diagnostics() -> None:
    details = _diagnostics()
    error = SolverError("native solve failed", details)

    assert str(error) == "native solve failed"
    assert error.diagnostics is details
