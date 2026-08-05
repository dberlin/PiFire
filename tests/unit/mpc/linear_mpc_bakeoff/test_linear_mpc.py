"""Behavioral contracts for common affine linear MPC and pulse realization."""

from __future__ import annotations


import numpy as np
import numpy.testing as npt
import pytest
from scipy.optimize import minimize
from docs.superpowers.experiments.linear_mpc_bakeoff import actuation

from docs.superpowers.experiments.linear_mpc_bakeoff.actuation import PulseSimulationDriver
from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import AffinePrediction
import controller.linear_mpc.policy as linear_mpc
from controller.linear_mpc.policy import (
    LinearMPC,
    LinearMPCConfig,
    LinearSolve,
    projected_gradient_qp,
)
from docs.superpowers.experiments.linear_mpc_bakeoff.linear_mpc import (
    select_validation_horizon,
)


def positive_definite_box_qp(*, seed: int, size: int) -> tuple[np.ndarray, np.ndarray]:
    """Return a deterministic strictly convex box-constrained quadratic program."""
    generator = np.random.default_rng(seed)
    factor = generator.normal(size=(size, size))
    return factor.T @ factor + np.eye(size), generator.normal(size=size)


def scipy_box_reference(H: np.ndarray, f: np.ndarray) -> np.ndarray:
    """Solve the QP independently with scipy's exact-gradient L-BFGS-B solver."""
    result = minimize(
        fun=lambda q: float(0.5 * q @ H @ q + f @ q),
        x0=np.full(f.size, 0.5),
        jac=lambda q: H @ q + f,
        bounds=[(0.0, 1.0)] * f.size,
        method="L-BFGS-B",
        options={"ftol": 1e-15, "gtol": 1e-12, "maxiter": 20_000},
    )
    assert result.success, result.message
    return result.x


def affine_integrator_model(*, steps: int = 50) -> AffinePrediction:
    """Return a positive-gain affine prediction for a sampled temperature integrator."""
    gain = 0.25
    response = np.tril(np.full((steps, steps), gain, dtype=np.float64))
    return AffinePrediction(np.full(steps, 20.0), response)


def test_projected_gradient_matches_scipy_reference() -> None:
    H, f = positive_definite_box_qp(seed=5, size=40)

    actual = projected_gradient_qp(H, f, np.zeros(40), np.ones(40), np.full(40, 0.5))
    expected = scipy_box_reference(H, f)

    npt.assert_allclose(actual.x, expected, atol=1e-5)
    assert actual.kkt_residual < 1e-6


@pytest.mark.parametrize("invalid", (np.nan, np.inf, -np.inf))
def test_linear_solve_rejects_nonfinite_sequence(invalid: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        LinearSolve(np.array([invalid]), 0.0, 0.0, 0, 1.0)


@pytest.mark.parametrize(
    ("objective", "kkt_residual"),
    ((np.nan, 0.0), (0.0, np.inf)),
)
def test_linear_solve_rejects_nonfinite_certificate(objective: float, kkt_residual: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        LinearSolve(np.array([0.5]), objective, kkt_residual, 0, 1.0)


def test_diagonally_scaled_active_set_certifies_ill_conditioned_box_qp() -> None:
    """The primary solver must certify original-coordinate KKT at condition 1e9."""
    generator = np.random.default_rng(27)
    basis, _ = np.linalg.qr(generator.normal(size=(40, 40)))
    hessian = basis @ np.diag(np.geomspace(1.0, 1e9, 40)) @ basis.T
    hessian = 0.5 * (hessian + hessian.T)
    target = np.full(40, 0.55)
    linear = -(hessian @ target)

    actual = projected_gradient_qp(
        hessian,
        linear,
        np.zeros(40),
        np.ones(40),
        np.full(40, 0.5),
        tolerance=1e-7,
    )

    assert actual.kkt_residual <= 1e-7
    npt.assert_allclose(actual.x, target, atol=1e-5)


def test_solver_tolerates_roundoff_negative_eigenvalue_in_constructed_psd_hessian() -> None:
    """A relative roundoff eigenvalue must not reject an exact zero-gradient QP."""
    generator = np.random.default_rng(0)
    basis, _ = np.linalg.qr(generator.normal(size=(20, 20)))
    hessian = basis @ np.diag(np.geomspace(1.0, 1e19, 20)) @ basis.T
    hessian = 0.5 * (hessian + hessian.T)
    assert np.linalg.eigvalsh(hessian)[0] < 0.0

    result = projected_gradient_qp(hessian, np.zeros(20), np.zeros(20), np.ones(20), np.zeros(20))

    npt.assert_array_equal(result.x, np.zeros(20))
    assert result.kkt_residual == 0.0


def test_projected_gradient_respects_exact_box_bounds() -> None:
    result = projected_gradient_qp(np.eye(3), np.array([2.0, -2.0, -0.5]), np.zeros(3), np.ones(3), np.full(3, 0.5))

    npt.assert_array_equal(result.x, np.array([0.0, 1.0, 0.5]))


def test_bvls_eliminates_fixed_coordinates_and_preserves_mixed_bounds() -> None:
    result = projected_gradient_qp(
        np.array([[4.0, 1.0, 0.0], [1.0, 3.0, 1.0], [0.0, 1.0, 2.0]]),
        np.array([-2.0, 0.0, -1.0]),
        np.array([0.25, 0.0, 0.6]),
        np.array([0.25, 1.0, 0.6]),
        np.array([0.9, 0.5, 0.1]),
    )

    npt.assert_array_equal(result.x[[0, 2]], np.array([0.25, 0.6]))
    assert 0.0 <= result.x[1] <= 1.0
    assert result.kkt_residual < 1e-7


def test_all_fixed_box_qp_has_zero_iterations() -> None:
    result = projected_gradient_qp(
        np.array([[2.0, 1.0], [1.0, 2.0]]),
        np.array([-3.0, 4.0]),
        np.array([0.2, 0.8]),
        np.array([0.2, 0.8]),
        np.zeros(2),
    )

    npt.assert_array_equal(result.x, np.array([0.2, 0.8]))
    assert result.iterations == 0
    assert result.kkt_residual == 0.0


@pytest.mark.parametrize("max_iterations", (1, 2, 3, 4))
def test_bvls_never_exceeds_the_public_iteration_cap(
    max_iterations: int,
) -> None:
    result = projected_gradient_qp(
        np.eye(2),
        np.array([-2.0, -0.5]),
        np.zeros(2),
        np.ones(2),
        np.zeros(2),
        max_iterations=max_iterations,
        tolerance=1e-12,
    )

    assert result.iterations <= max_iterations
    npt.assert_array_less(-1e-15, result.x)
    npt.assert_array_less(result.x, 1.0 + 1e-15)
    if max_iterations == 1:
        npt.assert_allclose(result.x, np.array([1.0, 0.25]), atol=1e-12)
        assert result.kkt_residual == pytest.approx(0.25)
    else:
        npt.assert_allclose(result.x, np.array([1.0, 0.5]), atol=1e-12)
        assert result.kkt_residual <= 1e-12


def test_bvls_reserves_setup_and_initialization_from_the_public_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    class Result:
        x = np.array([1.0, 0.5])
        nit = 1

    def fake_lsq_linear(*args: object, **kwargs: object) -> Result:
        calls.append(int(kwargs["max_iter"]))
        return Result()

    monkeypatch.setattr(linear_mpc, "lsq_linear", fake_lsq_linear)

    result = projected_gradient_qp(
        np.eye(2),
        np.array([-2.0, -0.5]),
        np.zeros(2),
        np.ones(2),
        np.zeros(2),
        max_iterations=4,
        tolerance=1e-12,
    )

    assert calls == [1]
    assert result.iterations == 3
    assert result.kkt_residual == 0.0


@pytest.mark.parametrize(
    "bvls_x",
    (
        np.array([np.nan, 0.5]),
        np.array([1.25, -0.25]),
    ),
)
def test_budget_exhausted_bvls_invalid_candidate_uses_bounded_fallback(
    monkeypatch: pytest.MonkeyPatch, bvls_x: np.ndarray
) -> None:
    """A BVLS budget result is usable only with a finite feasible certificate."""

    class Result:
        x = bvls_x
        nit = 2

    monkeypatch.setattr(linear_mpc, "lsq_linear", lambda *args, **kwargs: Result())

    result = projected_gradient_qp(
        np.eye(2),
        np.array([-2.0, -0.5]),
        np.zeros(2),
        np.ones(2),
        np.zeros(2),
        max_iterations=5,
        tolerance=1e-12,
    )

    npt.assert_allclose(result.x, np.array([1.0, 0.5]), atol=1e-12)
    assert np.isfinite(result.kkt_residual)
    assert result.kkt_residual <= 1e-12


def test_zero_curvature_qp_selects_the_deterministic_bounded_linear_optimum() -> None:
    result = projected_gradient_qp(
        np.zeros((3, 3)),
        np.array([1.0, -1.0, 0.0]),
        np.zeros(3),
        np.ones(3),
        np.array([0.75, 0.25, 0.5]),
    )

    npt.assert_array_equal(result.x, np.array([0.0, 1.0, 0.5]))
    assert result.kkt_residual == 0.0


def test_linear_solve_arrays_are_defensive_read_only_copies() -> None:
    result = LinearMPC(LinearMPCConfig(horizon_steps=40)).solve(
        affine_integrator_model(steps=40),
        setpoint_c=120.0,
        q_previous=0.0,
        equilibrium_q=0.0,
    )

    with pytest.raises(ValueError):
        result.sequence_q[0] = 0.0


def test_mpc_uses_only_configured_horizon() -> None:
    model = affine_integrator_model(steps=40)
    mpc = LinearMPC(LinearMPCConfig(horizon_steps=40))

    result = mpc.solve(
        model,
        setpoint_c=120.0,
        q_previous=0.0,
        equilibrium_q=0.0,
    )

    assert result.sequence_q.shape == (40,)


@pytest.mark.parametrize("horizon_steps", (30, 40, 50))
def test_mpc_accepts_nonnegative_horizons(horizon_steps: int) -> None:
    assert LinearMPCConfig(horizon_steps=horizon_steps).horizon_steps == horizon_steps


def test_validation_horizon_tie_breaks_to_shorter_candidate() -> None:
    selected = select_validation_horizon({600: 10.05, 800: 10.0, 1000: 9.96})

    assert selected == 600


def test_mpc_rejects_negative_horizon() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        LinearMPCConfig(horizon_steps=-1)


def test_mpc_warm_start_is_deterministic() -> None:
    model = affine_integrator_model(steps=40)
    config = LinearMPCConfig(horizon_steps=40)

    first = LinearMPC(config).solve(
        model,
        setpoint_c=120.0,
        q_previous=0.0,
        equilibrium_q=0.0,
    )
    second = LinearMPC(config).solve(
        model,
        setpoint_c=120.0,
        q_previous=0.0,
        equilibrium_q=0.0,
    )

    npt.assert_allclose(first.sequence_q, second.sequence_q, atol=1e-12)


def test_fractional_pulse_carries_between_frames() -> None:
    pulse = PulseSimulationDriver()

    realized = [pulse.frame(0.15) for _ in range(10)]

    assert sum(frame.on_seconds for frame in realized) == pytest.approx(30.0, abs=2.0)
    assert all(frame.on_seconds <= 10 * 2 for frame in realized)
    assert all(frame.requested_duty == pytest.approx(0.15) for frame in realized)


def test_pulse_driver_uses_production_scheduler_authority() -> None:
    frame = PulseSimulationDriver().frame(1.0)

    assert frame.on_seconds == 20.0
    assert frame.realized_duty == 1.0
    assert frame.transitions == 1


def test_pulse_results_are_immutable() -> None:
    frame = PulseSimulationDriver().frame(0.5)

    with pytest.raises((AttributeError, TypeError)):
        setattr(frame, "on_seconds", 0.0)


def test_pulse_transition_counts_preserve_prior_relay_state() -> None:
    pulse = PulseSimulationDriver()

    first = pulse.frame(1.0)
    continued = pulse.frame(1.0)
    stopped = pulse.frame(0.0)

    assert first.transitions == 1
    assert continued.transitions == 0
    assert stopped.transitions == 1


def test_pulse_simulation_driver_uses_production_two_second_scheduler() -> None:
    driver_type = getattr(actuation, "PulseSimulationDriver", None)

    assert driver_type is not None
    driver = driver_type()
    frames = [driver.frame(0.15) for _ in range(2)]

    assert [frame.on_seconds for frame in frames] == [2.0, 4.0]
    assert [frame.realized_duty for frame in frames] == [0.1, 0.2]


def test_pulse_driver_reports_production_delivered_accounting() -> None:
    driver = PulseSimulationDriver()

    driver.frame(0.1)
    driver.frame(0.0)

    completed = getattr(driver, "last_completed_frame", None)
    assert completed is not None
    assert completed.delivered_on_s == 2.0
    assert completed.observed_transition_count == 2
