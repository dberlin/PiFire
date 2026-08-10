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

export type CandidateOrigin = "passive-online" | "operator-calibration" | "cook-refit";
export type ActivationPolicy = "passive-auto" | "operator-reviewed" | "cook-refit";
export type FitStatus = "idle" | "queued" | "running" | "succeeded" | "failed" | "stale";
export type CheckStatus = "not-run" | "pending" | "passed" | "failed";
export type ActivationPhase = "prepared" | "active" | "aborted";

export interface EvidenceSummary {
  count: number;
  audit_count: number;
  high_water: [number, string] | null;
  retired_excluded: number;
}

export interface FitReport {
  status: FitStatus;
  request_id: string | null;
  window_id: string | null;
  error: string | null;
}

export type CookRefitOutcome =
  | "disabled"
  | "insufficient"
  | "rejected"
  | "failed"
  | "ready-for-review"
  | "accepted-next-cook"
  | "checkpoint-failure";

export type CookRefitAuthorization = "blocked" | "operator-review" | "next-cook";

export interface CookRefitReport {
  status: FitStatus;
  latest: CookRefitOutcome | null;
  final_status: FitStatus | CookRefitOutcome;
  authorization: CookRefitAuthorization;
  next_cook: boolean;
}

export interface FitWindowIdentity {
  session_id: string;
  cook_id: string | null;
  first_observation_sequence: number;
  last_observation_sequence: number;
  configuration_digest: string;
  incumbent_digest: string;
  role_generation: number;
}

export interface GreyParameters {
  C_c: number;
  h_amb: number;
  T_amb: number;
  theta: number;
  n_delay: 8;
  K_Q: number;
  sigma: number;
}

export interface CandidateAssessment {
  decision_id: string;
  origin: CandidateOrigin;
  policy: ActivationPolicy;
  fit_accepted: boolean;
  identifiability_accepted: boolean;
  native_build: CheckStatus;
  native_dry_solve: CheckStatus;
  target_timing: CheckStatus;
  confidence_accepted: boolean;
  rejection_reasons: string[];
  payload_type: "candidate_assessment";
}

export interface CandidateReport {
  digest: string | null;
  origin: CandidateOrigin | null;
  policy: ActivationPolicy | null;
  role_generation: number | null;
  candidate_generation: number | null;
  parameters: GreyParameters | null;
  parameter_deltas: Partial<Record<keyof GreyParameters, number | null>> | null;
  fit_quality: number | null;
  identifiability: number | null;
  assessment: CandidateAssessment | null;
}

/**
 * Exact fields emitted by ModelActivationState plus the report-owned overlays.
 * Durable-state fields are optional because an empty/missing authority still
 * serializes an activation section containing only its phase and pending flags.
 */
export interface ActivationReport {
  active_snapshot_json?: string;
  rollback_snapshot_json?: string;
  evidence_decision_id?: string;
  controller_configuration_digest?: string;
  role_generation?: number;
  transaction_id?: string | null;
  incumbent_pair_json?: string | null;
  candidate_pair_json?: string | null;
  rollback_pair_json?: string | null;
  origin?: CandidateOrigin | null;
  policy?: ActivationPolicy | null;
  candidate_generation?: number | null;
  candidate_digest?: string | null;
  phase: ActivationPhase;
  reason: string | null;
  pending_persistence: boolean;
  pending_frame_boundary_swap: boolean;
}

export interface ActiveModelReport {
  digest: string | null;
  role_generation: number | null;
}

export interface ModelIdentities {
  active_digest: string | null;
  active_generation: number | null;
  candidate_digest: string | null;
  candidate_generation: number | null;
  rollback_digest: string | null;
  rollback_generation: number | null;
}

export interface CalibrationReport {
  revision: number;
  command_high_water: number;
}

export interface ActivationLifecycle {
  decision_id: string;
  phase: ActivationPhase;
  origin: CandidateOrigin;
  policy: ActivationPolicy;
  reason: string | null;
  payload_type: "activation_lifecycle";
}

export interface LearningFailure {
  code: string;
  detail: string;
  terminal: boolean;
  payload_type?: "learning_failure";
}

export interface EvidenceGate {
  name: string;
  passed: boolean;
  reason: string | null;
}

/** Exact schema-v2 JSON returned by GET /api/model-evidence/report. */
export interface ModelEvidenceReport {
  schema_version: 2;
  status: ModelEvidenceStatus;
  mode: CandidateOrigin | null;
  decision_id: string | null;
  evidence: EvidenceSummary;
  fit: FitReport;
  cook_refit: CookRefitReport;
  window: FitWindowIdentity | null;
  checks: Record<string, CheckStatus>;
  candidate: CandidateReport;
  activation: ActivationReport;
  active_model: ActiveModelReport;
  identities: ModelIdentities;
  calibration: CalibrationReport;
  latest_lifecycle: ActivationLifecycle | null;
  failure: LearningFailure | null;
  gates: EvidenceGate[];
  blockers: string[];
  errors: string[];
  revision: string;
}

export interface ModelEvidenceResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
}

export interface ModelActivationRequest {
  candidate_digest: string;
  decision_id: string;
}

export interface ModelActivationAccepted {
  accepted: true;
  phase: "prepared";
  transaction_id: string;
  decision_id: string;
  candidate_digest: string;
  role_generation: number;
}

export interface ModelActionRejected {
  accepted: false;
  active_kind: "grey-box";
  error: "model-activation-rejected";
  detail: string;
}

export type ModelActivationAcknowledgement = ModelActivationAccepted | ModelActionRejected;

export interface ModelRollbackRequest {
  reason: string;
}

export interface ModelRollbackAccepted {
  accepted: true;
  active_kind: "grey-box";
  decision_id: string;
  reason: string;
  role_generation: number;
  rollback_digest: string;
}

export type ModelRollbackAcknowledgement = ModelRollbackAccepted | ModelActionRejected;

export type MpcCalibrationAction = "start" | "pause" | "resume" | "stop" | "reset-progress";
export type AmbientSource = "measured" | "manual" | "weather" | "configured";
export type TemperatureUnit = "F" | "C";

/** UI-domain calibration intent. */
export interface MpcCalibrationRequest {
  action: MpcCalibrationAction;
  revision: number;
  ambient_c: number;
  ambient_source: AmbientSource;
  empty_grill_confirmed: boolean;
  pellets_confirmed: boolean;
}

/** Exact revisioned body accepted by POST /api/set_mpc_calibration. */
export interface MpcCalibrationCommand {
  action: MpcCalibrationAction;
  revision: number;
  ambient_c: number;
  ambient_source: AmbientSource;
  empty_grill_confirmed: boolean;
  pellets_confirmed: boolean;
}
