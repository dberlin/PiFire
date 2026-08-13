from dataclasses import replace
from importlib import import_module
from types import SimpleNamespace

import numpy as np
import pytest

from controller.applied_output import AppliedOutput, OutputSource
from controller.base import MpcFailureState
from controller.model_learning.activation import ActivationPhase, OwnedGreyControlPair, PreparedActivationRecord
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin


class _Estimator:
    def __init__(self, **_kwargs):
        self.calls = []
        self.closed = 0

    def update(self, applied_load, temperature_c):
        self.calls.append((applied_load, temperature_c))
        return np.array([0.0] * 8 + [temperature_c, 0.0], dtype=float)

    def close(self):
        self.closed += 1


class _Solver:
    def __init__(self, config):
        self.config = config
        self.calls = []
        self.closed = 0

    def solve(self, state, *, setpoint_c, q_previous, equilibrium_q):
        self.calls.append((tuple(state), setpoint_c, q_previous, equilibrium_q))
        horizon = self.config.horizon_steps
        return SimpleNamespace(
            sequence_q=np.full(horizon, 0.25, dtype=float),
            sequence_residual=np.zeros(horizon, dtype=float),
            objective=1.0,
            diagnostics=SimpleNamespace(
                status=0,
                backend_status=0,
                iterations=1,
                solve_time_s=0.001,
                objective=1.0,
                kkt_residual=0.0,
                constraint_residual=0.0,
                warm_started=False,
            ),
        )

    def close(self):
        self.closed += 1


def test_dynamic_controller_composes_config_control_status_and_trace_contract(monkeypatch):
    mpc = import_module("controller.mpc")
    estimator = _Estimator()
    solver_box = {}
    monkeypatch.setattr(mpc, "GreyBoxEKF", lambda **_kwargs: estimator)

    def build_solver(config):
        solver_box["solver"] = _Solver(config)
        return solver_box["solver"]

    monkeypatch.setattr(mpc, "AcadosGreyBoxMPC", build_solver)
    controller = mpc.Controller(
        {
            "control_period": 2.0,
            "n_horizon": 5,
            "enable_fan_input": True,
            "enable_online_adaptation": False,
        },
        "F",
        {"u_min": 0.1, "u_max": 0.8},
    )
    solver = solver_box["solver"]

    assert controller.get_control_period() == 2.0
    assert controller.commands_fan() is True
    assert controller.wants_async() is True
    assert controller.trace_diagnostics() is None
    assert controller.trace_allocation() is None
    assert controller.trace_baseline_allocation() is None

    controller.set_target(212.0)
    controller.set_output(AppliedOutput(0.4, OutputSource.CONTROLLER, 1.0))
    output = controller.update(77.0)

    assert estimator.calls == [(0.5, 25.0)]
    _state, setpoint_c, previous_load, _equilibrium = solver.calls[0]
    assert setpoint_c == pytest.approx(100.0)
    assert previous_load == pytest.approx(0.5)
    assert output["cycle_ratio"] == pytest.approx(0.2)
    assert set(output) == {"cycle_ratio", "fan"}

    status = controller.get_status()
    assert status["set_point"] == 212.0
    assert status["set_point_c"] == pytest.approx(100.0)
    assert status["n_horizon"] == 5
    assert status["applied_combustion_load"] == pytest.approx(0.5)
    assert status["policy_kind"] == "acados-grey"

    diagnostics = controller.trace_diagnostics()
    allocation = controller.trace_allocation()
    baseline = controller.trace_baseline_allocation()
    calibration = controller.trace_calibration()
    assert diagnostics.failure_state is MpcFailureState.SUCCESS
    assert diagnostics.applied_combustion_load == pytest.approx(0.5)
    assert allocation.auger_duty == pytest.approx(output["cycle_ratio"])
    assert baseline == allocation
    assert calibration.active is False
    assert calibration.probe_q == 0.0

    incumbent = controller.active_control_pair
    candidate_descriptor = replace(
        incumbent.descriptor,
        candidate_generation=incumbent.descriptor.candidate_generation + 1,
        role_generation=incumbent.descriptor.role_generation + 1,
        ownership_digest="",
    )
    candidate_estimator = _Estimator()
    candidate_solver = _Solver(solver.config)
    candidate = OwnedGreyControlPair(
        candidate_descriptor,
        candidate_estimator,
        candidate_solver,
    )
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent.descriptor,
        candidate=candidate_descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.PASSIVE_AUTO,
        decision_id="public-close-contract",
    )
    controller._persist_grey_lifecycle = lambda *_args, **_kwargs: None
    assert controller.install_candidate_pair_inert(candidate, prepared)
    assert controller.authorize_candidate_pair(prepared.transition(ActivationPhase.ACTIVE))

    close_events = []

    def record_close(handle, label):
        def close():
            close_events.append(label)
            handle.closed += 1

        return close

    controller._learning = SimpleNamespace(close=lambda: close_events.append("learning"))
    candidate_solver.close = record_close(candidate_solver, "active-solver")
    candidate_estimator.close = record_close(candidate_estimator, "active-estimator")
    solver.close = record_close(solver, "rollback-solver")
    estimator.close = record_close(estimator, "rollback-estimator")

    controller.close()
    controller.close()

    assert close_events == [
        "learning",
        "active-solver",
        "active-estimator",
        "rollback-solver",
        "rollback-estimator",
    ]
    assert candidate_estimator.closed == candidate_solver.closed == 1
    assert estimator.closed == solver.closed == 1
