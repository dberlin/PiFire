import type {
  FopdtPidSpModel,
  FopdtPidSpPredictorModel,
  IpdtPidSpModel,
  IpdtPidSpPredictorModel,
  PidSpConfirmationProgress,
  PidSpGateValue,
  PidSpIdentifierReport,
  PidSpLearningFailure,
  PidSpLearningGate,
  PidSpLearningReport,
  PidSpLearningResult,
  PidSpLearningStatus,
  PidSpModel,
  PidSpPredictorModel,
  PidSpPredictorReport,
} from "./types";

const DEFAULT_BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";
const REPORT_PATH = "/api/pid-sp-learning/report";
const REPORT_STATUSES: Record<PidSpLearningStatus, true> = {
  idle: true,
  collecting: true,
  "insufficient-excitation": true,
  evaluating: true,
  active: true,
  fallback: true,
  error: true,
};

type UnknownRecord = Record<string, unknown>;

function invalid(detail: string): never {
  throw new Error(`Invalid PID-SP learning report: ${detail}`);
}

function record(value: unknown, path: string): UnknownRecord {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return invalid(`${path} must be an object`);
  }
  return value as UnknownRecord;
}

function exactKeys(value: UnknownRecord, expected: readonly string[], path: string) {
  const keys = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (keys.length !== wanted.length || keys.some((key, index) => key !== wanted[index])) {
    invalid(`${path} fields are invalid`);
  }
}

function finiteNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return invalid(`${path} must be a finite number`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, path: string): number {
  const parsed = finiteNumber(value, path);
  if (!Number.isInteger(parsed) || parsed < 0) {
    return invalid(`${path} must be a non-negative integer`);
  }
  return parsed;
}

function booleanValue(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") return invalid(`${path} must be a boolean`);
  return value;
}

function stringValue(value: unknown, path: string): string {
  if (typeof value !== "string") return invalid(`${path} must be a string`);
  return value;
}

function nullable<T>(value: unknown, parse: (item: unknown) => T): T | null {
  return value === null ? null : parse(value);
}

function parseModel(value: unknown, path: string): PidSpModel {
  const source = record(value, path);
  if (source.form === "fopdt") {
    exactKeys(
      source,
      source.identified_at_f === undefined
        ? ["form", "K", "tau", "theta", "revision"]
        : ["form", "K", "tau", "theta", "revision", "identified_at_f"],
      path,
    );
    const model: FopdtPidSpModel = {
      form: "fopdt",
      K: finiteNumber(source.K, `${path}.K`),
      tau: finiteNumber(source.tau, `${path}.tau`),
      theta: finiteNumber(source.theta, `${path}.theta`),
      revision: nonNegativeInteger(source.revision, `${path}.revision`),
    };
    if (source.identified_at_f !== undefined) {
      model.identified_at_f = finiteNumber(source.identified_at_f, `${path}.identified_at_f`);
    }
    return model;
  }
  if (source.form === "ipdt") {
    exactKeys(
      source,
      source.identified_at_f === undefined
        ? ["form", "K_i", "c0", "theta", "revision"]
        : ["form", "K_i", "c0", "theta", "revision", "identified_at_f"],
      path,
    );
    const model: IpdtPidSpModel = {
      form: "ipdt",
      K_i: finiteNumber(source.K_i, `${path}.K_i`),
      c0: finiteNumber(source.c0, `${path}.c0`),
      theta: finiteNumber(source.theta, `${path}.theta`),
      revision: nonNegativeInteger(source.revision, `${path}.revision`),
    };
    if (source.identified_at_f !== undefined) {
      model.identified_at_f = finiteNumber(source.identified_at_f, `${path}.identified_at_f`);
    }
    return model;
  }
  return invalid(`${path}.form must be fopdt or ipdt`);
}

function parsePredictorModel(value: unknown, path: string): PidSpPredictorModel {
  const source = record(value, path);
  if (source.form === "fopdt") {
    exactKeys(source, ["form", "K", "tau", "theta"], path);
    const model: FopdtPidSpPredictorModel = {
      form: "fopdt",
      K: finiteNumber(source.K, `${path}.K`),
      tau: finiteNumber(source.tau, `${path}.tau`),
      theta: finiteNumber(source.theta, `${path}.theta`),
    };
    return model;
  }
  if (source.form === "ipdt") {
    exactKeys(source, ["form", "K_i", "c0", "theta"], path);
    const model: IpdtPidSpPredictorModel = {
      form: "ipdt",
      K_i: finiteNumber(source.K_i, `${path}.K_i`),
      c0: finiteNumber(source.c0, `${path}.c0`),
      theta: finiteNumber(source.theta, `${path}.theta`),
    };
    return model;
  }
  return invalid(`${path}.form must be fopdt or ipdt`);
}

function gateValue(value: unknown, path: string): PidSpGateValue {
  return typeof value === "boolean" ? value : finiteNumber(value, path);
}

function parseGate(value: unknown, index: number): PidSpLearningGate {
  const path = `gates[${index}]`;
  const source = record(value, path);
  exactKeys(source, ["name", "passed", "observed", "required", "unit"], path);
  return {
    name: stringValue(source.name, `${path}.name`),
    passed: booleanValue(source.passed, `${path}.passed`),
    observed: gateValue(source.observed, `${path}.observed`),
    required: gateValue(source.required, `${path}.required`),
    unit: source.unit === null ? null : stringValue(source.unit, `${path}.unit`),
  };
}

function parseIdentifier(value: unknown): PidSpIdentifierReport {
  const path = "identifier";
  const source = record(value, path);
  exactKeys(
    source,
    [
      "accepted",
      "accepted_seconds",
      "duty_std",
      "temp_span",
      "transition_seen",
      "duty_segments",
      "best_residual",
      "runner_up_residual",
      "candidates_passing",
      "confirming",
      "trusted",
      "distrust_count",
      "distrust_ratio",
    ],
    path,
  );
  return {
    accepted: finiteNumber(source.accepted, `${path}.accepted`),
    accepted_seconds: finiteNumber(source.accepted_seconds, `${path}.accepted_seconds`),
    duty_std: finiteNumber(source.duty_std, `${path}.duty_std`),
    temp_span: finiteNumber(source.temp_span, `${path}.temp_span`),
    transition_seen: booleanValue(source.transition_seen, `${path}.transition_seen`),
    duty_segments: nonNegativeInteger(source.duty_segments, `${path}.duty_segments`),
    best_residual: finiteNumber(source.best_residual, `${path}.best_residual`),
    runner_up_residual: finiteNumber(source.runner_up_residual, `${path}.runner_up_residual`),
    candidates_passing: nonNegativeInteger(source.candidates_passing, `${path}.candidates_passing`),
    confirming:
      source.confirming === null
        ? null
        : nonNegativeInteger(source.confirming, `${path}.confirming`),
    trusted: nullable(source.trusted, (item) => parseModel(item, `${path}.trusted`)),
    distrust_count: nonNegativeInteger(source.distrust_count, `${path}.distrust_count`),
    distrust_ratio: finiteNumber(source.distrust_ratio, `${path}.distrust_ratio`),
  };
}

function parsePredictor(value: unknown): PidSpPredictorReport {
  const path = "predictor";
  const source = record(value, path);
  exactKeys(
    source,
    ["active", "disabled", "x0", "xd", "residual_streak", "truncated", "model"],
    path,
  );
  return {
    active: booleanValue(source.active, `${path}.active`),
    disabled: booleanValue(source.disabled, `${path}.disabled`),
    x0: finiteNumber(source.x0, `${path}.x0`),
    xd: finiteNumber(source.xd, `${path}.xd`),
    residual_streak: nonNegativeInteger(source.residual_streak, `${path}.residual_streak`),
    truncated: nonNegativeInteger(source.truncated, `${path}.truncated`),
    model: nullable(source.model, (item) => parsePredictorModel(item, `${path}.model`)),
  };
}

function parseConfirmation(value: unknown): PidSpConfirmationProgress {
  const path = "confirmation";
  const source = record(value, path);
  exactKeys(source, ["observed", "required"], path);
  return {
    observed:
      source.observed === null ? null : nonNegativeInteger(source.observed, `${path}.observed`),
    required: nonNegativeInteger(source.required, `${path}.required`),
  };
}

function parseFailure(value: unknown): PidSpLearningFailure {
  const path = "failure";
  const source = record(value, path);
  exactKeys(source, ["code", "detail", "terminal"], path);
  return {
    code: stringValue(source.code, `${path}.code`),
    detail: stringValue(source.detail, `${path}.detail`),
    terminal: booleanValue(source.terminal, `${path}.terminal`),
  };
}

export function parsePidSpLearningReport(value: unknown): PidSpLearningReport {
  const source = record(value, "report");
  exactKeys(
    source,
    [
      "schema_version",
      "controller",
      "status",
      "live",
      "revision",
      "gates",
      "confirmation",
      "identifier",
      "predictor",
      "checkpoint",
      "failure",
    ],
    "report",
  );
  if (source.schema_version !== 1) invalid("schema_version must be 1");
  if (source.controller !== "pid_sp") invalid("controller must be pid_sp");
  if (REPORT_STATUSES[source.status as PidSpLearningStatus] !== true) {
    invalid("status is invalid");
  }
  const status = source.status as PidSpLearningStatus;
  const live = booleanValue(source.live, "live");
  const gatesSource = source.gates;
  if (!Array.isArray(gatesSource)) invalid("gates must be an array");
  const gates = gatesSource.map(parseGate);
  const confirmation = nullable(source.confirmation, parseConfirmation);
  const identifier = nullable(source.identifier, parseIdentifier);
  const predictor = nullable(source.predictor, parsePredictor);
  const checkpoint = nullable(source.checkpoint, (item) => parseModel(item, "checkpoint"));
  const failure = nullable(source.failure, parseFailure);

  const hasIdentifier = identifier !== null;
  const hasPredictor = predictor !== null;
  if ((live && (!hasIdentifier || !hasPredictor)) || (!live && (hasIdentifier || hasPredictor))) {
    invalid("live detail does not match live flag");
  }
  if (live !== (confirmation !== null)) {
    invalid("confirmation does not match live flag");
  }
  if (!live && gates.length !== 0) invalid("non-live reports cannot contain gates");
  if (status === "idle" && (live || failure !== null)) {
    invalid("idle report fields are inconsistent");
  }
  if (status === "error" && (live || failure === null)) {
    invalid("error report fields are inconsistent");
  }
  if (status !== "idle" && status !== "error" && !live) {
    invalid(`${status} report must contain live detail`);
  }
  if (failure !== null && status !== "error") {
    invalid("failure requires error status");
  }

  const revision = stringValue(source.revision, "revision");
  if (!/^[0-9a-f]{64}$/.test(revision)) {
    invalid("revision must be a SHA-256 digest");
  }

  return {
    schema_version: 1,
    controller: "pid_sp",
    status,
    live,
    revision,
    gates,
    confirmation,
    identifier,
    predictor,
    checkpoint,
    failure,
  };
}

async function responseMessage(response: Response): Promise<string> {
  const decoded: unknown = await response.json().catch(() => ({}));
  const body =
    decoded !== null && typeof decoded === "object" && !Array.isArray(decoded)
      ? (decoded as {
          message?: unknown;
          error?: unknown;
          detail?: unknown;
        })
      : {};
  for (const candidate of [body.detail, body.message, body.error]) {
    if (typeof candidate === "string" && candidate !== "") return candidate;
  }
  return `HTTP ${response.status}`;
}

export async function fetchPidSpLearningReport(
  baseUrl = DEFAULT_BASE_URL,
  signal?: AbortSignal,
): Promise<PidSpLearningResult<PidSpLearningReport>> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl}${REPORT_PATH}`, {
      method: "GET",
      signal,
    });
  } catch (error) {
    return {
      ok: false,
      status: 0,
      message: error instanceof Error ? error.message : "network error",
      data: null,
    };
  }

  if (!response.ok) {
    return {
      ok: false,
      status: response.status,
      message: await responseMessage(response),
      data: null,
    };
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return {
      ok: false,
      status: response.status,
      message: "Invalid PID-SP learning report JSON",
      data: null,
    };
  }

  try {
    return {
      ok: true,
      status: response.status,
      message: "",
      data: parsePidSpLearningReport(body),
    };
  } catch (error) {
    return {
      ok: false,
      status: response.status,
      message:
        error instanceof Error ? error.message : "Invalid PID-SP learning report: schema mismatch",
      data: null,
    };
  }
}
