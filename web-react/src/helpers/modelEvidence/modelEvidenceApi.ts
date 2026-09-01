import type {
  ModelEvidenceReport,
  ModelRollbackAcknowledgement,
  ModelRollbackRequest,
  MpcCalibrationCommand,
  MpcCalibrationCommandResponse,
} from "@pifire/core/contracts/learning";

export interface ModelEvidenceResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
}

const DEFAULT_BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

const endpoint = (baseUrl: string, path: string) => `${baseUrl}/api/${path}`;

type UnknownRecord = Record<string, unknown>;

const ORIGINS = ["passive-online", "operator-calibration"] as const;
const POLICIES = ["causal-auto"] as const;
const REPORT_STATUSES = [
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
] as const;
const FIT_STATUSES = ["idle", "queued", "running", "succeeded", "failed", "stale"] as const;
const CHECK_STATUSES = ["not-run", "pending", "passed", "failed"] as const;
const ACTIVATION_PHASES = ["prepared", "active", "aborted"] as const;
const CANDIDATE_PHASES = ["built", "evaluating", "qualified", "activating"] as const;

function invalidReport(detail: string): never {
  throw new Error(`Invalid model evidence report: ${detail}`);
}

function record(value: unknown, path: string): UnknownRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return invalidReport(`${path} must be an object`);
  }
  return value as UnknownRecord;
}

function exactKeys(value: UnknownRecord, expected: readonly string[], path: string) {
  const keys = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (keys.length !== wanted.length || keys.some((key, index) => key !== wanted[index])) {
    invalidReport(`${path} has an invalid shape`);
  }
}

function allowedKeys(
  value: UnknownRecord,
  required: readonly string[],
  allowed: readonly string[],
  path: string,
) {
  if (
    required.some((key) => !(key in value)) ||
    Object.keys(value).some((key) => !allowed.includes(key))
  ) {
    invalidReport(`${path} has an invalid shape`);
  }
}

function stringValue(value: unknown, path: string): string {
  if (typeof value !== "string") return invalidReport(`${path} must be a string`);
  return value;
}

function nonBlankString(value: unknown, path: string): string {
  const parsed = stringValue(value, path);
  if (parsed.length === 0) return invalidReport(`${path} must be non-blank`);
  return parsed;
}

function finiteNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return invalidReport(`${path} must be a finite number`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, path: string): number {
  const parsed = finiteNumber(value, path);
  if (!Number.isInteger(parsed) || parsed < 0) {
    return invalidReport(`${path} must be a non-negative integer`);
  }
  return parsed;
}

function booleanValue(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") return invalidReport(`${path} must be a boolean`);
  return value;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], path: string): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    return invalidReport(`${path} has an invalid value`);
  }
  return value as T;
}

function digest(value: unknown, path: string): string {
  const parsed = stringValue(value, path);
  if (!/^[0-9a-f]{64}$/.test(parsed)) return invalidReport(`${path} must be a SHA-256 digest`);
  return parsed;
}

function nullable(value: unknown, validate: (item: unknown) => unknown) {
  if (value !== null) validate(value);
}

function stringArray(value: unknown, path: string) {
  if (!Array.isArray(value)) return invalidReport(`${path} must be an array`);
  value.forEach((item, index) => {
    stringValue(item, `${path}[${index}]`);
  });
}

function nonNegativeIntegerArray(value: unknown, path: string) {
  if (!Array.isArray(value)) return invalidReport(`${path} must be an array`);
  value.forEach((item, index) => {
    nonNegativeInteger(item, `${path}[${index}]`);
  });
}

function validateEvidence(value: unknown) {
  const source = record(value, "evidence");
  exactKeys(source, ["count", "audit_count", "high_water", "retired_excluded"], "evidence");
  nonNegativeInteger(source.count, "evidence.count");
  nonNegativeInteger(source.audit_count, "evidence.audit_count");
  nonNegativeInteger(source.retired_excluded, "evidence.retired_excluded");
  if (source.high_water !== null) {
    if (!Array.isArray(source.high_water) || source.high_water.length !== 2) {
      invalidReport("evidence.high_water must be a two-item tuple");
    }
    nonNegativeInteger(source.high_water[0], "evidence.high_water[0]");
    stringValue(source.high_water[1], "evidence.high_water[1]");
  }
}

function validateFit(value: unknown, path: string) {
  const source = record(value, path);
  exactKeys(source, ["status", "request_id", "fit_corpus_digest", "error"], path);
  oneOf(source.status, FIT_STATUSES, `${path}.status`);
  nullable(source.request_id, (item) => stringValue(item, `${path}.request_id`));
  nullable(source.fit_corpus_digest, (item) => digest(item, `${path}.fit_corpus_digest`));
  nullable(source.error, (item) => stringValue(item, `${path}.error`));
}

function validateParameters(value: unknown) {
  const source = record(value, "candidate.parameters");
  exactKeys(
    source,
    ["C_c", "h_amb", "T_amb", "theta", "n_delay", "K_Q", "sigma"],
    "candidate.parameters",
  );
  finiteNumber(source.C_c, "candidate.parameters.C_c");
  finiteNumber(source.h_amb, "candidate.parameters.h_amb");
  finiteNumber(source.T_amb, "candidate.parameters.T_amb");
  finiteNumber(source.theta, "candidate.parameters.theta");
  if (source.n_delay !== 8) invalidReport("candidate.parameters.n_delay must equal 8");
  finiteNumber(source.K_Q, "candidate.parameters.K_Q");
  finiteNumber(source.sigma, "candidate.parameters.sigma");
}

function validateAssessment(value: unknown) {
  if (value === null) return;
  const source = record(value, "candidate.assessment");
  exactKeys(
    source,
    [
      "decision_id",
      "origin",
      "policy",
      "fit_accepted",
      "identifiability_accepted",
      "native_build",
      "native_dry_solve",
      "target_timing",
      "confidence_accepted",
      "rejection_reasons",
      "payload_type",
    ],
    "candidate.assessment",
  );
  nonBlankString(source.decision_id, "candidate.assessment.decision_id");
  oneOf(source.origin, ORIGINS, "candidate.assessment.origin");
  oneOf(source.policy, POLICIES, "candidate.assessment.policy");
  booleanValue(source.fit_accepted, "candidate.assessment.fit_accepted");
  booleanValue(source.identifiability_accepted, "candidate.assessment.identifiability_accepted");
  oneOf(source.native_build, CHECK_STATUSES, "candidate.assessment.native_build");
  oneOf(source.native_dry_solve, CHECK_STATUSES, "candidate.assessment.native_dry_solve");
  oneOf(source.target_timing, CHECK_STATUSES, "candidate.assessment.target_timing");
  booleanValue(source.confidence_accepted, "candidate.assessment.confidence_accepted");
  stringArray(source.rejection_reasons, "candidate.assessment.rejection_reasons");
  if (source.payload_type !== "candidate_assessment") {
    invalidReport("candidate.assessment.payload_type has an invalid value");
  }
}

function validateCandidate(value: unknown) {
  if (value === null) return;
  const source = record(value, "candidate");
  exactKeys(
    source,
    [
      "challenger_id",
      "phase",
      "digest",
      "origin",
      "policy",
      "role_generation",
      "candidate_generation",
      "parameters",
      "parameter_deltas",
      "fit_quality",
      "identifiability",
      "assessment",
      "lineage",
    ],
    "candidate",
  );
  nonBlankString(source.challenger_id, "candidate.challenger_id");
  oneOf(source.phase, CANDIDATE_PHASES, "candidate.phase");
  digest(source.digest, "candidate.digest");
  oneOf(source.origin, ORIGINS, "candidate.origin");
  oneOf(source.policy, POLICIES, "candidate.policy");
  nonNegativeInteger(source.role_generation, "candidate.role_generation");
  nonNegativeInteger(source.candidate_generation, "candidate.candidate_generation");
  nullable(source.parameters, validateParameters);
  if (source.parameter_deltas !== null) {
    const deltas = record(source.parameter_deltas, "candidate.parameter_deltas");
    for (const [name, item] of Object.entries(deltas)) {
      nullable(item, (entry) => finiteNumber(entry, `candidate.parameter_deltas.${name}`));
    }
  }
  nullable(source.fit_quality, (item) => finiteNumber(item, "candidate.fit_quality"));
  nullable(source.identifiability, (item) => finiteNumber(item, "candidate.identifiability"));
  validateAssessment(source.assessment);

  const lineage = record(source.lineage, "candidate.lineage");
  exactKeys(
    lineage,
    [
      "request_id",
      "parent_incumbent_digest",
      "parent_incumbent_generation",
      "candidate_generation",
      "fit_corpus_digest",
      "trigger_origin",
      "result_status",
      "candidate_digest",
    ],
    "candidate.lineage",
  );
  nonBlankString(lineage.request_id, "candidate.lineage.request_id");
  digest(lineage.parent_incumbent_digest, "candidate.lineage.parent_incumbent_digest");
  nonNegativeInteger(
    lineage.parent_incumbent_generation,
    "candidate.lineage.parent_incumbent_generation",
  );
  nonNegativeInteger(lineage.candidate_generation, "candidate.lineage.candidate_generation");
  digest(lineage.fit_corpus_digest, "candidate.lineage.fit_corpus_digest");
  oneOf(lineage.trigger_origin, ORIGINS, "candidate.lineage.trigger_origin");
  if (lineage.result_status !== "succeeded") {
    invalidReport("candidate.lineage.result_status has an invalid value");
  }
  digest(lineage.candidate_digest, "candidate.lineage.candidate_digest");
}

function validateEvaluation(value: unknown) {
  if (value === null) return;
  const source = record(value, "evaluation");
  exactKeys(
    source,
    [
      "epoch",
      "round",
      "completed_horizons",
      "required_horizons",
      "wins",
      "required_wins",
      "resumed_from_previous_cook",
      "pending_origins",
    ],
    "evaluation",
  );
  nonNegativeInteger(source.epoch, "evaluation.epoch");
  nonNegativeInteger(source.round, "evaluation.round");
  nonNegativeIntegerArray(source.completed_horizons, "evaluation.completed_horizons");
  nonNegativeIntegerArray(source.required_horizons, "evaluation.required_horizons");
  nonNegativeInteger(source.wins, "evaluation.wins");
  nonNegativeInteger(source.required_wins, "evaluation.required_wins");
  booleanValue(source.resumed_from_previous_cook, "evaluation.resumed_from_previous_cook");
  if (!Array.isArray(source.pending_origins)) {
    invalidReport("evaluation.pending_origins must be an array");
  }
  source.pending_origins.forEach((value, index) => {
    const path = `evaluation.pending_origins[${index}]`;
    const pending = record(value, path);
    exactKeys(
      pending,
      [
        "origin_sequence",
        "horizon_steps",
        "role_generation",
        "candidate_generation",
        "incumbent_digest",
        "candidate_digest",
      ],
      path,
    );
    nonNegativeInteger(pending.origin_sequence, `${path}.origin_sequence`);
    nonNegativeInteger(pending.horizon_steps, `${path}.horizon_steps`);
    nonNegativeInteger(pending.role_generation, `${path}.role_generation`);
    nonNegativeInteger(pending.candidate_generation, `${path}.candidate_generation`);
    digest(pending.incumbent_digest, `${path}.incumbent_digest`);
    digest(pending.candidate_digest, `${path}.candidate_digest`);
  });
}

function validateCorpus(value: unknown) {
  const source = record(value, "corpus");
  exactKeys(source, ["digest", "revision", "fit_partition_digest", "slices"], "corpus");
  nullable(source.digest, (item) => digest(item, "corpus.digest"));
  nullable(source.revision, (item) => nonNegativeInteger(item, "corpus.revision"));
  nullable(source.fit_partition_digest, (item) => digest(item, "corpus.fit_partition_digest"));
  if (!Array.isArray(source.slices)) invalidReport("corpus.slices must be an array");
  source.slices.forEach((value, index) => {
    const path = `corpus.slices[${index}]`;
    const slice = record(value, path);
    exactKeys(
      slice,
      [
        "segment_id",
        "through_ordinal",
        "prefix_digest",
        "segment_content_digest",
        "pre_roll_count",
        "scored_count",
      ],
      path,
    );
    nonBlankString(slice.segment_id, `${path}.segment_id`);
    nonNegativeInteger(slice.through_ordinal, `${path}.through_ordinal`);
    digest(slice.prefix_digest, `${path}.prefix_digest`);
    nullable(slice.segment_content_digest, (item) =>
      digest(item, `${path}.segment_content_digest`),
    );
    nonNegativeInteger(slice.pre_roll_count, `${path}.pre_roll_count`);
    nonNegativeInteger(slice.scored_count, `${path}.scored_count`);
  });
}

function validateActivation(value: unknown) {
  const source = record(value, "activation");
  const allowed = [
    "active_snapshot_json",
    "rollback_snapshot_json",
    "evidence_decision_id",
    "decision_id",
    "controller_configuration_digest",
    "role_generation",
    "transaction_id",
    "incumbent_pair_json",
    "candidate_pair_json",
    "rollback_pair_json",
    "origin",
    "policy",
    "candidate_generation",
    "candidate_digest",
    "incumbent_digest",
    "phase",
    "reason",
    "pending_persistence",
    "pending_frame_boundary_swap",
  ] as const;
  allowedKeys(
    source,
    ["phase", "reason", "pending_persistence", "pending_frame_boundary_swap"],
    allowed,
    "activation",
  );
  for (const key of [
    "active_snapshot_json",
    "rollback_snapshot_json",
    "evidence_decision_id",
    "controller_configuration_digest",
    "transaction_id",
    "incumbent_pair_json",
    "candidate_pair_json",
    "rollback_pair_json",
  ] as const) {
    if (key in source) nullable(source[key], (item) => stringValue(item, `activation.${key}`));
  }
  if ("decision_id" in source) {
    nullable(source.decision_id, (item) => nonBlankString(item, "activation.decision_id"));
  }
  for (const key of ["role_generation", "candidate_generation"] as const) {
    if (key in source) {
      nullable(source[key], (item) => nonNegativeInteger(item, `activation.${key}`));
    }
  }
  for (const key of ["candidate_digest", "incumbent_digest"] as const) {
    if (key in source) nullable(source[key], (item) => digest(item, `activation.${key}`));
  }
  if ("origin" in source)
    nullable(source.origin, (item) => oneOf(item, ORIGINS, "activation.origin"));
  if ("policy" in source)
    nullable(source.policy, (item) => oneOf(item, POLICIES, "activation.policy"));
  oneOf(source.phase, ACTIVATION_PHASES, "activation.phase");
  nullable(source.reason, (item) => stringValue(item, "activation.reason"));
  booleanValue(source.pending_persistence, "activation.pending_persistence");
  booleanValue(source.pending_frame_boundary_swap, "activation.pending_frame_boundary_swap");
}

function validateIdentities(value: unknown) {
  const source = record(value, "identities");
  exactKeys(
    source,
    [
      "active_digest",
      "active_generation",
      "candidate_digest",
      "candidate_generation",
      "rollback_digest",
      "rollback_generation",
    ],
    "identities",
  );
  for (const key of ["active_digest", "candidate_digest", "rollback_digest"] as const) {
    nullable(source[key], (item) => digest(item, `identities.${key}`));
  }
  for (const key of ["active_generation", "candidate_generation", "rollback_generation"] as const) {
    nullable(source[key], (item) => nonNegativeInteger(item, `identities.${key}`));
  }
}

function validateLifecycle(value: unknown) {
  if (value === null) return;
  const source = record(value, "latest_lifecycle");
  exactKeys(
    source,
    ["decision_id", "phase", "origin", "policy", "reason", "payload_type"],
    "latest_lifecycle",
  );
  nonBlankString(source.decision_id, "latest_lifecycle.decision_id");
  oneOf(source.phase, ACTIVATION_PHASES, "latest_lifecycle.phase");
  oneOf(source.origin, ORIGINS, "latest_lifecycle.origin");
  oneOf(source.policy, POLICIES, "latest_lifecycle.policy");
  nullable(source.reason, (item) => stringValue(item, "latest_lifecycle.reason"));
  if (source.payload_type !== "activation_lifecycle") {
    invalidReport("latest_lifecycle.payload_type has an invalid value");
  }
}

function validateFailure(value: unknown) {
  if (value === null) return;
  const source = record(value, "failure");
  allowedKeys(
    source,
    ["code", "detail", "terminal"],
    ["code", "detail", "terminal", "payload_type"],
    "failure",
  );
  nonBlankString(source.code, "failure.code");
  nonBlankString(source.detail, "failure.detail");
  booleanValue(source.terminal, "failure.terminal");
  if (
    "payload_type" in source &&
    source.payload_type !== null &&
    source.payload_type !== "learning_failure"
  ) {
    invalidReport("failure.payload_type has an invalid value");
  }
}

function parseModelEvidenceReport(value: unknown): ModelEvidenceReport {
  const source = record(value, "report");
  exactKeys(
    source,
    [
      "schema_version",
      "status",
      "mode",
      "decision_id",
      "evidence",
      "fit",
      "checks",
      "candidate",
      "evaluation",
      "corpus",
      "activation",
      "active_model",
      "identities",
      "calibration",
      "latest_lifecycle",
      "failure",
      "gates",
      "blockers",
      "errors",
      "revision",
    ],
    "report",
  );
  if (source.schema_version !== 3) invalidReport("schema_version must equal 3");
  oneOf(source.status, REPORT_STATUSES, "status");
  nullable(source.mode, (item) => oneOf(item, ORIGINS, "mode"));
  nullable(source.decision_id, (item) => stringValue(item, "decision_id"));
  validateEvidence(source.evidence);
  validateFit(source.fit, "fit");
  const checks = record(source.checks, "checks");
  for (const [name, status] of Object.entries(checks)) {
    oneOf(status, CHECK_STATUSES, `checks.${name}`);
  }
  validateCandidate(source.candidate);
  validateEvaluation(source.evaluation);
  validateCorpus(source.corpus);
  validateActivation(source.activation);
  const activeModel = record(source.active_model, "active_model");
  exactKeys(activeModel, ["digest", "role_generation"], "active_model");
  nullable(activeModel.digest, (item) => digest(item, "active_model.digest"));
  nullable(activeModel.role_generation, (item) =>
    nonNegativeInteger(item, "active_model.role_generation"),
  );
  validateIdentities(source.identities);
  const calibration = record(source.calibration, "calibration");
  exactKeys(calibration, ["revision", "command_high_water"], "calibration");
  nonNegativeInteger(calibration.revision, "calibration.revision");
  nonNegativeInteger(calibration.command_high_water, "calibration.command_high_water");
  validateLifecycle(source.latest_lifecycle);
  validateFailure(source.failure);
  if (!Array.isArray(source.gates)) invalidReport("gates must be an array");
  source.gates.forEach((value, index) => {
    const gate = record(value, `gates[${index}]`);
    exactKeys(gate, ["name", "passed", "reason"], `gates[${index}]`);
    stringValue(gate.name, `gates[${index}].name`);
    booleanValue(gate.passed, `gates[${index}].passed`);
    nullable(gate.reason, (item) => stringValue(item, `gates[${index}].reason`));
  });
  stringArray(source.blockers, "blockers");
  stringArray(source.errors, "errors");
  digest(source.revision, "revision");
  return structuredClone(source) as unknown as ModelEvidenceReport;
}

async function responseMessage(response: Response): Promise<string> {
  const body = (await response.json().catch(() => ({}))) as {
    message?: string;
    error?: string;
    detail?: string;
  };
  return body.detail ?? body.message ?? body.error ?? `HTTP ${response.status}`;
}

/** Read-only confidence projection. An empty ledger is still a successful collecting report. */
export async function fetchModelEvidenceReport(
  baseUrl = DEFAULT_BASE_URL,
  signal?: AbortSignal,
): Promise<ModelEvidenceResult<ModelEvidenceReport>> {
  try {
    const response = await fetch(endpoint(baseUrl, "model-evidence/report"), { signal });
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseMessage(response),
        data: null,
      };
    }
    try {
      return {
        ok: true,
        status: response.status,
        message: "",
        data: parseModelEvidenceReport(await response.json()),
      };
    } catch (error) {
      return {
        ok: false,
        status: response.status,
        message: error instanceof Error ? error.message : "Invalid model evidence report",
        data: null,
      };
    }
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "network error",
      data: null,
    };
  }
}

/** Canonical sorted-key UTF-8 JSON bytes. Kept as bytes so the client cannot reserialize them. */
export async function fetchModelEvidenceArtifact(
  baseUrl = DEFAULT_BASE_URL,
): Promise<ModelEvidenceResult<Uint8Array>> {
  try {
    const response = await fetch(endpoint(baseUrl, "model-evidence/artifact"));
    if (!response.ok) {
      return {
        ok: false,
        status: response.status,
        message: await responseMessage(response),
        data: null,
      };
    }
    return {
      ok: true,
      status: response.status,
      message: "",
      data: new Uint8Array(await response.arrayBuffer()),
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "network error",
      data: null,
    };
  }
}

async function postModelAction<
  TRequest,
  TResponse extends {
    accepted: boolean;
    detail?: string | null;
  },
>(path: string, request: TRequest, baseUrl: string): Promise<ModelEvidenceResult<TResponse>> {
  try {
    const response = await fetch(endpoint(baseUrl, path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    const body = (await response.json().catch(() => null)) as
      | (Partial<TResponse> & {
          message?: string;
          error?: string;
          detail?: string | null;
        })
      | null;
    const acknowledgement =
      body !== null && typeof body.accepted === "boolean" ? (body as TResponse) : null;
    const ok = response.ok && acknowledgement?.accepted === true;
    return {
      ok,
      status: response.status,
      message: ok
        ? ""
        : (body?.detail ??
          body?.message ??
          body?.error ??
          (acknowledgement === null ? "Invalid action response" : `HTTP ${response.status}`)),
      data: acknowledgement,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "network error",
      data: null,
    };
  }
}

/** Roll back only when the unified report names an explicit rollback owner. */
export function rollbackModel(
  request: ModelRollbackRequest,
  baseUrl = DEFAULT_BASE_URL,
): Promise<ModelEvidenceResult<ModelRollbackAcknowledgement>> {
  return postModelAction("model-evidence/rollback", request, baseUrl);
}

/** Send one revisioned calibration intent. */
export async function setMpcCalibration(
  request: MpcCalibrationCommand,
  baseUrl = DEFAULT_BASE_URL,
): Promise<ModelEvidenceResult<MpcCalibrationCommand>> {
  const command: MpcCalibrationCommand = {
    action: request.action,
    revision: request.revision,
    ambient_c: request.ambient_c,
    ambient_source: request.ambient_source,
    empty_grill_confirmed: request.empty_grill_confirmed,
    pellets_confirmed: request.pellets_confirmed,
  };

  try {
    const response = await fetch(endpoint(baseUrl, "set_mpc_calibration"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(command),
    });
    const body = (await response.json().catch(() => ({}))) as MpcCalibrationCommandResponse;
    const responseCommand =
      body.data && "mpc_calibration" in body.data ? body.data.mpc_calibration : command;
    const ok = response.ok && body.result?.toUpperCase() === "OK";
    return {
      ok,
      status: response.status,
      message: body.message ?? `HTTP ${response.status}`,
      data: ok ? responseCommand : null,
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "network error",
      data: null,
    };
  }
}
