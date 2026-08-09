from types import SimpleNamespace

import numpy as np
import pytest

import controller.mpc as mpc_module
from common.control_trace import ActuationMode, AmbientSource
from common.model_evidence import ForecastOriginEvidence
from controller.acados import SolverError
from controller.applied_output import AppliedOutput, OutputSource
from controller.base import MpcFailureState
from controller.model_learning.contracts import FrameObservation
from controller.model_learning.evaluation import CompletedForecastOrigin, ForecastOrigin


CYCLE = {"u_min": 0.1, "u_max": 0.9}
CONFIG = {
    "n_horizon": 5,
    "control_period": 1.0,
    "Q_w": 1.0,
    "R_dQ": 0.1,
    "C_c": 320.0,
    "h_amb": 0.5,
    "T_amb": 20.0,
    "theta": 50.0,
    "K_Q": 350.0,
    "sigma": 1.4e-9,
    "estimator": "ekf",
    "est_q_temp": 1e-2,
    "est_q_dist": 0.05,
    "est_r_meas": 0.04,
    "enable_fan_input": True,
    "fan_min_pct": 40.0,
    "fan_max_pct": 100.0,
    "enable_online_adaptation": False,
}


class FakeEstimator:
    def __init__(self, **_kwargs):
        self.calls = []
        self.closed = 0
        self.x = np.array([0.1] * 8 + [72.0, 0.03], dtype=float)

    def update(self, applied_load, measured_c):
        self.calls.append((applied_load, measured_c))
        return self.x.copy()

    def close(self):
        self.closed += 1


class FakeSolver:
    def __init__(self, config, results=None):
        self.config = config
        self.results = list(results or [])
        self.calls = []
        self.closed = 0

    def solve(self, state, *, setpoint_c, q_previous, equilibrium_q):
        self.calls.append((np.asarray(state).copy(), setpoint_c, q_previous, equilibrium_q))
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, BaseException):
                raise result
            return result
        return _solve(self.config.horizon_steps, 0.5)

    def close(self):
        self.closed += 1


def _diagnostics(**overrides):
    values = dict(
        status=0,
        backend_status=0,
        iterations=2,
        solve_time_s=0.001,
        objective=3.0,
        kkt_residual=1e-7,
        constraint_residual=0.0,
        warm_started=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _solve(length, first, **overrides):
    values = dict(
        sequence_q=np.array([first] + [first / 2.0] * (length - 1), dtype=float),
        sequence_residual=np.zeros(length, dtype=float),
        objective=3.0,
        diagnostics=_diagnostics(),
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _make(monkeypatch, *, results=None, config=None):
    estimator = FakeEstimator()
    solver_box = {}
    monkeypatch.setattr(mpc_module, "GreyBoxEKF", lambda **_kwargs: estimator)

    def build_solver(native_config):
        solver = FakeSolver(native_config, results)
        solver_box["solver"] = solver
        return solver

    monkeypatch.setattr(mpc_module, "AcadosGreyBoxMPC", build_solver, raising=False)
    controller = mpc_module.Controller(dict(CONFIG if config is None else config), "C", dict(CYCLE))
    controller.set_target(110.0)
    return controller, estimator, solver_box["solver"]


def test_success_estimates_from_realized_load_and_uses_first_valid_native_command(monkeypatch):
    controller, estimator, solver = _make(monkeypatch, results=[_solve(5, 0.625)])
    controller.set_output(AppliedOutput(0.45, OutputSource.CONTROLLER, 1.0))

    output = controller.update(74.0)

    assert estimator.calls == [(0.5, 74.0)]
    state, setpoint, previous, equilibrium = solver.calls[0]
    assert state == pytest.approx(estimator.x)
    assert setpoint == 110.0
    assert previous == 0.5
    assert equilibrium == 0.0
    assert controller._last_combustion_load == pytest.approx(0.625)
    assert output["cycle_ratio"] == pytest.approx(0.625 * CYCLE["u_max"])
    assert 40.0 <= output["fan"]["duty"] <= 100.0
    trace = controller.trace_diagnostics()
    assert trace.policy_kind == "acados-grey"
    assert trace.failure_state is MpcFailureState.SUCCESS
    assert trace.applied_combustion_load == 0.5


@pytest.mark.parametrize(
    "invalid",
    [
        _solve(4, 0.5),
        _solve(5, 0.5, sequence_residual=np.zeros(4)),
        _solve(5, float("nan")),
        _solve(5, 1.01),
        _solve(5, 0.5, objective=float("inf")),
        _solve(5, 0.5, diagnostics=_diagnostics(status=1)),
        _solve(5, 0.5, diagnostics=_diagnostics(kkt_residual=float("nan"))),
    ],
    ids=["length", "residual-length", "nonfinite-q", "bounds", "objective", "status", "diagnostics"],
)
def test_every_malformed_native_result_holds_the_last_safe_load(monkeypatch, invalid):
    controller, _estimator, _solver = _make(
        monkeypatch, results=[_solve(5, 0.6), invalid]
    )
    controller.update(72.0)

    output = controller.update(73.0)

    assert controller._last_combustion_load == pytest.approx(0.6)
    assert output["cycle_ratio"] == pytest.approx(0.54)
    trace = controller.trace_diagnostics()
    assert trace.failure_state is MpcFailureState.POLICY_EXCEPTION
    assert trace.consecutive_policy_failures == 1
    assert trace.policy_kind == "acados-grey"


def test_structured_solver_failure_holds_and_preserves_diagnostics(monkeypatch):
    diagnostics = mpc_module.SolverDiagnostics(
        status=4,
        backend_status=7,
        iterations=10,
        solve_time_s=0.02,
        objective=1.0,
        kkt_residual=0.2,
        constraint_residual=0.1,
        warm_started=True,
    )
    error = SolverError("native solve failed", diagnostics)
    controller, _estimator, _solver = _make(
        monkeypatch, results=[_solve(5, 0.7), error]
    )
    controller.update(70.0)
    controller.update(71.0)

    assert controller._last_combustion_load == pytest.approx(0.7)
    assert controller.native_failure_diagnostics() is diagnostics
    assert controller.get_status()["policy_failures"] == 1


def test_partial_native_build_closes_the_estimator(monkeypatch):
    estimator = FakeEstimator()
    monkeypatch.setattr(mpc_module, "GreyBoxEKF", lambda **_kwargs: estimator)
    monkeypatch.setattr(
        mpc_module,
        "AcadosGreyBoxMPC",
        lambda _config: (_ for _ in ()).throw(RuntimeError("native unavailable")),
        raising=False,
    )

    with pytest.raises(RuntimeError, match="native unavailable"):
        mpc_module.Controller(dict(CONFIG), "C", dict(CYCLE))

    assert estimator.closed == 1


def test_close_releases_learning_then_exactly_one_complete_pair(monkeypatch):
    events = []
    controller, estimator, solver = _make(monkeypatch)
    estimator.close = lambda: events.append("estimator")
    solver.close = lambda: events.append("solver")
    controller._learning = SimpleNamespace(close=lambda: events.append("learning"))

    controller.close()
    controller.close()

    assert events == ["learning", "solver", "estimator"]


def _frame(*, sequence=1, operator=False):
    return FrameObservation(
        frame_start_s=float(sequence * 20 - 20),
        frame_end_s=float(sequence * 20),
        temp_c=80.0,
        setpoint_c=110.0,
        ambient_c=20.0,
        requested_q=0.4,
        realized_q=0.35,
        requested_auger_duty=0.36,
        delivered_on_s=7.0,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=sequence,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
        observation_sequence=sequence,
        calibration_fit=operator,
        baseline_q=0.3 if operator else None,
        calibration_stage="low" if operator else None,
        probe_q=0.1 if operator else 0.0,
    )


def test_passive_and_operator_observations_dispatch_to_task7_orchestrator(monkeypatch):
    controller, _estimator, _solver = _make(monkeypatch)
    seen = []
    orchestrator = SimpleNamespace(
        observe_completed_frame=lambda frame, *, identifiability: seen.append((frame, identifiability))
        or SimpleNamespace(
            history=SimpleNamespace(accepted=True, reasons=()),
            completed_forecasts=(),
        ),
        poll_fit_off_path=lambda **kwargs: ("polled", kwargs),
        evaluate_ready_off_path=lambda: "evaluated",
        close=lambda: None,
    )
    controller._learning = orchestrator
    passive = _frame(sequence=1)
    operator = _frame(sequence=2, operator=True)

    passive_outcome = controller.observe_frame(passive)
    operator_outcome = controller.observe_frame(operator)
    off_path = controller.poll_learning_off_path(live_origin="passive-online")

    assert [item[0] for item in seen] == [passive, operator]
    assert all(item[1] >= 0.0 for item in seen)
    assert passive_outcome["eligible"] is True
    assert operator_outcome["eligible"] is True
    assert off_path[0][0] == "polled"
    assert off_path[1] == "evaluated"


def test_completed_task7_forecasts_are_translated_to_compact_runner_evidence(monkeypatch):
    controller, _estimator, _solver = _make(monkeypatch)
    completed = CompletedForecastOrigin(
        forecast=ForecastOrigin(
            origin_sequence=1,
            origin_time_s=20.0,
            horizon_steps=3,
            role_generation=0,
            candidate_generation=1,
            incumbent_digest="a" * 64,
            challenger_digest="b" * 64,
            incumbent_prediction_c=79.0,
            challenger_prediction_c=81.0,
            temperature_band="below-target",
            phase="heating",
            ambient_source=AmbientSource.CONFIGURED,
            calibration_fit=False,
        ),
        completion_time_s=80.0,
        observed_temperature_c=82.0,
    )
    controller._learning = SimpleNamespace(
        observe_completed_frame=lambda _frame, *, identifiability: SimpleNamespace(
            history=SimpleNamespace(accepted=True, reasons=()),
            completed_forecasts=(completed,),
            request=None,
        ),
        passive_history=SimpleNamespace(observations=()),
        register_causal_forecasts=lambda *_args, **_kwargs: (),
        close=lambda: None,
    )

    outcome = controller.observe_frame(_frame(sequence=4))

    assert outcome["forecast_origin_evidence"] == (
        ForecastOriginEvidence(
            origin_sequence=1,
            origin_time_ms=20_000,
            completion_time_ms=80_000,
            horizon_steps=3,
            incumbent_digest="a" * 64,
            challenger_digest="b" * 64,
            incumbent_prediction_c=79.0,
            challenger_prediction_c=81.0,
            observed_temperature_c=82.0,
            incumbent_error_c=3.0,
            challenger_error_c=1.0,
            temperature_band="below-target",
            phase="heating",
            ambient_source=AmbientSource.CONFIGURED,
            calibration_fit=False,
        ),
    )


def test_control_capabilities_and_status_remain_stable(monkeypatch):
    controller, _estimator, _solver = _make(monkeypatch)
    assert controller.get_control_period() == 1.0
    assert controller.commands_fan() is True
    assert controller.wants_async() is True
    assert controller.actuation_mode() is ActuationMode.FRAMED_PULSE
    status = controller.get_status()
    assert status["policy_kind"] == "acados-grey"
    assert status["n_horizon"] == 5
