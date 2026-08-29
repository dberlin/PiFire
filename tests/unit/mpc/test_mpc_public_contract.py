from dataclasses import replace
from importlib import import_module
from math import ceil
from types import SimpleNamespace

import numpy as np
import pytest

from common.persistence.model_challenger import prepare_model_challenger_activation
from controller.applied_output import AppliedOutput, OutputSource
from controller.base import MpcFailureState
from controller.model_learning.activation import ActivationPhase, PreparedActivationRecord
from controller.model_learning.contracts import ActivationPolicy, CandidateOrigin
from controller.mpc_model import EstimatorSeed
from tests.unit.mpc._model_activation_helpers import _seed_qualified_challenger
from tests.unit.mpc._solver_fixtures import owned_pair


class _Estimator:
    def __init__(self, **_kwargs):
        self.calls = []
        self.closed = 0

    def update(self, applied_load, temperature_c):
        self.calls.append((applied_load, temperature_c))
        return np.array([0.0] * 8 + [temperature_c, 0.0], dtype=float)

    def reset(
        self,
        applied_load,
        measured_temperature,
        *,
        delay_states=None,
        disturbance=0.0,
    ):
        if measured_temperature is None:
            return None
        delays = [applied_load] * 8 if delay_states is None else list(delay_states)
        return np.array([*delays, measured_temperature, disturbance], dtype=float)

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

    def reset(self):
        return None

    def close(self):
        self.closed += 1


def test_dynamic_controller_composes_config_control_status_and_trace_contract(monkeypatch, ds):
    mpc = import_module("controller.mpc")
    mpc_core = import_module("controller.mpc_core")
    estimator = _Estimator()
    solver_box = {}
    monkeypatch.setattr(mpc_core, "GreyBoxEKF", lambda **_kwargs: estimator)

    def build_solver(config):
        solver_box["solver"] = _Solver(config)
        return solver_box["solver"]

    monkeypatch.setattr(mpc_core, "AcadosGreyBoxMPC", build_solver)
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

    incumbent = controller.active_control_pair
    status = controller.get_status()
    assert set(status) == {
        "set_point",
        "set_point_c",
        "last_combustion_load",
        "last_raw_combustion_load",
        "last_equilibrium_load",
        "last_residual_load",
        "applied_combustion_load",
        "policy",
        "policy_kind",
        "n_horizon",
        "policy_failures",
        "u_max",
        "x_hat",
        "cycle_data",
        "model",
        "feasibility",
        "learning",
        "activation",
    }
    assert status["set_point"] == 212.0
    assert status["set_point_c"] == pytest.approx(100.0)
    assert status["n_horizon"] == 5
    assert status["applied_combustion_load"] == pytest.approx(0.5)
    assert status["policy_kind"] == "acados-grey"
    assert status["learning"] == {
        "status": "collecting",
        "fit_status": "idle",
        "role_generation": 0,
        "candidate_generation": None,
        "checkpoint_digest": incumbent.descriptor.model_digest,
        "candidate_digest": None,
        "origin": None,
        "checks": {},
        "activation_phase": "aborted",
        "pending_persistence": False,
        "pending_swap": False,
        "completed_horizons": [],
        "required_horizons": [3, 15, 45, 90, 180],
        "resumed_from_previous_cook": False,
        "pending_origins": [],
        "failure": None,
    }
    assert status["activation"] == {
        "active_kind": "grey-box",
        "active_digest": incumbent.descriptor.model_digest,
        "decision_id": None,
        "role_generation": 0,
        "failed_digest": None,
        "failed_generation": None,
        "seed_refresh_status": None,
        "last_safe_command": 0.25,
        "fallback_kind": None,
        "fallback_reason": None,
    }
    assert controller.get_learning_diagnostics().schema_version == 1

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

    candidate_descriptor = replace(
        incumbent.descriptor,
        candidate_generation=incumbent.descriptor.candidate_generation + 1,
        role_generation=incumbent.descriptor.role_generation + 1,
        ownership_digest="",
    )
    candidate_estimator = _Estimator()
    candidate_solver = _Solver(solver.config)
    candidate = owned_pair(candidate_descriptor, candidate_estimator, candidate_solver)
    controller._activation_runtime.bind_estimator_seed_source(
        lambda theta, n_delay: EstimatorSeed(
            delay_states=(0.4,) * n_delay,
            chamber_temperature_c=100.0,
            disturbance=0.0,
            segment_id="public-contract",
            pre_roll_digest="c" * 64,
            pre_roll_frame_count=ceil(3 * theta / 2.0),
            required_frame_count=ceil(3 * theta / 2.0),
            status="exact",
        )
    )
    prepared = PreparedActivationRecord.prepared(
        timestamp_ms=1_000,
        incumbent=incumbent.descriptor,
        candidate=candidate_descriptor,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        policy=ActivationPolicy.CAUSAL_AUTO,
        decision_id="public-close-contract",
    )
    qualified = _seed_qualified_challenger(
        incumbent.descriptor,
        candidate_descriptor,
        decision_id=prepared.decision_id,
    )
    prepare_model_challenger_activation(
        expected_revision=qualified.revision,
        activation=prepared,
    )
    assert controller.install_candidate_pair_inert(candidate, prepared)
    assert controller.authorize_candidate_pair(prepared.transition(ActivationPhase.ACTIVE))

    close_events = []

    def record_close(handle, label):
        def close():
            close_events.append(label)
            handle.closed += 1

        return close

    candidate_solver.close = record_close(candidate_solver, "active-solver")
    candidate_estimator.close = record_close(candidate_estimator, "active-estimator")
    solver.close = record_close(solver, "rollback-solver")
    estimator.close = record_close(estimator, "rollback-estimator")

    controller.close()
    controller.close()

    assert close_events == [
        "active-solver",
        "active-estimator",
        "rollback-solver",
        "rollback-estimator",
    ]
    assert candidate_estimator.closed == candidate_solver.closed == 1
    assert estimator.closed == solver.closed == 1
