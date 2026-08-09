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

export type LearningMode = "passive" | "calibration" | "cook-refit";
export type CandidateOrigin = "passive-online" | "operator-calibration" | "cook-refit";
export type FitStatus = "idle" | "queued" | "running" | "succeeded" | "failed" | "stale";
export type EvidenceCheckStatus = "not-run" | "pending" | "passed" | "failed";
export type ActivationPolicy = "passive-auto" | "operator-reviewed" | "cook-refit";
export type ActivationReason = "passive-auto" | "operator-reviewed" | "cook-refit";

/** Every model role uses this identity shape; there is no state-space special case. */
export interface ModelIdentity {
  kind: string;
  digest: string | null;
  model_schema: number | null;
  role_generation: number | null;
  candidate_generation: number | null;
}

export interface ObservationRejectionReason {
  reason: string;
  count: number;
}

export interface ObservationEligibility {
  window_id: string | null;
  eligible_count: number;
  ineligible_count: number;
  rejection_reasons: ObservationRejectionReason[];
  probe_provenance: string;
  mixed_window_authority: CandidateOrigin | null;
}

export type CalibrationStatus =
  | "inactive"
  | "idle"
  | "active"
  | "running"
  | "paused"
  | "accepted"
  | "completed"
  | "cancelled"
  | "timed-out"
  | "failed";

export interface CalibrationEvidence {
  status: CalibrationStatus;
  stage: string | null;
  current_probe: number | null;
  completed_stages: string[];
  missing_stages: string[];
  eligible_count: number;
  ineligible_count: number;
  ineligible_reasons: string[];
  timed_out: boolean;
  incomplete: boolean;
  revision: number;
}

export interface FitEvidenceWindow {
  window_id: string;
  session_id: string | null;
  cook_id: string | null;
  sample_count: number;
  config_digest: string;
  incumbent_digest: string;
  started_at_ms: number;
  ended_at_ms: number;
}

export interface FitEvidenceResult {
  reason: string;
  solver_iterations: number | null;
  finished_at_ms: number | null;
}

export interface FitEvidence {
  status: FitStatus;
  job_id: string | null;
  process_id: number | null;
  role_generation: number;
  origin: CandidateOrigin;
  window: FitEvidenceWindow | null;
  result: FitEvidenceResult | null;
}

export interface GreyParameterDelta {
  name: string;
  unit: string;
  incumbent_value: number | null;
  candidate_value: number | null;
  delta: number | null;
}

export interface CandidateStructure {
  prediction_step_seconds: number;
  delay_states: number;
  horizon_steps: number;
}

export interface ParameterInterval {
  lower: number;
  upper: number;
}

export interface PhysicalBoundsEvidence {
  status: EvidenceCheckStatus;
  detail: string | null;
}

export interface IdentifiabilityDiagnostics {
  status: EvidenceCheckStatus;
  reason: string | null;
  matrix_rank: number | null;
  parameter_count: number;
  condition_number: number | null;
  finite_diagnostics: boolean;
  confidence_intervals: Record<string, ParameterInterval> | null;
  physical_bounds: PhysicalBoundsEvidence;
}

export interface NativeBuildEvidence {
  status: EvidenceCheckStatus;
  build_digest: string | null;
  manifest_digest: string | null;
  detail: string | null;
}

export interface NativeDrySolveEvidence {
  status: EvidenceCheckStatus;
  solve_time_ms: number | null;
  finite_diagnostics: boolean;
  detail: string | null;
}

export interface NativeCandidateEvidence {
  build: NativeBuildEvidence;
  dry_solve: NativeDrySolveEvidence;
}

export interface BootstrapEvidence {
  available: boolean;
  method: string;
  replicate_count: number;
  rmse_ratio_upper_bound: number | null;
}

export interface ModelScore {
  horizon_steps: number;
  temperature_band: string;
  phase: string;
  ambient_source: string;
  candidate_generation: number;
  challenger_rmse_c: number | null;
  incumbent_rmse_c: number | null;
  challenger_bias_c: number | null;
  incumbent_bias_c: number | null;
  challenger_band_error_c: number | null;
  incumbent_band_error_c: number | null;
  bootstrap: BootstrapEvidence;
}

export interface EvidenceGate {
  name: string;
  status: EvidenceCheckStatus;
  reason: string | null;
}

export type ActivationPersistencePhase = "prepared" | "active" | "aborted" | null;

export interface ActivationPersistence {
  status: EvidenceCheckStatus;
  phase: ActivationPersistencePhase;
  record_id: string | null;
  detail: string | null;
}

export interface PendingModelSwap {
  status: EvidenceCheckStatus;
  frame_boundary: number | null;
  detail: string | null;
}

export interface ActivationEvidence {
  policy: ActivationPolicy;
  reason: ActivationReason;
  decision_id: string | null;
  persistence: ActivationPersistence;
  pending_swap: PendingModelSwap;
}

export interface RollbackEvidence {
  permitted: boolean;
  confidence_window_remaining: number;
  latest_reason: string | null;
}

export type CookRefitStatus =
  | "not-run"
  | "disabled"
  | "queued"
  | "running"
  | "accepted"
  | "rejected"
  | "failed"
  | "stale";

export interface CookRefitEvidence {
  authorized: boolean;
  status: CookRefitStatus;
  outcome: string | null;
  activation_timing: "next-cook-restore" | null;
}

export interface TargetTimingEvidence {
  available: boolean;
  sample_count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  hardware_provenance: string | null;
  status: EvidenceCheckStatus;
}

export interface LearningLifecycleEntry {
  phase: string;
  timestamp_ms: number;
  reason: string | null;
  role_generation: number;
  candidate_generation: number | null;
}

export interface LearningReportError {
  code: string;
  message: string;
  phase: string;
  retryable: boolean;
  timestamp_ms: number;
}

export interface ModelEvidenceHistoryEntry {
  evidence_id: string;
  timestamp_ms: number;
  event: "activation" | "rejection" | "fallback" | "interrupted-activation" | "rollback";
  decision_id: string | null;
  reason: string | null;
  role_generation: number | null;
  candidate_generation: number | null;
}

export interface EvidenceArtifactMetadata {
  schema_version: number;
  provenance_digest: string | null;
  bootstrap_seed: number;
  bootstrap_replicates: number;
  decision_id: string | null;
  evidence_ids: string[];
}

/** Raw schema-v2 JSON returned by GET /api/model-evidence/report. */
export interface ModelEvidenceReport {
  schema_version: 2;
  status: ModelEvidenceStatus;
  mode: LearningMode;
  origin: CandidateOrigin;
  role_generation: number;
  candidate_generation: number | null;
  decision_id: string | null;
  enable_online_adaptation: boolean;
  enable_identification: boolean;
  active_model: ModelIdentity;
  default_model: ModelIdentity;
  candidate: ModelIdentity;
  rollback_owner: ModelIdentity | null;
  observation: ObservationEligibility;
  calibration: CalibrationEvidence;
  fit: FitEvidence;
  grey_parameters: GreyParameterDelta[];
  candidate_structure: CandidateStructure;
  identifiability: IdentifiabilityDiagnostics;
  native: NativeCandidateEvidence;
  scores: ModelScore[];
  gates: EvidenceGate[];
  missing_gates: string[];
  blockers: string[];
  activation: ActivationEvidence;
  rollback: RollbackEvidence;
  cook_refit: CookRefitEvidence;
  target_timing: TargetTimingEvidence;
  lifecycle: LearningLifecycleEntry[];
  errors: LearningReportError[];
  history: ModelEvidenceHistoryEntry[];
  ambient_provenance_limitation: string | null;
  artifact_metadata: EvidenceArtifactMetadata;
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

export interface ModelActivationAcknowledgement {
  accepted: boolean;
  acknowledgement: string;
  detail?: string | null;
}

export interface ModelRollbackRequest {
  reason: string;
}

export interface ModelRollbackAcknowledgement {
  accepted: boolean;
  acknowledgement: string;
  detail?: string | null;
}

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
