/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

export type DecisionId = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CandidateOrigin".
 */
export type CandidateOrigin = "passive-online" | "operator-calibration" | "cook-refit";
export type PayloadType = "activation_lifecycle";
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
export type Reason = string | null;
export type ActiveSnapshotJson = string | null;
export type CandidateDigest = string | null;
export type CandidateGeneration = number | null;
export type CandidatePairJson = string | null;
export type ControllerConfigurationDigest = string | null;
export type EvidenceDecisionId = string | null;
export type IncumbentPairJson = string | null;
export type PendingFrameBoundarySwap = boolean;
export type PendingPersistence = boolean;
export type Reason1 = string | null;
export type RoleGeneration = number | null;
export type RollbackPairJson = string | null;
export type RollbackSnapshotJson = string | null;
export type TransactionId = string | null;
export type Digest = string | null;
export type RoleGeneration1 = number | null;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "AmbientSource".
 */
export type AmbientSource = "measured" | "manual" | "weather" | "configured";
export type CommandHighWater = number;
export type Revision = number;
export type ConfidenceAccepted = boolean;
export type DecisionId1 = string;
export type FitAccepted = boolean;
export type IdentifiabilityAccepted = boolean;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "CheckStatus".
 */
export type CheckStatus = "not-run" | "pending" | "passed" | "failed";
export type PayloadType1 = "candidate_assessment";
export type RejectionReasons = string[];
export type CandidateGeneration1 = number | null;
export type Digest1 = string | null;
export type FitQuality = number | null;
export type Identifiability = number | null;
export type ParameterDeltas = {
  [k: string]: (number | null) | undefined;
} | null;
export type CC = number;
export type KQ = number;
export type TAmb = number;
export type HAmb = number;
export type NDelay = 8;
export type Sigma = number;
export type Theta = number;
export type RoleGeneration2 = number | null;
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
export type FinalStatus = FitStatus | CookRefitOutcome;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FitStatus".
 */
export type FitStatus = "idle" | "queued" | "running" | "succeeded" | "failed" | "stale";
export type NextCook = boolean;
export type Name = string;
export type Passed = boolean;
export type Reason2 = string | null;
export type AuditCount = number;
export type Count = number;
export type HighWater = [unknown, unknown] | null;
export type RetiredExcluded = number;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "FiniteNumber".
 */
export type FiniteNumber = number;
export type Error = string | null;
export type RequestId = string | null;
export type WindowId = string | null;
export type ConfigurationDigest = string;
export type CookId = string | null;
export type FirstObservationSequence = number;
export type IncumbentDigest = string;
export type LastObservationSequence = number;
export type RoleGeneration3 = number;
export type SessionId = string;
export type K = number;
export type Form = "fopdt";
export type IdentifiedAtF = number | null;
export type Revision1 = number;
export type Tau = number;
export type Theta1 = number;
export type K1 = number;
export type Form1 = "fopdt";
export type Tau1 = number;
export type Theta2 = number;
export type KI = number;
export type C0 = number;
export type Form2 = "ipdt";
export type IdentifiedAtF1 = number | null;
export type Revision2 = number;
export type Theta3 = number;
export type KI1 = number;
export type C01 = number;
export type Form3 = "ipdt";
export type Theta4 = number;
export type Code = string;
export type Detail = string;
export type PayloadType2 = "learning_failure" | null;
export type Terminal = boolean;
export type Accepted = false;
export type ActiveKind = "grey-box";
export type Detail1 = string;
export type Error1 = "model-activation-rejected";
export type Accepted1 = true;
export type CandidateDigest1 = string;
export type DecisionId2 = string;
export type Phase = "prepared";
export type RoleGeneration4 = number;
export type TransactionId1 = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelActivationAcknowledgement".
 */
export type ModelActivationAcknowledgement = ModelActivationAccepted | ModelActionRejected;
export type CandidateDigest2 = string;
export type DecisionId3 = string;
export type Blockers = string[];
export type DecisionId4 = string | null;
export type Errors = string[];
export type Gates = EvidenceGate[];
export type ActiveDigest = string | null;
export type ActiveGeneration = number | null;
export type CandidateDigest3 = string | null;
export type CandidateGeneration2 = number | null;
export type RollbackDigest = string | null;
export type RollbackGeneration = number | null;
export type Revision3 = string;
export type SchemaVersion = 2;
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
export type Accepted2 = true;
export type ActiveKind1 = "grey-box";
export type DecisionId5 = string;
export type Reason3 = string;
export type RoleGeneration5 = number;
export type RollbackDigest1 = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "ModelRollbackAcknowledgement".
 */
export type ModelRollbackAcknowledgement = ModelRollbackAccepted | ModelActionRejected;
export type Reason4 = string;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "MpcCalibrationAction".
 */
export type MpcCalibrationAction = "start" | "pause" | "resume" | "stop" | "reset-progress";
export type AmbientC = number;
export type EmptyGrillConfirmed = boolean;
export type PelletsConfirmed = boolean;
export type Revision4 = number;
export type Data = MpcCalibrationCommandResponseData | CommandResponseData | null;
export type Message = string;
export type Result = "OK" | "ERROR";
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpCheckpointModel".
 */
export type PidSpCheckpointModel = FopdtPidSpCheckpoint | IpdtPidSpCheckpoint;
export type Observed = number | null;
export type Required = number;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpGateValue".
 */
export type PidSpGateValue = number | boolean;
export type CandidatesPassing = number;
export type Confirming = number | null;
export type DistrustCount = number;
export type DutySegments = number;
export type TransitionSeen = boolean;
export type Code1 = string;
export type Detail2 = string;
export type Terminal1 = boolean;
export type Name1 = string;
export type Passed1 = boolean;
export type Unit = string | null;
export type Controller = "pid_sp";
export type Gates1 = PidSpLearningGate[];
export type Live = boolean;
export type Active = boolean;
export type Disabled = boolean;
/**
 * This interface was referenced by `PiFireLearningWebContracts`'s JSON-Schema
 * via the `definition` "PidSpPredictorModel".
 */
export type PidSpPredictorModel = FopdtPidSpPredictor | IpdtPidSpPredictor;
export type ResidualStreak = number;
export type Truncated = number;
export type Revision5 = string;
export type SchemaVersion1 = 1;
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
export interface CommandResponseData {}
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
export interface Checks {
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
