"""Direct behavioral contracts for the grey learning runtime."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, replace
from types import SimpleNamespace
from typing import ClassVar

import pytest

from common.controller_model_state import CheckpointSaveOutcome
from controller.acados import GreyBoxMPCConfig
from controller.model_learning.activation import GreyControlPairDescriptor, canonical_snapshot_digest
from controller.model_learning.activation_runtime import ActivationRuntime
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitRequest,
    FrameObservation,
    LearningStatus,
)
from controller.model_learning.evaluation import (
    EvaluationConfig,
    EvaluationDecision,
    HorizonScore,
)
from controller.model_learning.grey_runtime import GreyLearningRuntime
from controller.mpc_config import DEFAULT_MPC_CONFIG, MpcConfig
from controller.mpc_core import MpcCore
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.runtime.model_fitting import (
    CandidatePair,
    CandidatePreparation,
    FitErrorCode,
    FitSubmission,
    GreyFitError,
    GreyFitMetric,
    GreyFitMetrics,
    GreyFitSuccess,
    GreyFitWorker,
    GreyLearningOrchestrator,
    LiveLearningIdentity,
    TargetTimingEvidence,
    TeardownRefitOutcome,
    TriggerConfig,
    TriggerDecision,
)
from controller.runtime.model_persistence import DurableActivationReceipt, ModelPersistenceWorker
from tests.unit.mpc._solver_fixtures import CYCLE, _config, _Estimator, _Solver, inactive_calibration


class _Persistence(ModelPersistenceWorker):
    def __init__(self) -> None:
        self.close_count = 0
        self.evidence = []
        self.confidence = []
        self.confidence_preceding = []
        self.accept_confidence = True
        self.confidence_durable = True
        self.accept_evidence = True
        self.accept_phase = True

    def submit_evidence(self, record):
        self.evidence.append(record)
        return SimpleNamespace(accepted=self.accept_evidence)

    def submit_activation_confidence(self, record, *, preceding_evidence=()):
        self.confidence.append(record)
        self.confidence_preceding.append(preceding_evidence)
        receipt = DurableActivationReceipt(accepted=self.accept_confidence)
        if self.accept_confidence:
            receipt._complete(durable=self.confidence_durable)
        return receipt

    def submit_activation_phase(self, record, *, expected_phase):
        receipt = DurableActivationReceipt(accepted=self.accept_phase)
        if self.accept_phase:
            receipt._complete(durable=True)
        return receipt

    def flush_and_stop(self, *, timeout: float = 0.1) -> bool:

        self.close_count += 1
        return True


class _CheckpointStore:
    def __init__(
        self,
        outcome: CheckpointSaveOutcome = CheckpointSaveOutcome.SAVED,
    ) -> None:
        self.outcome = outcome
        self.snapshots = []

    def save_outcome(self, controller_type, snapshot):
        self.snapshots.append((controller_type, snapshot))
        return self.outcome


class _ProbeSolver(_Solver):
    def solve(self, _state, **_kwargs):
        return SimpleNamespace(
            sequence_q=[0.4] * self.config.horizon_steps,
            objective=0.0,
        )


class _CandidateEstimator(_Estimator):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _metric_bundle(job, rmse_c: float) -> GreyFitMetrics:
    segment = job.segments[0]
    loads = tuple(float(value) for value in segment.scored_load)
    temperatures = tuple(float(value) for value in segment.scored_temperature_c)
    mean_load = sum(loads) / len(loads)
    excitation = sum((value - mean_load) ** 2 for value in loads) / len(loads)
    pooled = GreyFitMetric(
        sample_count=len(loads),
        rmse_c=rmse_c,
        bias_c=0.0,
        error_band_c=(-rmse_c, rmse_c),
        max_error_c=rmse_c,
        input_excitation=excitation,
        input_levels=len(set(loads)),
        identifiability_row_count=len(loads),
        temperature_span_c=max(temperatures) - min(temperatures),
        identifiability=1.0,
    )
    return GreyFitMetrics(
        pooled=pooled,
        by_segment=(replace(pooled, segment_id=segment.segment_id),),
        by_cook=(
            replace(
                pooled,
                cook_id=segment.cook_id,
                supports_regression_gate=True,
            ),
        ),
    )


def _fit_success(job, *, rmse_c: float = 0.5) -> GreyFitSuccess:
    metrics = _metric_bundle(job, rmse_c)
    incumbent_metrics = _metric_bundle(job, 5.0)
    temperatures = tuple(
        float(value)
        for segment in job.segments
        for value in segment.scored_temperature_c
    )
    return GreyFitSuccess(
        request=job.request,
        config=GreyBoxMPCConfig(
            C_c=420.0,
            K_Q=400.0,
            theta=60.0,
            h_amb=0.7,
            T_amb=18.0,
        ),
        rmse_c=rmse_c,
        max_error_c=max(rmse_c, 1.0),
        identifiability=0.9,
        sample_count=len(temperatures),
        temperature_band_c=(min(temperatures), max(temperatures)),
        nfev=4,
        metrics=metrics,
        incumbent_metrics=incumbent_metrics,
        effective_masks=tuple((True,) * len(segment.scored_load) for segment in job.segments),
        optimizer_residual_count=len(temperatures),
    )


class _SuccessfulWorker:
    instances: ClassVar[list[_SuccessfulWorker]] = []

    def __init__(self) -> None:
        self.job = None
        self.closed = False
        self.__class__.instances.append(self)

    def start(self) -> None:
        return None

    def submit(self, job) -> FitSubmission:
        self.job = job
        return FitSubmission.ACCEPTED

    def receive(self, *, timeout_s: float):
        assert timeout_s == 120.0
        assert self.job is not None
        return SimpleNamespace(outcome=_fit_success(self.job))

    def close(self) -> None:
        self.closed = True


class _BusyWorker(_SuccessfulWorker):
    def submit(self, job) -> FitSubmission:
        self.job = job
        return FitSubmission.BUSY


class _CrashedWorker(_SuccessfulWorker):
    def receive(self, *, timeout_s: float):
        raise RuntimeError("worker crashed")


class _FitErrorWorker(_SuccessfulWorker):
    def receive(self, *, timeout_s: float):
        assert self.job is not None
        return SimpleNamespace(
            outcome=GreyFitError(
                request=self.job.request,
                code=FitErrorCode.FIT_EXCEPTION,
                error_type="RuntimeError",
                detail="fitter exploded",
            )
        )


class _RejectedWorker(_SuccessfulWorker):
    def receive(self, *, timeout_s: float):
        super().receive(timeout_s=timeout_s)
        return SimpleNamespace(outcome=_fit_success(self.job, rmse_c=500.0))


class _InvalidProbeSolver(_ProbeSolver):
    def solve(self, _state, **_kwargs):
        return SimpleNamespace(
            sequence_q=[float("nan")] * self.config.horizon_steps,
            objective=0.0,
        )


@dataclass(slots=True)
class _Harness:
    runtime: GreyLearningRuntime
    activation: ActivationRuntime
    active: OwnedMpcPair
    persistence: _Persistence
    factory: MpcPairFactory
    published: list[dict]


def _descriptor() -> GreyControlPairDescriptor:
    configuration = {
        "schema": "pifire-grey-box-model/v4",
        "n_delay": 8,
        "parameters": {
            "C_c": 410.0,
            "K_Q": 390.0,
            "theta": 62.0,
            "h_amb": 0.65,
            "T_amb": 18.0,
            "sigma": 1.1e-9,
        },
    }
    return GreyControlPairDescriptor(
        model_digest=canonical_snapshot_digest(configuration),
        configuration=configuration,
        estimator_kind="ekf",
        solver_kind="acados-grey",
        candidate_generation=0,
        role_generation=0,
    )


def _harness(
    *,
    history: tuple[tuple[float, float, float], ...] = (),
    fit_worker_factory=GreyFitWorker,
    learning_enabled: bool = False,
    solver_factory=_ProbeSolver,
    append_trace=lambda _records: None,
    estimator_kind="ekf",
    checkpoint_store=None,
    snapshot_parameters=None,
    base_configuration=None,
) -> _Harness:
    configured = dict(DEFAULT_MPC_CONFIG)
    if base_configuration is not None:
        configured.update(base_configuration)
    native = GreyBoxMPCConfig(
        C_c=410.0,
        K_Q=390.0,
        theta=62.0,
        h_amb=0.65,
        T_amb=18.0,
    )
    factory = MpcPairFactory(
        configured,
        "C",
        dict(CYCLE),
        advance_calibration=inactive_calibration,
        model_authority=lambda: (0, None),
        on_policy_failure=lambda _error: None,
        ekf_factory=_Estimator,
        kf_factory=_Estimator,
        solver_factory=solver_factory,
    )
    active = factory.build(
        factory.native(
            native,
            estimator_kind=estimator_kind,
            candidate_generation=0,
            role_generation=0,
        ),
        authorized=True,
    )
    persistence = _Persistence()
    activation = ActivationRuntime(factory, active, persistence)
    published: list[dict] = []
    runtime = GreyLearningRuntime(
        pair_factory=factory,
        activation_runtime=activation,
        learning_enabled=learning_enabled,
        units="C",
        cycle_data=dict(CYCLE),
        checkpoint_store=checkpoint_store,
        append_trace=append_trace,
        active_pair=lambda: activation.active_pair,
        active_components=lambda: CandidatePair(
            activation.active_pair.estimator,
            activation.active_pair.solver,
        ),
        configuration=lambda: MpcConfig(activation.active_pair.core.config),
        snapshot_parameters=(
            (lambda: activation.active_pair.core.snapshot_parameters())
            if snapshot_parameters is None
            else snapshot_parameters
        ),
        cook_history=lambda: history,
        sync_configuration=lambda: published.append(dict(activation.active_pair.core.config)),
        fit_worker_factory=fit_worker_factory,
    )
    return _Harness(runtime, activation, active, persistence, factory, published)


def _automatic_candidate(harness: _Harness):
    native = GreyBoxMPCConfig(
        C_c=425.0,
        K_Q=405.0,
        theta=59.0,
        h_amb=0.72,
        T_amb=18.0,
    )
    active_descriptor = harness.activation.active_pair.descriptor
    request = SimpleNamespace(
        origin=CandidateOrigin.PASSIVE_ONLINE,
        candidate_generation=active_descriptor.candidate_generation + 1,
        window=SimpleNamespace(role_generation=active_descriptor.role_generation),
    )
    descriptor = harness.factory.descriptor(
        harness.factory.native(
            native,
            estimator_kind=active_descriptor.estimator_kind,
            candidate_generation=request.candidate_generation,
            role_generation=active_descriptor.role_generation + 1,
        )
    )
    components = CandidatePair(_CandidateEstimator(), _ProbeSolver(native))
    preparation = SimpleNamespace(
        candidate_pair=components,
        candidate=SimpleNamespace(request=request, config=native),
        candidate_digest=descriptor.model_digest,
        dry_solve_finite=True,
    )
    evaluation = SimpleNamespace(
        decision_id="a" * 64,
        consecutive_wins=1,
        scores=(
            HorizonScore(3, 0.0, 0.0, 0),
            HorizonScore(15, 0.0, 0.0, 0),
        ),
        accepted=True,
        blockers=(),
        role_generation=active_descriptor.role_generation,
        candidate_generation=request.candidate_generation,
        incumbent_digest=harness.activation.active_pair.descriptor.model_digest,
        challenger_digest=descriptor.model_digest,
        completed_origins=(),
    )
    return preparation, evaluation, components


def _reviewed_candidate(harness: _Harness):
    identity = harness.runtime.learning_identity()
    window = identity.window(0, 119)
    request = FitRequest(
        request_id="b" * 64,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        window=window,
        candidate_generation=identity.candidate_generation,
    )
    native = GreyBoxMPCConfig(
        C_c=425.0,
        K_Q=405.0,
        theta=59.0,
        h_amb=0.72,
        T_amb=18.0,
    )
    descriptor = harness.factory.descriptor(
        harness.factory.native(
            native,
            estimator_kind=harness.active.descriptor.estimator_kind,
            candidate_generation=identity.candidate_generation,
            role_generation=identity.role_generation + 1,
        )
    )
    candidate = GreyFitSuccess(
        request=request,
        config=native,
        rmse_c=0.5,
        max_error_c=1.0,
        identifiability=0.9,
        sample_count=120,
        temperature_band_c=(75.0, 160.0),
        nfev=4,
    )
    components = CandidatePair(_CandidateEstimator(), _ProbeSolver(native))
    preparation = CandidatePreparation(
        candidate=candidate,
        incumbent_pair=CandidatePair(
            harness.active.estimator,
            harness.active.solver,
        ),
        accepted=True,
        blockers=(),
        candidate_pair=components,
        dry_solve_finite=True,
        timing=TargetTimingEvidence(
            target="candidate-dry-solve",
            samples=1,
            p99_ms=1.0,
            limit_ms=25.0,
        ),
    )
    evaluation = EvaluationDecision(
        decision_id="c" * 64,
        accepted=True,
        role_generation=identity.role_generation,
        candidate_generation=identity.candidate_generation,
        incumbent_digest=harness.active.descriptor.model_digest,
        challenger_digest=descriptor.model_digest,
        scores=(
            HorizonScore(3, 0.0, 0.0, 0),
            HorizonScore(15, 0.0, 0.0, 0),
        ),
        consecutive_wins=1,
        blockers=(),
    )
    return preparation, evaluation, components


def _stage_passive_checkpoint_preparation(
    harness: _Harness,
) -> CandidatePreparation:
    reviewed, _evaluation, _components = _reviewed_candidate(harness)
    request = replace(
        reviewed.candidate.request,
        origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    preparation = replace(
        reviewed,
        candidate=replace(reviewed.candidate, request=request),
    )
    harness.runtime._adopt_prepared_checkpoint_lineage(preparation)
    harness.runtime._close_prepared_candidate(preparation)
    return preparation


def _frame(sequence: int = 0) -> FrameObservation:
    return FrameObservation(
        frame_start_s=sequence * 20.0,
        frame_end_s=(sequence + 1) * 20.0,
        temp_c=75.0 + sequence,
        setpoint_c=120.0,
        ambient_c=20.0,
        requested_q=0.5,
        realized_q=0.5,
        requested_auger_duty=0.5,
        delivered_on_s=10.0,
        requested_fan_duty=0.5,
        actual_fan_duty=0.5,
        result_revision=sequence + 1,
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
    )


def test_accepted_fit_lineage_advances_once_per_request() -> None:
    harness = _harness()
    reviewed, _evaluation, components = _reviewed_candidate(harness)
    request = replace(
        reviewed.candidate.request,
        origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    preparation = replace(
        reviewed,
        candidate=replace(reviewed.candidate, request=request),
    )

    def delivery_for(candidate_preparation):
        return SimpleNamespace(
            message=SimpleNamespace(
                request=candidate_preparation.candidate.request,
                outcome=SimpleNamespace(config=candidate_preparation.candidate.config),
            ),
            stale_reasons=(),
            blockers=(),
            preparation=candidate_preparation,
        )

    class _Learning:
        def __init__(self):
            self.prepared = preparation
            self.pending_request = request
            self.handoff = None
            self.worker = SimpleNamespace(busy=False)
            self.delivery = delivery_for(preparation)

        def poll_fit_off_path(self, **_kwargs):
            return self.delivery

        def evaluate_ready_off_path(self):
            return None

        def close(self):
            components.estimator.close()
            components.controller.close()

    learning = _Learning()
    harness.runtime._learning = learning
    before = harness.runtime.get_model_snapshot()

    harness.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    first = harness.runtime.get_model_snapshot()
    harness.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    second = harness.runtime.get_model_snapshot()

    next_request = replace(
        request,
        request_id="d" * 64,
        window=replace(
            request.window,
            cook_id="next-cook",
            first_observation_sequence=1,
            last_observation_sequence=120,
        ),
    )
    next_preparation = replace(
        preparation,
        candidate=replace(preparation.candidate, request=next_request),
    )
    learning.prepared = next_preparation
    learning.pending_request = next_request
    learning.delivery = delivery_for(next_preparation)
    harness.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    third = harness.runtime.get_model_snapshot()
    harness.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    fourth = harness.runtime.get_model_snapshot()

    assert first == second
    assert first["revision"] == before["revision"] + 1
    assert first["origin"] == CandidateOrigin.PASSIVE_ONLINE.value
    assert first["policy"] == ActivationPolicy.PASSIVE_AUTO.value
    assert first["window"] == asdict(request.window)
    assert first["identities"]["candidate_digest"] == preparation.candidate_digest
    assert first["identities"]["candidate_generation"] == request.candidate_generation
    assert third == fourth
    assert third["revision"] == first["revision"] + 1
    assert third["window"] == asdict(next_request.window)
    assert third["identities"]["candidate_digest"] == preparation.candidate_digest
    assert third["identities"]["candidate_generation"] == request.candidate_generation
    harness.runtime.close()
    assert components.estimator.closed
    assert components.controller.closed
    harness.activation.close()


def test_queued_fit_lifecycle_is_memory_only_until_off_path_poll(monkeypatch) -> None:
    instances = []

    class _Learning:
        def __init__(self, **_kwargs) -> None:
            self.request = None
            self.prepared = None
            self.passive_history = SimpleNamespace(observations=())
            instances.append(self)

        def start(self) -> None:
            return None

        def observe_completed_frame(self, _observation, *, identifiability):
            assert identifiability == 1.0
            return SimpleNamespace(
                request=self.request,
                completed_forecasts=(),
                history=SimpleNamespace(accepted=True, reasons=()),
                trigger=TriggerDecision(False, ("minimum-samples",), 0.0, 1),
            )

        def register_causal_forecasts(self, *_args, **_kwargs):
            return ()

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    control_thread = []
    trace_threads = []

    def append_trace(_records) -> None:
        trace_threads.append(threading.get_ident())
        if threading.get_ident() in control_thread:
            raise AssertionError("trace persistence ran on observe_frame worker")

    harness = _harness(learning_enabled=True, append_trace=append_trace)
    identity = harness.runtime.learning_identity()
    instances[0].request = FitRequest(
        request_id="d" * 64,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        window=identity.window(0, 119),
        candidate_generation=identity.candidate_generation,
    )
    errors = []

    def observe() -> None:
        control_thread.append(threading.get_ident())
        try:
            harness.runtime.observe_frame(_frame())
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=observe)
    worker.start()
    worker.join()
    assert errors == []
    assert trace_threads == []

    harness.runtime.poll_learning_off_path()

    assert trace_threads == [threading.get_ident()]
    harness.runtime.close()
    harness.activation.close()


def test_disabled_runtime_records_teardown_history_without_starting_learning() -> None:
    harness = _harness()
    assert harness.runtime.observe_frame(_frame()) is None
    assert harness.runtime.teardown_observations == (_frame(),)
    assert harness.runtime.observation_failure(_frame(), RuntimeError("isolated")) is None
    assert harness.runtime.learning_status()["status"] == "collecting"
    harness.runtime.close()


def test_identity_rebind_rotates_teardown_history_and_accepts_first_new_role_frame() -> None:
    harness = _harness()
    harness.runtime.observe_frame(_frame(1))

    harness.runtime.bind_learning_identity("session", "cook", 7)
    current = replace(_frame(2), role_generation=7)
    decision = harness.runtime.observe_frame(current)

    assert decision is None
    assert harness.runtime.teardown_role_generation == 7
    assert harness.runtime.teardown_observations == (current,)
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize("estimator_kind", ("ekf", "kf"))
def test_completed_cook_refit_adopts_validated_pair_for_next_cook(
    estimator_kind,
) -> None:
    harness = _harness(
        fit_worker_factory=_SuccessfulWorker,
        estimator_kind=estimator_kind,
    )
    _stage_passive_checkpoint_preparation(harness)
    incumbent = harness.activation.active_pair
    for sequence in range(120):
        harness.runtime.observe_frame(_frame(sequence))

    result = harness.runtime.refit_from_cook()

    assert result.outcome is TeardownRefitOutcome.ACCEPTED_NEXT_COOK
    assert result.origin is CandidateOrigin.COOK_REFIT
    assert harness.activation.active_pair is not incumbent
    assert harness.activation.rollback_pair is incumbent
    assert not incumbent.closed
    assert _SuccessfulWorker.instances[-1].closed
    snapshot = harness.runtime.get_model_snapshot()
    assert harness.runtime._checkpoint_preparation is None
    assert harness.runtime._checkpoint_preparation_key is None
    assert snapshot["challenger"] is None
    assert snapshot["window"] is None
    assert snapshot["identities"]["candidate_digest"] is None
    assert snapshot["origin"] == CandidateOrigin.COOK_REFIT.value
    assert snapshot["policy"] == ActivationPolicy.COOK_REFIT.value
    harness.runtime.close()
    harness.activation.close()


def test_operator_probe_refit_persists_authority_without_installing_pair() -> None:
    harness = _harness(fit_worker_factory=_SuccessfulWorker)
    _stage_passive_checkpoint_preparation(harness)
    harness.runtime.bind_learning_identity("session", "cook", 0)
    incumbent = harness.activation.active_pair
    for sequence in range(120):
        frame = _frame(sequence)
        if sequence == 60:
            frame = replace(
                frame,
                baseline_q=0.4,
                probe_q=0.1,
                calibration_stage="low",
                calibration_fit=True,
            )
        harness.runtime.observe_frame(frame)

    result = harness.runtime.refit_from_cook()

    assert result.outcome is TeardownRefitOutcome.READY_FOR_REVIEW
    assert result.origin is CandidateOrigin.OPERATOR_CALIBRATION
    assert harness.activation.active_pair is incumbent
    assert not incumbent.closed
    assert len(harness.persistence.confidence) == 1
    snapshot = harness.runtime.get_model_snapshot()
    assert snapshot["challenger"] is not None
    assert snapshot["origin"] == CandidateOrigin.OPERATOR_CALIBRATION.value
    assert snapshot["policy"] == ActivationPolicy.OPERATOR_REVIEWED.value
    assert harness.runtime._checkpoint_preparation is None
    assert harness.runtime._checkpoint_preparation_key is None
    assert snapshot["challenger"]["parameters"]["C_c"] == 420.0
    assert snapshot["window"]["session_id"] == "session"
    assert snapshot["window"]["cook_id"] == "cook"
    assert harness.persistence.confidence[0].payload.blocked is False
    assert any(record.payload.policy == "operator-reviewed" for record in harness.persistence.evidence)
    snapshot["identities"]["candidate_digest"] = "f" * 64
    target = _harness()
    target_incumbent = target.activation.active_pair
    assert target.runtime.restore_model(snapshot) is False
    assert target.activation.active_pair is target_incumbent
    assert target_incumbent.authorized
    target.runtime.close()
    target.activation.close()
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize(
    ("worker_factory", "reason"),
    (
        (_BusyWorker, "fitting worker was busy"),
        (_CrashedWorker, "fit failed: worker crashed"),
        (_FitErrorWorker, "fit failed: fitter exploded"),
    ),
)
def test_completed_cook_refit_reports_worker_failures_and_closes_worker(
    worker_factory,
    reason,
) -> None:
    harness = _harness(fit_worker_factory=worker_factory)
    incumbent = harness.activation.active_pair
    for sequence in range(120):
        harness.runtime.observe_frame(_frame(sequence))

    result = harness.runtime.refit_from_cook()

    assert result.outcome is TeardownRefitOutcome.FAILED
    assert result.reason == reason
    assert harness.activation.active_pair is incumbent
    assert not incumbent.closed
    assert worker_factory.instances[-1].closed
    harness.runtime.close()
    harness.activation.close()


def test_completed_cook_refit_rejection_keeps_incumbent_and_closes_worker() -> None:
    harness = _harness(fit_worker_factory=_RejectedWorker)
    incumbent = harness.activation.active_pair
    for sequence in range(120):
        harness.runtime.observe_frame(_frame(sequence))

    result = harness.runtime.refit_from_cook()

    assert result.outcome is TeardownRefitOutcome.REJECTED
    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    assert not incumbent.closed
    assert _RejectedWorker.instances[-1].closed
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize(
    ("preparation", "policy", "message"),
    (
        (SimpleNamespace(), ActivationPolicy.OPERATOR_REVIEWED, "manual candidate"),
        (
            SimpleNamespace(),
            ActivationPolicy.PASSIVE_AUTO,
            "candidate preparation is incomplete",
        ),
    ),
)
def test_automatic_activation_rejects_wrong_policy_or_incomplete_preparation(
    preparation,
    policy,
    message,
) -> None:
    harness = _harness()

    with pytest.raises(ValueError, match=message):
        harness.runtime.prepare_automatic_activation(preparation, policy)

    assert harness.activation.active_pair is harness.active
    harness.runtime.close()
    harness.activation.close()


def test_completed_cook_refit_rejects_failed_native_preparation_without_owner_swap() -> None:
    harness = _harness(
        fit_worker_factory=_SuccessfulWorker,
        solver_factory=_InvalidProbeSolver,
    )
    incumbent = harness.activation.active_pair
    for sequence in range(120):
        harness.runtime.observe_frame(_frame(sequence))

    result = harness.runtime.refit_from_cook()

    assert result.outcome is TeardownRefitOutcome.REJECTED
    assert result.reason == "native-dry-solve"
    assert harness.activation.active_pair is incumbent
    assert not incumbent.closed
    harness.runtime.close()
    harness.activation.close()


def test_operator_refit_requires_durable_confidence_before_exposing_candidate() -> None:
    harness = _harness(fit_worker_factory=_SuccessfulWorker)
    harness.persistence.accept_confidence = False
    for sequence in range(120):
        frame = _frame(sequence)
        if sequence == 60:
            frame = replace(
                frame,
                baseline_q=0.4,
                probe_q=0.1,
                calibration_stage="low",
                calibration_fit=True,
            )
        harness.runtime.observe_frame(frame)

    with pytest.raises(RuntimeError, match="operator-review-confidence-not-durable"):
        harness.runtime.refit_from_cook()

    assert harness.activation.active_pair is harness.active
    assert not harness.active.closed
    harness.runtime.close()
    harness.activation.close()


def test_orchestrator_start_failure_closes_partial_learning_owner(monkeypatch) -> None:
    instances = []

    class _FailingLearning:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            instances.append(self)

        def start(self) -> None:
            raise RuntimeError("start failed")

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _FailingLearning,
    )

    with pytest.raises(RuntimeError, match="start failed"):
        _harness(learning_enabled=True)

    assert instances[0].closed


def test_rejected_combined_confidence_submission_does_not_trace_assessment() -> None:
    traces = []
    harness = _harness(append_trace=lambda records: traces.extend(records))
    preparation, evaluation, _components = _automatic_candidate(harness)
    harness.persistence.accept_confidence = False

    with pytest.raises(RuntimeError, match="activation-confidence-not-durable"):
        harness.runtime._persist_candidate_evaluation(evaluation, preparation)

    assert traces == []
    harness.runtime.close()
    harness.activation.close()


def test_rejected_evaluation_persists_failure_checks_and_projects_once(monkeypatch) -> None:
    evaluation = EvaluationDecision(
        decision_id="c" * 64,
        accepted=True,
        role_generation=0,
        candidate_generation=1,
        incumbent_digest=_descriptor().model_digest,
        challenger_digest="d" * 64,
        scores=(
            HorizonScore(3, 0.0, 0.0, 0),
            HorizonScore(15, 0.0, 0.0, 0),
        ),
        consecutive_wins=1,
        blockers=("candidate-confidence-low",),
    )
    request = SimpleNamespace(origin=CandidateOrigin.PASSIVE_ONLINE)
    preparation = SimpleNamespace(
        candidate=SimpleNamespace(request=request),
        candidate_pair=None,
        timing=SimpleNamespace(accepted=False),
        dry_solve_finite=False,
    )

    class _Learning:
        prepared = preparation
        passive_history = SimpleNamespace(observations=())

        def __init__(self, **_kwargs) -> None:
            self.closed = False

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return evaluation

        def observe_completed_frame(self, _frame, *, identifiability):
            assert identifiability == 1.0
            return SimpleNamespace(
                history=SimpleNamespace(accepted=True, reasons=()),
                completed_forecasts=(),
                request=None,
                trigger=TriggerDecision(False, ("minimum-samples",), 0.125, 3),
            )

        def register_causal_forecasts(self, *_args, **_kwargs):
            return ()

        def update_identity(self, *_args, **_kwargs) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    harness = _harness(learning_enabled=True)

    delivery, projected = harness.runtime.poll_learning_off_path()
    outcome = harness.runtime.observe_frame(_frame())

    assert delivery is None
    assert projected is not None
    assert outcome["evaluation_payload"] == projected
    assert outcome["confidence_accepted"] is False
    assert outcome["input_variance"] == 0.125
    assert outcome["input_levels"] == 3
    assessment = harness.persistence.confidence_preceding[-1][0].payload
    assert assessment.rejection_reasons == (
        "candidate-confidence-low",
        "native-build-failed",
        "native-dry-solve-failed",
        "target-timing-failed",
    )
    assert harness.persistence.confidence[-1].payload.reason == "candidate-confidence-low"
    assert harness.runtime.observation_failure(_frame(), RuntimeError("boom"))["rejection_reasons"] == (
        "learner-exception",
    )
    harness.runtime.bind_learning_identity("next", "cook", 1)
    harness.runtime.close()
    harness.activation.close()


def test_candidate_assessment_uses_activation_fifo_when_unrelated_evidence_is_rejected(
    monkeypatch,
) -> None:
    evaluation = EvaluationDecision(
        decision_id="e" * 64,
        accepted=True,
        role_generation=0,
        candidate_generation=1,
        incumbent_digest=_descriptor().model_digest,
        challenger_digest="f" * 64,
        scores=(
            HorizonScore(3, 0.0, 0.0, 0),
            HorizonScore(15, 0.0, 0.0, 0),
        ),
        consecutive_wins=1,
        blockers=("stale-session",),
    )
    preparation = SimpleNamespace(
        candidate=SimpleNamespace(request=SimpleNamespace(origin=CandidateOrigin.PASSIVE_ONLINE)),
        candidate_pair=None,
        timing=None,
        dry_solve_finite=False,
    )

    class _Learning:
        prepared = preparation

        def __init__(self, **_kwargs) -> None:
            return None

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return evaluation

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    harness = _harness(learning_enabled=True)
    harness.persistence.accept_evidence = False

    harness.runtime.poll_learning_off_path()

    assert harness.persistence.evidence == []
    assert len(harness.persistence.confidence) == 1
    assert len(harness.persistence.confidence_preceding) == 1
    assert harness.persistence.confidence_preceding[0][0].payload.decision_id == evaluation.decision_id
    assert harness.activation.active_pair is harness.active
    harness.runtime.close()
    harness.activation.close()


def test_successful_poll_hands_off_once_and_deduplicates_confidence(monkeypatch) -> None:
    instances = []

    class _Learning:
        prepared = None

        def __init__(self, **_kwargs) -> None:
            self.evaluation = None
            self.handoffs = []
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return self.evaluation

        def handoff_if_ready(self, **kwargs) -> None:
            self.handoffs.append(kwargs)

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    harness = _harness(learning_enabled=True)
    preparation, evaluation, _components = _automatic_candidate(harness)
    instances[0].prepared = preparation
    instances[0].evaluation = evaluation

    first = harness.runtime.poll_learning_off_path()
    second = harness.runtime.poll_learning_off_path()

    assert first[1] is not None
    assert second[1].decision_id == first[1].decision_id
    assert len(instances[0].handoffs) == 2
    assert len(harness.persistence.confidence) == 1
    assert harness.activation.active_pair is harness.active
    harness.runtime.close()
    harness.activation.close()


def test_trace_projection_failure_terminates_activation_without_losing_evidence(
    monkeypatch,
) -> None:
    instances = []

    class _Learning:
        prepared = None

        def __init__(self, **_kwargs) -> None:
            self.evaluation = None
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return self.evaluation

        def handoff_if_ready(self, **_kwargs) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )

    def fail_trace(_records):
        raise RuntimeError("trace unavailable")

    harness = _harness(learning_enabled=True, append_trace=fail_trace)
    preparation, evaluation, _components = _automatic_candidate(harness)
    instances[0].prepared = preparation
    instances[0].evaluation = evaluation

    harness.runtime.poll_learning_off_path()

    assert harness.persistence.evidence == []
    assert len(harness.persistence.confidence_preceding) == 1
    assert len(harness.persistence.confidence_preceding[0]) == 1
    assert harness.persistence.confidence_preceding[0][0].payload.decision_id == evaluation.decision_id
    assert harness.activation.terminated_reason == ("learning lifecycle trace failed: trace unavailable")
    harness.runtime.close()


def test_reviewed_checkpoint_is_durable_idempotent_and_confidence_ordered(
    monkeypatch,
) -> None:
    instances = []

    class _Learning:
        def __init__(self, **_kwargs) -> None:
            self.prepared = None
            self.evaluation = None
            self.pending_request = None
            self.handoff = None
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return self.evaluation

        def close(self) -> None:
            if self.prepared is not None:
                self.prepared.candidate_pair.estimator.close()
                self.prepared.candidate_pair.controller.close()

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    store = _CheckpointStore()
    harness = _harness(learning_enabled=True, checkpoint_store=store)
    preparation, evaluation, components = _reviewed_candidate(harness)
    instances[0].prepared = preparation
    instances[0].evaluation = evaluation

    harness.runtime.poll_learning_off_path()
    harness.runtime.poll_learning_off_path()

    assert len(store.snapshots) == 1
    assert len(harness.persistence.confidence) == 1
    assert harness.persistence.evidence == []
    assert len(harness.persistence.confidence_preceding) == 1
    assessment = harness.persistence.confidence_preceding[0][0]
    confidence = harness.persistence.confidence[0]
    assert assessment.kind.value == "candidate_assessment"
    assert assessment.payload.decision_id == evaluation.decision_id
    assert confidence.payload.decision_id == evaluation.decision_id
    assert harness.runtime.model_authority()[0] == 2
    assert store.snapshots[0][1]["evidence"]["confidence_decision_id"] == (evaluation.decision_id)
    assert store.snapshots[0][1]["identities"]["candidate_digest"] == (evaluation.challenger_digest)
    harness.runtime.close()
    assert components.estimator.closed
    assert components.controller.closed
    harness.activation.close()


@pytest.mark.parametrize(
    ("failure", "message"),
    (
        ("identity", "reviewed-candidate-identity-changed"),
        ("snapshot", "reviewed-candidate-checkpoint-invalid"),
        ("checkpoint", "reviewed-candidate-checkpoint-not-durable"),
        ("confidence", "activation-confidence-not-durable"),
        (
            "confidence-not-durable",
            "activation-confidence-not-durable",
        ),
    ),
)
def test_reviewed_checkpoint_failures_preserve_active_owner_and_close_candidate(
    monkeypatch,
    failure,
    message,
) -> None:
    instances = []

    class _Learning:
        def __init__(self, **_kwargs) -> None:
            self.prepared = None
            self.pending_request = None
            self.handoff = None
            self.evaluation = None
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return self.evaluation

        def close(self) -> None:
            if self.prepared is not None:
                self.prepared.candidate_pair.estimator.close()
                self.prepared.candidate_pair.controller.close()

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    store = _CheckpointStore(CheckpointSaveOutcome.FAILED if failure == "checkpoint" else CheckpointSaveOutcome.SAVED)
    snapshot_parameters = (
        (lambda: (_ for _ in ()).throw(ValueError("not serializable"))) if failure == "snapshot" else None
    )
    harness = _harness(
        learning_enabled=True,
        checkpoint_store=store,
        snapshot_parameters=snapshot_parameters,
    )
    preparation, evaluation, components = _reviewed_candidate(harness)
    if failure == "identity":
        evaluation = replace(evaluation, challenger_digest="f" * 64)
    elif failure == "confidence":
        harness.persistence.accept_confidence = False
    elif failure == "confidence-not-durable":
        harness.persistence.confidence_durable = False
    instances[0].prepared = preparation
    instances[0].evaluation = evaluation
    incumbent = harness.activation.active_pair

    with pytest.raises(RuntimeError, match=message):
        harness.runtime.poll_learning_off_path()

    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    assert not incumbent.closed
    harness.runtime.close()
    assert components.estimator.closed
    assert components.controller.closed
    harness.activation.close()


@pytest.mark.parametrize(
    ("failure", "message"),
    (
        ("identity", "reviewed-candidate-identity-changed"),
        ("snapshot", "reviewed-candidate-checkpoint-invalid"),
        ("checkpoint", "reviewed-candidate-checkpoint-not-durable"),
    ),
)
def test_reviewed_checkpoint_failure_leaves_the_checkpoint_lineage_untouched(
    monkeypatch,
    failure,
    message,
) -> None:
    instances = []

    class _Learning:
        def __init__(self, **_kwargs) -> None:
            self.prepared = None
            self.pending_request = None
            self.handoff = None
            self.evaluation = None
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            return None

        def evaluate_ready_off_path(self):
            return self.evaluation

        def close(self) -> None:
            if self.prepared is not None:
                self.prepared.candidate_pair.estimator.close()
                self.prepared.candidate_pair.controller.close()

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    store = _CheckpointStore(CheckpointSaveOutcome.FAILED if failure == "checkpoint" else CheckpointSaveOutcome.SAVED)
    snapshot_parameters = (
        (lambda: (_ for _ in ()).throw(ValueError("not serializable"))) if failure == "snapshot" else None
    )
    harness = _harness(
        learning_enabled=True,
        checkpoint_store=store,
        snapshot_parameters=snapshot_parameters,
    )
    preparation, evaluation, components = _reviewed_candidate(harness)
    if failure == "identity":
        evaluation = replace(evaluation, challenger_digest="f" * 64)
    instances[0].prepared = preparation
    instances[0].evaluation = evaluation
    revision_before = harness.runtime.model_authority()[0]
    snapshot_before = harness.runtime.get_model_snapshot()

    with pytest.raises(RuntimeError, match=message):
        harness.runtime.poll_learning_off_path()

    assert harness.runtime.model_authority()[0] == revision_before
    assert harness.runtime.get_model_snapshot() == snapshot_before
    harness.runtime.close()
    harness.activation.close()
    assert components.estimator.closed
    assert components.controller.closed


def test_learning_status_projects_queued_running_preparing_and_handoff_states(
    monkeypatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    instances = []

    class _Learning:
        def __init__(self, **_kwargs) -> None:
            self.pending_request = None
            self.worker = SimpleNamespace(busy=False)
            self.prepared = None
            self.handoff = None
            instances.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, **_kwargs):
            entered.set()
            assert release.wait(2.0)

        def evaluate_ready_off_path(self):
            return None

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _Learning,
    )
    harness = _harness(learning_enabled=True)
    learning = instances[0]
    identity = harness.runtime.learning_identity()
    learning.pending_request = FitRequest(
        request_id="q" * 64,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        window=identity.window(0, 0),
        candidate_generation=1,
    )

    assert harness.runtime.learning_status()["fit_status"] == "queued"
    learning.worker.busy = True
    assert harness.runtime.learning_status()["fit_status"] == "running"

    learning.pending_request = None
    polling = threading.Thread(
        target=harness.runtime.poll_learning_off_path,
        kwargs={"live_origin": CandidateOrigin.PASSIVE_ONLINE},
    )
    polling.start()
    assert entered.wait(2.0)
    assert harness.runtime.learning_status()["fit_status"] == "running"
    release.set()
    polling.join(2.0)
    assert not polling.is_alive()

    learning.handoff = SimpleNamespace(status=LearningStatus.ACTIVE)
    assert harness.runtime.learning_status()["status"] == "active"
    harness.runtime.close()
    harness.activation.close()


def test_observation_and_adoption_reject_invalid_public_inputs_without_owner_change() -> None:
    harness = _harness()
    incumbent = harness.activation.active_pair

    with pytest.raises(TypeError, match="FrameObservation"):
        harness.runtime.observe_frame(SimpleNamespace())
    with pytest.raises(ValueError, match="distinct validated owner"):
        harness.runtime.adopt_model(
            incumbent,
            rmse=0.5,
            samples=120,
            band_c=(75.0, 160.0),
        )

    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize("estimator_kind", ("ekf", "kf"))
def test_automatic_activation_prepares_one_inert_owner_without_installing_output(
    estimator_kind,
) -> None:
    harness = _harness(estimator_kind=estimator_kind)
    incumbent = harness.activation.active_pair
    preparation, evaluation, components = _automatic_candidate(harness)

    transaction_id = harness.runtime.prepare_automatic_activation(
        preparation,
        ActivationPolicy.PASSIVE_AUTO,
        evaluation,
    )

    assert transaction_id
    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    assert harness.activation.activation_pending
    assert not components.controller.closed
    assert harness.runtime.learning_status()["status"] == "activating"
    harness.runtime.close()
    harness.activation.close()
    assert components.controller.closed


def test_automatic_activation_reuses_already_durable_confidence_decision() -> None:
    harness = _harness()
    preparation, evaluation, _components = _automatic_candidate(harness)
    harness.activation.mark_confidence_persisted(evaluation.decision_id)

    transaction_id = harness.runtime.prepare_automatic_activation(
        preparation,
        ActivationPolicy.PASSIVE_AUTO,
        evaluation,
    )

    assert transaction_id
    assert harness.persistence.confidence == []
    assert harness.activation.activation_pending
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize(
    ("failure", "message"),
    (
        ("digest", "candidate-digest-changed"),
        ("evaluation", "activation-confidence-changed"),
        ("missing-evaluation", "activation-confidence-changed"),
        ("confidence", "activation-confidence-not-durable"),
        ("phase", "activation-persistence-unavailable"),
    ),
)
def test_automatic_activation_failure_closes_transferred_candidate_components(
    failure,
    message,
) -> None:
    harness = _harness()
    preparation, evaluation, components = _automatic_candidate(harness)
    if failure == "digest":
        preparation.candidate_digest = "f" * 64
    elif failure == "evaluation":
        evaluation.accepted = False
    elif failure == "missing-evaluation":
        evaluation = None
    elif failure == "confidence":
        harness.persistence.accept_confidence = False
    else:
        harness.persistence.accept_phase = False

    with pytest.raises((RuntimeError, ValueError), match=message):
        harness.runtime.prepare_automatic_activation(
            preparation,
            ActivationPolicy.PASSIVE_AUTO,
            evaluation,
        )

    assert components.controller.closed
    assert harness.activation.active_pair is harness.active
    assert harness.active.authorized
    harness.runtime.close()
    harness.activation.close()


def test_real_orchestrator_detaches_raw_owner_after_queued_lifecycle_abort() -> None:
    harness = _harness()
    active_descriptor = harness.activation.active_pair.descriptor

    class _CountingEstimator:
        def __init__(self, _native_config) -> None:
            self.close_count = 0

        def update(self, _load, temperature):
            return [0.0] * 8 + [float(temperature), 0.0]

        def close(self) -> None:
            self.close_count += 1

    class _CountingSolver(_ProbeSolver):
        def __init__(self, config) -> None:
            super().__init__(config)
            self.close_count = 0

        def close(self) -> None:
            self.close_count += 1
            super().close()

    class _ImmediateWorker(_SuccessfulWorker):
        def receive(self, *, timeout_s: float):
            assert timeout_s == 0.0
            assert self.job is not None
            return SimpleNamespace(outcome=_fit_success(self.job))

    identity = LiveLearningIdentity(
        session_id="session-handoff",
        cook_id="cook-handoff",
        configuration_digest="d" * 64,
        incumbent_digest=active_descriptor.model_digest,
        role_generation=active_descriptor.role_generation,
        candidate_generation=active_descriptor.candidate_generation + 1,
    )
    orchestrator = GreyLearningOrchestrator(
        identity=identity,
        config=harness.activation.active_pair.solver.config,
        incumbent_pair=CandidatePair(
            harness.activation.active_pair.estimator,
            harness.activation.active_pair.solver,
        ),
        estimator_factory=_CountingEstimator,
        controller_factory=_CountingSolver,
        timing_probe=lambda _solver: TargetTimingEvidence(
            "candidate-dry-solve",
            3,
            1.0,
            25.0,
        ),
        trigger_config=TriggerConfig(
            min_samples=9,
            min_input_variance=0.02,
            min_input_levels=3,
            min_temperature_span_c=8.0,
            min_identifiability=0.5,
        ),
        evaluation_config=EvaluationConfig(required_consecutive_wins=2),
        worker=_ImmediateWorker(),
        max_observations=12,
    )
    for sequence in range(9):
        q = (sequence % 4) / 3.0
        orchestrator.observe_completed_frame(
            replace(
                _frame(sequence),
                requested_q=q,
                realized_q=q,
                requested_auger_duty=q,
                baseline_q=q,
                delivered_on_s=q * 20.0,
            ),
            identifiability=0.8,
        )
    delivery = orchestrator.poll_fit_off_path(
        live_identity=identity,
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    assert delivery.preparation is not None
    estimator = delivery.preparation.candidate_pair.estimator
    solver = delivery.preparation.candidate_pair.controller
    incumbent_predict = lambda _origin: -1_000.0
    challenger_predict = lambda _origin: 0.0
    orchestrator.register_causal_forecasts(
        _frame(9),
        incumbent_predict=incumbent_predict,
        challenger_predict=challenger_predict,
    )
    for sequence in range(10, 190):
        orchestrator.observe_completed_frame(_frame(sequence), identifiability=0.8)
    assert not orchestrator.evaluate_ready_off_path().accepted
    orchestrator.register_causal_forecasts(
        _frame(190),
        incumbent_predict=incumbent_predict,
        challenger_predict=challenger_predict,
    )
    for sequence in range(191, 371):
        orchestrator.observe_completed_frame(_frame(sequence), identifiability=0.8)
    evaluation = orchestrator.evaluate_ready_off_path()
    assert evaluation.accepted
    harness.persistence.accept_evidence = False

    with pytest.raises(
        RuntimeError,
        match="learning-lifecycle-evidence-not-accepted",
    ):
        orchestrator.handoff_if_ready(
            confidence_accepted=True,
            online_enabled=True,
            prepare=lambda preparation, policy: harness.runtime.prepare_automatic_activation(
                preparation,
                policy,
                evaluation,
            ),
        )

    orchestrator.close()
    assert estimator.close_count == 1
    assert solver.close_count == 1
    harness.runtime.close()
    harness.activation.close()


def test_lifecycle_rejection_aborts_durable_prepared_owner_transactionally() -> None:
    harness = _harness()
    preparation, evaluation, components = _automatic_candidate(harness)
    harness.persistence.accept_evidence = False

    with pytest.raises(
        RuntimeError,
        match="learning-lifecycle-evidence-not-accepted",
    ):
        harness.runtime.prepare_automatic_activation(
            preparation,
            ActivationPolicy.PASSIVE_AUTO,
            evaluation,
        )

    assert components.estimator.closed
    assert components.controller.closed
    assert harness.activation.active_pair is harness.active
    assert harness.active.authorized
    assert not harness.activation.activation_pending
    harness.runtime.close()
    harness.activation.close()


def test_automatic_activation_queue_rejection_closes_inert_candidate(monkeypatch) -> None:
    harness = _harness()
    preparation, evaluation, components = _automatic_candidate(harness)
    monkeypatch.setattr(
        harness.activation,
        "queue_prepared_activation",
        lambda *_args, **_kwargs: False,
    )

    with pytest.raises(RuntimeError, match="activation-transition-rejected"):
        harness.runtime.prepare_automatic_activation(
            preparation,
            ActivationPolicy.PASSIVE_AUTO,
            evaluation,
        )

    assert components.controller.closed
    assert harness.activation.active_pair is harness.active
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize(
    ("section", "key", "value"),
    (
        ("cook_refit", "status", "succeeded"),
        ("cook_refit", "status", "failed"),
        ("activation", "phase", "prepared"),
        ("activation", "phase", "active"),
    ),
)
def test_restore_preserves_each_supported_checkpoint_state(section, key, value) -> None:
    source = _harness()
    snapshot = source.runtime.get_model_snapshot()
    snapshot[section][key] = value
    target = _harness()

    assert target.runtime.restore_model(snapshot)
    restored = target.runtime.get_model_snapshot()
    assert restored[section][key] == value
    assert restored == snapshot

    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


def test_restore_rejects_crossed_active_identity_without_replacing_owner() -> None:
    harness = _harness()
    snapshot = harness.runtime.get_model_snapshot()
    snapshot["identities"]["active_digest"] = "f" * 64
    incumbent = harness.activation.active_pair

    assert harness.runtime.restore_model(snapshot) is False
    assert harness.activation.active_pair is incumbent
    assert incumbent.authorized
    assert not incumbent.closed
    harness.runtime.close()
    harness.activation.close()


def test_restore_replace_failure_closes_new_pair_and_keeps_incumbent(monkeypatch) -> None:
    source = _harness()
    snapshot = source.runtime.get_model_snapshot()
    target = _harness()
    incumbent = target.activation.active_pair
    built = []
    real_restore = target.factory.restore

    def restore(descriptor):
        pair = real_restore(descriptor)
        built.append(pair)
        return pair

    monkeypatch.setattr(target.factory, "restore", restore)
    monkeypatch.setattr(
        target.activation,
        "replace_active_pair",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("swap failed")),
    )

    assert target.runtime.restore_model(snapshot) is False
    assert target.activation.active_pair is incumbent
    assert incumbent.authorized
    assert built[0].closed
    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


def test_runtime_snapshot_is_exact_json_safe_v4_without_process_jobs() -> None:
    harness = _harness()
    snapshot = harness.runtime.get_model_snapshot()
    assert snapshot is not None
    assert snapshot["version"] == 4
    assert set(snapshot) == {
        "version",
        "schema",
        "revision",
        "structure",
        "active",
        "challenger",
        "evidence",
        "origin",
        "policy",
        "identification",
        "cook_refit",
        "window",
        "identities",
        "activation",
        "failure",
        "active_pair",
        "candidate_pair",
    }
    encoded = json.dumps(snapshot, sort_keys=True, allow_nan=False)
    assert "process" not in encoded
    assert "job" not in encoded
    harness.runtime.close()
    harness.activation.close()


def test_restore_parameter_mismatch_closes_built_pair_and_keeps_incumbent(
    monkeypatch,
) -> None:
    source = _harness()
    snapshot = source.runtime.get_model_snapshot()
    target = _harness()
    incumbent = target.activation.active_pair
    built = []
    real_restore = target.factory.restore

    def restore(descriptor):
        pair = real_restore(descriptor)
        built.append(pair)
        pair.core.config["theta"] = float(pair.core.config["theta"]) + 1.0
        return pair

    monkeypatch.setattr(target.factory, "restore", restore)

    assert target.runtime.restore_model(snapshot) is False
    assert target.activation.active_pair is incumbent
    assert incumbent.authorized
    assert built[0].closed
    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


def test_finalize_checkpoint_failure_can_override_one_completed_outcome() -> None:
    harness = _harness()
    revision = harness.runtime.model_authority()[0]

    assert harness.runtime.finalize_cook_refit(TeardownRefitOutcome.INSUFFICIENT)
    assert not harness.runtime.finalize_cook_refit(TeardownRefitOutcome.REJECTED)
    assert harness.runtime.finalize_cook_refit(TeardownRefitOutcome.CHECKPOINT_FAILURE)
    assert harness.runtime.model_authority()[0] == revision + 1
    assert harness.runtime.get_model_snapshot()["cook_refit"]["latest"] == TeardownRefitOutcome.CHECKPOINT_FAILURE.value
    harness.runtime.close()
    harness.activation.close()


@pytest.mark.parametrize("failure", ("start", "replace"))
def test_restore_stages_learning_before_atomic_active_replacement(
    monkeypatch,
    failure,
) -> None:
    source = _harness()
    snapshot = source.runtime.get_model_snapshot()
    target = _harness(learning_enabled=True)
    incumbent = target.activation.active_pair
    before = target.runtime.get_model_snapshot()
    staged = []
    restored = []

    class _StagedLearning:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            staged.append(self)

        def start(self) -> None:
            if failure == "start":
                raise RuntimeError("staged learning failed")

        def close(self) -> None:
            self.closed = True

    real_restore = target.factory.restore

    def restore(descriptor):
        pair = real_restore(descriptor)
        restored.append(pair)
        return pair

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _StagedLearning,
    )
    monkeypatch.setattr(target.factory, "restore", restore)
    if failure == "replace":
        monkeypatch.setattr(
            target.activation,
            "replace_active_pair",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("replacement failed")),
        )

    assert target.runtime.restore_model(snapshot) is False
    assert target.activation.active_pair is incumbent
    assert incumbent.authorized
    assert not incumbent.closed
    assert target.runtime.get_model_snapshot() == before
    assert staged and staged[0].closed
    assert restored and restored[0].closed
    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


def test_restore_stages_identity_from_exact_restored_full_configuration(
    monkeypatch,
) -> None:
    source = _harness(
        estimator_kind="kf",
        base_configuration={
            "estimator": "kf",
            "control_period": 7.0,
        },
    )
    snapshot = source.runtime.get_model_snapshot()
    target = _harness(
        learning_enabled=True,
        estimator_kind="ekf",
        base_configuration={
            "estimator": "ekf",
            "control_period": 3.0,
        },
    )
    staged = []

    class _StagedLearning:
        def __init__(self, **kwargs) -> None:
            self.identity = kwargs["identity"]
            self.poll_identities = []
            self.closed = False
            staged.append(self)

        def start(self) -> None:
            return None

        def poll_fit_off_path(self, *, live_identity, **_kwargs):
            self.poll_identities.append(live_identity)

        def evaluate_ready_off_path(self):
            return None

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "controller.model_learning.grey_runtime.GreyLearningOrchestrator",
        _StagedLearning,
    )

    assert target.runtime.restore_model(snapshot)
    restored_learning = staged[-1]
    live_identity = target.runtime.learning_identity()
    assert restored_learning.identity == live_identity
    assert target.activation.active_pair.core.config["estimator"] == "kf"
    assert target.activation.active_pair.core.config["control_period"] == 7.0
    target.runtime.poll_learning_off_path(
        live_origin=CandidateOrigin.PASSIVE_ONLINE,
    )
    assert restored_learning.poll_identities == [live_identity]
    target.runtime.close()
    target.activation.close()
    source.runtime.close()
    source.activation.close()


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        {"version": 5},
        {
            "version": 4,
            "active": {"parameters": {"C_c": float("nan")}},
        },
    ],
)
def test_invalid_restore_is_atomic_and_leaves_active_owner_authorized(invalid) -> None:
    harness = _harness()
    before = harness.runtime.get_model_snapshot()
    assert harness.runtime.restore_model(invalid) is False
    assert harness.activation.active_pair is harness.active
    assert harness.active.authorized
    assert harness.runtime.get_model_snapshot() == before
    harness.runtime.close()
    harness.activation.close()


def test_refit_insufficient_samples_does_not_build_or_replace_a_pair() -> None:
    harness = _harness()
    verdict = harness.runtime.refit_from_cook([(0.0, 20.0, 0.2), (5.0, 21.0, 0.3)])
    assert verdict.accepted is False
    assert "need 120" in verdict.reason
    assert harness.activation.active_pair is harness.active
    harness.runtime.close()
    harness.activation.close()


def test_cook_history_is_an_owned_copy_and_finalize_advances_revision_once() -> None:
    rows = ((0.0, 20.0, 0.1), (5.0, 21.0, 0.2))
    harness = _harness(history=rows)
    copied = harness.runtime.cook_history()
    copied.append((10.0, 22.0, 0.3))
    assert harness.runtime.cook_history() == list(rows)
    before = harness.runtime.model_authority()[0]
    assert harness.runtime.finalize_cook_refit(TeardownRefitOutcome.INSUFFICIENT) is True
    assert harness.runtime.finalize_cook_refit(TeardownRefitOutcome.INSUFFICIENT) is False
    assert harness.runtime.model_authority()[0] == before + 1
    harness.runtime.close()
    harness.activation.close()


def test_close_is_idempotent_and_leaves_injected_activation_persistence_open() -> None:
    harness = _harness()
    harness.runtime.close()
    harness.runtime.close()
    assert harness.active.closed is False
    assert harness.persistence.close_count == 0
    harness.activation.close()
    assert harness.persistence.close_count == 0
