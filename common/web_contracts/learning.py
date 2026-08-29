from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, RootModel, field_validator

from .base import FiniteFloat, WireModel
from .core import ApiEnvelope, CommandResponseData

NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]
NonBlankString = Annotated[str, Field(min_length=1, strict=True)]
type FiniteNumber = int | FiniteFloat

type CandidateOrigin = Literal["passive-online", "operator-calibration"]
type ActivationPolicy = Literal["causal-auto"]
type ModelEvidenceStatus = Literal[
    "warming",
    "collecting",
    "fitting",
    "evaluating",
    "interrupted",
    "qualified",
    "activating",
    "active",
    "fallback",
    "error",
]
type FitStatus = Literal["idle", "queued", "running", "succeeded", "failed", "stale"]
type CheckStatus = Literal["not-run", "pending", "passed", "failed"]
type ActivationPhase = Literal["prepared", "active", "aborted"]
type MpcCalibrationAction = Literal["start", "pause", "resume", "stop", "reset-progress"]
type AmbientSource = Literal["measured", "manual", "weather", "configured"]


class EvidenceSummary(WireModel):
    count: NonNegativeInt
    audit_count: NonNegativeInt
    high_water: tuple[NonNegativeInt, str] | None
    retired_excluded: NonNegativeInt


class FitReport(WireModel):
    status: FitStatus
    request_id: str | None
    fit_corpus_digest: Digest | None
    error: str | None


class GreyParameters(WireModel):
    C_c: FiniteFloat
    h_amb: FiniteFloat
    T_amb: FiniteFloat
    theta: FiniteFloat
    n_delay: Literal[8]
    K_Q: FiniteFloat
    sigma: FiniteFloat


class CandidateAssessment(WireModel):
    decision_id: NonBlankString
    origin: CandidateOrigin
    policy: ActivationPolicy
    fit_accepted: bool
    identifiability_accepted: bool
    native_build: CheckStatus
    native_dry_solve: CheckStatus
    target_timing: CheckStatus
    confidence_accepted: bool
    rejection_reasons: list[str]
    payload_type: Literal["candidate_assessment"]


class ModelFitLineageReport(WireModel):
    request_id: NonBlankString
    parent_incumbent_digest: Digest
    parent_incumbent_generation: NonNegativeInt
    candidate_generation: NonNegativeInt
    fit_corpus_digest: Digest
    trigger_origin: CandidateOrigin
    result_status: Literal["succeeded"]
    candidate_digest: Digest


class FitCorpusSliceReport(WireModel):
    segment_id: NonBlankString
    through_ordinal: NonNegativeInt
    prefix_digest: Digest
    pre_roll_count: NonNegativeInt
    scored_count: NonNegativeInt


class CorpusStatusReport(WireModel):
    digest: Digest | None
    revision: NonNegativeInt | None
    fit_partition_digest: Digest | None
    slices: list[FitCorpusSliceReport]


class PendingForecastOriginReport(WireModel):
    origin_sequence: NonNegativeInt
    horizon_steps: Literal[3, 15, 45, 90, 180]
    role_generation: NonNegativeInt
    candidate_generation: NonNegativeInt
    incumbent_digest: Digest
    candidate_digest: Digest


class CausalEvaluationProgress(WireModel):
    epoch: NonNegativeInt
    round: NonNegativeInt
    completed_horizons: list[Literal[3, 15, 45, 90, 180]]
    required_horizons: list[Literal[3, 15, 45, 90, 180]]
    wins: NonNegativeInt
    required_wins: NonNegativeInt
    resumed_from_previous_cook: bool
    pending_origins: list[PendingForecastOriginReport]


class CandidateReport(WireModel):
    challenger_id: NonBlankString
    phase: Literal["built", "evaluating", "qualified", "activating"]
    lineage: ModelFitLineageReport
    digest: Digest
    origin: CandidateOrigin
    policy: ActivationPolicy
    role_generation: NonNegativeInt
    candidate_generation: NonNegativeInt
    parameters: GreyParameters | None
    parameter_deltas: dict[str, FiniteFloat | None] | None
    fit_quality: FiniteFloat | None
    identifiability: FiniteFloat | None
    assessment: CandidateAssessment | None


class ActivationReport(WireModel):
    active_snapshot_json: str | None = None
    rollback_snapshot_json: str | None = None
    evidence_decision_id: str | None = None
    decision_id: NonBlankString | None = None
    controller_configuration_digest: str | None = None
    role_generation: NonNegativeInt | None = None
    transaction_id: str | None = None
    incumbent_pair_json: str | None = None
    candidate_pair_json: str | None = None
    rollback_pair_json: str | None = None
    origin: CandidateOrigin | None = None
    policy: ActivationPolicy | None = None
    candidate_generation: NonNegativeInt | None = None
    candidate_digest: Digest | None = None
    incumbent_digest: Digest | None = None
    phase: ActivationPhase
    reason: str | None
    pending_persistence: bool
    pending_frame_boundary_swap: bool


class ActiveModelReport(WireModel):
    digest: Digest | None
    role_generation: NonNegativeInt | None


class ModelIdentities(WireModel):
    active_digest: Digest | None
    active_generation: NonNegativeInt | None
    candidate_digest: Digest | None
    candidate_generation: NonNegativeInt | None
    rollback_digest: Digest | None
    rollback_generation: NonNegativeInt | None


class CalibrationReport(WireModel):
    revision: NonNegativeInt
    command_high_water: NonNegativeInt


class ActivationLifecycle(WireModel):
    decision_id: NonBlankString
    phase: ActivationPhase
    origin: CandidateOrigin
    policy: ActivationPolicy
    reason: str | None
    payload_type: Literal["activation_lifecycle"]


class LearningFailure(WireModel):
    code: NonBlankString
    detail: NonBlankString
    terminal: bool
    payload_type: Literal["learning_failure"] | None = None


class EvidenceGate(WireModel):
    name: str
    passed: bool
    reason: str | None


class ModelEvidenceReport(WireModel):
    schema_version: Literal[3]
    status: ModelEvidenceStatus
    mode: CandidateOrigin | None
    decision_id: str | None
    evidence: EvidenceSummary
    fit: FitReport
    checks: dict[str, CheckStatus]
    evaluation: CausalEvaluationProgress | None
    corpus: CorpusStatusReport
    candidate: CandidateReport | None
    activation: ActivationReport
    active_model: ActiveModelReport
    identities: ModelIdentities
    calibration: CalibrationReport
    latest_lifecycle: ActivationLifecycle | None
    failure: LearningFailure | None
    gates: list[EvidenceGate]
    blockers: list[str]
    errors: list[str]
    revision: Digest


class ModelRollbackRequest(WireModel):
    reason: NonBlankString

    @field_validator("reason")
    @classmethod
    def _reason_must_be_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must be non-blank")
        return value


class ModelRollbackAccepted(WireModel):
    accepted: Literal[True]
    active_kind: Literal["grey-box"]
    decision_id: NonBlankString
    reason: NonBlankString
    role_generation: NonNegativeInt
    rollback_digest: Digest


class ModelRollbackRejected(WireModel):
    accepted: Literal[False]
    active_kind: Literal["grey-box"]
    error: Literal["model-rollback-rejected"]
    detail: str


class ModelRollbackAcknowledgement(
    RootModel[
        Annotated[
            ModelRollbackAccepted | ModelRollbackRejected,
            Field(discriminator="accepted"),
        ]
    ]
):
    pass


class MpcCalibrationCommand(WireModel):
    action: MpcCalibrationAction
    revision: NonNegativeInt
    ambient_c: FiniteFloat
    ambient_source: AmbientSource
    empty_grill_confirmed: bool
    pellets_confirmed: bool


class MpcCalibrationCommandResponseData(WireModel):
    mpc_calibration: MpcCalibrationCommand


class MpcCalibrationCommandResponse(ApiEnvelope[MpcCalibrationCommandResponseData | CommandResponseData]):
    pass


type PidSpLearningStatus = Literal[
    "idle",
    "collecting",
    "insufficient-excitation",
    "evaluating",
    "active",
    "fallback",
    "error",
]
type PidSpLiveLearningStatus = Literal[
    "collecting",
    "insufficient-excitation",
    "evaluating",
    "active",
    "fallback",
]
type PidSpGateValue = int | FiniteFloat | bool


class FopdtPidSpCheckpoint(WireModel):
    form: Literal["fopdt"]
    K: FiniteFloat
    tau: FiniteFloat
    theta: FiniteFloat
    revision: NonNegativeInt
    identified_at_f: FiniteFloat | None = None


class IpdtPidSpCheckpoint(WireModel):
    form: Literal["ipdt"]
    K_i: FiniteFloat
    c0: FiniteFloat
    theta: FiniteFloat
    revision: NonNegativeInt
    identified_at_f: FiniteFloat | None = None


type PidSpCheckpointModel = Annotated[
    FopdtPidSpCheckpoint | IpdtPidSpCheckpoint,
    Field(discriminator="form"),
]


class FopdtPidSpPredictor(WireModel):
    form: Literal["fopdt"]
    K: FiniteFloat
    tau: FiniteFloat
    theta: FiniteFloat


class IpdtPidSpPredictor(WireModel):
    form: Literal["ipdt"]
    K_i: FiniteFloat
    c0: FiniteFloat
    theta: FiniteFloat


type PidSpPredictorModel = Annotated[
    FopdtPidSpPredictor | IpdtPidSpPredictor,
    Field(discriminator="form"),
]


class PidSpLearningGate(WireModel):
    name: str
    passed: bool
    observed: PidSpGateValue
    required: PidSpGateValue
    unit: str | None


class PidSpConfirmationProgress(WireModel):
    observed: NonNegativeInt | None
    required: NonNegativeInt


class PidSpIdentifierReport(WireModel):
    accepted: FiniteNumber
    accepted_seconds: FiniteNumber
    duty_std: FiniteNumber
    temp_span: FiniteNumber
    transition_seen: bool
    duty_segments: NonNegativeInt
    best_residual: FiniteNumber
    runner_up_residual: FiniteNumber
    candidates_passing: NonNegativeInt
    confirming: NonNegativeInt | None
    trusted: PidSpCheckpointModel | None
    distrust_count: NonNegativeInt
    distrust_ratio: FiniteNumber | None


class PidSpPredictorReport(WireModel):
    active: bool
    disabled: bool
    x0: FiniteNumber
    xd: FiniteNumber
    residual_streak: NonNegativeInt
    truncated: NonNegativeInt
    model: PidSpPredictorModel | None


class PidSpLearningFailure(WireModel):
    code: NonBlankString
    detail: NonBlankString
    terminal: bool


class PidSpLiveLearning(WireModel):
    schema_version: Literal[1]
    controller: Literal["pid_sp"]
    status: PidSpLiveLearningStatus
    identifier: dict[str, object]
    predictor: dict[str, object]
    confirmation: PidSpConfirmationProgress
    gates: tuple[PidSpLearningGate, ...]


class PidSpLearningReport(WireModel):
    schema_version: Literal[1]
    controller: Literal["pid_sp"]
    status: PidSpLearningStatus
    live: bool
    revision: Digest
    gates: list[PidSpLearningGate]
    confirmation: PidSpConfirmationProgress | None
    identifier: PidSpIdentifierReport | None
    predictor: PidSpPredictorReport | None
    checkpoint: PidSpCheckpointModel | None
    failure: PidSpLearningFailure | None
