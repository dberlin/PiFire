import type { ModelEvidenceReport } from "@pifire/core/contracts/learning";
import { afterEach, beforeEach, describe, expect, it, type Mock, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MpcLearningView } from "../../../../src/components/dashboard/learning/MpcLearningView";
import { testQueryClient } from "../../test-utils";

const ACTIVE_DIGEST = "a".repeat(64);
const CANDIDATE_DIGEST = "b".repeat(64);
const ROLLBACK_DIGEST = "c".repeat(64);
const CORPUS_DIGEST = "d".repeat(64);
const FIT_PARTITION_DIGEST = "e".repeat(64);
const PREFIX_DIGEST = "1".repeat(64);
const DECISION_ID = "causal-round-3-1";

const REPORT: ModelEvidenceReport = {
  schema_version: 3,
  status: "evaluating",
  mode: "operator-calibration",
  decision_id: DECISION_ID,
  evidence: {
    count: 8,
    audit_count: 10,
    high_water: [1_780_000_000_000, "evidence-8"],
    retired_excluded: 2,
  },
  fit: {
    status: "succeeded",
    request_id: "fit-request-7",
    fit_corpus_digest: CORPUS_DIGEST,
    error: null,
  },
  checks: {
    identifiability: "passed",
    native_build: "passed",
    native_dry_solve: "passed",
    target_timing: "passed",
  },
  candidate: {
    challenger_id: "challenger-7",
    phase: "evaluating",
    digest: CANDIDATE_DIGEST,
    origin: "operator-calibration",
    policy: "causal-auto",
    role_generation: 12,
    candidate_generation: 7,
    parameters: {
      C_c: 4475,
      h_amb: 18.5,
      T_amb: 20,
      theta: 150,
      n_delay: 8,
      K_Q: 0.076,
      sigma: 0,
    },
    parameter_deltas: null,
    fit_quality: 1.2,
    identifiability: null,
    assessment: {
      decision_id: DECISION_ID,
      origin: "operator-calibration",
      policy: "causal-auto",
      fit_accepted: true,
      identifiability_accepted: true,
      native_build: "passed",
      native_dry_solve: "passed",
      target_timing: "passed",
      confidence_accepted: true,
      rejection_reasons: [],
      payload_type: "candidate_assessment",
    },
    lineage: {
      request_id: "fit-request-7",
      parent_incumbent_digest: ACTIVE_DIGEST,
      parent_incumbent_generation: 12,
      candidate_generation: 7,
      fit_corpus_digest: CORPUS_DIGEST,
      trigger_origin: "operator-calibration",
      result_status: "succeeded",
      candidate_digest: CANDIDATE_DIGEST,
    },
  },
  evaluation: {
    epoch: 3,
    round: 1,
    completed_horizons: [3, 15, 45],
    required_horizons: [3, 15, 45, 90, 180],
    wins: 1,
    required_wins: 2,
    resumed_from_previous_cook: true,
    pending_origins: [
      {
        origin_sequence: 221,
        horizon_steps: 90,
        role_generation: 12,
        candidate_generation: 7,
        incumbent_digest: ACTIVE_DIGEST,
        candidate_digest: CANDIDATE_DIGEST,
      },
    ],
  },
  corpus: {
    digest: CORPUS_DIGEST,
    revision: 17,
    fit_partition_digest: FIT_PARTITION_DIGEST,
    slices: [
      {
        segment_id: "segment-cook-7-hold-1",
        through_ordinal: 120,
        prefix_digest: PREFIX_DIGEST,
        pre_roll_count: 24,
        scored_count: 96,
      },
    ],
  },
  activation: {
    phase: "prepared",
    transaction_id: "transaction-7",
    candidate_digest: CANDIDATE_DIGEST,
    candidate_generation: 7,
    role_generation: 12,
    origin: "operator-calibration",
    policy: "causal-auto",
    reason: null,
    pending_persistence: true,
    pending_frame_boundary_swap: true,
  },
  active_model: {
    digest: ACTIVE_DIGEST,
    role_generation: 12,
  },
  identities: {
    active_digest: ACTIVE_DIGEST,
    active_generation: 12,
    candidate_digest: CANDIDATE_DIGEST,
    candidate_generation: 7,
    rollback_digest: ROLLBACK_DIGEST,
    rollback_generation: 11,
  },
  calibration: {
    revision: 4,
    command_high_water: 4,
  },
  latest_lifecycle: {
    decision_id: DECISION_ID,
    phase: "prepared",
    origin: "operator-calibration",
    policy: "causal-auto",
    reason: null,
    payload_type: "activation_lifecycle",
  },
  failure: null,
  gates: [
    { name: "native_build", passed: true, reason: null },
    { name: "native_dry_solve", passed: true, reason: null },
    { name: "target_timing", passed: true, reason: null },
  ],
  blockers: [],
  errors: [],
  revision: "9".repeat(64),
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

type FetchMock = Mock<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>;
let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = rs.fn(async () => jsonResponse(REPORT));
  globalThis.fetch = fetchMock as typeof fetch;
});

afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
  rs.useRealTimers();
});

function renderPanel(props: Partial<React.ComponentProps<typeof MpcLearningView>> = {}) {
  const queryClient = testQueryClient();
  return render(
    <MpcLearningView
      apiBase=""
      selectedController="mpc"
      units="F"
      ambientC={20}
      modelLearningRevision="wire-1"
      {...props}
      currentMode="Hold"
      displayMode="Hold"
      criticalError={false}
    />,
    {
      wrapper: ({ children }: React.PropsWithChildren) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
      ),
    },
  );
}

async function openPanel() {
  await userEvent.click(await screen.findByRole("button", { name: /MPC learning:/i }));
  return screen.findByRole("dialog", { name: "MPC model learning" });
}

describe("MpcLearningView", () => {
  it("does not request or render learning authority for a non-MPC controller", () => {
    renderPanel({ selectedController: "pid" });

    expect(screen.queryByRole("button", { name: /MPC learning/i })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("supplies the shared-shell labels and keeps every calibration action", async () => {
    renderPanel();

    expect(await screen.findByRole("button", { name: "MPC learning: evaluating" })).toBeVisible();
    const dialog = await openPanel();
    expect(dialog).toHaveAccessibleName("MPC model learning");
    for (const action of [
      "Start calibration",
      "Pause calibration",
      "Resume calibration",
      "Stop calibration",
      "Reset calibration progress",
    ]) {
      expect(screen.getByRole("button", { name: action })).toBeVisible();
    }
  });

  it("shows an explicit loading state while the first report is pending", async () => {
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
    renderPanel();

    const trigger = screen.getByRole("button", {
      name: "MPC learning: loading",
    });
    await userEvent.click(trigger);
    expect(screen.getByText("Loading model evidence…")).toBeVisible();
  });

  it("renders loading, stale-data error, and retry states without replacing prior authority", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(REPORT))
      .mockResolvedValueOnce(jsonResponse({ detail: "projection unavailable" }, 503))
      .mockResolvedValueOnce(jsonResponse({ ...REPORT, status: "active" }));
    const view = renderPanel({ modelLearningRevision: "wire-10" });
    expect(await screen.findByRole("button", { name: "MPC learning: evaluating" })).toBeVisible();

    view.rerender(
      <MpcLearningView
        apiBase=""
        selectedController="mpc"
        units="F"
        ambientC={20}
        modelLearningRevision="wire-11"
        currentMode="Hold"
        displayMode="Hold"
        criticalError={false}
      />,
    );
    const errorTrigger = await screen.findByRole("button", {
      name: "MPC learning: error",
    });
    await userEvent.click(errorTrigger);

    expect(screen.getByRole("alert")).toHaveTextContent("projection unavailable");
    expect(screen.getByText(CANDIDATE_DIGEST, { exact: true })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Retry evidence report" }));
    expect(await screen.findByText("Active", { exact: true })).toBeVisible();
  });

  it.each([
    ["warming", "Warming"],
    ["collecting", "Collecting"],
    ["fitting", "Fitting"],
    ["evaluating", "Evaluating"],
    ["interrupted", "Interrupted"],
    ["qualified", "Qualified"],
    ["activating", "Activating"],
    ["active", "Active"],
    ["fallback", "Fallback"],
    ["error", "Error"],
  ] as const)(
    "projects the backend %s status into the pill and open panel",
    async (status, label) => {
      fetchMock.mockResolvedValue(jsonResponse({ ...REPORT, status }));
      renderPanel();

      const trigger = await screen.findByRole("button", {
        name: `MPC learning: ${label.toLowerCase()}`,
      });
      await userEvent.click(trigger);

      expect(screen.getByText(label, { exact: true })).toBeVisible();
    },
  );

  it.each(["idle", "queued", "running", "succeeded", "failed", "stale"] as const)(
    "renders fit status %s without deriving it from the top-level state",
    async (fitStatus) => {
      fetchMock.mockResolvedValue(
        jsonResponse({
          ...REPORT,
          fit: {
            ...REPORT.fit,
            status: fitStatus,
            error: fitStatus === "failed" ? "native fitter failed" : null,
          },
        }),
      );
      renderPanel();
      await openPanel();

      const fit = screen.getByRole("heading", { name: "Fit request" }).closest("section");
      expect(fit).not.toBeNull();
      expect(fit!).toHaveTextContent(`Status: ${fitStatus}`);
      if (fitStatus === "failed") expect(fit!).toHaveTextContent("native fitter failed");
    },
  );

  it("renders exact causal progress and candidate/corpus lineage", async () => {
    renderPanel();
    const dialog = await openPanel();

    expect(dialog).toHaveTextContent("Mode: operator-calibration");
    expect(dialog).toHaveTextContent("Evaluation epoch: 3");
    expect(dialog).toHaveTextContent("Evaluation round: 1");
    expect(dialog).toHaveTextContent("Completed horizons: 3, 15, 45");
    expect(dialog).toHaveTextContent("Required horizons: 3, 15, 45, 90, 180");
    expect(dialog).toHaveTextContent("Wins: 1 / 2");
    expect(dialog).toHaveTextContent("Resumed from previous cook: yes");
    expect(dialog).toHaveTextContent("Origin sequence: 221");
    expect(dialog).toHaveTextContent("Horizon: 90");

    expect(dialog).toHaveTextContent("Challenger: challenger-7");
    expect(dialog).toHaveTextContent("Role generation: 12");
    expect(dialog).toHaveTextContent("Candidate generation: 7");
    expect(dialog).toHaveTextContent("Parent incumbent generation: 12");
    expect(dialog).toHaveTextContent("Trigger origin: operator-calibration");
    expect(dialog).toHaveTextContent("Fit result: succeeded");
    expect(dialog).toHaveTextContent(CANDIDATE_DIGEST);
    expect(dialog).toHaveTextContent(ACTIVE_DIGEST);
    expect(dialog).toHaveTextContent("fit-request-7");
    expect(dialog).toHaveTextContent("Fit corpus:");

    expect(dialog).toHaveTextContent("Corpus revision: 17");
    expect(dialog).toHaveTextContent(CORPUS_DIGEST);
    expect(dialog).toHaveTextContent(FIT_PARTITION_DIGEST);
    expect(dialog).toHaveTextContent("segment-cook-7-hold-1");
    expect(dialog).toHaveTextContent("Through ordinal: 120");
    expect(dialog).toHaveTextContent(PREFIX_DIGEST);
    expect(dialog).toHaveTextContent("Pre-roll: 24");
    expect(dialog).toHaveTextContent("Scored: 96");

    expect(dialog).toHaveTextContent("C_c");
    expect(dialog).toHaveTextContent("4475");
    expect(dialog).toHaveTextContent("Native build: passed");
    expect(dialog).toHaveTextContent("Native dry solve: passed");
    expect(dialog).toHaveTextContent("Target timing: passed");
    expect(dialog).toHaveTextContent("Durable phase: prepared");
    expect(dialog).toHaveTextContent("Frame-boundary swap pending: yes");
    expect(dialog).toHaveTextContent("Current evidence: 8");
    expect(dialog).toHaveTextContent("Audit evidence: 10");
    expect(dialog).toHaveTextContent("Retired schema entries excluded: 2");
  });

  it("renders current corpus and fit identity without a candidate or evaluation", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        candidate: null,
        evaluation: null,
      }),
    );
    renderPanel();
    const dialog = await openPanel();

    expect(dialog).toHaveTextContent("No causal evaluation progress is currently reported.");
    expect(dialog).toHaveTextContent("No challenger is currently active.");
    expect(dialog).toHaveTextContent(`Fit corpus: ${CORPUS_DIGEST}`);
    expect(dialog).toHaveTextContent(`Corpus digest: ${CORPUS_DIGEST}`);
  });

  it("gates calibration start on acknowledgements and advances from backend command high-water", async () => {
    let accepted = false;
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).endsWith("/api/set_mpc_calibration")) {
        const body = JSON.parse(String(init?.body));
        expect(body).toEqual({
          action: "start",
          revision: 5,
          ambient_c: 20,
          ambient_source: "configured",
          empty_grill_confirmed: true,
          pellets_confirmed: true,
        });
        accepted = true;
        return jsonResponse({
          result: "OK",
          message: "accepted",
          data: { mpc_calibration: body },
        });
      }
      return jsonResponse(
        accepted ? { ...REPORT, calibration: { revision: 5, command_high_water: 5 } } : REPORT,
      );
    });
    renderPanel();
    await openPanel();

    const start = screen.getByRole("button", { name: "Start calibration" });
    expect(start).toBeDisabled();
    await userEvent.click(
      screen.getByLabelText("The grill is empty, with normal grates and drip tray installed."),
    );
    await userEvent.click(
      screen.getByLabelText("Sufficient pellets are loaded for the calibration run."),
    );
    expect(start).toBeEnabled();
    await userEvent.click(start);
    expect(await screen.findByText("Accepted command high-water: 5")).toBeVisible();
  });

  it("shows an interrupted causal evaluation as resumable durable progress", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        status: "interrupted",
        candidate: {
          ...REPORT.candidate!,
          phase: "evaluating",
        },
        evaluation: {
          ...REPORT.evaluation!,
          epoch: 4,
          round: 0,
          completed_horizons: [],
          wins: 1,
          pending_origins: [],
          resumed_from_previous_cook: true,
        },
      }),
    );
    renderPanel();
    const dialog = await openPanel();

    expect(dialog).toHaveTextContent("Interrupted");
    expect(dialog).toHaveTextContent("Evaluation epoch: 4");
    expect(dialog).toHaveTextContent("Evaluation round: 0");
    expect(dialog).toHaveTextContent("Completed horizons: none");
    expect(dialog).toHaveTextContent("Wins: 1 / 2");
    expect(dialog).toHaveTextContent("Resumed from previous cook: yes");
    expect(dialog).toHaveTextContent("Pending origins: none");
  });

  it("exposes no manual review controls, operator-review copy, or activation request", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        status: "qualified",
        candidate: {
          ...REPORT.candidate!,
          phase: "qualified",
        },
        evaluation: {
          ...REPORT.evaluation!,
          round: 2,
          completed_horizons: [...REPORT.evaluation!.required_horizons],
          wins: 2,
          pending_origins: [],
        },
      }),
    );
    renderPanel();
    const dialog = await openPanel();

    expect(dialog).toHaveTextContent("Qualified");
    expect(dialog).toHaveTextContent("Wins: 2 / 2");
    expect(screen.queryByLabelText("Type the exact candidate digest")).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Type the exact confidence decision ID"),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate exact model" })).not.toBeInTheDocument();
    expect(dialog).not.toHaveTextContent(/operator review|ready for review|reviewed model/i);
    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).endsWith("/api/model-evidence/activate"),
      ),
    ).toBe(false);
  });

  it("shows rollback only for an explicit active rollback owner and posts the reason", async () => {
    const active = {
      ...REPORT,
      status: "active" as const,
      activation: { ...REPORT.activation, phase: "active" as const },
    };
    const fallback = {
      ...active,
      status: "fallback" as const,
      activation: {
        ...active.activation,
        reason: "operator requested rollback",
      },
    };
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).endsWith("/api/model-evidence/rollback")) {
        expect(JSON.parse(String(init?.body))).toEqual({
          reason: "active-solve-failed",
        });
        return jsonResponse({
          accepted: true,
          active_kind: "grey-box",
          decision_id: DECISION_ID,
          reason: "operator requested rollback",
          role_generation: 13,
          rollback_digest: ROLLBACK_DIGEST,
        });
      }
      const reads = fetchMock.mock.calls.filter(([request]) =>
        String(request).endsWith("/api/model-evidence/report"),
      ).length;
      return jsonResponse(reads > 1 ? fallback : active);
    });
    renderPanel();
    await openPanel();

    await userEvent.type(screen.getByLabelText("Required rollback reason"), "active-solve-failed");
    await userEvent.click(screen.getByRole("button", { name: "Roll back to explicit owner" }));
    expect(await screen.findByText("Fallback", { exact: true })).toBeVisible();

    cleanup();
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...active,
        identities: {
          ...active.identities,
          rollback_digest: null,
          rollback_generation: null,
        },
      }),
    );
    renderPanel();
    await openPanel();
    expect(screen.queryByLabelText("Required rollback reason")).not.toBeInTheDocument();
  });

  it("renders rejected assessment, fallback reason, fit failure, and structured terminal failure", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        status: "fallback",
        fit: {
          ...REPORT.fit,
          status: "failed",
          error: "optimizer-nonconvergence",
        },
        candidate: {
          ...REPORT.candidate!,
          assessment: {
            ...REPORT.candidate!.assessment!,
            fit_accepted: false,
            confidence_accepted: false,
            rejection_reasons: ["identifiability", "target-timing"],
          },
        },
        activation: {
          ...REPORT.activation,
          phase: "aborted",
          reason: "swap-compensated",
        },
        blockers: ["identifiability", "target-timing"],
        errors: ["activation-terminal"],
        failure: {
          code: "activation-terminal",
          detail: "native solver crashed",
          terminal: true,
        },
      }),
    );
    renderPanel();
    await openPanel();

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("activation-terminal");
    expect(alert).toHaveTextContent("native solver crashed");
    expect(alert).toHaveTextContent("terminal");
    expect(
      screen.getByRole("heading", { name: "Fit request" }).closest("section"),
    ).toHaveTextContent("optimizer-nonconvergence");
    expect(
      screen.getByRole("heading", { name: "Readiness and rejection" }).closest("section"),
    ).toHaveTextContent("identifiability");
    expect(
      screen.getByRole("heading", { name: "Activation and swap" }).closest("section"),
    ).toHaveTextContent("swap-compensated");
  });

  it("invalidates immediately on socket revision and ignores the superseded response", async () => {
    let resolveFirst: ((response: Response) => void) | undefined;
    fetchMock
      .mockImplementationOnce(
        (_input, init) =>
          new Promise<Response>((resolve, reject) => {
            resolveFirst = resolve;
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ ...REPORT, status: "active", revision: "f".repeat(64) }),
      );
    const view = renderPanel({ modelLearningRevision: "wire-40" });

    view.rerender(
      <MpcLearningView
        apiBase=""
        selectedController="mpc"
        units="F"
        ambientC={20}
        modelLearningRevision="wire-41"
        currentMode="Hold"
        displayMode="Hold"
        criticalError={false}
      />,
    );
    expect(await screen.findByRole("button", { name: "MPC learning: active" })).toBeVisible();

    await act(async () => {
      resolveFirst?.(jsonResponse({ ...REPORT, status: "collecting", revision: "1".repeat(64) }));
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "MPC learning: active" })).toBeVisible();
  });

  it("keeps schema-invalidated authority absent through refresh errors until a valid report returns", async () => {
    const published = REPORT;
    const restored = {
      ...REPORT,
      status: "active" as const,
      activation: { ...REPORT.activation, phase: "active" as const },
      revision: "2".repeat(64),
    };
    fetchMock
      .mockResolvedValueOnce(jsonResponse(published))
      .mockResolvedValueOnce(
        jsonResponse({
          ...published,
          candidate: {
            ...published.candidate!,
            parameters: {
              ...published.candidate!.parameters!,
              C_c: "NaN",
            },
          },
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ detail: "temporarily unavailable" }, 503))
      .mockResolvedValueOnce(jsonResponse(restored));
    const view = renderPanel({ modelLearningRevision: "wire-valid" });
    await openPanel();
    expect(screen.queryByRole("button", { name: "Activate exact model" })).not.toBeInTheDocument();
    expect(screen.getAllByText(CANDIDATE_DIGEST).length).toBeGreaterThan(0);

    view.rerender(
      <MpcLearningView
        apiBase=""
        selectedController="mpc"
        units="F"
        ambientC={20}
        modelLearningRevision="wire-invalid"
        currentMode="Hold"
        displayMode="Hold"
        criticalError={false}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid model evidence report: candidate.parameters.C_c must be a finite number",
    );
    expect(screen.queryByRole("button", { name: "Activate exact model" })).not.toBeInTheDocument();
    expect(screen.queryByText(CANDIDATE_DIGEST)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Roll back to explicit owner" }),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry evidence report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("temporarily unavailable");
    expect(screen.queryByRole("button", { name: "Activate exact model" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Roll back to explicit owner" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(CANDIDATE_DIGEST)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Retry evidence report" }));
    expect(
      await screen.findByRole("button", { name: "Roll back to explicit owner" }),
    ).toBeVisible();
    expect(screen.getAllByText(CANDIDATE_DIGEST).length).toBeGreaterThan(0);
  });

  it("recovers through the five-second poll after a failed projection", async () => {
    rs.useFakeTimers();
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ detail: "temporarily unavailable" }, 503))
      .mockResolvedValueOnce(jsonResponse(REPORT));
    renderPanel();
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByRole("button", { name: "MPC learning: error" })).toBeVisible();

    await act(async () => {
      await rs.advanceTimersByTimeAsync(5_000);
      await rs.advanceTimersByTimeAsync(1);
    });
    expect(screen.getByRole("button", { name: "MPC learning: evaluating" })).toBeVisible();
  });
});
