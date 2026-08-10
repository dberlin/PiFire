import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, type Mock, rs } from "@rstest/core";
import { MpcLearningPanel } from "../../../../src/components/dashboard/MpcLearningPanel";
import type {
  CookRefitOutcome,
  ModelEvidenceReport,
  ModelEvidenceStatus,
} from "../../../../src/helpers/modelEvidence/types";
import { testQueryClient } from "../../test-utils";

const ACTIVE_DIGEST = "a".repeat(64);
const CANDIDATE_DIGEST = "b".repeat(64);
const ROLLBACK_DIGEST = "c".repeat(64);
const DECISION_ID = "decision-reviewed-7";

const REPORT: ModelEvidenceReport = {
  schema_version: 2,
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
    window_id: "fit-window-7",
    error: null,
  },
  cook_refit: {
    status: "idle",
    latest: "ready-for-review",
    final_status: "ready-for-review",
    authorization: "operator-review",
    next_cook: false,
  },
  window: {
    session_id: "session-7",
    cook_id: "cook-7",
    first_observation_sequence: 101,
    last_observation_sequence: 220,
    configuration_digest: "d".repeat(64),
    incumbent_digest: ACTIVE_DIGEST,
    role_generation: 12,
  },
  checks: {
    identifiability: "passed",
    native_build: "passed",
    native_dry_solve: "passed",
    target_timing: "passed",
  },
  candidate: {
    digest: CANDIDATE_DIGEST,
    origin: "operator-calibration",
    policy: "operator-reviewed",
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
      policy: "operator-reviewed",
      fit_accepted: true,
      identifiability_accepted: true,
      native_build: "passed",
      native_dry_solve: "passed",
      target_timing: "passed",
      confidence_accepted: true,
      rejection_reasons: [],
      payload_type: "candidate_assessment",
    },
  },
  activation: {
    phase: "prepared",
    transaction_id: "transaction-7",
    candidate_digest: CANDIDATE_DIGEST,
    candidate_generation: 7,
    role_generation: 12,
    origin: "operator-calibration",
    policy: "operator-reviewed",
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
    policy: "operator-reviewed",
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
  revision: "report-revision-7",
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

function renderPanel(
  props: Partial<React.ComponentProps<typeof MpcLearningPanel>> = {},
) {
  const queryClient = testQueryClient();
  return render(
    <MpcLearningPanel
      apiBase=""
      selectedController="mpc"
      units="F"
      ambientC={20}
      learningReportRevision={1}
      {...props}
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

function passiveReport(status: ModelEvidenceStatus): ModelEvidenceReport {
  return {
    ...REPORT,
    status,
    mode: "passive-online",
    candidate: {
      ...REPORT.candidate,
      origin: "passive-online",
      policy: "passive-auto",
      assessment: REPORT.candidate.assessment
        ? {
            ...REPORT.candidate.assessment,
            origin: "passive-online",
            policy: "passive-auto",
          }
        : null,
    },
    activation: {
      ...REPORT.activation,
      origin: "passive-online",
      policy: "passive-auto",
    },
  };
}

describe("MpcLearningPanel", () => {
  it("does not request or render learning authority for a non-MPC controller", () => {
    renderPanel({ selectedController: "pid" });

    expect(screen.queryByRole("button", { name: /MPC learning/i })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows an explicit loading state while the first report is pending", async () => {
    fetchMock.mockImplementation(() => new Promise<Response>(() => {}));
    renderPanel();

    const trigger = screen.getByRole("button", { name: "MPC learning: loading" });
    await userEvent.click(trigger);
    expect(screen.getByText("Loading model evidence…")).toBeVisible();
  });

  it("renders loading, stale-data error, and retry states without replacing prior authority", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(REPORT))
      .mockResolvedValueOnce(jsonResponse({ detail: "projection unavailable" }, 503))
      .mockResolvedValueOnce(jsonResponse({ ...REPORT, status: "active" }));
    const view = renderPanel({ learningReportRevision: 10 });
    expect(await screen.findByRole("button", { name: "MPC learning: evaluating" })).toBeVisible();

    view.rerender(
      <MpcLearningPanel
        apiBase=""
        selectedController="mpc"
        units="F"
        ambientC={20}
        learningReportRevision={11}
      />,
    );
    const errorTrigger = await screen.findByRole("button", { name: "MPC learning: error" });
    await userEvent.click(errorTrigger);

    expect(screen.getByRole("alert")).toHaveTextContent("projection unavailable");
    expect(screen.getByText(CANDIDATE_DIGEST, { exact: true })).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Retry evidence report" }));
    expect(await screen.findByText("Active", { exact: true })).toBeVisible();
  });

  it.each([
    ["collecting", "Collecting"],
    ["insufficient-excitation", "Insufficient excitation"],
    ["fitting", "Fitting"],
    ["evaluating", "Evaluating"],
    ["ready-for-review", "Ready for review"],
    ["activating", "Activating"],
    ["active", "Active"],
    ["fallback", "Fallback"],
    ["error", "Error"],
    ["schema-invalidated", "Schema invalidated"],
  ] as const)("projects the backend %s status into the pill and open panel", async (status, label) => {
    fetchMock.mockResolvedValue(jsonResponse({ ...REPORT, status }));
    renderPanel();

    const trigger = await screen.findByRole("button", {
      name: `MPC learning: ${label.toLowerCase()}`,
    });
    await userEvent.click(trigger);

    expect(screen.getByText(label, { exact: true })).toBeVisible();
  });

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

      const fit = screen.getByRole("heading", { name: "Fit and evidence window" }).closest("section");
      expect(fit).not.toBeNull();
      expect(fit!).toHaveTextContent(`Status: ${fitStatus}`);
      if (fitStatus === "failed") expect(fit!).toHaveTextContent("native fitter failed");
    },
  );

  it("renders exact unified authority, native checks, persistence, swap, provenance availability, and timing", async () => {
    renderPanel();
    const dialog = await openPanel();

    expect(dialog).toHaveTextContent("Mode: operator-calibration");
    expect(dialog).toHaveTextContent("Role generation: 12");
    expect(dialog).toHaveTextContent("Candidate generation: 7");
    expect(dialog).toHaveTextContent(CANDIDATE_DIGEST);
    expect(dialog).toHaveTextContent(ACTIVE_DIGEST);
    expect(dialog).toHaveTextContent("fit-request-7");
    expect(dialog).toHaveTextContent("101–220");
    expect(dialog).toHaveTextContent("C_c");
    expect(dialog).toHaveTextContent("4475");
    expect(dialog).toHaveTextContent("Native build: passed");
    expect(dialog).toHaveTextContent("Native dry solve: passed");
    expect(dialog).toHaveTextContent("Target timing: passed");
    expect(dialog).toHaveTextContent("Ambient provenance: not reported by backend");
    expect(dialog).toHaveTextContent("Durable phase: prepared");
    expect(dialog).toHaveTextContent("Persistence pending: yes");
    expect(dialog).toHaveTextContent("Frame-boundary swap pending: yes");
    expect(dialog).toHaveTextContent("transaction-7");
    expect(dialog).toHaveTextContent("Current evidence: 8");
    expect(dialog).toHaveTextContent("Audit evidence: 10");
    expect(dialog).toHaveTextContent("Retired schema entries excluded: 2");
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
        accepted
          ? { ...REPORT, calibration: { revision: 5, command_high_water: 5 } }
          : REPORT,
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

  it.each([
    ["disabled", "blocked", false],
    ["insufficient", "blocked", false],
    ["rejected", "blocked", false],
    ["failed", "blocked", false],
    ["ready-for-review", "operator-review", false],
    ["accepted-next-cook", "next-cook", true],
    ["checkpoint-failure", "blocked", false],
  ] as const)(
    "renders cook-refit outcome %s with exact authorization and next-cook state",
    async (latest, authorization, nextCook) => {
      const cookRefit = {
        status: "idle" as const,
        latest: latest as CookRefitOutcome,
        final_status: latest as CookRefitOutcome,
        authorization,
        next_cook: nextCook,
      };
      fetchMock.mockResolvedValue(jsonResponse({ ...REPORT, cook_refit: cookRefit }));
      renderPanel();
      await openPanel();

      const section = screen.getByRole("heading", { name: "Cook refit" }).closest("section");
      expect(section).not.toBeNull();
      expect(section!).toHaveTextContent(`Final outcome: ${latest}`);
      expect(section!).toHaveTextContent(`Authorization: ${authorization}`);
      expect(section!).toHaveTextContent(`Next cook: ${nextCook ? "yes" : "no"}`);
    },
  );

  it("never exposes reviewed activation controls for passive automatic authority", async () => {
    fetchMock.mockResolvedValue(jsonResponse(passiveReport("ready-for-review")));
    renderPanel();
    await openPanel();

    expect(screen.queryByLabelText("Type the exact candidate digest")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Type the exact confidence decision ID")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Activate exact model" })).not.toBeInTheDocument();
  });

  it("requires the exact reviewed digest and decision and posts only those serialized names", async () => {
    const ready = { ...REPORT, status: "ready-for-review" as const };
    const activating = {
      ...ready,
      status: "activating" as const,
      activation: {
        ...ready.activation,
        phase: "prepared" as const,
        pending_persistence: false,
        pending_frame_boundary_swap: true,
      },
    };
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/api/model-evidence/activate")) {
        expect(JSON.parse(String(init?.body))).toEqual({
          candidate_digest: CANDIDATE_DIGEST,
          decision_id: DECISION_ID,
        });
        return jsonResponse({
          accepted: true,
          phase: "prepared",
          transaction_id: "transaction-7",
          decision_id: DECISION_ID,
          candidate_digest: CANDIDATE_DIGEST,
          role_generation: 12,
        });
      }
      const reportReads = fetchMock.mock.calls.filter(([request]) =>
        String(request).endsWith("/api/model-evidence/report"),
      ).length;
      return jsonResponse(reportReads > 1 ? activating : ready);
    });
    renderPanel();
    await openPanel();

    const activate = screen.getByRole("button", { name: "Activate exact model" });
    expect(activate).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Type the exact candidate digest"), CANDIDATE_DIGEST);
    await userEvent.type(screen.getByLabelText("Type the exact confidence decision ID"), "wrong");
    expect(activate).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Type the exact confidence decision ID"));
    await userEvent.type(screen.getByLabelText("Type the exact confidence decision ID"), DECISION_ID);
    expect(activate).toBeEnabled();
    await userEvent.click(activate);

    expect(await screen.findByText("Activating", { exact: true })).toBeVisible();
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
      activation: { ...active.activation, reason: "operator requested rollback" },
    };
    fetchMock.mockImplementation(async (input, init) => {
      if (String(input).endsWith("/api/model-evidence/rollback")) {
        expect(JSON.parse(String(init?.body))).toEqual({ reason: "active-solve-failed" });
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
        identities: { ...active.identities, rollback_digest: null, rollback_generation: null },
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
        fit: { ...REPORT.fit, status: "failed", error: "optimizer-nonconvergence" },
        candidate: {
          ...REPORT.candidate,
          assessment: {
            ...REPORT.candidate.assessment!,
            fit_accepted: false,
            confidence_accepted: false,
            rejection_reasons: ["identifiability", "target-timing"],
          },
        },
        activation: { ...REPORT.activation, phase: "aborted", reason: "swap-compensated" },
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
    expect(screen.getByRole("heading", { name: "Fit and evidence window" }).closest("section"))
      .toHaveTextContent("optimizer-nonconvergence");
    expect(screen.getByRole("heading", { name: "Readiness and rejection" }).closest("section"))
      .toHaveTextContent("identifiability");
    expect(screen.getByRole("heading", { name: "Activation and swap" }).closest("section"))
      .toHaveTextContent("swap-compensated");
  });

  it("invalidates immediately on socket revision and ignores the superseded response", async () => {
    let resolveFirst: ((response: Response) => void) | undefined;
    fetchMock
      .mockImplementationOnce(
        (_input, init) =>
          new Promise<Response>((resolve, reject) => {
            resolveFirst = resolve;
            init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          }),
      )
      .mockResolvedValueOnce(jsonResponse({ ...REPORT, status: "active", revision: "new" }));
    const view = renderPanel({ learningReportRevision: 40 });

    view.rerender(
      <MpcLearningPanel
        apiBase=""
        selectedController="mpc"
        units="F"
        ambientC={20}
        learningReportRevision={41}
      />,
    );
    expect(await screen.findByRole("button", { name: "MPC learning: active" })).toBeVisible();

    await act(async () => {
      resolveFirst?.(jsonResponse({ ...REPORT, status: "collecting", revision: "old" }));
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "MPC learning: active" })).toBeVisible();
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
