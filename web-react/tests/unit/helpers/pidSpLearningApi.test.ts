import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  type Mock,
  rs,
} from "@rstest/core";
import type { PidSpLearningReport } from "../../../src/helpers/pidSpLearning/types";
import {
  fetchPidSpLearningReport,
  parsePidSpLearningReport,
} from "../../../src/helpers/pidSpLearning/pidSpLearningApi";

const FOPDT = {
  form: "fopdt" as const,
  K: 86.5,
  tau: 610,
  theta: 45,
  revision: 7,
  identified_at_f: 250,
};

const IDENTIFIER = {
  accepted: 144,
  accepted_seconds: 720.5,
  duty_std: 0.082,
  temp_span: 18.25,
  transition_seen: true,
  duty_segments: 6,
  best_residual: 0.42,
  runner_up_residual: 0.73,
  candidates_passing: 2,
  confirming: 3,
  trusted: FOPDT,
  distrust_count: 1,
  distrust_ratio: 0.125,
};

const PREDICTOR = {
  active: true,
  disabled: false,
  x0: 241.25,
  xd: 238.75,
  residual_streak: 1,
  truncated: 2,
  model: FOPDT,
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
  confirmation: { observed: 3, required: 4 },
  identifier: IDENTIFIER,
  predictor: PREDICTOR,
  checkpoint: FOPDT,
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
  failure: null,
};

interface StubResponse {
  ok: boolean;
  status: number;
  json: Mock<() => Promise<unknown>>;
}

type FetchMock = Mock<
  (input: string, init?: RequestInit) => Promise<StubResponse>
>;
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

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "https://grill.example/api/pid-sp-learning/report",
    );
  });

  it("decodes both discriminated durable model forms", async () => {
    const ipdt = {
      form: "ipdt" as const,
      K_i: 0.87,
      c0: -0.18,
      theta: 30,
      revision: 9,
      identified_at_f: 275,
    };
    fetchMock
      .mockResolvedValueOnce(response(structuredClone(REPORT)))
      .mockResolvedValueOnce(
        response({
          ...structuredClone(REPORT),
          checkpoint: ipdt,
          identifier: { ...structuredClone(IDENTIFIER), trusted: ipdt },
          predictor: { ...structuredClone(PREDICTOR), model: ipdt },
        }),
      );

    const fopdt = await fetchPidSpLearningReport();
    const integrating = await fetchPidSpLearningReport();

    expect(fopdt.data?.checkpoint).toEqual(FOPDT);
    expect(integrating.data?.checkpoint).toEqual(ipdt);
    expect(integrating.data?.identifier?.trusted).toEqual(ipdt);
    expect(integrating.data?.predictor?.model).toEqual(ipdt);
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
      { ...REPORT, checkpoint: { ...FOPDT, K: true } },
    ],
    [
      "identifier numeric boolean",
      { ...REPORT, identifier: { ...IDENTIFIER, accepted: false } },
    ],
    [
      "predictor numeric boolean",
      { ...REPORT, predictor: { ...PREDICTOR, residual_streak: true } },
    ],
    [
      "non-finite checkpoint",
      { ...REPORT, checkpoint: { ...FOPDT, theta: Number.POSITIVE_INFINITY } },
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
  ])("rejects %s", async (_case, body) => {
    fetchMock.mockResolvedValue(response(body));

    const result = await fetchPidSpLearningReport();

    expect(result.ok).toBe(false);
    expect(result.message).toMatch(/^Invalid PID-SP learning report:/);
  });

  it.each([
    ["report live", { ...REPORT, live: 1 }],
    ["gate passed", { ...REPORT, gates: [{ ...REPORT.gates[0], passed: 1 }] }],
    [
      "identifier transition",
      { ...REPORT, identifier: { ...IDENTIFIER, transition_seen: 1 } },
    ],
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
    if (parsed.identifier?.trusted?.form === "fopdt") {
      parsed.identifier.trusted.K = -1;
    }

    expect(source.gates[0]?.name).toBe("accepted_samples");
    expect(source.identifier?.trusted?.form).toBe("fopdt");
    if (source.identifier?.trusted?.form === "fopdt") {
      expect(source.identifier.trusted.K).toBe(86.5);
    }
  });
});
