export type ModelEvidenceStatus =
  | "collecting"
  | "insufficient-excitation"
  | "fitting"
  | "evaluating"
  | "ready-for-review"
  | "active"
  | "fallback"
  | "schema-invalidated";

export interface ModelIdentity {
  kind: string;
  digest: string | null;
}

export interface CandidateIdentity extends ModelIdentity {
  generation: number | null;
}

export interface CalibrationEvidence {
  status: string;
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

export interface IdentifiabilityDiagnostics {
  available: boolean;
  accepted: boolean;
  reason: string | null;
  full_rank: boolean;
  finite_diagnostics: boolean;
  pole_magnitude: number | null;
  gain: number | null;
  delay_steps: number | null;
  covariance_finite: boolean;
  alignment_error_c: number | null;
  snapshot_round_trip: boolean;
  sequential_wins: number;
  generation_continuity: boolean;
  atomic_persistence: boolean;
  production_prospective: boolean;
  braking_error_c: number | null;
  incumbent_braking_error_c: number | null;
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
  generation: number;
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
  passed: boolean;
  reason: string | null;
}

export interface TargetTimingEvidence {
  available: boolean;
  sample_count: number;
  p50_ms: number | null;
  p95_ms: number | null;
  p99_ms: number | null;
  hardware_provenance: string | null;
  gate_passed: boolean;
}

export interface ModelEvidenceHistoryEntry {
  evidence_id: string;
  timestamp_ms: number;
  event: "activation" | "rollback" | "fallback";
  decision_id: string | null;
  reason: string | null;
  failed_digest?: string | null;
  failed_generation?: number | null;
  last_safe_command?: number | null;
  fallback_kind?: string | null;
}

export interface EvidenceArtifactMetadata {
  schema_version: number;
  provenance_digest: string | null;
  bootstrap_seed: number;
  bootstrap_replicates: number;
  decision_id: string | null;
  evidence_ids: string[];
}

/** Raw JSON returned by GET /api/model-evidence/report. */
export interface ModelEvidenceReport {
  schema_version: number;
  status: ModelEvidenceStatus;
  decision_id: string | null;
  active_model: ModelIdentity;
  default_model: ModelIdentity;
  candidate: CandidateIdentity;
  calibration: CalibrationEvidence;
  identifiability: IdentifiabilityDiagnostics;
  scores: ModelScore[];
  gates: EvidenceGate[];
  missing_gates: string[];
  blockers: string[];
  target_timing: TargetTimingEvidence;
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

export interface ModelActivationResponse {
  accepted: boolean;
  active_kind: string;
  candidate_digest?: string;
  decision_id: string;
  role_generation: number;
  controller_configuration_digest?: string;
  reason?: string;
}

export interface ModelRollbackRequest {
  reason: string;
}

export type MpcCalibrationAction = "start" | "pause" | "resume" | "stop";
export type AmbientSource = "measured" | "manual" | "weather" | "configured";
export type TemperatureUnit = "F" | "C";

/** UI-domain action. */
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
