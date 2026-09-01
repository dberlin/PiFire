import type { PidSpLearningReport } from "@pifire/core/contracts/learning";
import { afterEach, beforeEach, describe, expect, it, type Mock, rs } from "@rstest/core";
import {
  fetchPidSpLearningReport,
  parsePidSpLearningReport,
} from "../../../src/helpers/pidSpLearning/pidSpLearningApi";

const PREDICTOR_SOPDT = {
  form: "sopdt" as const,
  K: 86.5,
  tau_1: 210,
  tau_2: 610,
  theta: 45,
};

const IDENTIFIER = {
  accepted: 144,
  accepted_seconds: 720.5,
  duty_std: 0.082,
  temp_span: 18.25,
  transition_seen: true,
  duty_segments: 6,
  raw_best_residual: 0.42,
  raw_runner_up_residual: 0.73,
  raw_candidates_passing: 2,
  trusted: PREDICTOR_SOPDT,
  distrust_count: 1,
  distrust_ratio: 0.125,
};

const PREDICTOR = {
  active: true,
  disabled: false,
  x0: 241.25,
  xd: 238.75,
  z0: 240.5,
  zd: 239.25,
  residual_streak: 1,
  truncated: 2,
  model: PREDICTOR_SOPDT,
};

const CHECKPOINT = {
  schema_version: 2 as const,
  revision: 7,
  provenance: "confirmed-online-fit",
  selected: {
    schema_version: "pid-sp-model-selection/v1" as const,
    form: "sopdt" as const,
    parameters: { K: 86.5, tau_1: 210, tau_2: 610, theta: 45 },
    delay_basin: {
      lower_s: 40,
      upper_s: 50,
      representative_s: 45,
      confidence_lower_s: 40,
      confidence_upper_s: 50,
      confidence_method: "moving-block-refit" as const,
      confidence_resamples: 128,
      episode_count: 3,
      interior: true,
      blockers: [],
    },
    one_step_loss: 0.42,
    horizon_losses: [
      [3, 0.43],
      [15, 0.48],
    ] as [number, number][],
    fold_losses: [0.44, 0.46],
    standard_error: 0.01,
    episode_ids: ["episode-a", "episode-b", "episode-c"],
    common_row_digest: "c".repeat(64),
    fit_corpus_digest: "d".repeat(64),
    configuration_digest: "e".repeat(64),
    comparison_threshold: 0.5,
    selection_margin: 0.08,
    confirmation_observed: 20 as const,
    confirmation_required: 20 as const,
    authorized: true as const,
    model_digest: "f".repeat(64),
  },
};

const DELAY_EVIDENCE = {
  status: "no-physically-valid-delay-candidate" as const,
  completed_episode_count: 3,
  evaluated_bound_s: 300,
  profile_form: "fopdt" as const,
  raw_basin_lower_s: null,
  raw_basin_upper_s: null,
  raw_basin_representative_s: null,
  confidence_lower_s: null,
  confidence_upper_s: null,
  confidence_method: null,
  confidence_resamples: null,
  blockers: ["no-physically-valid-delay-candidate" as const],
  authorized: false,
};

const COMPARISON = {
  forms: [
    {
      form: "fopdt" as const,
      eligible: false,
      blockers: ["no-physically-valid-delay-candidate"],
      one_step_loss: null,
      horizon_losses: [
        { horizon_s: 3, loss: null },
        { horizon_s: 15, loss: null },
      ],
      fold_losses: [null, null],
      standard_error: null,
      basin_lower_s: null,
      basin_upper_s: null,
      confidence_lower_s: null,
      confidence_upper_s: null,
      confidence_method: null,
    },
  ],
  best_form: "sopdt" as const,
  comparison_threshold: 0.5,
  selection_margin: 0.08,
  selected_form: "sopdt" as const,
  confirmation: { observed: 20, required: 20 },
  primary_blocker: null,
};

const REPORT: PidSpLearningReport = {
  schema_version: 1,
  controller: "pid_sp",
  status: "active",
  live: true,
  revision: "a".repeat(64),
  gates: [
    {
      name: "accepted_samples",
      passed: true,
      observed: 144,
      required: 120,
      unit: "samples",
    },
    {
      name: "duty_transition",
      passed: true,
      observed: true,
      required: true,
      unit: null,
    },
  ],
  confirmation: { observed: 20, required: 20 },
  identifier: IDENTIFIER,
  predictor: PREDICTOR,
  checkpoint: CHECKPOINT,
  comparison: COMPARISON,
  active_model: { form: "sopdt", model_digest: "f".repeat(64) },
  delay_evidence: DELAY_EVIDENCE,
  failure: null,
};

const IDLE: PidSpLearningReport = {
  schema_version: 1,
  controller: "pid_sp",
  status: "idle",
  live: false,
  revision: "b".repeat(64),
  gates: [],
  confirmation: null,
  identifier: null,
  predictor: null,
  checkpoint: null,
  comparison: null,
  active_model: null,
  delay_evidence: null,
  failure: null,
};

interface StubResponse {
  ok: boolean;
  status: number;
  json: Mock<() => Promise<unknown>>;
}

type FetchMock = Mock<(input: string, init?: RequestInit) => Promise<StubResponse>>;
let fetchMock: FetchMock;

function response(body: unknown, status = 200): StubResponse {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: rs.fn(async () => body),
  };
}

beforeEach(() => {
  fetchMock = rs.fn(async () => response(structuredClone(REPORT)));
  rs.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  rs.unstubAllGlobals();
  rs.restoreAllMocks();
});

describe("fetchPidSpLearningReport", () => {
  it("GETs the exact same-origin report path and forwards the abort signal", async () => {
    const controller = new AbortController();

    const result = await fetchPidSpLearningReport("", controller.signal);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]).toEqual([
      "/api/pid-sp-learning/report",
      { method: "GET", signal: controller.signal },
    ]);
    expect(result).toEqual({
      ok: true,
      status: 200,
      message: "",
      data: REPORT,
    });
  });

  it("prefixes the exact report path with an explicit API base", async () => {
    await fetchPidSpLearningReport("https://grill.example");

    expect(fetchMock.mock.calls[0]?.[0]).toBe("https://grill.example/api/pid-sp-learning/report");
  });

  it("decodes schema-2 checkpoints and all three discriminated model forms", async () => {
    const predictorIpdt = {
      form: "ipdt" as const,
      K_i: 0.87,
      c0: -0.18,
      theta: 30,
    };
    const ipdt = {
      ...structuredClone(CHECKPOINT),
      revision: 9,
      selected: {
        ...structuredClone(CHECKPOINT.selected),
        form: "ipdt" as const,
        parameters: { K_i: 0.87, c0: -0.18, theta: 30 },
      },
    };
    fetchMock.mockResolvedValueOnce(response(structuredClone(REPORT))).mockResolvedValueOnce(
      response({
        ...structuredClone(REPORT),
        checkpoint: ipdt,
        identifier: { ...structuredClone(IDENTIFIER), trusted: predictorIpdt },
        predictor: { ...structuredClone(PREDICTOR), model: predictorIpdt },
      }),
    );

    const sopdt = await fetchPidSpLearningReport();
    const integrating = await fetchPidSpLearningReport();

    expect(sopdt.data?.checkpoint).toEqual(CHECKPOINT);
    expect(sopdt.data?.identifier?.trusted).toEqual(PREDICTOR_SOPDT);
    expect(integrating.data?.checkpoint).toEqual(ipdt);
    expect(integrating.data?.identifier?.trusted).toEqual(predictorIpdt);
    expect(integrating.data?.predictor?.model).toEqual(predictorIpdt);
  });

  it("accepts the complete idle report without inventing live detail", async () => {
    fetchMock.mockResolvedValue(response(structuredClone(IDLE)));

    await expect(fetchPidSpLearningReport()).resolves.toEqual({
      ok: true,
      status: 200,
      message: "",
      data: IDLE,
    });
  });

  it("maps a non-2xx detail response to the established result shape", async () => {
    fetchMock.mockResolvedValue(
      response(
        {
          error: "pid-sp-learning-report-invalid",
          detail: "checkpoint.K must be finite",
        },
        422,
      ),
    );

    await expect(fetchPidSpLearningReport()).resolves.toEqual({
      ok: false,
      status: 422,
      message: "checkpoint.K must be finite",
      data: null,
    });
  });
  it("maps a non-2xx null JSON body to the HTTP status fallback", async () => {
    fetchMock.mockResolvedValue(response(null, 503));

    await expect(fetchPidSpLearningReport()).resolves.toEqual({
      ok: false,
      status: 503,
      message: "HTTP 503",
      data: null,
    });
  });

  it("maps invalid success JSON without losing the HTTP status", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: rs.fn(async () => {
        throw new SyntaxError("Unexpected token");
      }),
    });

    await expect(fetchPidSpLearningReport()).resolves.toEqual({
      ok: false,
      status: 200,
      message: "Invalid PID-SP learning report JSON",
      data: null,
    });
  });

  it("maps a network failure to status zero", async () => {
    fetchMock.mockRejectedValue(new Error("connection refused"));

    await expect(fetchPidSpLearningReport()).resolves.toEqual({
      ok: false,
      status: 0,
      message: "connection refused",
      data: null,
    });
  });

  it.each([
    ["wrong schema version", { ...REPORT, schema_version: 2 }],
    ["wrong controller", { ...REPORT, controller: "mpc" }],
    ["unknown status", { ...REPORT, status: "ready" }],
    ["missing field", { ...REPORT, failure: undefined }],
    ["extra field", { ...REPORT, action: "reset" }],
    ["live detail disagreement", { ...IDLE, live: true }],
    [
      "empty failure code",
      {
        ...IDLE,
        status: "error",
        failure: { code: "", detail: "predictor unavailable", terminal: true },
      },
    ],
    [
      "empty failure detail",
      {
        ...IDLE,
        status: "error",
        failure: { code: "predictor-unavailable", detail: "", terminal: true },
      },
    ],
    [
      "failure attached to live status",
      {
        ...REPORT,
        failure: { code: "bad-live", detail: "invalid", terminal: false },
      },
    ],
  ])("rejects schema mismatch: %s", async (_case, body) => {
    fetchMock.mockResolvedValue(response(body));

    const result = await fetchPidSpLearningReport();

    expect(result.ok).toBe(false);
    expect(result.status).toBe(200);
    expect(result.message).toMatch(/^Invalid PID-SP learning report:/);
    expect(result.data).toBeNull();
  });
  it.each([
    ["idle with identifier only", { ...IDLE, identifier: IDENTIFIER }],
    ["idle with predictor only", { ...IDLE, predictor: PREDICTOR }],
    [
      "error with identifier only",
      {
        ...IDLE,
        status: "error",
        identifier: IDENTIFIER,
        failure: {
          code: "live-status-invalid",
          detail: "predictor is missing",
          terminal: false,
        },
      },
    ],
    [
      "error with predictor only",
      {
        ...IDLE,
        status: "error",
        predictor: PREDICTOR,
        failure: {
          code: "live-status-invalid",
          detail: "identifier is missing",
          terminal: false,
        },
      },
    ],
  ])("rejects partial non-live detail: %s", async (_case, body) => {
    fetchMock.mockResolvedValue(response(body));

    const result = await fetchPidSpLearningReport();

    expect(result.ok).toBe(false);
    expect(result.status).toBe(200);
    expect(result.message).toMatch(/^Invalid PID-SP learning report:/);
    expect(result.data).toBeNull();
  });

  it.each([
    [
      "checkpoint numeric boolean",
      {
        ...REPORT,
        checkpoint: {
          ...CHECKPOINT,
          selected: {
            ...CHECKPOINT.selected,
            parameters: { ...CHECKPOINT.selected.parameters, K: true },
          },
        },
      },
    ],
    ["null checkpoint provenance", { ...REPORT, checkpoint: { ...CHECKPOINT, provenance: null } }],
    ["identifier numeric boolean", { ...REPORT, identifier: { ...IDENTIFIER, accepted: false } }],
    [
      "predictor numeric boolean",
      { ...REPORT, predictor: { ...PREDICTOR, residual_streak: true } },
    ],
    [
      "non-finite checkpoint",
      {
        ...REPORT,
        checkpoint: {
          ...CHECKPOINT,
          selected: {
            ...CHECKPOINT.selected,
            parameters: {
              ...CHECKPOINT.selected.parameters,
              theta: Number.POSITIVE_INFINITY,
            },
          },
        },
      },
    ],
    [
      "non-finite gate",
      {
        ...REPORT,
        gates: [{ ...REPORT.gates[0], observed: Number.NaN }],
      },
    ],
    [
      "non-finite confirmation observation",
      {
        ...REPORT,
        confirmation: { observed: Number.POSITIVE_INFINITY, required: 4 },
      },
    ],
    [
      "non-finite predictor model",
      {
        ...REPORT,
        predictor: {
          ...PREDICTOR,
          model: { ...PREDICTOR_SOPDT, theta: Number.POSITIVE_INFINITY },
        },
      },
    ],
    [
      "predictor model numeric boolean",
      {
        ...REPORT,
        predictor: {
          ...PREDICTOR,
          model: { ...PREDICTOR_SOPDT, K: true },
        },
      },
    ],
    [
      "checkpoint-only field on predictor model",
      {
        ...REPORT,
        predictor: {
          ...PREDICTOR,
          model: { ...PREDICTOR_SOPDT, revision: 7 },
        },
      },
    ],
  ])("rejects %s", async (_case, body) => {
    fetchMock.mockResolvedValue(response(body));

    const result = await fetchPidSpLearningReport();

    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/^Invalid PID-SP learning report:/);
  });

  it.each([
    ["report live", { ...REPORT, live: 1 }],
    ["gate passed", { ...REPORT, gates: [{ ...REPORT.gates[0], passed: 1 }] }],
    ["identifier transition", { ...REPORT, identifier: { ...IDENTIFIER, transition_seen: 1 } }],
    ["predictor active", { ...REPORT, predictor: { ...PREDICTOR, active: 1 } }],
    [
      "failure terminal",
      {
        ...REPORT,
        failure: { code: "bad-live", detail: "invalid", terminal: 1 },
      },
    ],
    [
      "confirmation required",
      {
        ...REPORT,
        confirmation: { observed: 3, required: true },
      },
    ],
  ])("strictly validates the %s boolean", async (_case, body) => {
    fetchMock.mockResolvedValue(response(body));

    const result = await fetchPidSpLearningReport();

    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/^Invalid PID-SP learning report:/);
  });

  it("returns caller-owned nested data rather than aliasing the decoded input", () => {
    const source = structuredClone(REPORT);

    const parsed = parsePidSpLearningReport(source);
    parsed.gates[0]!.name = "changed";
    if (parsed.identifier?.trusted?.form === "sopdt") {
      parsed.identifier.trusted.tau_2 = -1;
    }

    expect(source.gates[0]?.name).toBe("accepted_samples");
    expect(source.identifier?.trusted?.form).toBe("sopdt");
    if (source.identifier?.trusted?.form === "sopdt") {
      expect(source.identifier.trusted.tau_2).toBe(610);
    }
  });
});
