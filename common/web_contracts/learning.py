from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, RootModel, field_validator
from typing_extensions import TypeAliasType

from .base import FiniteFloat, WireModel
from .core import ApiEnvelope, CommandResponseData

NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]
NonBlankString = Annotated[str, Field(min_length=1, strict=True)]
FiniteNumber = TypeAliasType("FiniteNumber", int | FiniteFloat)

CandidateOrigin = TypeAliasType(
    "CandidateOrigin",
    Literal["passive-online", "operator-calibration", "cook-refit"],
)
ActivationPolicy = TypeAliasType(
    "ActivationPolicy",
    Literal["passive-auto", "operator-reviewed", "cook-refit"],
)
ModelEvidenceStatus = TypeAliasType(
    "ModelEvidenceStatus",
    Literal[
        "collecting",
        "insufficient-excitation",
        "fitting",
        "evaluating",
        "ready-for-review",
        "activating",
        "active",
        "fallback",
        "error",
        "schema-invalidated",
    ],
)
FitStatus = TypeAliasType(
    "FitStatus",
    Literal["idle", "queued", "running", "succeeded", "failed", "stale"],
)
CheckStatus = TypeAliasType(
    "CheckStatus",
    Literal["not-run", "pending", "passed", "failed"],
)
ActivationPhase = TypeAliasType(
    "ActivationPhase",
    Literal["prepared", "active", "aborted"],
)
CookRefitOutcome = TypeAliasType(
    "CookRefitOutcome",
    Literal[
        "disabled",
        "insufficient",
        "rejected",
        "failed",
        "ready-for-review",
        "accepted-next-cook",
        "checkpoint-failure",
    ],
)
CookRefitAuthorization = TypeAliasType(
    "CookRefitAuthorization",
    #: "not-run" is the absence of a verdict, not a refusal: no refit has
    #: reached this checkpoint yet. "blocked" means one ran and authorized
    #: nothing.
    Literal["not-run", "blocked", "operator-review", "next-cook"],
)
MpcCalibrationAction = TypeAliasType(
    "MpcCalibrationAction",
    Literal["start", "pause", "resume", "stop", "reset-progress"],
)
AmbientSource = TypeAliasType(
    "AmbientSource",
    Literal["measured", "manual", "weather", "configured"],
)


class EvidenceSummary(WireModel):
    count: NonNegativeInt
    audit_count: NonNegativeInt
    high_water: tuple[NonNegativeInt, str] | None
    retired_excluded: NonNegativeInt


class FitReport(WireModel):
    status: FitStatus
    request_id: str | None
    window_id: str | None
    error: str | None


class CookRefitReport(WireModel):
    status: FitStatus
    latest: CookRefitOutcome | None
    final_status: FitStatus | CookRefitOutcome
    authorization: CookRefitAuthorization
    next_cook: bool


class FitWindowIdentity(WireModel):
    session_id: NonBlankString
    cook_id: NonBlankString | None
    first_observation_sequence: NonNegativeInt
    last_observation_sequence: NonNegativeInt
    configuration_digest: Digest
    incumbent_digest: Digest
    role_generation: NonNegativeInt


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


class CandidateReport(WireModel):
    digest: Digest | None
    origin: CandidateOrigin | None
    policy: ActivationPolicy | None
    role_generation: NonNegativeInt | None
    candidate_generation: NonNegativeInt | None
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
    schema_version: Literal[2]
    status: ModelEvidenceStatus
    mode: CandidateOrigin | None
    decision_id: str | None
    evidence: EvidenceSummary
    fit: FitReport
    cook_refit: CookRefitReport
    window: FitWindowIdentity | None
    checks: dict[str, CheckStatus]
    candidate: CandidateReport
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


class ModelActivationRequest(WireModel):
    candidate_digest: Digest
    decision_id: NonBlankString


class ModelActivationAccepted(WireModel):
    accepted: Literal[True]
    phase: Literal["prepared"]
    transaction_id: Digest
    decision_id: NonBlankString
    candidate_digest: Digest
    role_generation: NonNegativeInt


class ModelActionRejected(WireModel):
    accepted: Literal[False]
    active_kind: Literal["grey-box"]
    error: Literal["model-activation-rejected"]
    detail: str


class ModelActivationAcknowledgement(
    RootModel[
        Annotated[
            ModelActivationAccepted | ModelActionRejected,
            Field(discriminator="accepted"),
        ]
    ]
):
    pass


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


class ModelRollbackAcknowledgement(
    RootModel[
        Annotated[
            ModelRollbackAccepted | ModelActionRejected,
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


PidSpLearningStatus = TypeAliasType(
    "PidSpLearningStatus",
    Literal[
        "idle",
        "collecting",
        "insufficient-excitation",
        "evaluating",
        "active",
        "fallback",
        "error",
    ],
)
PidSpLiveLearningStatus = TypeAliasType(
    "PidSpLiveLearningStatus",
    Literal[
        "collecting",
        "insufficient-excitation",
        "evaluating",
        "active",
        "fallback",
    ],
)
PidSpGateValue = TypeAliasType("PidSpGateValue", int | FiniteFloat | bool)


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


PidSpCheckpointModel = TypeAliasType(
    "PidSpCheckpointModel",
    Annotated[
        FopdtPidSpCheckpoint | IpdtPidSpCheckpoint,
        Field(discriminator="form"),
    ],
)


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


PidSpPredictorModel = TypeAliasType(
    "PidSpPredictorModel",
    Annotated[
        FopdtPidSpPredictor | IpdtPidSpPredictor,
        Field(discriminator="form"),
    ],
)


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
    distrust_ratio: FiniteNumber


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
