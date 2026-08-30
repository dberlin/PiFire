from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, RootModel, field_validator, model_validator

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
    segment_content_digest: Digest | None
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


class IpdtPidSpParameters(WireModel):
    K_i: FiniteFloat
    c0: FiniteFloat
    theta: FiniteFloat


class FopdtPidSpParameters(WireModel):
    K: FiniteFloat
    tau: FiniteFloat
    theta: FiniteFloat


class SopdtPidSpParameters(WireModel):
    K: FiniteFloat
    tau_1: FiniteFloat
    tau_2: FiniteFloat
    theta: FiniteFloat


type PidSpCheckpointParameters = IpdtPidSpParameters | FopdtPidSpParameters | SopdtPidSpParameters


class PidSpCheckpointBasin(WireModel):
    lower_s: NonNegativeInt
    upper_s: NonNegativeInt
    representative_s: NonNegativeInt
    confidence_lower_s: NonNegativeInt
    confidence_upper_s: NonNegativeInt
    confidence_method: Literal["raw-basin", "provided", "moving-block-refit"]
    confidence_resamples: NonNegativeInt
    episode_count: NonNegativeInt
    interior: bool
    blockers: tuple[str, ...]


class PidSpSelectedCheckpoint(WireModel):
    schema_version: Literal["pid-sp-model-selection/v1"]
    form: Literal["ipdt", "fopdt", "sopdt"]
    parameters: PidSpCheckpointParameters
    delay_basin: PidSpCheckpointBasin
    one_step_loss: FiniteFloat
    horizon_losses: tuple[tuple[NonNegativeInt, FiniteFloat], ...]
    fold_losses: tuple[FiniteFloat, ...]
    standard_error: FiniteFloat
    episode_ids: tuple[NonBlankString, ...]
    common_row_digest: Digest
    fit_corpus_digest: Digest
    configuration_digest: Digest
    comparison_threshold: FiniteFloat
    selection_margin: FiniteFloat
    confirmation_observed: Literal[20]
    confirmation_required: Literal[20]
    authorized: Literal[True]
    model_digest: Digest

    @model_validator(mode="after")
    def _parameters_match_form(self) -> PidSpSelectedCheckpoint:
        expected = {
            "ipdt": IpdtPidSpParameters,
            "fopdt": FopdtPidSpParameters,
            "sopdt": SopdtPidSpParameters,
        }[self.form]
        if not isinstance(self.parameters, expected):
            raise ValueError("checkpoint parameters must match form")  # noqa: TRY004
        return self


class PidSpCheckpointModel(WireModel):
    schema_version: Literal[2]
    revision: NonNegativeInt
    provenance: NonBlankString
    selected: PidSpSelectedCheckpoint


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


class SopdtPidSpPredictor(WireModel):
    form: Literal["sopdt"]
    K: FiniteFloat
    tau_1: FiniteFloat
    tau_2: FiniteFloat
    theta: FiniteFloat


type PidSpPredictorModel = Annotated[
    FopdtPidSpPredictor | IpdtPidSpPredictor | SopdtPidSpPredictor,
    Field(discriminator="form"),
]

type PidSpDelayProfileForm = Literal["ipdt", "fopdt", "sopdt"]
type PidSpDelayConfidenceMethod = Literal[
    "raw-basin",
    "provided",
    "moving-block-refit",
]
type PidSpDelayBlocker = Literal[
    "insufficient-excitation-episodes",
    "insufficient-confidence-evidence",
    "delay-basin-too-wide",
    "delay-basin-edge",
    "delay-range-exhausted",
    "no-physically-valid-delay-candidate",
]
type PidSpDelayEvidenceStatus = Literal[
    "insufficient-excitation-episodes",
    "insufficient-confidence-evidence",
    "delay-basin-too-wide",
    "delay-basin-edge",
    "delay-range-exhausted",
    "no-physically-valid-delay-candidate",
    "delay-basin-stable",
]


class PidSpDelayEvidence(WireModel):
    status: PidSpDelayEvidenceStatus
    completed_episode_count: NonNegativeInt
    evaluated_bound_s: NonNegativeInt
    profile_form: PidSpDelayProfileForm | None
    raw_basin_lower_s: NonNegativeInt | None
    raw_basin_upper_s: NonNegativeInt | None
    raw_basin_representative_s: NonNegativeInt | None
    confidence_lower_s: NonNegativeInt | None
    confidence_upper_s: NonNegativeInt | None
    confidence_method: PidSpDelayConfidenceMethod | None
    confidence_resamples: NonNegativeInt | None
    blockers: list[PidSpDelayBlocker]
    authorized: bool

    @model_validator(mode="after")
    def _validate_delay_evidence(self) -> PidSpDelayEvidence:
        blocker_set = set(self.blockers)
        if len(blocker_set) != len(self.blockers):
            raise ValueError("delay blockers must be unique")
        if self.authorized != (not self.blockers):
            raise ValueError("authorized must be true exactly when blockers are empty")
        stable = self.status == "delay-basin-stable"
        if stable != self.authorized:
            raise ValueError("stable status must be authorized and blocker-free")
        if not stable and self.status not in blocker_set:
            raise ValueError("nonstable status must be present in blockers")

        audit = (
            self.raw_basin_lower_s,
            self.raw_basin_upper_s,
            self.raw_basin_representative_s,
            self.confidence_lower_s,
            self.confidence_upper_s,
            self.confidence_method,
            self.confidence_resamples,
        )
        unavailable = "no-physically-valid-delay-candidate" in blocker_set
        if unavailable:
            if self.profile_form is None:
                raise ValueError("unavailable delay evidence requires its profile form")
            if any(value is not None for value in audit):
                raise ValueError("unavailable delay evidence cannot report basin audit")
            return self
        if self.profile_form is None:
            if any(value is not None for value in audit):
                raise ValueError("profile audit fields must be null without a profile")
            return self
        if any(value is None for value in audit):
            raise ValueError("profile audit fields must all be present with a profile")

        assert self.raw_basin_lower_s is not None
        assert self.raw_basin_upper_s is not None
        assert self.raw_basin_representative_s is not None
        assert self.confidence_lower_s is not None
        assert self.confidence_upper_s is not None
        assert self.confidence_method is not None
        assert self.confidence_resamples is not None
        if not (
            self.raw_basin_lower_s
            <= self.raw_basin_representative_s
            <= self.raw_basin_upper_s
            <= self.evaluated_bound_s
        ):
            raise ValueError("raw basin bounds and representative must be ordered")
        if not (self.confidence_lower_s <= self.confidence_upper_s <= self.evaluated_bound_s):
            raise ValueError("confidence bounds must be ordered")
        if self.confidence_method == "provided" and self.confidence_resamples != 0:
            raise ValueError("provided confidence must not claim resamples")
        if self.confidence_method == "moving-block-refit" and self.confidence_resamples == 0:
            raise ValueError("moving-block confidence must report resamples")
        confidence_blocked = "insufficient-confidence-evidence" in blocker_set
        if (self.confidence_method == "raw-basin") != confidence_blocked:
            raise ValueError("confidence method must agree with insufficient-confidence blocker")
        return self


class PidSpHorizonLossReport(WireModel):
    horizon_s: NonNegativeInt
    loss: FiniteFloat | None


class PidSpFormComparisonReport(WireModel):
    form: PidSpDelayProfileForm
    eligible: bool
    blockers: tuple[NonBlankString, ...]
    one_step_loss: FiniteFloat | None
    horizon_losses: tuple[PidSpHorizonLossReport, ...]
    fold_losses: tuple[FiniteFloat | None, ...]
    standard_error: FiniteFloat | None
    basin_lower_s: NonNegativeInt | None
    basin_upper_s: NonNegativeInt | None
    confidence_lower_s: NonNegativeInt | None
    confidence_upper_s: NonNegativeInt | None
    confidence_method: PidSpDelayConfidenceMethod | None

    @model_validator(mode="after")
    def _validate_basin_audit(self) -> PidSpFormComparisonReport:
        unavailable = "no-physically-valid-delay-candidate" in self.blockers
        audit = (
            self.basin_lower_s,
            self.basin_upper_s,
            self.confidence_lower_s,
            self.confidence_upper_s,
            self.confidence_method,
        )
        if unavailable:
            if self.eligible or any(value is not None for value in audit):
                raise ValueError("an unavailable form must be ineligible and omit basin audit")
        elif any(value is None for value in audit):
            raise ValueError("only a physically unavailable form may omit basin audit")
        return self


class PidSpModelComparisonReport(WireModel):
    forms: tuple[PidSpFormComparisonReport, ...]
    best_form: PidSpDelayProfileForm | None
    comparison_threshold: FiniteFloat | None
    selection_margin: FiniteFloat | None
    selected_form: PidSpDelayProfileForm | None
    confirmation: PidSpConfirmationProgress
    primary_blocker: NonBlankString | None


class PidSpActiveModelReport(WireModel):
    form: PidSpDelayProfileForm
    model_digest: Digest


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
    raw_best_residual: FiniteNumber
    raw_runner_up_residual: FiniteNumber
    raw_candidates_passing: NonNegativeInt
    trusted: PidSpPredictorModel | None
    distrust_count: NonNegativeInt
    distrust_ratio: FiniteNumber | None


class PidSpPredictorReport(WireModel):
    active: bool
    disabled: bool
    x0: FiniteNumber
    xd: FiniteNumber
    z0: FiniteNumber
    zd: FiniteNumber
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
    delay_evidence: PidSpDelayEvidence
    gates: tuple[PidSpLearningGate, ...]
    comparison: PidSpModelComparisonReport | None
    active_model: PidSpActiveModelReport | None


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
    comparison: PidSpModelComparisonReport | None
    active_model: PidSpActiveModelReport | None
    delay_evidence: PidSpDelayEvidence | None
    failure: PidSpLearningFailure | None
