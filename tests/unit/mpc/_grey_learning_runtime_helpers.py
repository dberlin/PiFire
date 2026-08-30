"""Shared grey-learning runtime fixtures for unit tests."""

from __future__ import annotations

from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import ClassVar

from common.control_trace import AmbientSource
from common.controller_model_state import CheckpointSaveOutcome
from common.learning_trajectory import FitCorpusIdentity, ModelFitLineage
from common.model_evidence import (
    ChallengerRoundEvidence,
    EvidenceKind,
    ModelEvidenceRecord,
)
from common.persistence.learning_trajectory import LearningTrajectoryRepository
from common.persistence.model_challenger import (
    ModelChallengerState,
    abort_model_challenger_activation,
    create_model_challenger,
    read_model_challenger,
    retire_model_challenger,
)
from common.persistence.model_evidence import append_model_evidence
from controller.acados import GreyBoxMPCConfig
from controller.model_learning.activation import (
    GreyControlPairDescriptor,
    canonical_snapshot_digest,
)
from controller.model_learning.activation_runtime import ActivationRuntime
from controller.model_learning.contracts import (
    ActivationPolicy,
    CandidateOrigin,
    FitRequest,
    FrameObservation,
)
from controller.model_learning.evaluation import (
    CompletedForecastOrigin,
    EvaluationDecision,
    ForecastOrigin,
    HorizonScore,
)
from controller.model_learning.grey_runtime import GreyLearningRuntime
from controller.mpc_config import DEFAULT_MPC_CONFIG, MpcConfig
from controller.mpc_factory import MpcPairFactory, OwnedMpcPair
from controller.runtime.model_fitting import (
    CandidatePair,
    CandidatePreparation,
    FitSubmission,
    GreyFitMetric,
    GreyFitMetrics,
    GreyFitSuccess,
    GreyFitWorker,
    TargetTimingEvidence,
)
from controller.runtime.model_persistence import (
    DurableActivationReceipt,
    ModelPersistenceWorker,
)
from tests.unit.common._learning_trajectory_helpers import (
    _finalize_segment,
    _segment,
)
from tests.unit.common._model_challenger_helpers import _corpus, _manifest
from tests.unit.mpc._solver_fixtures import (
    CYCLE,
    _Estimator,
    _Solver,
    inactive_calibration,
)

_REQUIRED_HORIZONS = (3, 15, 45, 90, 180)
_COMPLETE_SCORES = tuple(HorizonScore(horizon, 1.0, 0.5, 1) for horizon in _REQUIRED_HORIZONS)


def _complete_origins(
    *,
    incumbent_digest: str,
    challenger_digest: str,
    role_generation: int,
    candidate_generation: int,
) -> tuple[CompletedForecastOrigin, ...]:
    return tuple(
        CompletedForecastOrigin(
            forecast=ForecastOrigin(
                origin_sequence=index,
                origin_time_s=100.0,
                horizon_steps=horizon,
                role_generation=role_generation,
                candidate_generation=candidate_generation,
                incumbent_digest=incumbent_digest,
                challenger_digest=challenger_digest,
                incumbent_prediction_c=101.0,
                challenger_prediction_c=100.5,
                temperature_band="middle",
                phase="heating",
                ambient_source=AmbientSource.CONFIGURED,
                calibration_fit=False,
            ),
            completion_time_s=100.0 + horizon,
            observed_temperature_c=100.0,
        )
        for index, horizon in enumerate(_REQUIRED_HORIZONS, start=1)
    )


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


def _close_prepared_candidate(preparation) -> None:
    candidate_pair = getattr(preparation, "candidate_pair", None)
    if candidate_pair is None:
        return
    closed: set[int] = set()
    for component in (candidate_pair.controller, candidate_pair.estimator):
        close = getattr(component, "close", None)
        if callable(close) and id(component) not in closed:
            close()
            closed.add(id(component))


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
    temperatures = tuple(float(value) for segment in job.segments for value in segment.scored_temperature_c)
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
        result_digest=job.request.request_id,
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


class _CorpusWorker:
    instances: ClassVar[list[_CorpusWorker]] = []

    def __init__(self) -> None:
        self.job = None
        self.closed = False
        self.__class__.instances.append(self)

    @property
    def busy(self) -> bool:
        return False

    def start(self) -> None:
        return None

    def submit(self, job) -> FitSubmission:
        self.job = job
        return FitSubmission.ACCEPTED

    def receive(self, *, timeout_s: float):
        del timeout_s

    def close(self) -> None:
        self.closed = True


class _SubmissionFailureWorker(_CorpusWorker):
    def submit(self, job) -> FitSubmission:
        self.job = job
        raise RuntimeError("fit submission unavailable")


class _DeliveringCorpusWorker(_CorpusWorker):
    def receive(self, *, timeout_s: float):
        del timeout_s
        assert self.job is not None
        return SimpleNamespace(
            request=self.job.request,
            outcome=_fit_success(self.job),
        )


class _ControlledDeliveryCorpusWorker(_CorpusWorker):
    instances: ClassVar[list[_ControlledDeliveryCorpusWorker]] = []

    def __init__(self) -> None:
        super().__init__()
        self.released = False

    def receive(self, *, timeout_s: float):
        del timeout_s
        if not self.released:
            raise TimeoutError
        assert self.job is not None
        return SimpleNamespace(
            request=self.job.request,
            outcome=_fit_success(self.job),
        )


class _CorpusRepositoryProbe:
    def __init__(
        self,
        repository,
        *,
        events=None,
        snapshot_error: Exception | None = None,
        record_error: Exception | None = None,
    ) -> None:
        self.repository = repository
        self.events = [] if events is None else events
        self.snapshot_error = snapshot_error
        self.record_error = record_error

    def snapshot_fit_corpus(self, fit_partition_digest, *, through_revision=None):
        self.events.append(("snapshot", fit_partition_digest))
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self.repository.snapshot_fit_corpus(
            fit_partition_digest,
            through_revision=through_revision,
        )

    def record_fit_request(self, snapshot, lineage):
        self.events.append(("record", lineage.request_id))
        if self.record_error is not None:
            raise self.record_error
        return self.repository.record_fit_request(snapshot, lineage)

    def complete_fit(self, request_id, *, candidate_digest, error):
        self.events.append(("complete", request_id))
        return self.repository.complete_fit(
            request_id,
            candidate_digest=candidate_digest,
            error=error,
        )

    def mark_fit_stale(self, request_id):
        self.events.append(("stale", request_id))
        return self.repository.mark_fit_stale(request_id)

    def __getattr__(self, name):
        return getattr(self.repository, name)


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
    fit_worker_factory=GreyFitWorker,
    learning_enabled: bool = False,
    solver_factory=_ProbeSolver,
    append_trace=lambda _records: None,
    estimator_kind="ekf",
    checkpoint_store=None,
    snapshot_parameters=None,
    base_configuration=None,
    trajectory_repository=None,
    fit_partition_digest=None,
    process_owner=None,
    installation_identity_provider=lambda: "test-installation",
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
    runtime_kwargs = {
        "pair_factory": factory,
        "activation_runtime": activation,
        "learning_enabled": learning_enabled,
        "units": "C",
        "cycle_data": dict(CYCLE),
        "checkpoint_store": checkpoint_store,
        "append_trace": append_trace,
        "active_pair": lambda: activation.active_pair,
        "active_components": lambda: CandidatePair(
            activation.active_pair.estimator,
            activation.active_pair.solver,
        ),
        "configuration": lambda: MpcConfig(activation.active_pair.core.config),
        "snapshot_parameters": (
            (lambda: activation.active_pair.core.snapshot_parameters())
            if snapshot_parameters is None
            else snapshot_parameters
        ),
        "sync_configuration": lambda: published.append(dict(activation.active_pair.core.config)),
        "fit_worker_factory": fit_worker_factory,
        "installation_identity_provider": installation_identity_provider,
    }
    if trajectory_repository is not None:
        runtime_kwargs["trajectory_repository"] = trajectory_repository
        runtime_kwargs["fit_partition_digest"] = fit_partition_digest
    if process_owner is not None:
        runtime_kwargs["process_owner"] = process_owner
    runtime = GreyLearningRuntime(**runtime_kwargs)
    return _Harness(runtime, activation, active, persistence, factory, published)


def _seed_durable_challenger(
    harness: _Harness,
    preparation,
    *,
    phase: str,
    decision_id: str | None = None,
    fit_corpus: FitCorpusIdentity | None = None,
    wins: int = 0,
) -> ModelChallengerState:
    request = preparation.candidate.request
    incumbent = harness.activation.active_pair.descriptor
    candidate = harness.runtime._prepared_candidate_descriptor(preparation)
    request_id = request.request_id
    corpus = request.fit_corpus if fit_corpus is None else fit_corpus
    assert corpus == request.fit_corpus
    qualified = phase == "qualified"
    retained_wins = 2 if qualified else wins
    calibration_manifest = None
    if request.origin is CandidateOrigin.OPERATOR_CALIBRATION:
        calibration_manifest = _manifest(request_id)
        calibration_manifest["session_id"] = harness.runtime.learning_identity().session_id
    challenger_id = f"challenger-{request_id}"
    retained_decision_id = decision_id if qualified else f"retained-decision-{request_id}" if retained_wins else None
    retained_evidence_id = (
        f"challenger-round:{challenger_id}:0:{retained_wins}:{retained_decision_id}"
        if retained_decision_id is not None
        else None
    )
    state = ModelChallengerState(
        schema_version=1,
        challenger_id=challenger_id,
        revision=0,
        phase=phase,
        origin=request.origin,
        policy=ActivationPolicy.CAUSAL_AUTO,
        fit_corpus=corpus,
        fit_lineage=ModelFitLineage(
            request_id=request_id,
            parent_incumbent_digest=incumbent.model_digest,
            parent_incumbent_generation=incumbent.role_generation,
            candidate_generation=candidate.candidate_generation,
            fit_corpus=corpus,
            fit_corpus_digest=corpus.corpus_digest,
            trigger_origin=request.origin.value,
            result_status="succeeded",
            candidate_digest=candidate.model_digest,
        ),
        fit_preparation={
            "request_id": request_id,
            "accepted": True,
            "candidate_digest": candidate.model_digest,
            "required_horizons": list(_REQUIRED_HORIZONS),
            "native_build": "passed",
            "dry_solve": "passed",
            "target_timing": {
                "target": "candidate-dry-solve",
                "samples": 1,
                "p99_ms": 1.0,
                "limit_ms": 25.0,
            },
            "fit_corpus_digest": corpus.corpus_digest,
            "fit_result": {
                "rmse_c": getattr(preparation.candidate, "rmse_c", 0.5),
                "max_error_c": getattr(preparation.candidate, "max_error_c", 1.0),
                "identifiability": getattr(preparation.candidate, "identifiability", 0.9),
                "sample_count": getattr(preparation.candidate, "sample_count", 120),
                "temperature_band_c": list(getattr(preparation.candidate, "temperature_band_c", (75.0, 160.0))),
                "nfev": getattr(preparation.candidate, "nfev", 4),
                "result_digest": preparation.candidate.result_digest,
            },
        },
        controller_configuration_digest=request.configuration_digest,
        incumbent=incumbent,
        candidate=candidate,
        calibration_manifest=calibration_manifest,
        evaluation_epoch=0,
        evaluation_round=retained_wins,
        consecutive_wins=retained_wins,
        required_wins=2,
        last_decision_id=retained_decision_id,
        last_evidence_id=retained_evidence_id,
        activation_transaction_id=None,
        retirement_reason=None,
        created_ms=1,
        updated_ms=1,
        retired_ms=None,
    )
    if retained_evidence_id is not None and retained_decision_id is not None:
        append_model_evidence(
            (
                ModelEvidenceRecord(
                    evidence_id=retained_evidence_id,
                    kind=EvidenceKind.CHALLENGER_ROUND,
                    session_id=harness.runtime.learning_identity().session_id,
                    cook_id=harness.runtime.learning_identity().cook_id,
                    timestamp_ms=state.updated_ms,
                    role_generation=incumbent.role_generation,
                    model_digest=candidate.model_digest,
                    provenance_digest=incumbent.model_digest,
                    payload=ChallengerRoundEvidence(
                        challenger_id=challenger_id,
                        evaluation_epoch=0,
                        evaluation_round=retained_wins,
                        decision_id=retained_decision_id,
                        accepted=True,
                        required_horizons=_REQUIRED_HORIZONS,
                        completed_horizons=_REQUIRED_HORIZONS,
                        incumbent_digest=incumbent.model_digest,
                        candidate_digest=candidate.model_digest,
                    ),
                ),
            ),
            database_path=getattr(
                harness.runtime._trajectory_repository,
                "_database_path",
                None,
            ),
        )
    current = read_model_challenger()
    if current is not None and current != state and current.phase != "retired":
        retired_ms = current.updated_ms + 1
        if current.phase == "activating":
            abort_model_challenger_activation(
                expected_revision=current.revision,
                activation_transaction_id=current.activation_transaction_id,
                reason="legacy-harness-replaced",
                retired_ms=retired_ms,
            )
        else:
            retire_model_challenger(
                expected_revision=current.revision,
                reason="legacy-harness-replaced",
                retired_ms=retired_ms,
            )
    durable = create_model_challenger(state)
    harness.runtime._challenger_state = durable
    return durable


def _automatic_candidate(
    harness: _Harness,
    *,
    fit_corpus: FitCorpusIdentity | None = None,
):
    native = GreyBoxMPCConfig(
        C_c=425.0,
        K_Q=405.0,
        theta=59.0,
        h_amb=0.72,
        T_amb=18.0,
    )
    active_descriptor = harness.activation.active_pair.descriptor
    fit_corpus = _corpus("automatic-fit") if fit_corpus is None else fit_corpus
    request = FitRequest(
        request_id="a" * 64,
        origin=CandidateOrigin.PASSIVE_ONLINE,
        fit_corpus=fit_corpus,
        configuration_digest=harness.runtime.learning_identity().configuration_digest,
        parent_incumbent_digest=active_descriptor.model_digest,
        parent_incumbent_generation=active_descriptor.role_generation,
        candidate_generation=active_descriptor.candidate_generation + 1,
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
        incumbent_pair=CandidatePair(
            harness.active.estimator,
            harness.active.solver,
        ),
        candidate=GreyFitSuccess(
            request=request,
            config=native,
            rmse_c=0.5,
            max_error_c=1.0,
            identifiability=0.9,
            sample_count=120,
            temperature_band_c=(75.0, 160.0),
            nfev=4,
            result_digest=request.request_id,
        ),
        candidate_digest=descriptor.model_digest,
        accepted=True,
        blockers=(),
        dry_solve_finite=True,
        timing=TargetTimingEvidence(
            target="candidate-dry-solve",
            samples=1,
            p99_ms=1.0,
            limit_ms=25.0,
        ),
    )
    evaluation = SimpleNamespace(
        decision_id="a" * 64,
        consecutive_wins=2,
        scores=_COMPLETE_SCORES,
        accepted=True,
        blockers=(),
        role_generation=active_descriptor.role_generation,
        candidate_generation=request.candidate_generation,
        incumbent_digest=harness.activation.active_pair.descriptor.model_digest,
        challenger_digest=descriptor.model_digest,
        completed_origins=_complete_origins(
            incumbent_digest=active_descriptor.model_digest,
            challenger_digest=descriptor.model_digest,
            role_generation=active_descriptor.role_generation,
            candidate_generation=request.candidate_generation,
        ),
        completed_horizons=_REQUIRED_HORIZONS,
    )
    return preparation, evaluation, components


def _operator_candidate(
    harness: _Harness,
    *,
    fit_corpus: FitCorpusIdentity | None = None,
):
    identity = harness.runtime.learning_identity()
    fit_corpus = _corpus("operator-fit") if fit_corpus is None else fit_corpus
    request = FitRequest(
        request_id="b" * 64,
        origin=CandidateOrigin.OPERATOR_CALIBRATION,
        fit_corpus=fit_corpus,
        configuration_digest=identity.configuration_digest,
        parent_incumbent_digest=identity.incumbent_digest,
        parent_incumbent_generation=identity.role_generation,
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
        result_digest=request.request_id,
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
        scores=_COMPLETE_SCORES,
        consecutive_wins=1,
        blockers=(),
        completed_origins=_complete_origins(
            incumbent_digest=harness.active.descriptor.model_digest,
            challenger_digest=descriptor.model_digest,
            role_generation=identity.role_generation,
            candidate_generation=identity.candidate_generation,
        ),
    )
    return preparation, evaluation, components


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


def _reopened_corpus(tmp_path, *, include_incompatible: bool = False):
    database_path = tmp_path / "grey-learning.sqlite"
    first_repository = LearningTrajectoryRepository(str(database_path))
    first = _segment("segment-a", epoch_ms=0, scored_count=2)
    _finalize_segment(first_repository, first)
    repository = LearningTrajectoryRepository(str(database_path))

    second_source = _segment("segment-b", epoch_ms=200_000, scored_count=2)
    second = replace(
        second_source,
        scored_hold_frames=(
            second_source.scored_hold_frames[0],
            replace(
                second_source.scored_hold_frames[1],
                calibration_origin=True,
            ),
        ),
        collection_provenance={
            "origin": CandidateOrigin.PASSIVE_ONLINE.value,
            "role_generation": 4,
        },
    )
    _finalize_segment(repository, second)
    if include_incompatible:
        incompatible = replace(
            _segment("segment-incompatible", epoch_ms=400_000, scored_count=2),
            ambient_semantics_digest="f" * 64,
        )
        _finalize_segment(repository, incompatible)
    return repository, first.fit_partition_digest


def _reopened_ready_passive_corpus(tmp_path):
    database_path = tmp_path / "grey-learning-passive-ready.sqlite"
    repository = LearningTrajectoryRepository(str(database_path))
    source = _segment("passive-ready", scored_count=120)
    scored_frames = []
    for ordinal, frame in enumerate(source.scored_hold_frames):
        load = (0.15, 0.50, 0.85)[ordinal % 3]
        scored_frames.append(
            replace(
                frame,
                chamber_temperature_c=80.0 + ordinal * 0.1,
                delivered_auger_on_seconds=load * 20.0,
                realized_auger_duty=load,
                normalized_combustion_load=load,
            )
        )
    ready = replace(
        source,
        scored_hold_frames=tuple(scored_frames),
        collection_provenance={
            "origin": CandidateOrigin.PASSIVE_ONLINE.value,
            "role_generation": 4,
        },
    )
    _finalize_segment(repository, ready)
    return repository, ready.fit_partition_digest
