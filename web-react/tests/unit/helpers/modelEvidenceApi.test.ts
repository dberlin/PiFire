import { afterEach, describe, expect, it, type Mock, rs } from "@rstest/core";

import { fetchModelEvidenceReport } from "../../../src/helpers/modelEvidence/modelEvidenceApi";

type FetchMock = Mock<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>;

const fetchMock: FetchMock = rs.fn();
globalThis.fetch = fetchMock;

afterEach(() => {
  fetchMock.mockReset();
});

const VALID_REPORT = {
  schema_version: 2,
  status: "collecting",
  mode: null,
  decision_id: null,
  evidence: { count: 0, audit_count: 0, high_water: null, retired_excluded: 0 },
  fit: { status: "idle", request_id: null, window_id: null, error: null },
  cook_refit: {
    status: "idle",
    latest: null,
    final_status: "idle",
    authorization: "blocked",
    next_cook: false,
  },
  window: null,
  checks: {},
  candidate: {
    digest: null,
    origin: null,
    policy: null,
    role_generation: null,
    candidate_generation: null,
    parameters: null,
    parameter_deltas: null,
    fit_quality: null,
    identifiability: null,
    assessment: null,
  },
  activation: {
    phase: "aborted",
    reason: null,
    pending_persistence: false,
    pending_frame_boundary_swap: false,
  },
  active_model: { digest: null, role_generation: null },
  identities: {
    active_digest: null,
    active_generation: null,
    candidate_digest: null,
    candidate_generation: null,
    rollback_digest: null,
    rollback_generation: null,
  },
  calibration: { revision: 0, command_high_water: 0 },
  latest_lifecycle: null,
  failure: null,
  gates: [],
  blockers: [],
  errors: [],
  revision: "a".repeat(64),
} as const;

describe("fetchModelEvidenceReport", () => {
  it("fails closed on a malformed success response instead of publishing authority", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          ...VALID_REPORT,
          status: "active",
          candidate: {
            ...VALID_REPORT.candidate,
            parameters: {
              C_c: "NaN",
              h_amb: 18,
              T_amb: 20,
              theta: 150,
              n_delay: 8,
              K_Q: 0.07,
              sigma: 0,
            },
          },
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await expect(fetchModelEvidenceReport()).resolves.toEqual({
      ok: false,
      status: 200,
      message: expect.stringMatching(/^Invalid model evidence report:/),
      data: null,
    });
  });

  it("returns a strictly valid success report", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(VALID_REPORT), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await fetchModelEvidenceReport();

    expect(result.ok).toBe(true);
    expect(result.data).toEqual(VALID_REPORT);
  });

  it("accepts the unified causal-auto policy", async () => {
    const report = {
      ...VALID_REPORT,
      candidate: {
        ...VALID_REPORT.candidate,
        origin: "passive-online",
        policy: "causal-auto",
      },
    };
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(report), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await fetchModelEvidenceReport();

    expect(result.ok).toBe(true);
    expect(result.data).toEqual(report);
  });
});
