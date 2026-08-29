import threading
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

import controller.mpc as mpc_module
import controller.mpc_core as mpc_core_module
from common.control_trace import ActuationMode, AmbientSource, ModelEvaluationPayload
from common.learning_trajectory import (
    FitCorpusIdentity,
    FitCorpusSlice,
    canonical_trajectory_digest,
)
from common.model_evidence import ForecastOriginEvidence
from controller.acados import SolverDiagnostics, SolverError
from controller.applied_output import AppliedOutput, OutputSource
from controller.base import ControllerLearningDiagnostics, MpcFailureState
from controller.model_learning.contracts import (
    CandidateOrigin,
    FitRequest,
    FrameObservation,
)
from controller.model_learning.evaluation import (
    CompletedForecastOrigin,
    EvaluationConfig,
    EvaluationDecision,
    ForecastOrigin,
    HorizonScore,
)
from controller.runtime.model_fitting import (
    CandidatePair,
    CandidatePreparation,
    FitSubmission,
    GreyFitJob,
    GreyFitMessage,
    GreyFitSegmentArrays,
    GreyFitSuccess,
    GreyLearningOrchestrator,
    TargetTimingEvidence,
    TriggerConfig,
)

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


def _fit_corpus(identity) -> FitCorpusIdentity:
    corpus_slice = FitCorpusSlice(
        segment_id=f"{identity.session_id}:{identity.cook_id or 'no-cook'}",
        through_ordinal=0,
        prefix_digest=canonical_trajectory_digest(
            {
                "session_id": identity.session_id,
                "cook_id": identity.cook_id,
                "first_observation_sequence": 0,
                "last_observation_sequence": 0,
            }
        ),
        pre_roll_count=0,
        scored_count=1,
    )
    payload = {
        "schema_version": 1,
        "corpus_revision": 0,
        "fit_partition_digest": identity.configuration_digest,
        "slices": [
            {
                "segment_id": corpus_slice.segment_id,
                "through_ordinal": corpus_slice.through_ordinal,
                "prefix_digest": corpus_slice.prefix_digest,
                "pre_roll_count": corpus_slice.pre_roll_count,
                "scored_count": corpus_slice.scored_count,
            }
        ],
    }
    return FitCorpusIdentity(
        schema_version=1,
        corpus_revision=0,
        fit_partition_digest=identity.configuration_digest,
        slices=(corpus_slice,),
        corpus_digest=canonical_trajectory_digest(payload),
    )


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
    values = {
        "status": 0,
        "backend_status": 0,
        "iterations": 2,
        "solve_time_s": 0.001,
        "objective": 3.0,
        "kkt_residual": 1e-7,
        "constraint_residual": 0.0,
        "warm_started": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _solve(length, first, **overrides):
    values = {
        "sequence_q": np.array([first] + [first / 2.0] * (length - 1), dtype=float),
        "sequence_residual": np.zeros(length, dtype=float),
        "objective": 3.0,
        "diagnostics": _diagnostics(),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _make(monkeypatch, *, results=None, config=None):
    estimator = FakeEstimator()
    solver_box = {}
    monkeypatch.setattr(mpc_core_module, "GreyBoxEKF", lambda **_kwargs: estimator)

    def build_solver(native_config):
        solver = FakeSolver(native_config, results)
        solver_box["solver"] = solver
        return solver

    monkeypatch.setattr(mpc_core_module, "AcadosGreyBoxMPC", build_solver)
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
    assert controller.get_status()["last_combustion_load"] == pytest.approx(0.625)
    assert output["cycle_ratio"] == pytest.approx(0.625 * CYCLE["u_max"])
    assert 40.0 <= output["fan"]["duty"] <= 100.0
    trace = controller.trace_diagnostics()
    assert trace.policy_kind == "acados-grey"
    assert trace.failure_state is MpcFailureState.SUCCESS
    assert trace.applied_combustion_load == 0.5


def test_native_roundoff_at_command_bounds_is_clipped_not_rejected(monkeypatch):
    result = _solve(
        5,
        -1e-12,
        sequence_residual=np.array([-1e-12, 0.0, 0.5, 1.0, 1.0 + 1e-12]),
        diagnostics=_diagnostics(constraint_residual=1e-12),
    )
    controller, _estimator, _solver = _make(monkeypatch, results=[result])

    output = controller.update(72.0)

    assert controller.get_status()["last_combustion_load"] == 0.0
    assert output["cycle_ratio"] == 0.0
    assert controller.trace_diagnostics().failure_state is MpcFailureState.SUCCESS


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
    controller, _estimator, _solver = _make(monkeypatch, results=[_solve(5, 0.6), invalid])
    controller.update(72.0)

    output = controller.update(73.0)

    assert controller.get_status()["last_combustion_load"] == pytest.approx(0.6)
    assert output["cycle_ratio"] == pytest.approx(0.54)
    trace = controller.trace_diagnostics()
    assert trace.failure_state is MpcFailureState.POLICY_EXCEPTION
    assert trace.consecutive_policy_failures == 1
    assert trace.policy_kind == "acados-grey"


def test_structured_solver_failure_holds_and_preserves_diagnostics(monkeypatch):
    diagnostics = SolverDiagnostics(
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
    controller, _estimator, _solver = _make(monkeypatch, results=[_solve(5, 0.7), error])
    controller.update(70.0)
    controller.update(71.0)

    assert controller.get_status()["last_combustion_load"] == pytest.approx(0.7)
    assert controller.native_failure_diagnostics() is diagnostics
    assert controller.get_status()["policy_failures"] == 1


def test_partial_native_build_closes_the_estimator(monkeypatch):
    estimator = FakeEstimator()
    monkeypatch.setattr(mpc_core_module, "GreyBoxEKF", lambda **_kwargs: estimator)
    monkeypatch.setattr(
        mpc_core_module,
        "AcadosGreyBoxMPC",
        lambda _config: (_ for _ in ()).throw(RuntimeError("native unavailable")),
    )

    with pytest.raises(RuntimeError, match="native unavailable"):
        mpc_module.Controller(dict(CONFIG), "C", dict(CYCLE))

    assert estimator.closed == 1


def test_close_releases_learning_then_exactly_one_complete_pair(monkeypatch):
    events = []
    controller, estimator, solver = _make(monkeypatch)
    estimator.close = lambda: events.append("estimator")
    solver.close = lambda: events.append("solver")
    controller._grey_learning_runtime._learning = SimpleNamespace(close=lambda: events.append("learning"))

    controller.close()
    controller.close()

    assert events == ["learning", "solver", "estimator"]


@pytest.mark.parametrize(
    ("origin", "replace_owned_prepared"),
    (
        (CandidateOrigin.PASSIVE_ONLINE, False),
        (CandidateOrigin.OPERATOR_CALIBRATION, True),
    ),
)
def test_only_operator_fit_requests_may_replace_an_owned_challenger(
    monkeypatch,
    origin,
    replace_owned_prepared,
):
    controller, _estimator, _solver = _make(monkeypatch)
    requests = []
    tickets = []
    monkeypatch.setattr(
        controller._grey_learning_runtime,
        "request_corpus_fit",
        lambda actual_origin, *, replace_owned_prepared: (
            requests.append((actual_origin, replace_owned_prepared)) or True
        ),
    )
    monkeypatch.setattr(
        controller._grey_learning_runtime,
        "_request_corpus_fit_ticket",
        lambda actual_origin, *, replace_owned_prepared: (
            tickets.append((actual_origin, replace_owned_prepared)) or "fit-ticket"
        ),
    )

    assert controller.schedule_corpus_fit(origin)
    assert controller._schedule_corpus_fit_ticket(origin) == "fit-ticket"

    expected = (origin, replace_owned_prepared)
    assert requests == [expected]
    assert tickets == [expected]


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
        observe_completed_frame=lambda frame, *, identifiability: (
            seen.append((frame, identifiability))
            or SimpleNamespace(
                history=SimpleNamespace(accepted=True, reasons=()),
                completed_forecasts=(),
                trigger=SimpleNamespace(input_variance=0.03, input_levels=3),
            )
        ),
        poll_fit_off_path=lambda **kwargs: ("polled", kwargs),
        evaluate_ready_off_path=lambda: None,
        close=lambda: None,
    )
    controller._grey_learning_runtime._learning = orchestrator
    passive = _frame(sequence=1)
    operator = _frame(sequence=2, operator=True)

    passive_outcome = controller.observe_frame(passive)
    operator_outcome = controller.observe_frame(operator)
    off_path = controller.poll_learning_off_path(live_origin="passive-online")

    assert [item[0] for item in seen] == [passive, operator]
    assert all(item[1] >= 0.0 for item in seen)
    assert passive_outcome["eligible"] is True
    assert operator_outcome["eligible"] is True
    assert passive_outcome["input_variance"] == 0.03
    assert passive_outcome["input_levels"] == 3
    assert off_path[0][0] == "polled"
    assert off_path[1] is None


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
    controller._grey_learning_runtime._learning = SimpleNamespace(
        observe_completed_frame=lambda _frame, *, identifiability: SimpleNamespace(
            history=SimpleNamespace(accepted=True, reasons=()),
            completed_forecasts=(completed,),
            request=None,
            trigger=SimpleNamespace(input_variance=0.03, input_levels=3),
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


def test_task7_evaluation_is_published_through_the_established_runner_payload(monkeypatch):
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
    decision = EvaluationDecision(
        decision_id="c" * 64,
        accepted=False,
        role_generation=0,
        candidate_generation=1,
        incumbent_digest="a" * 64,
        challenger_digest="b" * 64,
        consecutive_wins=1,
        blockers=(),
        scores=(HorizonScore(3, 3.0, 1.0, 1), HorizonScore(15, 0.0, 0.0, 0)),
        completed_origins=(completed,),
    )
    controller._grey_learning_runtime._learning = SimpleNamespace(
        evaluation_config=EvaluationConfig(),
        poll_fit_off_path=lambda **_kwargs: None,
        evaluate_ready_off_path=lambda: decision,
        observe_completed_frame=lambda _frame, *, identifiability: SimpleNamespace(
            history=SimpleNamespace(accepted=True, reasons=()),
            completed_forecasts=(),
            request=None,
            trigger=SimpleNamespace(input_variance=0.03, input_levels=3),
        ),
        passive_history=SimpleNamespace(observations=()),
        register_causal_forecasts=lambda *_args, **_kwargs: (),
        close=lambda: None,
    )
    monkeypatch.setattr(
        controller._grey_learning_runtime,
        "_persist_durable_evaluation_round",
        lambda _evaluation, _preparation: SimpleNamespace(
            phase="evaluating",
            last_decision_id=decision.decision_id,
        ),
    )
    monkeypatch.setattr(
        controller._grey_learning_runtime,
        "_persist_candidate_evaluation",
        lambda _evaluation, _preparation: None,
    )

    _delivery, evaluation = controller.poll_learning_off_path()
    outcome = controller.observe_frame(_frame(sequence=5))

    assert isinstance(evaluation, ModelEvaluationPayload)
    assert outcome["evaluation_payload"] is evaluation
    assert evaluation.challenger_model_kind == "grey-box"
    assert evaluation.completed_origins[0].observed_temperature_c == 82.0
    assert evaluation.horizon_scores[0].challenger_rmse_c == 1.0


def test_slow_candidate_preparation_does_not_block_live_observation(monkeypatch):
    controller, _estimator, _solver = _make(monkeypatch)
    preparing = threading.Event()
    release = threading.Event()
    observed = threading.Event()

    def slow_poll(**_kwargs):
        preparing.set()
        assert release.wait(2.0)

    controller._grey_learning_runtime._learning = SimpleNamespace(
        poll_fit_off_path=slow_poll,
        evaluate_ready_off_path=lambda: None,
        observe_completed_frame=lambda _frame, *, identifiability: (
            observed.set(),
            SimpleNamespace(
                history=SimpleNamespace(accepted=True, reasons=()),
                completed_forecasts=(),
                request=None,
                trigger=SimpleNamespace(input_variance=0.03, input_levels=3),
            ),
        )[1],
        passive_history=SimpleNamespace(observations=()),
        register_causal_forecasts=lambda *_args, **_kwargs: (),
        close=lambda: None,
    )
    controller._grey_learning_runtime._learning_pending_origin = "passive-online"
    polling = threading.Thread(target=controller.poll_learning_off_path)
    observing = threading.Thread(target=controller.observe_frame, args=(_frame(sequence=6),))
    polling.start()
    assert preparing.wait(1.0)
    observing.start()
    try:
        assert observed.wait(0.1)
    finally:
        release.set()
        polling.join(2.0)
        observing.join(2.0)


def test_identity_rebind_fences_and_discards_an_inflight_old_generation_candidate(monkeypatch):
    controller, _estimator, _solver = _make(monkeypatch)
    preparing = threading.Event()
    release = threading.Event()
    rebound = threading.Event()

    class Closable:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    old_pair = CandidatePair(Closable(), Closable())
    new_pair = CandidatePair(Closable(), Closable())

    incumbent_pair = CandidatePair(_estimator, _solver)
    initial_identity = controller._grey_learning_runtime.learning_identity()

    class RacingLearning:
        def __init__(self):
            self.calls = 0
            self.prepared = None
            self.identities = []
            self.identity = initial_identity
            self.evaluation_config = EvaluationConfig()

        def poll_fit_off_path(self, *, live_identity, **_kwargs):
            self.calls += 1
            pair = old_pair if self.calls == 1 else new_pair
            if self.calls == 1:
                preparing.set()
                assert release.wait(2.0)
            request = FitRequest(
                request_id=f"racing-{self.calls}",
                origin=CandidateOrigin.PASSIVE_ONLINE,
                fit_corpus=_fit_corpus(live_identity),
                configuration_digest=live_identity.configuration_digest,
                parent_incumbent_digest=live_identity.incumbent_digest,
                parent_incumbent_generation=live_identity.role_generation,
                candidate_generation=live_identity.candidate_generation,
            )
            candidate = GreyFitSuccess(
                request=request,
                config=_solver.config,
                rmse_c=1.0,
                max_error_c=1.0,
                identifiability=1.0,
                sample_count=1,
                temperature_band_c=(20.0, 21.0),
                nfev=1,
            )
            preparation = CandidatePreparation.accepted_for_test(
                candidate=candidate,
                candidate_pair=pair,
                incumbent_pair=incumbent_pair,
                timing=TargetTimingEvidence(
                    target="pi",
                    samples=1,
                    p99_ms=1.0,
                    limit_ms=5.0,
                ),
            )
            self.prepared = preparation
            return GreyLearningDelivery(
                message=None,
                stale_reasons=(),
                preparation=preparation,
            )

        def evaluate_ready_off_path(self):
            return None

        def _release_prepared(self):
            if self.prepared is not None:
                self.prepared.candidate_pair.controller.close()
                self.prepared.candidate_pair.estimator.close()
                self.prepared = None

        def _reset_prepared_evaluation(self):
            return None

        def update_identity(self, identity, **_kwargs):
            self.identities.append(identity)
            self.identity = identity
            self._release_prepared()

        def close(self):
            return None

    learning = RacingLearning()
    controller._grey_learning_runtime._learning = learning
    controller._grey_learning_runtime._learning_pending_origin = "passive-online"
    polling = threading.Thread(target=controller.poll_learning_off_path)
    rebinding = threading.Thread(
        target=lambda: (
            controller.bind_learning_identity("new-session", "new-cook", 1),
            rebound.set(),
        )
    )
    polling.start()
    assert preparing.wait(1.0)
    rebinding.start()
    try:
        release.set()
        polling.join(2.0)
        rebinding.join(2.0)
    finally:
        release.set()

    assert rebound.is_set()
    assert old_pair.controller.closed is True
    assert old_pair.estimator.closed is True
    assert controller._grey_learning_runtime._learning_candidate_pair is None
    assert learning.identities[-1].role_generation == 1

    controller._grey_learning_runtime._learning_pending_origin = "passive-online"
    delivery, _evaluation = controller.poll_learning_off_path()
    assert delivery.preparation.candidate_pair is new_pair
    assert controller._grey_learning_runtime._learning_candidate_pair is new_pair


def test_rejected_real_fit_candidate_is_released_and_a_later_fit_can_prepare(monkeypatch):
    controller, estimator, solver = _make(monkeypatch)

    class ImmediateWorker:
        def __init__(self):
            self.job = None

        def start(self):
            return self

        def submit(self, job):
            self.job = job
            return FitSubmission.ACCEPTED

        def receive(self, *, timeout_s):
            assert timeout_s == 0.0
            temperatures = tuple(
                float(value) for segment in self.job.segments for value in segment.scored_temperature_c
            )
            return GreyFitMessage(
                request=self.job.request,
                outcome=GreyFitSuccess(
                    request=self.job.request,
                    config=replace(self.job.config, C_c=900.0, K_Q=700.0, theta=75.0),
                    rmse_c=1.0,
                    max_error_c=2.0,
                    identifiability=1.0,
                    sample_count=len(temperatures),
                    temperature_band_c=(min(temperatures), max(temperatures)),
                    nfev=4,
                ),
                worker_start_method="spawn",
                worker_thread_environment=(
                    ("OMP_NUM_THREADS", "1"),
                    ("OPENBLAS_NUM_THREADS", "1"),
                    ("MKL_NUM_THREADS", "1"),
                    ("VECLIB_MAXIMUM_THREADS", "1"),
                    ("NUMEXPR_NUM_THREADS", "1"),
                ),
            )

        def close(self):
            return None

    class CandidateEstimator:
        def __init__(self, config):
            self.config = config
            self.state = np.zeros(10)
            self.closed = False

        def close(self):
            self.closed = True

    class CandidateController:
        def __init__(self, config):
            self.config = config
            self.closed = False

        def solve(self, state, **_kwargs):
            assert np.asarray(state).shape == (10,)
            return SimpleNamespace(
                sequence_q=np.full(self.config.horizon_steps, 0.4),
                objective=1.0,
            )

        def close(self):
            self.closed = True

    identity = controller._grey_learning_runtime.learning_identity()
    learning = GreyLearningOrchestrator(
        identity=identity,
        config=solver.config,
        incumbent_pair=CandidatePair(estimator, solver),
        estimator_factory=CandidateEstimator,
        controller_factory=CandidateController,
        timing_probe=lambda _native: TargetTimingEvidence(
            target="pi",
            samples=1_000,
            p99_ms=1.0,
            limit_ms=5.0,
        ),
        trigger_config=TriggerConfig(
            min_samples=9,
            min_input_variance=0.02,
            min_input_levels=3,
            min_temperature_span_c=8.0,
            min_identifiability=0.5,
        ),
        worker=ImmediateWorker(),
    ).start()
    controller._grey_learning_runtime._learning = learning

    def fit_job(request_id, first_sequence):
        count = 12
        loads = tuple((0.15, 0.5, 0.85)[index % 3] for index in range(count))
        segment = GreyFitSegmentArrays(
            segment_id=f"segment-{request_id}",
            cook_id="cook-controller-fixture",
            through_ordinal=count - 1,
            prefix_digest=canonical_trajectory_digest({"request_id": request_id}),
            fit_partition_digest=identity.configuration_digest,
            observation_sequences=tuple(range(first_sequence, first_sequence + count)),
            initial_load=loads[0],
            pre_roll_duration_s=(),
            pre_roll_load=(),
            pre_roll_temperature_c=(),
            hold_anchor_c=70.0,
            scored_duration_s=(20.0,) * count,
            scored_load=loads,
            scored_ambient_c=(20.0,) * count,
            scored_temperature_c=tuple(70.0 + index for index in range(count)),
            calibration_origin=(False,) * count,
        )
        corpus_slice = FitCorpusSlice(
            segment_id=segment.segment_id,
            through_ordinal=segment.through_ordinal,
            prefix_digest=segment.prefix_digest,
            pre_roll_count=0,
            scored_count=count,
        )
        corpus_payload = {
            "schema_version": 1,
            "corpus_revision": 1,
            "fit_partition_digest": identity.configuration_digest,
            "slices": [
                {
                    "segment_id": corpus_slice.segment_id,
                    "through_ordinal": corpus_slice.through_ordinal,
                    "prefix_digest": corpus_slice.prefix_digest,
                    "pre_roll_count": corpus_slice.pre_roll_count,
                    "scored_count": corpus_slice.scored_count,
                }
            ],
        }
        corpus = FitCorpusIdentity(
            schema_version=1,
            corpus_revision=1,
            fit_partition_digest=identity.configuration_digest,
            slices=(corpus_slice,),
            corpus_digest=canonical_trajectory_digest(corpus_payload),
        )
        request = FitRequest(
            request_id=request_id,
            origin=CandidateOrigin.PASSIVE_ONLINE,
            fit_corpus=corpus,
            configuration_digest=identity.configuration_digest,
            parent_incumbent_digest=identity.incumbent_digest,
            parent_incumbent_generation=identity.role_generation,
            candidate_generation=identity.candidate_generation,
        )
        return GreyFitJob(
            request=request,
            corpus=corpus,
            segments=(segment,),
            config=solver.config,
        )

    first_job = fit_job("first-corpus-fit", 1)
    assert learning.submit_corpus_fit(first_job) is FitSubmission.ACCEPTED
    controller._grey_learning_runtime._learning_pending_origin = first_job.request.origin
    first_delivery, _ = controller.poll_learning_off_path()
    first_candidate = first_delivery.preparation.candidate_pair.controller
    assert first_delivery.preparation.accepted is True

    origin_frame = _frame(sequence=10)
    learning.observe_completed_frame(origin_frame, identifiability=1.0)
    learning.register_causal_forecasts(
        origin_frame,
        incumbent_predict=lambda _origin: 80.0,
        challenger_predict=lambda _origin: 0.0,
    )
    for sequence in range(11, 191):
        learning.observe_completed_frame(_frame(sequence=sequence), identifiability=1.0)

    _delivery, evaluation = controller.poll_learning_off_path()

    assert isinstance(evaluation, ModelEvaluationPayload)
    assert evaluation.rejection_reasons == tuple(f"challenger-horizon-{horizon}" for horizon in (3, 15, 45, 90, 180))
    assert learning.prepared is None
    assert first_candidate.closed is True

    later_job = fit_job("later-corpus-fit", 300)
    assert learning.submit_corpus_fit(later_job) is FitSubmission.ACCEPTED
    controller._grey_learning_runtime._learning_pending_origin = later_job.request.origin
    later_delivery, _ = controller.poll_learning_off_path()
    assert later_delivery.preparation.accepted is True


def test_control_capabilities_and_status_remain_stable(monkeypatch):
    controller, _estimator, _solver = _make(monkeypatch)
    assert controller.get_control_period() == 1.0
    assert controller.commands_fan() is True
    assert controller.wants_async() is True
    assert controller.actuation_mode() is ActuationMode.FRAMED_PULSE
    status = controller.get_status()
    assert status["policy_kind"] == "acados-grey"
    assert status["n_horizon"] == 5


def test_get_learning_diagnostics_returns_owned_mpc_state(monkeypatch):
    controller, _estimator, _solver = _make(monkeypatch)

    diagnostics = controller.get_learning_diagnostics()
    first = diagnostics.as_json()
    first["status"] = "mutated"

    assert diagnostics.schema_version == 1
    assert diagnostics.as_json()["status"] != "mutated"


def test_get_status_uses_one_learning_capability_snapshot(monkeypatch):
    controller, _estimator, _solver = _make(monkeypatch)
    learning_payload = {
        "status": "evaluating",
        "fit_status": "succeeded",
        "role_generation": 4,
        "candidate_generation": 5,
        "checkpoint_digest": "a" * 64,
        "candidate_digest": "b" * 64,
        "origin": CandidateOrigin.PASSIVE_ONLINE.value,
        "checks": {
            "identifiability": "passed",
            "native_build": "passed",
            "native_dry_solve": "passed",
            "target_timing": "passed",
        },
        "activation_phase": "aborted",
        "pending_persistence": False,
        "pending_swap": False,
        "completed_horizons": [3, 15, 45],
        "required_horizons": [3, 15, 45, 90, 180],
        "resumed_from_previous_cook": True,
        "pending_origins": [
            {
                "origin_sequence": 12,
                "horizon_steps": 90,
                "role_generation": 4,
                "candidate_generation": 5,
                "incumbent_digest": "a" * 64,
                "candidate_digest": "b" * 64,
            }
        ],
        "failure": None,
    }
    diagnostics = ControllerLearningDiagnostics(
        schema_version=1,
        state=learning_payload,
    )
    calls = 0

    def get_learning_diagnostics():
        nonlocal calls
        calls += 1
        return diagnostics

    monkeypatch.setattr(controller, "get_learning_diagnostics", get_learning_diagnostics)

    status = controller.get_status()

    assert calls == 1
    assert status["learning"] == learning_payload
    assert status["activation"] == {
        "active_kind": "grey-box",
        "active_digest": controller.active_control_pair.descriptor.model_digest,
        "decision_id": None,
        "role_generation": 0,
        "failed_digest": None,
        "failed_generation": None,
        "seed_refresh_status": None,
        "last_safe_command": 0.0,
        "fallback_kind": None,
        "fallback_reason": None,
    }
    assert type(status["learning"]["role_generation"]) is int
    assert type(status["activation"]["role_generation"]) is int
