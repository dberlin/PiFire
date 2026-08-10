export type PidSpLearningStatus =
  | "idle"
  | "collecting"
  | "insufficient-excitation"
  | "evaluating"
  | "active"
  | "fallback"
  | "error";

export interface FopdtPidSpModel {
  form: "fopdt";
  K: number;
  tau: number;
  theta: number;
  revision: number;
  identified_at_f?: number;
}

export interface IpdtPidSpModel {
  form: "ipdt";
  K_i: number;
  c0: number;
  theta: number;
  revision: number;
  identified_at_f?: number;
}

export type PidSpModel = FopdtPidSpModel | IpdtPidSpModel;

export interface FopdtPidSpPredictorModel {
  form: "fopdt";
  K: number;
  tau: number;
  theta: number;
}

export interface IpdtPidSpPredictorModel {
  form: "ipdt";
  K_i: number;
  c0: number;
  theta: number;
}

export type PidSpPredictorModel = FopdtPidSpPredictorModel | IpdtPidSpPredictorModel;
export type PidSpGateValue = number | boolean;

export interface PidSpLearningGate {
  name: string;
  passed: boolean;
  observed: PidSpGateValue;
  required: PidSpGateValue;
  unit: string | null;
}
export interface PidSpConfirmationProgress {
  observed: number | null;
  required: number;
}

export interface PidSpIdentifierReport {
  accepted: number;
  accepted_seconds: number;
  duty_std: number;
  temp_span: number;
  transition_seen: boolean;
  duty_segments: number;
  best_residual: number;
  runner_up_residual: number;
  candidates_passing: number;
  confirming: number | null;
  trusted: PidSpModel | null;
  distrust_count: number;
  distrust_ratio: number;
}

export interface PidSpPredictorReport {
  active: boolean;
  disabled: boolean;
  x0: number;
  xd: number;
  residual_streak: number;
  truncated: number;
  model: PidSpPredictorModel | null;
}

export interface PidSpLearningFailure {
  code: string;
  detail: string;
  terminal: boolean;
}

/** Exact schema-v1 JSON returned by GET /api/pid-sp-learning/report. */
export interface PidSpLearningReport {
  schema_version: 1;
  controller: "pid_sp";
  status: PidSpLearningStatus;
  live: boolean;
  revision: string;
  gates: PidSpLearningGate[];
  confirmation: PidSpConfirmationProgress | null;
  identifier: PidSpIdentifierReport | null;
  predictor: PidSpPredictorReport | null;
  checkpoint: PidSpModel | null;
  failure: PidSpLearningFailure | null;
}

export interface PidSpLearningResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
}
