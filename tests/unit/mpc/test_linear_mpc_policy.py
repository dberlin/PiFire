"""Behavioral contracts for the production condensed linear MPC policy."""

from __future__ import annotations

import numpy as np
import numpy.testing as npt
import pytest

from controller.linear_mpc import policy as policy_module
from controller.linear_mpc.contracts import AffinePrediction
from controller.linear_mpc.policy import BoxQPSolve, LinearMPC, LinearMPCConfig


def _prediction(*, free: float, response: np.ndarray) -> AffinePrediction:
    return AffinePrediction(np.full(response.shape[0], free), response)


def test_solves_an_analytic_diagonal_box_qp_with_a_certificate() -> None:
    config = LinearMPCConfig(
        horizon_steps=3,
        temperature_weight=1.0,
        terminal_weight=0.0,
        move_weight=0.0,
    )
    policy = LinearMPC(config)

    solve = policy.solve(
        _prediction(free=119.5, response=np.eye(3)),
        setpoint_c=120.0,
        q_previous=0.2,
        equilibrium_q=0.35,
    )

    npt.assert_allclose(solve.sequence_q, np.full(3, 0.5), atol=1e-12)
    assert 0.0 <= solve.sequence_q[0] <= 1.0
    assert solve.kkt_residual <= config.tolerance
    with pytest.raises(ValueError):
        solve.sequence_q[0] = 0.0
    assert np.isfinite(solve.objective)


def test_zero_horizon_returns_an_immutable_certified_empty_solve() -> None:
    policy = LinearMPC(LinearMPCConfig(horizon_steps=0))

    solve = policy.solve(
        AffinePrediction(np.empty(0), np.empty((0, 0))),
        setpoint_c=120.0,
        q_previous=0.2,
        equilibrium_q=0.35,
    )

    assert solve.sequence_q.shape == (0,)
    assert solve.objective == 0.0
    assert solve.kkt_residual == 0.0
    assert solve.hessian_condition == 1.0


def test_clamps_an_unconstrained_optimum_to_the_duty_box() -> None:
    policy = LinearMPC(
        LinearMPCConfig(
            horizon_steps=2,
            temperature_weight=1.0,
            terminal_weight=0.0,
            move_weight=0.0,
        )
    )

    solve = policy.solve(
        _prediction(free=118.0, response=np.eye(2)),
        setpoint_c=120.0,
        q_previous=0.2,
        equilibrium_q=0.35,
    )

    npt.assert_array_equal(solve.sequence_q, np.ones(2))


def test_move_penalty_uses_previous_duty_only_for_the_first_move() -> None:
    policy = LinearMPC(
        LinearMPCConfig(
            horizon_steps=2,
            temperature_weight=0.0,
            terminal_weight=0.0,
            move_weight=1.0,
        )
    )

    solve = policy.solve(
        _prediction(free=20.0, response=np.zeros((2, 2))),
        setpoint_c=120.0,
        q_previous=0.2,
        equilibrium_q=0.8,
    )

    npt.assert_allclose(solve.sequence_q, np.full(2, 0.2), atol=1e-12)


def test_equilibrium_duty_seeds_the_initial_steady_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_sequences: list[np.ndarray] = []

    def capture_solver(
        hessian: np.ndarray,
        linear: np.ndarray,
        lower: np.ndarray,
        upper: np.ndarray,
        warm_start: np.ndarray,
        **_: object,
    ) -> BoxQPSolve:
        initial_sequences.append(warm_start.copy())
        return BoxQPSolve(np.full(2, 0.4), 0.0, 1, 0.0, 1.0)

    monkeypatch.setattr(policy_module, "projected_gradient_qp", capture_solver)
    LinearMPC(LinearMPCConfig(horizon_steps=2)).solve(
        _prediction(free=120.0, response=np.eye(2)),
        setpoint_c=120.0,
        q_previous=0.2,
        equilibrium_q=0.35,
    )

    npt.assert_array_equal(initial_sequences, [np.full(2, 0.35)])


def test_rejects_an_uncertified_solver_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def uncertified_solver(
        hessian: np.ndarray,
        linear: np.ndarray,
        lower: np.ndarray,
        upper: np.ndarray,
        warm_start: np.ndarray,
        **_: object,
    ) -> BoxQPSolve:
        return BoxQPSolve(np.full(2, 0.4), 0.0, 1, 1.0, 1.0)

    monkeypatch.setattr(policy_module, "projected_gradient_qp", uncertified_solver)

    with pytest.raises(FloatingPointError, match="certified"):
        LinearMPC(LinearMPCConfig(horizon_steps=2)).solve(
            _prediction(free=120.0, response=np.eye(2)),
            setpoint_c=120.0,
            q_previous=0.2,
            equilibrium_q=0.35,
        )


def test_warm_started_solves_are_deterministic() -> None:
    config = LinearMPCConfig(horizon_steps=3)
    prediction = _prediction(free=119.5, response=np.eye(3))

    first = LinearMPC(config).solve(prediction, setpoint_c=120.0, q_previous=0.2, equilibrium_q=0.35)
    second = LinearMPC(config).solve(prediction, setpoint_c=120.0, q_previous=0.2, equilibrium_q=0.35)

    npt.assert_allclose(first.sequence_q, second.sequence_q, atol=1e-12)


@pytest.mark.parametrize("invalid", (np.nan, np.inf, -np.inf))
def test_rejects_nonfinite_solve_inputs(invalid: float) -> None:
    policy = LinearMPC(LinearMPCConfig(horizon_steps=1))
    prediction = _prediction(free=119.5, response=np.eye(1))

    with pytest.raises(ValueError, match="finite"):
        policy.solve(
            prediction,
            setpoint_c=invalid,
            q_previous=0.2,
            equilibrium_q=0.35,
        )
