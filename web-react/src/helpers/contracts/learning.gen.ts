/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

type DecisionId = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CandidateOrigin".
 */
export type CandidateOrigin = "passive-online" | "operator-calibration" | "cook-refit";
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
export type ActivationPolicy = "passive-auto" | "operator-reviewed" | "cook-refit";
type Reason = string | null;
type ActiveSnapshotJson = string | null;
type CandidateDigest = string | null;
type CandidateGeneration = number | null;
type CandidatePairJson = string | null;
type ControllerConfigurationDigest = string | null;
type EvidenceDecisionId = string | null;
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
type DecisionId1 = string;
type FitAccepted = boolean;
type IdentifiabilityAccepted = boolean;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CheckStatus".
 */
export type CheckStatus = "not-run" | "pending" | "passed" | "failed";
type PayloadType1 = "candidate_assessment";
type RejectionReasons = string[];
type CandidateGeneration1 = number | null;
type Digest1 = string | null;
type FitQuality = number | null;
type Identifiability = number | null;
type ParameterDeltas = {
  [k: string]: (number | null) | undefined;
} | null;
type CC = number;
type KQ = number;
type TAmb = number;
type HAmb = number;
type NDelay = 8;
type Sigma = number;
type Theta = number;
type RoleGeneration2 = number | null;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CookRefitAuthorization".
 */
export type CookRefitAuthorization = "blocked" | "operator-review" | "next-cook";
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CookRefitOutcome".
 */
export type CookRefitOutcome =
  | "disabled"
  | "insufficient"
  | "rejected"
  | "failed"
  | "ready-for-review"
  | "accepted-next-cook"
  | "checkpoint-failure";
type FinalStatus = FitStatus | CookRefitOutcome;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FitStatus".
 */
export type FitStatus = "idle" | "queued" | "running" | "succeeded" | "failed" | "stale";
type NextCook = boolean;
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
type RequestId = string | null;
type WindowId = string | null;
type ConfigurationDigest = string;
type CookId = string | null;
type FirstObservationSequence = number;
type IncumbentDigest = string;
type LastObservationSequence = number;
type RoleGeneration3 = number;
type SessionId = string;
type K = number;
type Form = "fopdt";
type IdentifiedAtF = number | null;
type Revision1 = number;
type Tau = number;
type Theta1 = number;
type K1 = number;
type Form1 = "fopdt";
type Tau1 = number;
type Theta2 = number;
type KI = number;
type C0 = number;
type Form2 = "ipdt";
type IdentifiedAtF1 = number | null;
type Revision2 = number;
type Theta3 = number;
type KI1 = number;
type C01 = number;
type Form3 = "ipdt";
type Theta4 = number;
type Code = string;
type Detail = string;
type PayloadType2 = "learning_failure" | null;
type Terminal = boolean;
type Accepted = false;
type ActiveKind = "grey-box";
type Detail1 = string;
type Error1 = "model-activation-rejected";
type Accepted1 = true;
type CandidateDigest1 = string;
type DecisionId2 = string;
type Phase = "prepared";
type RoleGeneration4 = number;
type TransactionId1 = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelActivationAcknowledgement".
 */
export type ModelActivationAcknowledgement = ModelActivationAccepted | ModelActionRejected;
type CandidateDigest2 = string;
type DecisionId3 = string;
type Blockers = string[];
type DecisionId4 = string | null;
type Errors = string[];
type Gates = EvidenceGate[];
type ActiveDigest = string | null;
type ActiveGeneration = number | null;
type CandidateDigest3 = string | null;
type CandidateGeneration2 = number | null;
type RollbackDigest = string | null;
type RollbackGeneration = number | null;
type Revision3 = string;
type SchemaVersion = 2;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelEvidenceStatus".
 */
export type ModelEvidenceStatus =
  | "collecting"
  | "insufficient-excitation"
  | "fitting"
  | "evaluating"
  | "ready-for-review"
  | "activating"
  | "active"
  | "fallback"
  | "error"
  | "schema-invalidated";
type Accepted2 = true;
type ActiveKind1 = "grey-box";
type DecisionId5 = string;
type Reason3 = string;
type RoleGeneration5 = number;
type RollbackDigest1 = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelRollbackAcknowledgement".
 */
export type ModelRollbackAcknowledgement = ModelRollbackAccepted | ModelActionRejected;
type Reason4 = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "MpcCalibrationAction".
 */
export type MpcCalibrationAction = "start" | "pause" | "resume" | "stop" | "reset-progress";
type AmbientC = number;
type EmptyGrillConfirmed = boolean;
type PelletsConfirmed = boolean;
type Revision4 = number;
type Data = MpcCalibrationCommandResponseData | CommandResponseData | null;
type Message = string;
type Result = "OK" | "ERROR";
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpCheckpointModel".
 */
export type PidSpCheckpointModel = FopdtPidSpCheckpoint | IpdtPidSpCheckpoint;
type Observed = number | null;
type Required = number;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpGateValue".
 */
export type PidSpGateValue = number | boolean;
type CandidatesPassing = number;
type Confirming = number | null;
type DistrustCount = number;
type DutySegments = number;
type TransitionSeen = boolean;
type Code1 = string;
type Detail2 = string;
type Terminal1 = boolean;
type Name1 = string;
type Passed1 = boolean;
type Unit = string | null;
type Controller = "pid_sp";
type Gates1 = PidSpLearningGate[];
type Live = boolean;
type Active = boolean;
type Disabled = boolean;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpPredictorModel".
 */
export type PidSpPredictorModel = FopdtPidSpPredictor | IpdtPidSpPredictor;
type ResidualStreak = number;
type Truncated = number;
type Revision5 = string;
type SchemaVersion1 = 1;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpLearningStatus".
 */
export type PidSpLearningStatus =
  "idle" | "collecting" | "insufficient-excitation" | "evaluating" | "active" | "fallback" | "error";

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
  evidence_decision_id?: EvidenceDecisionId;
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
  decision_id: DecisionId1;
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
  digest: Digest1;
  fit_quality: FitQuality;
  identifiability: Identifiability;
  origin: CandidateOrigin | null;
  parameter_deltas: ParameterDeltas;
  parameters: GreyParameters | null;
  policy: ActivationPolicy | null;
  role_generation: RoleGeneration2;
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
 * via the `definition` "CommandResponseData".
 */
interface CommandResponseData {}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CookRefitReport".
 */
export interface CookRefitReport {
  authorization: CookRefitAuthorization;
  final_status: FinalStatus;
  latest: CookRefitOutcome | null;
  next_cook: NextCook;
  status: FitStatus;
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
  request_id: RequestId;
  status: FitStatus;
  window_id: WindowId;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FitWindowIdentity".
 */
export interface FitWindowIdentity {
  configuration_digest: ConfigurationDigest;
  cook_id: CookId;
  first_observation_sequence: FirstObservationSequence;
  incumbent_digest: IncumbentDigest;
  last_observation_sequence: LastObservationSequence;
  role_generation: RoleGeneration3;
  session_id: SessionId;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FopdtPidSpCheckpoint".
 */
export interface FopdtPidSpCheckpoint {
  K: K;
  form: Form;
  identified_at_f?: IdentifiedAtF;
  revision: Revision1;
  tau: Tau;
  theta: Theta1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FopdtPidSpPredictor".
 */
export interface FopdtPidSpPredictor {
  K: K1;
  form: Form1;
  tau: Tau1;
  theta: Theta2;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "IpdtPidSpCheckpoint".
 */
export interface IpdtPidSpCheckpoint {
  K_i: KI;
  c0: C0;
  form: Form2;
  identified_at_f?: IdentifiedAtF1;
  revision: Revision2;
  theta: Theta3;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "IpdtPidSpPredictor".
 */
export interface IpdtPidSpPredictor {
  K_i: KI1;
  c0: C01;
  form: Form3;
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
 * via the `definition` "ModelActionRejected".
 */
export interface ModelActionRejected {
  accepted: Accepted;
  active_kind: ActiveKind;
  detail: Detail1;
  error: Error1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelActivationAccepted".
 */
export interface ModelActivationAccepted {
  accepted: Accepted1;
  candidate_digest: CandidateDigest1;
  decision_id: DecisionId2;
  phase: Phase;
  role_generation: RoleGeneration4;
  transaction_id: TransactionId1;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelActivationRequest".
 */
export interface ModelActivationRequest {
  candidate_digest: CandidateDigest2;
  decision_id: DecisionId3;
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
  candidate: CandidateReport;
  checks: Checks;
  cook_refit: CookRefitReport;
  decision_id: DecisionId4;
  errors: Errors;
  evidence: EvidenceSummary;
  failure: LearningFailure | null;
  fit: FitReport;
  gates: Gates;
  identities: ModelIdentities;
  latest_lifecycle: ActivationLifecycle | null;
  mode: CandidateOrigin | null;
  revision: Revision3;
  schema_version: SchemaVersion;
  status: ModelEvidenceStatus;
  window: FitWindowIdentity | null;
}
interface Checks {
  [k: string]: CheckStatus;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelIdentities".
 */
export interface ModelIdentities {
  active_digest: ActiveDigest;
  active_generation: ActiveGeneration;
  candidate_digest: CandidateDigest3;
  candidate_generation: CandidateGeneration2;
  rollback_digest: RollbackDigest;
  rollback_generation: RollbackGeneration;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelRollbackAccepted".
 */
export interface ModelRollbackAccepted {
  accepted: Accepted2;
  active_kind: ActiveKind1;
  decision_id: DecisionId5;
  reason: Reason3;
  role_generation: RoleGeneration5;
  rollback_digest: RollbackDigest1;
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
  revision: Revision4;
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
 * via the `definition` "PidSpConfirmationProgress".
 */
export interface PidSpConfirmationProgress {
  observed: Observed;
  required: Required;
}
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpIdentifierReport".
 */
export interface PidSpIdentifierReport {
  accepted: FiniteNumber;
  accepted_seconds: FiniteNumber;
  best_residual: FiniteNumber;
  candidates_passing: CandidatesPassing;
  confirming: Confirming;
  distrust_count: DistrustCount;
  distrust_ratio: FiniteNumber;
  duty_segments: DutySegments;
  duty_std: FiniteNumber;
  runner_up_residual: FiniteNumber;
  temp_span: FiniteNumber;
  transition_seen: TransitionSeen;
  trusted: PidSpCheckpointModel | null;
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
  checkpoint: PidSpCheckpointModel | null;
  confirmation: PidSpConfirmationProgress | null;
  controller: Controller;
  failure: PidSpLearningFailure | null;
  gates: Gates1;
  identifier: PidSpIdentifierReport | null;
  live: Live;
  predictor: PidSpPredictorReport | null;
  revision: Revision5;
  schema_version: SchemaVersion1;
  status: PidSpLearningStatus;
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
}
