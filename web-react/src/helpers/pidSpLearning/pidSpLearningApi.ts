import type {
  FopdtPidSpParameters,
  FopdtPidSpPredictor,
  IpdtPidSpParameters,
  IpdtPidSpPredictor,
  PidSpActiveModelReport,
  PidSpCheckpointBasin,
  PidSpCheckpointModel,
  PidSpCheckpointParameters,
  PidSpConfirmationProgress,
  PidSpDelayBlocker,
  PidSpDelayConfidenceMethod,
  PidSpDelayEvidence,
  PidSpDelayEvidenceStatus,
  PidSpDelayProfileForm,
  PidSpFormComparisonReport,
  PidSpGateValue,
  PidSpHorizonLossReport,
  PidSpIdentifierReport,
  PidSpLearningFailure,
  PidSpLearningGate,
  PidSpLearningReport,
  PidSpLearningStatus,
  PidSpModelComparisonReport,
  PidSpPredictorModel,
  PidSpPredictorReport,
  PidSpSelectedCheckpoint,
  SopdtPidSpParameters,
  SopdtPidSpPredictor,
} from "@pifire/core/contracts/learning";

export interface PidSpLearningResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
}

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
const DELAY_BLOCKERS: Record<PidSpDelayBlocker, true> = {
  "insufficient-excitation-episodes": true,
  "insufficient-confidence-evidence": true,
  "delay-basin-too-wide": true,
  "delay-basin-edge": true,
  "delay-range-exhausted": true,
  "no-physically-valid-delay-candidate": true,
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

function nonBlankString(value: unknown, path: string): string {
  const parsed = stringValue(value, path);
  if (parsed.length === 0) return invalid(`${path} must be non-blank`);
  return parsed;
}

function nullable<T>(value: unknown, parse: (item: unknown) => T): T | null {
  return value === null ? null : parse(value);
}
function parseForm(value: unknown, path: string): PidSpDelayProfileForm {
  if (value !== "ipdt" && value !== "fopdt" && value !== "sopdt") {
    return invalid(`${path} is invalid`);
  }
  return value;
}

function digest(value: unknown, path: string): string {
  const parsed = stringValue(value, path);
  if (!/^[0-9a-f]{64}$/.test(parsed)) return invalid(`${path} must be a SHA-256 digest`);
  return parsed;
}

function stringArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) return invalid(`${path} must be an array`);
  return value.map((item, index) => nonBlankString(item, `${path}[${index}]`));
}

function parseParameters(
  value: unknown,
  form: PidSpDelayProfileForm,
  path: string,
): PidSpCheckpointParameters {
  const source = record(value, path);
  if (form === "ipdt") {
    exactKeys(source, ["K_i", "c0", "theta"], path);
    const parameters: IpdtPidSpParameters = {
      K_i: finiteNumber(source.K_i, `${path}.K_i`),
      c0: finiteNumber(source.c0, `${path}.c0`),
      theta: finiteNumber(source.theta, `${path}.theta`),
    };
    return parameters;
  }
  if (form === "fopdt") {
    exactKeys(source, ["K", "tau", "theta"], path);
    const parameters: FopdtPidSpParameters = {
      K: finiteNumber(source.K, `${path}.K`),
      tau: finiteNumber(source.tau, `${path}.tau`),
      theta: finiteNumber(source.theta, `${path}.theta`),
    };
    return parameters;
  }
  exactKeys(source, ["K", "tau_1", "tau_2", "theta"], path);
  const parameters: SopdtPidSpParameters = {
    K: finiteNumber(source.K, `${path}.K`),
    tau_1: finiteNumber(source.tau_1, `${path}.tau_1`),
    tau_2: finiteNumber(source.tau_2, `${path}.tau_2`),
    theta: finiteNumber(source.theta, `${path}.theta`),
  };
  return parameters;
}

function parseConfidenceMethod(value: unknown, path: string): PidSpDelayConfidenceMethod {
  if (value !== "raw-basin" && value !== "provided" && value !== "moving-block-refit") {
    return invalid(`${path} is invalid`);
  }
  return value;
}

function parseCheckpointBasin(value: unknown, path: string): PidSpCheckpointBasin {
  const source = record(value, path);
  exactKeys(
    source,
    [
      "lower_s",
      "upper_s",
      "representative_s",
      "confidence_lower_s",
      "confidence_upper_s",
      "confidence_method",
      "confidence_resamples",
      "episode_count",
      "interior",
      "blockers",
    ],
    path,
  );
  return {
    lower_s: nonNegativeInteger(source.lower_s, `${path}.lower_s`),
    upper_s: nonNegativeInteger(source.upper_s, `${path}.upper_s`),
    representative_s: nonNegativeInteger(source.representative_s, `${path}.representative_s`),
    confidence_lower_s: nonNegativeInteger(source.confidence_lower_s, `${path}.confidence_lower_s`),
    confidence_upper_s: nonNegativeInteger(source.confidence_upper_s, `${path}.confidence_upper_s`),
    confidence_method: parseConfidenceMethod(source.confidence_method, `${path}.confidence_method`),
    confidence_resamples: nonNegativeInteger(
      source.confidence_resamples,
      `${path}.confidence_resamples`,
    ),
    episode_count: nonNegativeInteger(source.episode_count, `${path}.episode_count`),
    interior: booleanValue(source.interior, `${path}.interior`),
    blockers: stringArray(source.blockers, `${path}.blockers`),
  };
}

function parseSelectedCheckpoint(value: unknown, path: string): PidSpSelectedCheckpoint {
  const source = record(value, path);
  exactKeys(
    source,
    [
      "schema_version",
      "form",
      "parameters",
      "delay_basin",
      "one_step_loss",
      "horizon_losses",
      "fold_losses",
      "standard_error",
      "episode_ids",
      "common_row_digest",
      "fit_corpus_digest",
      "configuration_digest",
      "comparison_threshold",
      "selection_margin",
      "confirmation_observed",
      "confirmation_required",
      "authorized",
      "model_digest",
    ],
    path,
  );
  if (source.schema_version !== "pid-sp-model-selection/v1") {
    return invalid(`${path}.schema_version is invalid`);
  }
  if (source.form !== "ipdt" && source.form !== "fopdt" && source.form !== "sopdt") {
    return invalid(`${path}.form is invalid`);
  }
  const form = source.form;
  if (!Array.isArray(source.horizon_losses)) {
    return invalid(`${path}.horizon_losses must be an array`);
  }
  const horizonLosses = source.horizon_losses.map((item, index): [number, number] => {
    if (!Array.isArray(item) || item.length !== 2) {
      return invalid(`${path}.horizon_losses[${index}] must be a pair`);
    }
    return [
      nonNegativeInteger(item[0], `${path}.horizon_losses[${index}][0]`),
      finiteNumber(item[1], `${path}.horizon_losses[${index}][1]`),
    ];
  });
  if (!Array.isArray(source.fold_losses)) {
    return invalid(`${path}.fold_losses must be an array`);
  }
  if (source.confirmation_observed !== 20 || source.confirmation_required !== 20) {
    return invalid(`${path}.confirmation must be complete`);
  }
  if (source.authorized !== true) return invalid(`${path}.authorized must be true`);
  return {
    schema_version: "pid-sp-model-selection/v1",
    form,
    parameters: parseParameters(source.parameters, form, `${path}.parameters`),
    delay_basin: parseCheckpointBasin(source.delay_basin, `${path}.delay_basin`),
    one_step_loss: finiteNumber(source.one_step_loss, `${path}.one_step_loss`),
    horizon_losses: horizonLosses,
    fold_losses: source.fold_losses.map((item, index) =>
      finiteNumber(item, `${path}.fold_losses[${index}]`),
    ),
    standard_error: finiteNumber(source.standard_error, `${path}.standard_error`),
    episode_ids: stringArray(source.episode_ids, `${path}.episode_ids`),
    common_row_digest: digest(source.common_row_digest, `${path}.common_row_digest`),
    fit_corpus_digest: digest(source.fit_corpus_digest, `${path}.fit_corpus_digest`),
    configuration_digest: digest(source.configuration_digest, `${path}.configuration_digest`),
    comparison_threshold: finiteNumber(source.comparison_threshold, `${path}.comparison_threshold`),
    selection_margin: finiteNumber(source.selection_margin, `${path}.selection_margin`),
    confirmation_observed: 20,
    confirmation_required: 20,
    authorized: true,
    model_digest: digest(source.model_digest, `${path}.model_digest`),
  };
}

function parseModel(value: unknown, path: string): PidSpCheckpointModel {
  const source = record(value, path);
  exactKeys(source, ["schema_version", "revision", "provenance", "selected"], path);
  if (source.schema_version !== 2) return invalid(`${path}.schema_version must be 2`);
  return {
    schema_version: 2,
    revision: nonNegativeInteger(source.revision, `${path}.revision`),
    provenance: nonBlankString(source.provenance, `${path}.provenance`),
    selected: parseSelectedCheckpoint(source.selected, `${path}.selected`),
  };
}

function parsePredictorModel(value: unknown, path: string): PidSpPredictorModel {
  const source = record(value, path);
  if (source.form === "fopdt") {
    exactKeys(source, ["form", "K", "tau", "theta"], path);
    const model: FopdtPidSpPredictor = {
      form: "fopdt",
      K: finiteNumber(source.K, `${path}.K`),
      tau: finiteNumber(source.tau, `${path}.tau`),
      theta: finiteNumber(source.theta, `${path}.theta`),
    };
    return model;
  }
  if (source.form === "ipdt") {
    exactKeys(source, ["form", "K_i", "c0", "theta"], path);
    const model: IpdtPidSpPredictor = {
      form: "ipdt",
      K_i: finiteNumber(source.K_i, `${path}.K_i`),
      c0: finiteNumber(source.c0, `${path}.c0`),
      theta: finiteNumber(source.theta, `${path}.theta`),
    };
    return model;
  }
  if (source.form === "sopdt") {
    exactKeys(source, ["form", "K", "tau_1", "tau_2", "theta"], path);
    const model: SopdtPidSpPredictor = {
      form: "sopdt",
      K: finiteNumber(source.K, `${path}.K`),
      tau_1: finiteNumber(source.tau_1, `${path}.tau_1`),
      tau_2: finiteNumber(source.tau_2, `${path}.tau_2`),
      theta: finiteNumber(source.theta, `${path}.theta`),
    };
    return model;
  }
  return invalid(`${path}.form must be fopdt, ipdt, or sopdt`);
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
      "raw_best_residual",
      "raw_runner_up_residual",
      "raw_candidates_passing",
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
    raw_best_residual: finiteNumber(source.raw_best_residual, `${path}.raw_best_residual`),
    raw_runner_up_residual: finiteNumber(
      source.raw_runner_up_residual,
      `${path}.raw_runner_up_residual`,
    ),
    raw_candidates_passing: nonNegativeInteger(
      source.raw_candidates_passing,
      `${path}.raw_candidates_passing`,
    ),
    trusted: nullable(source.trusted, (item) => parsePredictorModel(item, `${path}.trusted`)),
    distrust_count: nonNegativeInteger(source.distrust_count, `${path}.distrust_count`),
    distrust_ratio: nullable(source.distrust_ratio, (item) =>
      finiteNumber(item, `${path}.distrust_ratio`),
    ),
  };
}

function parsePredictor(value: unknown): PidSpPredictorReport {
  const path = "predictor";
  const source = record(value, path);
  exactKeys(
    source,
    ["active", "disabled", "x0", "xd", "z0", "zd", "residual_streak", "truncated", "model"],
    path,
  );
  return {
    active: booleanValue(source.active, `${path}.active`),
    disabled: booleanValue(source.disabled, `${path}.disabled`),
    x0: finiteNumber(source.x0, `${path}.x0`),
    xd: finiteNumber(source.xd, `${path}.xd`),
    z0: finiteNumber(source.z0, `${path}.z0`),
    zd: finiteNumber(source.zd, `${path}.zd`),
    residual_streak: nonNegativeInteger(source.residual_streak, `${path}.residual_streak`),
    truncated: nonNegativeInteger(source.truncated, `${path}.truncated`),
    model: nullable(source.model, (item) => parsePredictorModel(item, `${path}.model`)),
  };
}

function parseConfirmation(value: unknown, path = "confirmation"): PidSpConfirmationProgress {
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
    code: nonBlankString(source.code, `${path}.code`),
    detail: nonBlankString(source.detail, `${path}.detail`),
    terminal: booleanValue(source.terminal, `${path}.terminal`),
  };
}
function parseDelayEvidence(value: unknown): PidSpDelayEvidence {
  const path = "delay_evidence";
  const source = record(value, path);
  exactKeys(
    source,
    [
      "status",
      "completed_episode_count",
      "evaluated_bound_s",
      "profile_form",
      "raw_basin_lower_s",
      "raw_basin_upper_s",
      "raw_basin_representative_s",
      "confidence_lower_s",
      "confidence_upper_s",
      "confidence_method",
      "confidence_resamples",
      "blockers",
      "authorized",
    ],
    path,
  );
  if (!Array.isArray(source.blockers)) return invalid(`${path}.blockers must be an array`);
  const blockers = source.blockers.map((item, index): PidSpDelayBlocker => {
    const blocker = stringValue(item, `${path}.blockers[${index}]`) as PidSpDelayBlocker;
    if (DELAY_BLOCKERS[blocker] !== true) {
      return invalid(`${path}.blockers[${index}] is invalid`);
    }
    return blocker;
  });
  if (
    source.status !== "delay-basin-stable" &&
    DELAY_BLOCKERS[source.status as PidSpDelayBlocker] !== true
  ) {
    return invalid(`${path}.status is invalid`);
  }
  const status = source.status as PidSpDelayEvidenceStatus;
  const profileForm =
    source.profile_form === null ? null : parseForm(source.profile_form, `${path}.profile_form`);
  const rawBasinLower = nullable(source.raw_basin_lower_s, (item) =>
    nonNegativeInteger(item, `${path}.raw_basin_lower_s`),
  );
  const rawBasinUpper = nullable(source.raw_basin_upper_s, (item) =>
    nonNegativeInteger(item, `${path}.raw_basin_upper_s`),
  );
  const rawBasinRepresentative = nullable(source.raw_basin_representative_s, (item) =>
    nonNegativeInteger(item, `${path}.raw_basin_representative_s`),
  );
  const confidenceLower = nullable(source.confidence_lower_s, (item) =>
    nonNegativeInteger(item, `${path}.confidence_lower_s`),
  );
  const confidenceUpper = nullable(source.confidence_upper_s, (item) =>
    nonNegativeInteger(item, `${path}.confidence_upper_s`),
  );
  const confidenceMethod =
    source.confidence_method === null
      ? null
      : parseConfidenceMethod(source.confidence_method, `${path}.confidence_method`);
  const confidenceResamples = nullable(source.confidence_resamples, (item) =>
    nonNegativeInteger(item, `${path}.confidence_resamples`),
  );
  const authorized = booleanValue(source.authorized, `${path}.authorized`);
  const unavailable = blockers.includes("no-physically-valid-delay-candidate");
  const audit = [
    rawBasinLower,
    rawBasinUpper,
    rawBasinRepresentative,
    confidenceLower,
    confidenceUpper,
    confidenceMethod,
    confidenceResamples,
  ];
  if (unavailable && (profileForm === null || audit.some((item) => item !== null))) {
    return invalid(`${path} unavailable profile must omit basin audit`);
  }
  if (authorized !== (blockers.length === 0)) {
    return invalid(`${path}.authorized disagrees with blockers`);
  }
  if ((status === "delay-basin-stable") !== authorized) {
    return invalid(`${path}.status disagrees with authorization`);
  }
  return {
    status,
    completed_episode_count: nonNegativeInteger(
      source.completed_episode_count,
      `${path}.completed_episode_count`,
    ),
    evaluated_bound_s: nonNegativeInteger(source.evaluated_bound_s, `${path}.evaluated_bound_s`),
    profile_form: profileForm,
    raw_basin_lower_s: rawBasinLower,
    raw_basin_upper_s: rawBasinUpper,
    raw_basin_representative_s: rawBasinRepresentative,
    confidence_lower_s: confidenceLower,
    confidence_upper_s: confidenceUpper,
    confidence_method: confidenceMethod,
    confidence_resamples: confidenceResamples,
    blockers,
    authorized,
  };
}

function parseHorizonLoss(value: unknown, path: string): PidSpHorizonLossReport {
  const source = record(value, path);
  exactKeys(source, ["horizon_s", "loss"], path);
  return {
    horizon_s: nonNegativeInteger(source.horizon_s, `${path}.horizon_s`),
    loss: nullable(source.loss, (item) => finiteNumber(item, `${path}.loss`)),
  };
}

function parseFormComparison(value: unknown, index: number): PidSpFormComparisonReport {
  const path = `comparison.forms[${index}]`;
  const source = record(value, path);
  exactKeys(
    source,
    [
      "form",
      "eligible",
      "blockers",
      "one_step_loss",
      "horizon_losses",
      "fold_losses",
      "standard_error",
      "basin_lower_s",
      "basin_upper_s",
      "confidence_lower_s",
      "confidence_upper_s",
      "confidence_method",
    ],
    path,
  );
  if (!Array.isArray(source.horizon_losses)) {
    return invalid(`${path}.horizon_losses must be an array`);
  }
  if (!Array.isArray(source.fold_losses)) {
    return invalid(`${path}.fold_losses must be an array`);
  }
  const blockers = stringArray(source.blockers, `${path}.blockers`);
  const basinLower = nullable(source.basin_lower_s, (item) =>
    nonNegativeInteger(item, `${path}.basin_lower_s`),
  );
  const basinUpper = nullable(source.basin_upper_s, (item) =>
    nonNegativeInteger(item, `${path}.basin_upper_s`),
  );
  const confidenceLower = nullable(source.confidence_lower_s, (item) =>
    nonNegativeInteger(item, `${path}.confidence_lower_s`),
  );
  const confidenceUpper = nullable(source.confidence_upper_s, (item) =>
    nonNegativeInteger(item, `${path}.confidence_upper_s`),
  );
  const confidenceMethod =
    source.confidence_method === null
      ? null
      : parseConfidenceMethod(source.confidence_method, `${path}.confidence_method`);
  const eligible = booleanValue(source.eligible, `${path}.eligible`);
  const unavailable = blockers.includes("no-physically-valid-delay-candidate");
  if (
    unavailable &&
    (eligible ||
      [basinLower, basinUpper, confidenceLower, confidenceUpper, confidenceMethod].some(
        (item) => item !== null,
      ))
  ) {
    return invalid(`${path} unavailable form must omit basin audit`);
  }
  return {
    form: parseForm(source.form, `${path}.form`),
    eligible,
    blockers,
    one_step_loss: nullable(source.one_step_loss, (item) =>
      finiteNumber(item, `${path}.one_step_loss`),
    ),
    horizon_losses: source.horizon_losses.map((item, horizonIndex) =>
      parseHorizonLoss(item, `${path}.horizon_losses[${horizonIndex}]`),
    ),
    fold_losses: source.fold_losses.map((item, foldIndex) =>
      nullable(item, (loss) => finiteNumber(loss, `${path}.fold_losses[${foldIndex}]`)),
    ),
    standard_error: nullable(source.standard_error, (item) =>
      finiteNumber(item, `${path}.standard_error`),
    ),
    basin_lower_s: basinLower,
    basin_upper_s: basinUpper,
    confidence_lower_s: confidenceLower,
    confidence_upper_s: confidenceUpper,
    confidence_method: confidenceMethod,
  };
}

function parseComparison(value: unknown): PidSpModelComparisonReport {
  const path = "comparison";
  const source = record(value, path);
  exactKeys(
    source,
    [
      "forms",
      "best_form",
      "comparison_threshold",
      "selection_margin",
      "selected_form",
      "confirmation",
      "primary_blocker",
    ],
    path,
  );
  if (!Array.isArray(source.forms)) return invalid(`${path}.forms must be an array`);
  return {
    forms: source.forms.map(parseFormComparison),
    best_form: source.best_form === null ? null : parseForm(source.best_form, `${path}.best_form`),
    comparison_threshold: nullable(source.comparison_threshold, (item) =>
      finiteNumber(item, `${path}.comparison_threshold`),
    ),
    selection_margin: nullable(source.selection_margin, (item) =>
      finiteNumber(item, `${path}.selection_margin`),
    ),
    selected_form:
      source.selected_form === null
        ? null
        : parseForm(source.selected_form, `${path}.selected_form`),
    confirmation: parseConfirmation(source.confirmation, `${path}.confirmation`),
    primary_blocker: nullable(source.primary_blocker, (item) =>
      nonBlankString(item, `${path}.primary_blocker`),
    ),
  };
}

function parseActiveModel(value: unknown): PidSpActiveModelReport {
  const path = "active_model";
  const source = record(value, path);
  exactKeys(source, ["form", "model_digest"], path);
  return {
    form: parseForm(source.form, `${path}.form`),
    model_digest: digest(source.model_digest, `${path}.model_digest`),
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
      "comparison",
      "active_model",
      "delay_evidence",
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
  const comparison = nullable(source.comparison, parseComparison);
  const activeModel = nullable(source.active_model, parseActiveModel);
  const delayEvidence = nullable(source.delay_evidence, parseDelayEvidence);

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
    comparison,
    active_model: activeModel,
    delay_evidence: delayEvidence,
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
