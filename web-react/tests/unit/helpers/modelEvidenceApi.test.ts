import { afterEach, describe, expect, it, type Mock, rs } from "@rstest/core";

import * as modelEvidenceApi from "../../../src/helpers/modelEvidence/modelEvidenceApi";
import { fetchModelEvidenceReport } from "../../../src/helpers/modelEvidence/modelEvidenceApi";

type FetchMock = Mock<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>;

const fetchMock: FetchMock = rs.fn();
globalThis.fetch = fetchMock;

afterEach(() => {
  fetchMock.mockReset();
});

const ACTIVE_DIGEST = "a".repeat(64);
const CANDIDATE_DIGEST = "b".repeat(64);
const CORPUS_DIGEST = "c".repeat(64);

const VALID_REPORT = {
  schema_version: 3,
  status: "evaluating",
  mode: "passive-online",
  decision_id: "causal-round-2-1",
  evidence: { count: 4, audit_count: 4, high_water: [200, "round-1"], retired_excluded: 0 },
  fit: { status: "succeeded", request_id: "fit-4", window_id: "window-4", error: null },
  cook_refit: {
    status: "idle",
    latest: "disabled",
    final_status: "disabled",
    authorization: "blocked",
    next_cook: false,
  },
  window: {
    session_id: "session-4",
    cook_id: "cook-4",
    first_observation_sequence: 1,
    last_observation_sequence: 200,
    configuration_digest: "d".repeat(64),
    incumbent_digest: ACTIVE_DIGEST,
    role_generation: 4,
  },
  checks: {},
  candidate: {
    challenger_id: "challenger-4",
    phase: "evaluating",
    digest: CANDIDATE_DIGEST,
    origin: "passive-online",
    policy: "causal-auto",
    role_generation: 4,
    candidate_generation: 5,
    parameters: {
      C_c: 4475,
      h_amb: 18,
      T_amb: 20,
      theta: 150,
      n_delay: 8,
      K_Q: 0.07,
      sigma: 0,
    },
    parameter_deltas: null,
    fit_quality: null,
    identifiability: null,
    assessment: null,
    lineage: {
      request_id: "fit-4",
      parent_incumbent_digest: ACTIVE_DIGEST,
      parent_incumbent_generation: 4,
      candidate_generation: 5,
      fit_corpus_digest: CORPUS_DIGEST,
      trigger_origin: "passive-online",
      result_status: "succeeded",
      candidate_digest: CANDIDATE_DIGEST,
    },
  },
  evaluation: {
    epoch: 2,
    round: 1,
    completed_horizons: [3, 15],
    required_horizons: [3, 15, 45, 90, 180],
    wins: 1,
    required_wins: 2,
    resumed_from_previous_cook: true,
    pending_origins: [
      {
        origin_sequence: 201,
        horizon_steps: 45,
        role_generation: 4,
        candidate_generation: 5,
        incumbent_digest: ACTIVE_DIGEST,
        candidate_digest: CANDIDATE_DIGEST,
      },
    ],
  },
  corpus: {
    digest: CORPUS_DIGEST,
    revision: 8,
    fit_partition_digest: "e".repeat(64),
    slices: [
      {
        segment_id: "segment-4",
        through_ordinal: 200,
        prefix_digest: "f".repeat(64),
        pre_roll_count: 20,
        scored_count: 180,
      },
    ],
  },
  activation: {
    phase: "aborted",
    origin: "passive-online",
    policy: "causal-auto",
    reason: null,
    pending_persistence: false,
    pending_frame_boundary_swap: false,
  },
  active_model: { digest: ACTIVE_DIGEST, role_generation: 4 },
  identities: {
    active_digest: ACTIVE_DIGEST,
    active_generation: 4,
    candidate_digest: CANDIDATE_DIGEST,
    candidate_generation: 5,
    rollback_digest: null,
    rollback_generation: null,
  },
  calibration: { revision: 0, command_high_water: 0 },
  latest_lifecycle: null,
  failure: null,
  gates: [],
  blockers: [],
  errors: [],
  revision: "1".repeat(64),
} as const;

describe("fetchModelEvidenceReport", () => {
  it("does not expose a manual MPC activation client", () => {
    expect(modelEvidenceApi).not.toHaveProperty("activateModel");
  });

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

  it.each(["digest", "origin", "policy", "role_generation", "candidate_generation"] as const)(
    "rejects a null candidate %s",
    async (field) => {
      fetchMock.mockResolvedValue(
        new Response(
          JSON.stringify({
            ...VALID_REPORT,
            candidate: { ...VALID_REPORT.candidate, [field]: null },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );

      const result = await fetchModelEvidenceReport();

      expect(result.ok).toBe(false);
      expect(result.data).toBeNull();
      expect(result.message).toMatch(/^Invalid model evidence report:/);
    },
  );

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

  it.each([
    ["report schema v2", { ...VALID_REPORT, schema_version: 2 }],
    ["MPC ready-for-review status", { ...VALID_REPORT, status: "ready-for-review" }],
    [
      "operator-reviewed candidate policy",
      {
        ...VALID_REPORT,
        candidate: { ...VALID_REPORT.candidate, policy: "operator-reviewed" },
      },
    ],
  ])("rejects the retired %s current authority", async (_name, report) => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(report), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const result = await fetchModelEvidenceReport();

    expect(result.ok).toBe(false);
    expect(result.data).toBeNull();
    expect(result.message).toMatch(/^Invalid model evidence report:/);
  });
});
