// biome-ignore-all lint/suspicious/noEmptyInterface: Generated from closed empty JSON objects.
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

type DecisionId = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CandidateOrigin".
 */
export type CandidateOrigin = "passive-online" | "operator-calibration";
type PayloadType = "activation_lifecycle";
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ActivationPhase".
 */
export type ActivationPhase = "prepared" | "active" | "aborted";
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ActivationPolicy".
 */
export type ActivationPolicy = "causal-auto";
type Reason = string | null;
type ActiveSnapshotJson = string | null;
type CandidateDigest = string | null;
type CandidateGeneration = number | null;
type CandidatePairJson = string | null;
type ControllerConfigurationDigest = string | null;
type DecisionId1 = string | null;
type EvidenceDecisionId = string | null;
type IncumbentDigest = string | null;
type IncumbentPairJson = string | null;
type PendingFrameBoundarySwap = boolean;
type PendingPersistence = boolean;
type Reason1 = string | null;
type RoleGeneration = number | null;
type RollbackPairJson = string | null;
type RollbackSnapshotJson = string | null;
type TransactionId = string | null;
type Digest = string | null;
type RoleGeneration1 = number | null;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "AmbientSource".
 */
export type AmbientSource = "measured" | "manual" | "weather" | "configured";
type CommandHighWater = number;
type Revision = number;
type ConfidenceAccepted = boolean;
type DecisionId2 = string;
type FitAccepted = boolean;
type IdentifiabilityAccepted = boolean;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CheckStatus".
 */
export type CheckStatus = "not-run" | "pending" | "passed" | "failed";
type PayloadType1 = "candidate_assessment";
type RejectionReasons = string[];
type CandidateGeneration1 = number;
type ChallengerId = string;
type Digest1 = string;
type FitQuality = number | null;
type Identifiability = number | null;
type CandidateDigest1 = string;
type CandidateGeneration2 = number;
type FitCorpusDigest = string;
type ParentIncumbentDigest = string;
type ParentIncumbentGeneration = number;
type RequestId = string;
type ResultStatus = "succeeded";
type ParameterDeltas = {
  [k: string]: number | null | undefined;
} | null;
type CC = number;
type KQ = number;
type TAmb = number;
type HAmb = number;
type NDelay = 8;
type Sigma = number;
type Theta = number;
type Phase = "built" | "evaluating" | "qualified" | "activating";
type RoleGeneration2 = number;
type CompletedHorizons = (3 | 15 | 45 | 90 | 180)[];
type Epoch = number;
type CandidateDigest2 = string;
type CandidateGeneration3 = number;
type HorizonSteps = 3 | 15 | 45 | 90 | 180;
type IncumbentDigest1 = string;
type OriginSequence = number;
type RoleGeneration3 = number;
type PendingOrigins = PendingForecastOriginReport[];
type RequiredHorizons = (3 | 15 | 45 | 90 | 180)[];
type RequiredWins = number;
type ResumedFromPreviousCook = boolean;
type Round = number;
type Wins = number;
type Digest2 = string | null;
type FitPartitionDigest = string | null;
type Revision1 = number | null;
type PreRollCount = number;
type PrefixDigest = string;
type ScoredCount = number;
type SegmentContentDigest = string | null;
type SegmentId = string;
type ThroughOrdinal = number;
type Slices = FitCorpusSliceReport[];
type Name = string;
type Passed = boolean;
type Reason2 = string | null;
type AuditCount = number;
type Count = number;
type HighWater = [unknown, unknown] | null;
type RetiredExcluded = number;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FiniteNumber".
 */
type FiniteNumber = number;
type Error = string | null;
type FitCorpusDigest1 = string | null;
type RequestId1 = string | null;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FitStatus".
 */
export type FitStatus = "idle" | "queued" | "running" | "succeeded" | "failed" | "stale";
type K = number;
type Tau = number;
type Theta1 = number;
type K1 = number;
type Form = "fopdt";
type Tau1 = number;
type Theta2 = number;
type KI = number;
type C0 = number;
type Theta3 = number;
type KI1 = number;
type C01 = number;
type Form1 = "ipdt";
type Theta4 = number;
type Code = string;
type Detail = string;
type PayloadType2 = "learning_failure" | null;
type Terminal = boolean;
type Blockers = string[];
type DecisionId3 = string | null;
type Errors = string[];
type Gates = EvidenceGate[];
type ActiveDigest = string | null;
type ActiveGeneration = number | null;
type CandidateDigest3 = string | null;
type CandidateGeneration4 = number | null;
type RollbackDigest = string | null;
type RollbackGeneration = number | null;
type Revision2 = string;
type SchemaVersion = 3;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelEvidenceStatus".
 */
export type ModelEvidenceStatus =
  | "warming"
  | "collecting"
  | "fitting"
  | "evaluating"
  | "interrupted"
  | "qualified"
  | "activating"
  | "active"
  | "fallback"
  | "error";
type Accepted = true;
type ActiveKind = "grey-box";
type DecisionId4 = string;
type Reason3 = string;
type RoleGeneration4 = number;
type RollbackDigest1 = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelRollbackAcknowledgement".
 */
export type ModelRollbackAcknowledgement = ModelRollbackAccepted | ModelRollbackRejected;
type Accepted1 = false;
type ActiveKind1 = "grey-box";
type Detail1 = string;
type Error1 = "model-rollback-rejected";
type Reason4 = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "MpcCalibrationAction".
 */
export type MpcCalibrationAction = "start" | "pause" | "resume" | "stop" | "reset-progress";
type AmbientC = number;
type EmptyGrillConfirmed = boolean;
type PelletsConfirmed = boolean;
type Revision3 = number;
type Data = MpcCalibrationCommandResponseData | CommandResponseData | null;
type Message = string;
type Result = "OK" | "ERROR";
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpDelayProfileForm".
 */
export type PidSpDelayProfileForm = "ipdt" | "fopdt" | "sopdt";
type ModelDigest = string;
type Blockers1 = string[];
type ConfidenceLowerS = number;
type ConfidenceMethod = "raw-basin" | "provided" | "moving-block-refit";
type ConfidenceResamples = number;
type ConfidenceUpperS = number;
type EpisodeCount = number;
type Interior = boolean;
type LowerS = number;
type RepresentativeS = number;
type UpperS = number;
type Provenance = string;
type Revision4 = number;
type SchemaVersion1 = 2;
type Authorized = true;
type CommonRowDigest = string;
type ComparisonThreshold = number;
type ConfigurationDigest = string;
type ConfirmationObserved = 20;
type ConfirmationRequired = 20;
type EpisodeIds = string[];
type FitCorpusDigest2 = string;
type FoldLosses = number[];
type Form2 = "ipdt" | "fopdt" | "sopdt";
type HorizonLosses = [unknown, unknown][];
type ModelDigest1 = string;
type OneStepLoss = number;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpCheckpointParameters".
 */
export type PidSpCheckpointParameters =
  | IpdtPidSpParameters
  | FopdtPidSpParameters
  | SopdtPidSpParameters;
type K2 = number;
type Tau11 = number;
type Tau2 = number;
type Theta5 = number;
type SchemaVersion2 = "pid-sp-model-selection/v1";
type SelectionMargin = number;
type StandardError = number;
type Observed = number | null;
type Required = number;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpDelayBlocker".
 */
export type PidSpDelayBlocker =
  | "insufficient-excitation-episodes"
  | "insufficient-confidence-evidence"
  | "delay-basin-too-wide"
  | "delay-basin-edge"
  | "delay-range-exhausted"
  | "no-physically-valid-delay-candidate";
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpDelayConfidenceMethod".
 */
export type PidSpDelayConfidenceMethod = "raw-basin" | "provided" | "moving-block-refit";
type Authorized1 = boolean;
type Blockers2 = PidSpDelayBlocker[];
type CompletedEpisodeCount = number;
type ConfidenceLowerS1 = number | null;
type ConfidenceResamples1 = number | null;
type ConfidenceUpperS1 = number | null;
type EvaluatedBoundS = number;
type RawBasinLowerS = number | null;
type RawBasinRepresentativeS = number | null;
type RawBasinUpperS = number | null;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpDelayEvidenceStatus".
 */
export type PidSpDelayEvidenceStatus =
  | "insufficient-excitation-episodes"
  | "insufficient-confidence-evidence"
  | "delay-basin-too-wide"
  | "delay-basin-edge"
  | "delay-range-exhausted"
  | "no-physically-valid-delay-candidate"
  | "delay-basin-stable";
type BasinLowerS = number | null;
type BasinUpperS = number | null;
type Blockers3 = string[];
type ConfidenceLowerS2 = number | null;
type ConfidenceUpperS2 = number | null;
type Eligible = boolean;
type FoldLosses1 = (number | null)[];
type HorizonS = number;
type Loss = number | null;
type HorizonLosses1 = PidSpHorizonLossReport[];
type OneStepLoss1 = number | null;
type StandardError1 = number | null;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpGateValue".
 */
export type PidSpGateValue = number | boolean;
type DistrustCount = number;
type DutySegments = number;
type RawCandidatesPassing = number;
type TransitionSeen = boolean;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpPredictorModel".
 */
export type PidSpPredictorModel = FopdtPidSpPredictor | IpdtPidSpPredictor | SopdtPidSpPredictor;
type K3 = number;
type Form3 = "sopdt";
type Tau12 = number;
type Tau21 = number;
type Theta6 = number;
type Code1 = string;
type Detail2 = string;
type Terminal1 = boolean;
type Name1 = string;
type Passed1 = boolean;
type Unit = string | null;
type ComparisonThreshold1 = number | null;
type Forms = PidSpFormComparisonReport[];
type PrimaryBlocker = string | null;
type SelectionMargin1 = number | null;
type Controller = "pid_sp";
type Gates1 = PidSpLearningGate[];
type Live = boolean;
type Active = boolean;
type Disabled = boolean;
type ResidualStreak = number;
type Truncated = number;
type Revision5 = string;
type SchemaVersion3 = 1;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpLearningStatus".
 */
export type PidSpLearningStatus =
  | "idle"
  | "collecting"
  | "insufficient-excitation"
  | "evaluating"
  | "active"
  | "fallback"
  | "error";

export interface PiFireLearningWebContracts {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ActivationLifecycle".
 */
export interface ActivationLifecycle {
  decision_id: DecisionId;
  origin: CandidateOrigin;
  payload_type: PayloadType;
  phase: ActivationPhase;
  policy: ActivationPolicy;
  reason: Reason;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ActivationReport".
 */
export interface ActivationReport {
  active_snapshot_json?: ActiveSnapshotJson;
  candidate_digest?: CandidateDigest;
  candidate_generation?: CandidateGeneration;
  candidate_pair_json?: CandidatePairJson;
  controller_configuration_digest?: ControllerConfigurationDigest;
  decision_id?: DecisionId1;
  evidence_decision_id?: EvidenceDecisionId;
  incumbent_digest?: IncumbentDigest;
  incumbent_pair_json?: IncumbentPairJson;
  origin?: CandidateOrigin | null;
  pending_frame_boundary_swap: PendingFrameBoundarySwap;
  pending_persistence: PendingPersistence;
  phase: ActivationPhase;
  policy?: ActivationPolicy | null;
  reason: Reason1;
  role_generation?: RoleGeneration;
  rollback_pair_json?: RollbackPairJson;
  rollback_snapshot_json?: RollbackSnapshotJson;
  transaction_id?: TransactionId;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ActiveModelReport".
 */
export interface ActiveModelReport {
  digest: Digest;
  role_generation: RoleGeneration1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CalibrationReport".
 */
export interface CalibrationReport {
  command_high_water: CommandHighWater;
  revision: Revision;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CandidateAssessment".
 */
export interface CandidateAssessment {
  confidence_accepted: ConfidenceAccepted;
  decision_id: DecisionId2;
  fit_accepted: FitAccepted;
  identifiability_accepted: IdentifiabilityAccepted;
  native_build: CheckStatus;
  native_dry_solve: CheckStatus;
  origin: CandidateOrigin;
  payload_type: PayloadType1;
  policy: ActivationPolicy;
  rejection_reasons: RejectionReasons;
  target_timing: CheckStatus;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CandidateReport".
 */
export interface CandidateReport {
  assessment: CandidateAssessment | null;
  candidate_generation: CandidateGeneration1;
  challenger_id: ChallengerId;
  digest: Digest1;
  fit_quality: FitQuality;
  identifiability: Identifiability;
  lineage: ModelFitLineageReport;
  origin: CandidateOrigin;
  parameter_deltas: ParameterDeltas;
  parameters: GreyParameters | null;
  phase: Phase;
  policy: ActivationPolicy;
  role_generation: RoleGeneration2;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelFitLineageReport".
 */
export interface ModelFitLineageReport {
  candidate_digest: CandidateDigest1;
  candidate_generation: CandidateGeneration2;
  fit_corpus_digest: FitCorpusDigest;
  parent_incumbent_digest: ParentIncumbentDigest;
  parent_incumbent_generation: ParentIncumbentGeneration;
  request_id: RequestId;
  result_status: ResultStatus;
  trigger_origin: CandidateOrigin;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "GreyParameters".
 */
export interface GreyParameters {
  C_c: CC;
  K_Q: KQ;
  T_amb: TAmb;
  h_amb: HAmb;
  n_delay: NDelay;
  sigma: Sigma;
  theta: Theta;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CausalEvaluationProgress".
 */
export interface CausalEvaluationProgress {
  completed_horizons: CompletedHorizons;
  epoch: Epoch;
  pending_origins: PendingOrigins;
  required_horizons: RequiredHorizons;
  required_wins: RequiredWins;
  resumed_from_previous_cook: ResumedFromPreviousCook;
  round: Round;
  wins: Wins;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PendingForecastOriginReport".
 */
export interface PendingForecastOriginReport {
  candidate_digest: CandidateDigest2;
  candidate_generation: CandidateGeneration3;
  horizon_steps: HorizonSteps;
  incumbent_digest: IncumbentDigest1;
  origin_sequence: OriginSequence;
  role_generation: RoleGeneration3;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CommandResponseData".
 */
interface CommandResponseData {}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CorpusStatusReport".
 */
export interface CorpusStatusReport {
  digest: Digest2;
  fit_partition_digest: FitPartitionDigest;
  revision: Revision1;
  slices: Slices;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FitCorpusSliceReport".
 */
export interface FitCorpusSliceReport {
  pre_roll_count: PreRollCount;
  prefix_digest: PrefixDigest;
  scored_count: ScoredCount;
  segment_content_digest: SegmentContentDigest;
  segment_id: SegmentId;
  through_ordinal: ThroughOrdinal;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "EvidenceGate".
 */
export interface EvidenceGate {
  name: Name;
  passed: Passed;
  reason: Reason2;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "EvidenceSummary".
 */
export interface EvidenceSummary {
  audit_count: AuditCount;
  count: Count;
  high_water: HighWater;
  retired_excluded: RetiredExcluded;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FitReport".
 */
export interface FitReport {
  error: Error;
  fit_corpus_digest: FitCorpusDigest1;
  request_id: RequestId1;
  status: FitStatus;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FopdtPidSpParameters".
 */
export interface FopdtPidSpParameters {
  K: K;
  tau: Tau;
  theta: Theta1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FopdtPidSpPredictor".
 */
export interface FopdtPidSpPredictor {
  K: K1;
  form: Form;
  tau: Tau1;
  theta: Theta2;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "IpdtPidSpParameters".
 */
export interface IpdtPidSpParameters {
  K_i: KI;
  c0: C0;
  theta: Theta3;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "IpdtPidSpPredictor".
 */
export interface IpdtPidSpPredictor {
  K_i: KI1;
  c0: C01;
  form: Form1;
  theta: Theta4;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "LearningFailure".
 */
export interface LearningFailure {
  code: Code;
  detail: Detail;
  payload_type?: PayloadType2;
  terminal: Terminal;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelEvidenceReport".
 */
export interface ModelEvidenceReport {
  activation: ActivationReport;
  active_model: ActiveModelReport;
  blockers: Blockers;
  calibration: CalibrationReport;
  candidate: CandidateReport | null;
  checks: Checks;
  corpus: CorpusStatusReport;
  decision_id: DecisionId3;
  errors: Errors;
  evaluation: CausalEvaluationProgress | null;
  evidence: EvidenceSummary;
  failure: LearningFailure | null;
  fit: FitReport;
  gates: Gates;
  identities: ModelIdentities;
  latest_lifecycle: ActivationLifecycle | null;
  mode: CandidateOrigin | null;
  revision: Revision2;
  schema_version: SchemaVersion;
  status: ModelEvidenceStatus;
}
interface Checks {
  [k: string]: CheckStatus | undefined;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelIdentities".
 */
export interface ModelIdentities {
  active_digest: ActiveDigest;
  active_generation: ActiveGeneration;
  candidate_digest: CandidateDigest3;
  candidate_generation: CandidateGeneration4;
  rollback_digest: RollbackDigest;
  rollback_generation: RollbackGeneration;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelRollbackAccepted".
 */
export interface ModelRollbackAccepted {
  accepted: Accepted;
  active_kind: ActiveKind;
  decision_id: DecisionId4;
  reason: Reason3;
  role_generation: RoleGeneration4;
  rollback_digest: RollbackDigest1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelRollbackRejected".
 */
export interface ModelRollbackRejected {
  accepted: Accepted1;
  active_kind: ActiveKind1;
  detail: Detail1;
  error: Error1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelRollbackRequest".
 */
export interface ModelRollbackRequest {
  reason: Reason4;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "MpcCalibrationCommand".
 */
export interface MpcCalibrationCommand {
  action: MpcCalibrationAction;
  ambient_c: AmbientC;
  ambient_source: AmbientSource;
  empty_grill_confirmed: EmptyGrillConfirmed;
  pellets_confirmed: PelletsConfirmed;
  revision: Revision3;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "MpcCalibrationCommandResponse".
 */
export interface MpcCalibrationCommandResponse {
  data?: Data;
  message?: Message;
  result: Result;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "MpcCalibrationCommandResponseData".
 */
export interface MpcCalibrationCommandResponseData {
  mpc_calibration: MpcCalibrationCommand;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpActiveModelReport".
 */
export interface PidSpActiveModelReport {
  form: PidSpDelayProfileForm;
  model_digest: ModelDigest;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpCheckpointBasin".
 */
export interface PidSpCheckpointBasin {
  blockers: Blockers1;
  confidence_lower_s: ConfidenceLowerS;
  confidence_method: ConfidenceMethod;
  confidence_resamples: ConfidenceResamples;
  confidence_upper_s: ConfidenceUpperS;
  episode_count: EpisodeCount;
  interior: Interior;
  lower_s: LowerS;
  representative_s: RepresentativeS;
  upper_s: UpperS;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpCheckpointModel".
 */
export interface PidSpCheckpointModel {
  provenance: Provenance;
  revision: Revision4;
  schema_version: SchemaVersion1;
  selected: PidSpSelectedCheckpoint;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpSelectedCheckpoint".
 */
export interface PidSpSelectedCheckpoint {
  authorized: Authorized;
  common_row_digest: CommonRowDigest;
  comparison_threshold: ComparisonThreshold;
  configuration_digest: ConfigurationDigest;
  confirmation_observed: ConfirmationObserved;
  confirmation_required: ConfirmationRequired;
  delay_basin: PidSpCheckpointBasin;
  episode_ids: EpisodeIds;
  fit_corpus_digest: FitCorpusDigest2;
  fold_losses: FoldLosses;
  form: Form2;
  horizon_losses: HorizonLosses;
  model_digest: ModelDigest1;
  one_step_loss: OneStepLoss;
  parameters: PidSpCheckpointParameters;
  schema_version: SchemaVersion2;
  selection_margin: SelectionMargin;
  standard_error: StandardError;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "SopdtPidSpParameters".
 */
export interface SopdtPidSpParameters {
  K: K2;
  tau_1: Tau11;
  tau_2: Tau2;
  theta: Theta5;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpConfirmationProgress".
 */
export interface PidSpConfirmationProgress {
  observed: Observed;
  required: Required;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpDelayEvidence".
 */
export interface PidSpDelayEvidence {
  authorized: Authorized1;
  blockers: Blockers2;
  completed_episode_count: CompletedEpisodeCount;
  confidence_lower_s: ConfidenceLowerS1;
  confidence_method: PidSpDelayConfidenceMethod | null;
  confidence_resamples: ConfidenceResamples1;
  confidence_upper_s: ConfidenceUpperS1;
  evaluated_bound_s: EvaluatedBoundS;
  profile_form: PidSpDelayProfileForm | null;
  raw_basin_lower_s: RawBasinLowerS;
  raw_basin_representative_s: RawBasinRepresentativeS;
  raw_basin_upper_s: RawBasinUpperS;
  status: PidSpDelayEvidenceStatus;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpFormComparisonReport".
 */
export interface PidSpFormComparisonReport {
  basin_lower_s: BasinLowerS;
  basin_upper_s: BasinUpperS;
  blockers: Blockers3;
  confidence_lower_s: ConfidenceLowerS2;
  confidence_method: PidSpDelayConfidenceMethod | null;
  confidence_upper_s: ConfidenceUpperS2;
  eligible: Eligible;
  fold_losses: FoldLosses1;
  form: PidSpDelayProfileForm;
  horizon_losses: HorizonLosses1;
  one_step_loss: OneStepLoss1;
  standard_error: StandardError1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpHorizonLossReport".
 */
export interface PidSpHorizonLossReport {
  horizon_s: HorizonS;
  loss: Loss;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpIdentifierReport".
 */
export interface PidSpIdentifierReport {
  accepted: FiniteNumber;
  accepted_seconds: FiniteNumber;
  distrust_count: DistrustCount;
  distrust_ratio: FiniteNumber | null;
  duty_segments: DutySegments;
  duty_std: FiniteNumber;
  raw_best_residual: FiniteNumber;
  raw_candidates_passing: RawCandidatesPassing;
  raw_runner_up_residual: FiniteNumber;
  temp_span: FiniteNumber;
  transition_seen: TransitionSeen;
  trusted: PidSpPredictorModel | null;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "SopdtPidSpPredictor".
 */
export interface SopdtPidSpPredictor {
  K: K3;
  form: Form3;
  tau_1: Tau12;
  tau_2: Tau21;
  theta: Theta6;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpLearningFailure".
 */
export interface PidSpLearningFailure {
  code: Code1;
  detail: Detail2;
  terminal: Terminal1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpLearningGate".
 */
export interface PidSpLearningGate {
  name: Name1;
  observed: PidSpGateValue;
  passed: Passed1;
  required: PidSpGateValue;
  unit: Unit;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpLearningReport".
 */
export interface PidSpLearningReport {
  active_model: PidSpActiveModelReport | null;
  checkpoint: PidSpCheckpointModel | null;
  comparison: PidSpModelComparisonReport | null;
  confirmation: PidSpConfirmationProgress | null;
  controller: Controller;
  delay_evidence: PidSpDelayEvidence | null;
  failure: PidSpLearningFailure | null;
  gates: Gates1;
  identifier: PidSpIdentifierReport | null;
  live: Live;
  predictor: PidSpPredictorReport | null;
  revision: Revision5;
  schema_version: SchemaVersion3;
  status: PidSpLearningStatus;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpModelComparisonReport".
 */
export interface PidSpModelComparisonReport {
  best_form: PidSpDelayProfileForm | null;
  comparison_threshold: ComparisonThreshold1;
  confirmation: PidSpConfirmationProgress;
  forms: Forms;
  primary_blocker: PrimaryBlocker;
  selected_form: PidSpDelayProfileForm | null;
  selection_margin: SelectionMargin1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpPredictorReport".
 */
export interface PidSpPredictorReport {
  active: Active;
  disabled: Disabled;
  model: PidSpPredictorModel | null;
  residual_streak: ResidualStreak;
  truncated: Truncated;
  x0: FiniteNumber;
  xd: FiniteNumber;
  z0: FiniteNumber;
  zd: FiniteNumber;
}
