"""Behavioral contracts for common affine linear MPC and pulse realization."""

from __future__ import annotations


import numpy as np
import numpy.testing as npt
import pytest
from scipy.optimize import minimize

from docs.superpowers.experiments.linear_mpc_bakeoff.actuation import PulseRealizer
from docs.superpowers.experiments.linear_mpc_bakeoff.contracts import AffinePrediction
from docs.superpowers.experiments.linear_mpc_bakeoff.linear_mpc import (
    MPCConfig,
    LinearMPC,
    projected_gradient_qp,
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


def test_projected_gradient_respects_exact_box_bounds() -> None:
    result = projected_gradient_qp(
        np.eye(3), np.array([2.0, -2.0, -0.5]), np.zeros(3), np.ones(3), np.full(3, 0.5)
    )

    npt.assert_array_equal(result.x, np.array([0.0, 1.0, 0.5]))


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


def test_solve_result_arrays_are_defensive_read_only_copies() -> None:
    result = LinearMPC(MPCConfig(horizon_s=800, frame_s=20)).solve(
        affine_integrator_model(steps=40), setpoint_c=120.0
    )

    with pytest.raises(ValueError):
        result.x[0] = 0.0
    with pytest.raises(ValueError):
        assert result.predicted_c is not None
        result.predicted_c[0] = 0.0


def test_mpc_uses_only_selected_horizon() -> None:
    model = affine_integrator_model(steps=40)
    mpc = LinearMPC(MPCConfig(horizon_s=800, frame_s=20))

    result = mpc.solve(model, setpoint_c=120.0)

    assert result.sequence_q.shape == (40,)


@pytest.mark.parametrize("horizon_s", (600, 800, 1000))
def test_mpc_accepts_only_candidate_horizons(horizon_s: int) -> None:
    assert MPCConfig(horizon_s=horizon_s, frame_s=20).horizon_steps == horizon_s // 20



def test_validation_horizon_tie_breaks_to_shorter_candidate() -> None:
    selected = select_validation_horizon({600: 10.05, 800: 10.0, 1000: 9.96})

    assert selected == 600

def test_mpc_rejects_non_candidate_horizon() -> None:
    with pytest.raises(ValueError, match="600, 800, or 1000"):
        MPCConfig(horizon_s=700, frame_s=20)


def test_mpc_warm_start_is_deterministic() -> None:
    model = affine_integrator_model(steps=40)
    config = MPCConfig(horizon_s=800, frame_s=20)

    first = LinearMPC(config).solve(model, setpoint_c=120.0)
    second = LinearMPC(config).solve(model, setpoint_c=120.0)

    npt.assert_allclose(first.sequence_q, second.sequence_q, atol=1e-12)


def test_fractional_pulse_carries_between_frames() -> None:
    pulse = PulseRealizer(frame_s=20, quantum_s=2)

    realized = [pulse.frame(0.15) for _ in range(10)]

    assert sum(frame.on_seconds for frame in realized) == pytest.approx(30.0, abs=2.0)
    assert all(frame.on_seconds <= 10 * 2 for frame in realized)
    assert all(frame.requested_duty == pytest.approx(0.15) for frame in realized)


def test_skipped_frames_discard_unrealized_pulses_without_replay() -> None:
    pulse = PulseRealizer(frame_s=20, quantum_s=2)
    pulse.frame(0.95)

    realized = pulse.frame(0.15, skipped_frames=1_000_000_000)

    assert realized.on_seconds == 2.0
    assert realized.realized_duty == 0.1
    assert realized.transitions <= 2


def test_pulse_results_are_immutable() -> None:
    frame = PulseRealizer(frame_s=20, quantum_s=2).frame(0.5)

    with pytest.raises((AttributeError, TypeError)):
        setattr(frame, "on_seconds", 0.0)



def test_pulse_transition_counts_preserve_prior_state_and_reset_after_skip() -> None:
    pulse = PulseRealizer(frame_s=20, quantum_s=2)

    first = pulse.frame(1.0)
    continued = pulse.frame(1.0)
    stopped = pulse.frame(0.0)
    pulse.frame(1.0)
    after_skip = pulse.frame(0.0, skipped_frames=1)

    assert first.transitions == 1
    assert continued.transitions == 0
    assert stopped.transitions == 1
    assert after_skip.transitions == 0