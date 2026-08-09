import { afterEach, beforeEach, describe, expect, it, type Mock, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MpcLearningPanel } from "../../../../src/components/dashboard/MpcLearningPanel";
import type { ModelEvidenceReport } from "../../../../src/helpers/modelEvidence/types";

const REPORT: ModelEvidenceReport = {
  schema_version: 2,
  status: "evaluating",
  mode: "passive",
  origin: "passive-online",
  role_generation: 12,
  candidate_generation: 7,
  decision_id: "decision-7",
  enable_online_adaptation: true,
  enable_identification: true,
  active_model: {
    kind: "grey-box",
    digest: "active-digest-1234567890",
    model_schema: 4,
    role_generation: 12,
    candidate_generation: 6,
  },
  default_model: {
    kind: "grey-box",
    digest: "default-digest-1234567890",
    model_schema: 4,
    role_generation: 0,
    candidate_generation: 0,
  },
  candidate: {
    kind: "grey-box",
    digest: "candidate-digest-abcdef123456",
    model_schema: 4,
    role_generation: 12,
    candidate_generation: 7,
  },
  rollback_owner: {
    kind: "grey-box",
    digest: "rollback-digest-1234567890",
    model_schema: 4,
    role_generation: 11,
    candidate_generation: 6,
  },
  observation: {
    window_id: "window-passive-42",
    eligible_count: 218,
    ineligible_count: 9,
    rejection_reasons: [
      { reason: "lid-open", count: 4 },
      { reason: "frame-not-complete", count: 5 },
    ],
    probe_provenance: "ordinary-and-calibration",
    mixed_window_authority: "operator-calibration",
  },
  calibration: {
    status: "active",
    stage: "low",
    current_probe: 0.04,
    completed_stages: ["cold-start"],
    missing_stages: ["middle", "high", "coast"],
    eligible_count: 18,
    ineligible_count: 4,
    ineligible_reasons: ["lid_open", "stale_result"],
    timed_out: false,
    incomplete: false,
    revision: 12,
  },
  fit: {
    status: "succeeded",
    job_id: "fit-passive-42",
    process_id: 4242,
    role_generation: 12,
    origin: "passive-online",
    window: {
      window_id: "window-passive-42",
      session_id: "session-9",
      cook_id: "cook-31",
      sample_count: 218,
      config_digest: "config-digest-42",
      incumbent_digest: "active-digest-1234567890",
      started_at_ms: 1_780_000_000_000,
      ended_at_ms: 1_780_000_600_000,
    },
    result: {
      reason: "converged",
      solver_iterations: 17,
      finished_at_ms: 1_780_000_601_250,
    },
  },
  grey_parameters: [
    {
      name: "C_c",
      unit: "J/°C",
      incumbent_value: 4200,
      candidate_value: 4475,
      delta: 275,
    },
    {
      name: "K_Q",
      unit: "°C/s",
      incumbent_value: 0.071,
      candidate_value: 0.076,
      delta: 0.005,
    },
    {
      name: "theta",
      unit: "s",
      incumbent_value: 135,
      candidate_value: 150,
      delta: 15,
    },
  ],
  candidate_structure: {
    prediction_step_seconds: 25,
    delay_states: 8,
    horizon_steps: 12,
  },
  identifiability: {
    status: "failed",
    reason: "C_c interval overlaps physical bound",
    matrix_rank: 3,
    parameter_count: 3,
    condition_number: 28.4,
    finite_diagnostics: true,
    confidence_intervals: {
      C_c: { lower: 3900, upper: 4700 },
      K_Q: { lower: 0.068, upper: 0.082 },
      theta: { lower: 125, upper: 176 },
    },
    physical_bounds: {
      status: "failed",
      detail: "C_c interval overlaps physical bound",
    },
  },
  native: {
    build: {
      status: "passed",
      build_digest: "native-build-digest-42",
      manifest_digest: "native-build-digest-42",
      detail: "candidate handle built off-path",
    },
    dry_solve: {
      status: "passed",
      solve_time_ms: 18.75,
      finite_diagnostics: true,
      detail: "representative 12-step solve passed",
    },
  },
  scores: [
    {
      horizon_steps: 12,
      temperature_band: "low",
      phase: "heating",
      ambient_source: "configured",
      candidate_generation: 7,
      challenger_rmse_c: 1.2,
      incumbent_rmse_c: 1.8,
      challenger_bias_c: -0.15,
      incumbent_bias_c: 0.25,
      challenger_band_error_c: 0.42,
      incumbent_band_error_c: 0.68,
      bootstrap: {
        available: true,
        method: "hierarchical-cook-block",
        replicate_count: 10_000,
        rmse_ratio_upper_bound: 0.91,
      },
    },
  ],
  gates: [
    {
      name: "identifiability",
      status: "failed",
      reason: "C_c interval overlaps physical bound",
    },
    { name: "target-timing", status: "passed", reason: null },
    { name: "native-dry-solve", status: "passed", reason: null },
  ],
  missing_gates: ["identifiability"],
  blockers: ["C_c interval overlaps physical bound"],
  activation: {
    policy: "passive-auto",
    reason: "passive-auto",
    decision_id: "decision-7",
    persistence: {
      status: "passed",
      phase: "prepared",
      record_id: "activation-record-7",
      detail: "candidate and incumbent identities durably prepared",
    },
    pending_swap: {
      status: "pending",
      frame_boundary: 843,
      detail: "waiting for completed frame",
    },
  },
  rollback: {
    permitted: false,
    confidence_window_remaining: 24,
    latest_reason: null,
  },
  cook_refit: {
    authorized: true,
    status: "not-run",
    outcome: null,
    activation_timing: "next-cook-restore",
  },
  target_timing: {
    available: true,
    sample_count: 420,
    p50_ms: 12.4,
    p95_ms: 26.8,
    p99_ms: 41.7,
    hardware_provenance: "Raspberry Pi 5 / target-hardware",
    status: "passed",
  },
  lifecycle: [
    {
      phase: "fit-succeeded",
      timestamp_ms: 1_780_000_601_250,
      reason: "converged",
      role_generation: 12,
      candidate_generation: 7,
    },
    {
      phase: "persistence-prepared",
      timestamp_ms: 1_780_000_601_500,
      reason: "passive-auto",
      role_generation: 12,
      candidate_generation: 7,
    },
  ],
  errors: [],
  history: [],
  ambient_provenance_limitation:
    "Ambient temperature is configured, not measured; ambient gain is not separately identified.",
  artifact_metadata: {
    schema_version: 2,
    provenance_digest: "provenance-digest",
    bootstrap_seed: 17,
    bootstrap_replicates: 10_000,
    decision_id: "decision-7",
    evidence_ids: ["evidence-1"],
  },
};

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

type FetchMock = Mock<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>;
interface PromiseResolvers<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason?: unknown): void;
}

const promiseWithResolvers = Promise as PromiseConstructor & {
  withResolvers<T>(): PromiseResolvers<T>;
};

let fetchMock: FetchMock;

beforeEach(() => {
  fetchMock = rs.fn(async () => jsonResponse(REPORT));
  globalThis.fetch = fetchMock as typeof fetch;
});

afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
});

type PanelProps = React.ComponentProps<typeof MpcLearningPanel> & {
  learningReportRevision?: number;
};

const RevisionAwareMpcLearningPanel = MpcLearningPanel as React.ComponentType<PanelProps>;

function renderPanel(props: Partial<PanelProps> = {}) {
  return render(
    <RevisionAwareMpcLearningPanel
      apiBase=""
      selectedController="mpc"
      units="F"
      ambientC={20}
      {...props}
    />,
  );
}

async function openPanel() {
  await userEvent.click(await screen.findByRole("button", { name: /MPC learning:/i }));
  return screen.findByRole("dialog", { name: "MPC model learning" });
}

describe("MpcLearningPanel", () => {
  it("does not request or render model evidence for a non-MPC selection", () => {
    renderPanel({ selectedController: "pid" });

    expect(screen.queryByRole("button", { name: /MPC learning/i })).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("shows an explicit loading state while the report is pending", async () => {
    fetchMock.mockImplementation(() => promiseWithResolvers.withResolvers<Response>().promise);
    renderPanel();

    expect(screen.getByRole("button", { name: "MPC learning: loading" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "MPC learning: loading" }));
    expect(screen.getByText("Loading model evidence…")).toBeInTheDocument();
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
  ] as const)("exposes the %s report status in the pill and panel", async (status, label) => {
    fetchMock.mockResolvedValue(jsonResponse({ ...REPORT, status }));
    renderPanel();

    const trigger = await screen.findByRole("button", {
      name: `MPC learning: ${label.toLowerCase()}`,
    });
    await userEvent.click(trigger);

    expect(screen.getByRole("dialog", { name: "MPC model learning" })).toBeInTheDocument();
    expect(screen.getByText(label, { exact: true })).toBeInTheDocument();
  });

  it("shows a retriable report error rather than an empty success state", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ message: "ledger unavailable" }, 503))
      .mockResolvedValueOnce(jsonResponse(REPORT));
    renderPanel();
    await openPanel();

    expect(await screen.findByRole("alert")).toHaveTextContent("ledger unavailable");
    await userEvent.click(screen.getByRole("button", { name: "Retry evidence report" }));

    expect(await screen.findByText("Candidate generation 7")).toBeInTheDocument();
  });

  it("invalidates immediately on a live revision and keeps a stale report behind an explicit error", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse(REPORT))
      .mockResolvedValueOnce(jsonResponse({ message: "report projection unavailable" }, 503));
    const view = renderPanel({ learningReportRevision: 40 });
    expect(
      await screen.findByRole("button", { name: "MPC learning: evaluating" }),
    ).toBeInTheDocument();

    view.rerender(
      <RevisionAwareMpcLearningPanel
        apiBase=""
        selectedController="mpc"
        units="F"
        ambientC={20}
        learningReportRevision={41}
      />,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const trigger = await screen.findByRole("button", { name: "MPC learning: error" });
    await userEvent.click(trigger);

    expect(screen.getByRole("alert")).toHaveTextContent("report projection unavailable");
    expect(screen.getByText("Candidate generation 7")).toBeInTheDocument();
    expect(screen.getByText("candidate-digest-abcdef123456")).toBeInTheDocument();
  });

  it("renders the complete grey fit, native, activation, ownership, and lifecycle report", async () => {
    renderPanel();
    await openPanel();

    expect(screen.getByText("Evaluating", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Role generation 12")).toBeInTheDocument();
    expect(screen.getByText("Candidate generation 7")).toBeInTheDocument();
    expect(screen.getByText("candidate-digest-abcdef123456")).toBeInTheDocument();
    expect(screen.getByText("Active model: grey-box")).toBeInTheDocument();
    expect(screen.getByText("Default model: grey-box")).toBeInTheDocument();

    const observation = screen
      .getByRole("heading", { name: "Observation eligibility" })
      .closest("section");
    expect(observation).not.toBeNull();
    expect(observation!).toHaveTextContent("window-passive-42");
    expect(observation!).toHaveTextContent("218 eligible");
    expect(observation!).toHaveTextContent("9 ineligible");
    expect(observation!).toHaveTextContent("lid-open");
    expect(observation!).toHaveTextContent("operator-calibration");

    const fit = screen.getByRole("heading", { name: "Fit job" }).closest("section");
    expect(fit).not.toBeNull();
    expect(fit!).toHaveTextContent("succeeded");
    expect(fit!).toHaveTextContent("fit-passive-42");
    expect(fit!).toHaveTextContent("218");
    expect(fit!).toHaveTextContent("config-digest-42");
    expect(fit!).toHaveTextContent("converged");

    const parameters = screen
      .getByRole("heading", { name: "Grey parameter changes" })
      .closest("section");
    expect(parameters).not.toBeNull();
    expect(parameters!).toHaveTextContent("C_c");
    expect(parameters!).toHaveTextContent("4200");
    expect(parameters!).toHaveTextContent("4475");
    expect(parameters!).toHaveTextContent("275");
    expect(parameters!).toHaveTextContent("K_Q");
    expect(parameters!).toHaveTextContent("0.076");
    expect(parameters!).toHaveTextContent("theta");
    expect(parameters!).toHaveTextContent("150");
    expect(parameters!).toHaveTextContent("8 delay states");

    const native = screen.getByRole("heading", { name: "Native candidate" }).closest("section");
    expect(native).not.toBeNull();
    expect(native!).toHaveTextContent("native-build-digest-42");
    expect(native!).toHaveTextContent("candidate handle built off-path");
    expect(native!).toHaveTextContent("representative 12-step solve passed");
    expect(native!).toHaveTextContent("18.75 ms");

    const activation = screen
      .getByRole("heading", { name: "Activation and swap" })
      .closest("section");
    expect(activation).not.toBeNull();
    expect(activation!).toHaveTextContent("passive-auto");
    expect(activation!).toHaveTextContent("prepared");
    expect(activation!).toHaveTextContent("activation-record-7");
    expect(activation!).toHaveTextContent("waiting for completed frame");
    expect(activation!).toHaveTextContent("843");

    const ownership = screen.getByRole("heading", { name: "Model ownership" }).closest("section");
    expect(ownership).not.toBeNull();
    expect(ownership!).toHaveTextContent("rollback-digest-1234567890");
    expect(ownership!).toHaveTextContent("24");

    const lifecycle = screen.getByRole("heading", { name: "Lifecycle" }).closest("section");
    expect(lifecycle).not.toBeNull();
    expect(lifecycle!).toHaveTextContent("fit-succeeded");
    expect(lifecycle!).toHaveTextContent("persistence-prepared");
    expect(lifecycle!).toHaveTextContent("converged");

    expect(screen.getByText("Stage: low")).toBeInTheDocument();
    expect(screen.getByText("Current probe: +0.040 q")).toBeInTheDocument();
    expect(screen.getByText("Missing stages: middle, high, coast")).toBeInTheDocument();
    expect(screen.getByText("Missing gates: identifiability")).toBeInTheDocument();
    expect(screen.getAllByText("1.20 °C")).not.toHaveLength(0);
    expect(screen.getAllByText("0.91")).not.toHaveLength(0);
    expect(screen.getByText("Raspberry Pi 5 / target-hardware")).toBeInTheDocument();
    expect(screen.getByText(/configured, not measured/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /activate/i })).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("renders every durable history entry with its exact event, reason, and identity", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        history: [
          {
            evidence_id: "evidence-interrupted-9",
            timestamp_ms: 1_780_000_900_000,
            event: "interrupted-activation",
            decision_id: "decision-9",
            reason: "candidate persistence rejected",
            role_generation: 12,
            candidate_generation: 9,
          },
        ],
      }),
    );
    renderPanel();
    await openPanel();

    const history = screen.getByRole("heading", { name: "History" }).closest("section");
    expect(history).not.toBeNull();
    expect(history!).toHaveTextContent("Event: interrupted-activation");
    expect(history!).toHaveTextContent("Reason: candidate persistence rejected");
    expect(history!).toHaveTextContent("Evidence ID: evidence-interrupted-9");
    expect(history!).toHaveTextContent("Decision ID: decision-9");
    expect(history!).toHaveTextContent("Role generation: 12");
    expect(history!).toHaveTextContent("Candidate generation: 9");
    expect(history!).toHaveTextContent("Timestamp: 1780000900000");
  });

  it.each([
    ["idle", null, null],
    ["queued", "fit-passive-42", null],
    ["running", "fit-passive-42", null],
    ["succeeded", "fit-passive-42", "converged"],
    ["failed", "fit-passive-42", "optimizer-nonconvergence"],
    ["stale", "fit-passive-42", "role-generation-changed"],
  ] as const)("renders the backend fit status %s without deriving it from history", async (
    status,
    jobId,
    reason,
  ) => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        fit: {
          ...REPORT.fit,
          status,
          job_id: jobId,
          result:
            reason === null
              ? null
              : {
                  ...REPORT.fit.result,
                  reason,
                },
        },
        history: [],
      }),
    );
    renderPanel();
    await openPanel();

    const fit = screen.getByRole("heading", { name: "Fit job" }).closest("section");
    expect(fit).not.toBeNull();
    expect(fit!).toHaveTextContent(status);
    if (jobId !== null) expect(fit!).toHaveTextContent(jobId);
    if (reason !== null) expect(fit!).toHaveTextContent(reason);
  });

  it.each([
    [
      false,
      "disabled",
      "Learn This Grill is disabled",
      "No end-of-cook fit is authorized.",
    ],
    [
      true,
      "accepted",
      "checkpoint cook-31-grey-v4 persisted",
      "Becomes active on next-cook restore; no live end-of-cook swap.",
    ],
  ] as const)(
    "renders cook-refit authorization=%s and its exact outcome",
    async (authorized, status, outcome, timing) => {
      fetchMock.mockResolvedValue(
        jsonResponse({
          ...REPORT,
          origin: "cook-refit",
          enable_identification: authorized,
          activation: {
            ...REPORT.activation,
            policy: "cook-refit",
            reason: "cook-refit",
          },
          cook_refit: {
            authorized,
            status,
            outcome,
            activation_timing: authorized ? "next-cook-restore" : null,
          },
        }),
      );
      renderPanel();
      await openPanel();

      const cookRefit = screen.getByRole("heading", { name: "Cook refit" }).closest("section");
      expect(cookRefit).not.toBeNull();
      expect(cookRefit!).toHaveTextContent(authorized ? "Authorized" : "Not authorized");
      expect(cookRefit!).toHaveTextContent(status);
      expect(cookRefit!).toHaveTextContent(outcome);
      expect(cookRefit!).toHaveTextContent(timing);
      expect(
        screen.queryByRole("button", { name: "Activate exact model" }),
      ).not.toBeInTheDocument();
    },
  );

  it.each([
    [
      "error",
      "native-build-failed",
      "candidate handle could not load ABI v2",
      "native-build",
    ],
    [
      "schema-invalidated",
      "model-schema-invalid",
      "checkpoint delay structure is 6; expected 8",
      "checkpoint-load",
    ],
  ] as const)(
    "shows the structured %s report error without falling back to collecting",
    async (status, code, message, phase) => {
      const failed = status === "error";
      fetchMock.mockResolvedValue(
        jsonResponse({
          ...REPORT,
          status,
          native: failed
            ? {
                build: {
                  status: "failed",
                  build_digest: null,
                  manifest_digest: "native-build-digest-42",
                  detail: "candidate handle could not load ABI v2",
                },
                dry_solve: {
                  status: "not-run",
                  solve_time_ms: null,
                  finite_diagnostics: false,
                  detail: "build failed before dry solve",
                },
              }
            : REPORT.native,
          activation: failed
            ? {
                ...REPORT.activation,
                persistence: {
                  status: "failed",
                  phase: "aborted",
                  record_id: "activation-record-7",
                  detail: "activation record fsync failed",
                },
                pending_swap: {
                  status: "failed",
                  frame_boundary: 843,
                  detail: "incumbent restored before candidate output",
                },
              }
            : REPORT.activation,
          errors: [{ code, message, phase, retryable: false, timestamp_ms: 1_780_000_700_000 }],
        }),
      );
      renderPanel();
      await openPanel();

      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent(code);
      expect(alert).toHaveTextContent(message);
      expect(alert).toHaveTextContent(phase);
      expect(screen.queryByText("Collecting", { exact: true })).not.toBeInTheDocument();
      if (failed) {
        const native = screen
          .getByRole("heading", { name: "Native candidate" })
          .closest("section");
        expect(native).not.toBeNull();
        expect(native!).toHaveTextContent("failed");
        expect(native!).toHaveTextContent("candidate handle could not load ABI v2");
        expect(native!).toHaveTextContent("build failed before dry solve");

        const activation = screen
          .getByRole("heading", { name: "Activation and swap" })
          .closest("section");
        expect(activation).not.toBeNull();
        expect(activation!).toHaveTextContent("aborted");
        expect(activation!).toHaveTextContent("activation record fsync failed");
        expect(activation!).toHaveTextContent("incumbent restored before candidate output");
      }
    },
  );

  it("requires both safety confirmations before start", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        calibration: { ...REPORT.calibration, status: "idle" },
      }),
    );
    renderPanel();
    await openPanel();

    const start = screen.getByRole("button", { name: "Start calibration" });
    expect(start).toBeDisabled();

    await userEvent.click(screen.getByRole("checkbox", { name: /grill is empty/i }));
    expect(start).toBeDisabled();

    await userEvent.click(screen.getByRole("checkbox", { name: /sufficient pellets/i }));
    expect(start).toBeEnabled();
  });

  it("sends the exact revisioned command, which carries no temperature ceiling of its own", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        calibration: { ...REPORT.calibration, status: "idle" },
      }),
    );
    renderPanel();
    await openPanel();

    await userEvent.click(screen.getByRole("checkbox", { name: /grill is empty/i }));
    await userEvent.click(screen.getByRole("checkbox", { name: /sufficient pellets/i }));
    await userEvent.click(screen.getByRole("button", { name: "Start calibration" }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/set_mpc_calibration",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    const post = fetchMock.mock.calls.find((call) => call[1]?.method === "POST");
    const body = JSON.parse(String(post?.[1]?.body));
    expect(body).toEqual({
      action: "start",
      revision: 13,
      ambient_c: 20,
      ambient_source: "configured",
      empty_grill_confirmed: true,
      pellets_confirmed: true,
    });
  });

  it("keeps Stop enabled while another action is pending and prevents duplicate pause", async () => {
    const pause = promiseWithResolvers.withResolvers<Response>();
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST" && JSON.parse(String(init.body)).action === "pause") {
        return pause.promise;
      }
      if (init?.method === "POST") {
        return Promise.resolve(jsonResponse({ result: "OK", message: "accepted", data: {} }, 201));
      }
      return Promise.resolve(jsonResponse(REPORT));
    });
    renderPanel();
    await openPanel();

    await userEvent.click(screen.getByRole("button", { name: "Pause calibration" }));
    expect(screen.getByRole("button", { name: "Pause calibration…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Stop calibration" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Stop calibration" }));

    const commands = fetchMock.mock.calls
      .filter((call) => call[1]?.method === "POST")
      .map((call) => JSON.parse(String(call[1]?.body)));
    expect(commands.map(({ action }) => action)).toEqual(["pause", "stop"]);
    expect(commands.map(({ revision }) => revision)).toEqual([13, 14]);
    pause.resolve(jsonResponse({ result: "OK", message: "accepted", data: {} }, 201));
  });

  it("keeps a rejected calibration action pending until the authoritative report refetch settles", async () => {
    const refetch = promiseWithResolvers.withResolvers<Response>();
    let reportRequests = 0;
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({ result: "ERROR", message: "duplicate calibration revision" }, 409),
        );
      }
      reportRequests += 1;
      return reportRequests === 1 ? Promise.resolve(jsonResponse(REPORT)) : refetch.promise;
    });
    renderPanel();
    await openPanel();

    await userEvent.click(screen.getByRole("button", { name: "Pause calibration" }));
    await waitFor(() => expect(reportRequests).toBe(2));

    const pendingPause = screen.getByRole("button", { name: "Pause calibration…" });
    expect(pendingPause).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("duplicate calibration revision");
    await userEvent.click(pendingPause);
    expect(fetchMock.mock.calls.filter((call) => call[1]?.method === "POST")).toHaveLength(1);

    refetch.resolve(jsonResponse(REPORT));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Pause calibration" })).toBeEnabled(),
    );
  });

  it("resumes with a new revision without requiring start confirmations", async () => {
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Promise.resolve(jsonResponse({ result: "OK", message: "accepted", data: {} }, 201));
      }
      return Promise.resolve(
        jsonResponse({
          ...REPORT,
          calibration: { ...REPORT.calibration, status: "paused" },
        }),
      );
    });
    renderPanel();
    await openPanel();

    await userEvent.click(screen.getByRole("button", { name: "Resume calibration" }));

    const post = fetchMock.mock.calls.find((call) => call[1]?.method === "POST");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual(
      expect.objectContaining({ action: "resume", revision: 13 }),
    );
  });

  it("sends reset-progress with the next calibration revision and refetches the report", async () => {
    const stopped = {
      ...REPORT,
      calibration: {
        ...REPORT.calibration,
        status: "cancelled",
        current_probe: null,
        incomplete: true,
      },
    };
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? Promise.resolve(
            jsonResponse({ result: "OK", message: "accepted", data: { mpc_calibration: {} } }, 201),
          )
        : Promise.resolve(jsonResponse(stopped)),
    );
    renderPanel();
    await openPanel();

    await userEvent.click(screen.getByRole("button", { name: "Reset calibration progress" }));

    const post = fetchMock.mock.calls.find((call) => call[1]?.method === "POST");
    expect(String(post?.[0])).toContain("/api/set_mpc_calibration");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      action: "reset-progress",
      revision: 13,
      ambient_c: 20,
      ambient_source: "configured",
      empty_grill_confirmed: true,
      pellets_confirmed: true,
    });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter((call) =>
          String(call[0]).endsWith("/api/model-evidence/report"),
        ),
      ).toHaveLength(2),
    );
  });

  it("shows the backend's exact action rejection, including duplicate revisions", async () => {
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? Promise.resolve(
            jsonResponse({ result: "ERROR", message: "duplicate calibration revision" }, 400),
          )
        : Promise.resolve(jsonResponse(REPORT)),
    );
    renderPanel();
    await openPanel();

    await userEvent.click(screen.getByRole("button", { name: "Stop calibration" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("duplicate calibration revision");
  });

  it.each(["passive-auto", "cook-refit"] as const)(
    "never offers manual activation for the %s policy",
    async (policy) => {
      fetchMock.mockResolvedValue(
        jsonResponse({
          ...REPORT,
          status: "ready-for-review",
          origin: policy === "passive-auto" ? "passive-online" : "cook-refit",
          activation: {
            ...REPORT.activation,
            policy,
            reason: policy,
          },
        }),
      );
      renderPanel();
      await openPanel();

      expect(screen.getByText("Ready for review", { exact: true })).toBeInTheDocument();
      expect(screen.queryByLabelText("Type the exact candidate digest")).not.toBeInTheDocument();
      expect(
        screen.queryByLabelText("Type the exact confidence decision ID"),
      ).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Activate exact model" })).not.toBeInTheDocument();
    },
  );

  it("rejects a malformed 2xx activation acknowledgement with a blank acknowledgement", async () => {
    const ready = {
      ...REPORT,
      status: "ready-for-review" as const,
      mode: "calibration" as const,
      origin: "operator-calibration" as const,
      blockers: [],
      activation: {
        ...REPORT.activation,
        policy: "operator-reviewed" as const,
        reason: "operator-reviewed" as const,
      },
    };
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? Promise.resolve(jsonResponse({ accepted: true, acknowledgement: "   " }))
        : Promise.resolve(jsonResponse(ready)),
    );
    renderPanel();
    await openPanel();
    await userEvent.type(
      screen.getByLabelText("Type the exact candidate digest"),
      String(ready.candidate.digest),
    );
    await userEvent.type(
      screen.getByLabelText("Type the exact confidence decision ID"),
      String(ready.decision_id),
    );

    await userEvent.click(screen.getByRole("button", { name: "Activate exact model" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid acknowledgement response",
    );
  });
  it("requires both exact confirmations and sends the operator-reviewed digest and decision ID", async () => {
    const ready = {
      ...REPORT,
      status: "ready-for-review" as const,
      mode: "calibration" as const,
      origin: "operator-calibration" as const,
      blockers: [],
      activation: {
        ...REPORT.activation,
        policy: "operator-reviewed" as const,
        reason: "operator-reviewed" as const,
        persistence: { ...REPORT.activation.persistence, phase: null },
        pending_swap: {
          status: "not-run" as const,
          frame_boundary: null,
          detail: "awaiting operator decision",
        },
      },
    };
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? Promise.resolve(jsonResponse({ accepted: true, acknowledgement: "activation-requested" }))
        : Promise.resolve(jsonResponse(ready)),
    );
    renderPanel();
    await openPanel();

    const activate = screen.getByRole("button", { name: "Activate exact model" });
    expect(activate).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText("Type the exact candidate digest"),
      String(ready.candidate.digest),
    );
    expect(activate).toBeDisabled();
    await userEvent.type(
      screen.getByLabelText("Type the exact confidence decision ID"),
      String(ready.decision_id),
    );
    expect(activate).toBeEnabled();
    await userEvent.click(activate);

    const post = fetchMock.mock.calls.find((call) => call[1]?.method === "POST");
    expect(String(post?.[0])).toContain("/api/model-evidence/activate");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      candidate_digest: ready.candidate.digest,
      decision_id: ready.decision_id,
    });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter((call) =>
          String(call[0]).endsWith("/api/model-evidence/report"),
        ),
      ).toHaveLength(2),
    );
  });

  it("recovers Tab and Shift+Tab inside the dialog after activation removes the focused control", async () => {
    const ready = {
      ...REPORT,
      status: "ready-for-review" as const,
      mode: "calibration" as const,
      origin: "operator-calibration" as const,
      blockers: [],
      activation: {
        ...REPORT.activation,
        policy: "operator-reviewed" as const,
        reason: "operator-reviewed" as const,
      },
    };
    const activating = {
      ...ready,
      status: "activating" as const,
      rollback_owner: null,
      rollback: { ...ready.rollback, permitted: false },
    };
    const refetch = promiseWithResolvers.withResolvers<Response>();
    let reportRequests = 0;
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === "POST") {
        return Promise.resolve(
          jsonResponse({ accepted: true, acknowledgement: "activation-requested" }),
        );
      }
      reportRequests += 1;
      return reportRequests === 1 ? Promise.resolve(jsonResponse(ready)) : refetch.promise;
    });
    renderPanel();
    await openPanel();
    await userEvent.type(
      screen.getByLabelText("Type the exact candidate digest"),
      String(ready.candidate.digest),
    );
    await userEvent.type(
      screen.getByLabelText("Type the exact confidence decision ID"),
      String(ready.decision_id),
    );

    await userEvent.click(screen.getByRole("button", { name: "Activate exact model" }));
    await waitFor(() => expect(reportRequests).toBe(2));
    expect(screen.getByRole("button", { name: "Activating exact model…" })).toBeDisabled();

    refetch.resolve(jsonResponse(activating));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Activate exact model/ })).not.toBeInTheDocument(),
    );
    expect(document.body).toHaveFocus();

    const tab = new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true });
    window.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(true);
    expect(screen.getByRole("button", { name: "Close MPC model learning" })).toHaveFocus();

    screen.getByRole("button", { name: "MPC learning: activating" }).focus();
    const shiftTab = new KeyboardEvent("keydown", {
      key: "Tab",
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    });
    window.dispatchEvent(shiftTab);
    expect(shiftTab.defaultPrevented).toBe(true);
    expect(screen.getByRole("button", { name: "Stop calibration" })).toHaveFocus();
  });

  it("keeps operator-reviewed grey-box evidence visible with the exact activation rejection", async () => {
    const ready = {
      ...REPORT,
      status: "ready-for-review" as const,
      mode: "calibration" as const,
      origin: "operator-calibration" as const,
      blockers: [],
      activation: {
        ...REPORT.activation,
        policy: "operator-reviewed" as const,
        reason: "operator-reviewed" as const,
      },
    };
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? Promise.resolve(
            jsonResponse(
              {
                accepted: false,
                acknowledgement: "activation-rejected",
                detail: "candidate-digest-changed",
              },
              409,
            ),
          )
        : Promise.resolve(jsonResponse(ready)),
    );
    renderPanel();
    await openPanel();
    await userEvent.type(
      screen.getByLabelText("Type the exact candidate digest"),
      String(ready.candidate.digest),
    );
    await userEvent.type(
      screen.getByLabelText("Type the exact confidence decision ID"),
      String(ready.decision_id),
    );

    await userEvent.click(screen.getByRole("button", { name: "Activate exact model" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("candidate-digest-changed");
    expect(screen.getByText(/Active model: grey-box/i)).toBeInTheDocument();
    expect(screen.getAllByText(String(ready.candidate.digest)).length).toBeGreaterThanOrEqual(1);
  });

  it("does not offer rollback merely because the report status is active", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        status: "active",
        activation: {
          ...REPORT.activation,
          persistence: { ...REPORT.activation.persistence, phase: "active" },
          pending_swap: {
            status: "passed",
            frame_boundary: 843,
            detail: "completed frame swap committed",
          },
        },
        rollback: {
          ...REPORT.rollback,
          permitted: false,
          latest_reason: "confidence window complete",
        },
        rollback_owner: null,
      }),
    );
    renderPanel();
    await openPanel();

    expect(
      screen.queryByRole("button", { name: "Roll back to last safe model" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Latest rollback outcome: confidence window complete")).toBeInTheDocument();
  });

  it("posts an exact rollback reason only for the explicit rollback owner and refetches", async () => {
    const owned = {
      ...REPORT,
      status: "active" as const,
      rollback: { ...REPORT.rollback, permitted: true },
    };
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? Promise.resolve(jsonResponse({ accepted: true, acknowledgement: "rollback-requested" }))
        : Promise.resolve(jsonResponse(owned)),
    );
    renderPanel();
    await openPanel();

    await userEvent.type(
      screen.getByLabelText("Required rollback reason"),
      "operator-observed-oscillation",
    );
    await userEvent.click(screen.getByRole("button", { name: "Roll back to last safe model" }));

    const post = fetchMock.mock.calls.find((call) => call[1]?.method === "POST");
    expect(String(post?.[0])).toContain("/api/model-evidence/rollback");
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      reason: "operator-observed-oscillation",
    });
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.filter((call) =>
          String(call[0]).endsWith("/api/model-evidence/report"),
        ),
      ).toHaveLength(2),
    );
  });

  it("renders fallback ownership and outcome from explicit report fields with empty history", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        status: "fallback",
        active_model: REPORT.rollback_owner,
        rollback: {
          ...REPORT.rollback,
          permitted: false,
          latest_reason: "native-solve-nonfinite",
          confidence_window_remaining: 0,
        },
        history: [],
      }),
    );
    renderPanel();
    await openPanel();

    expect(screen.getByText("Fallback", { exact: true })).toBeInTheDocument();
    expect(screen.getByText("Latest rollback outcome: native-solve-nonfinite")).toBeInTheDocument();
    expect(screen.getAllByText("rollback-digest-1234567890").length).toBeGreaterThanOrEqual(1);
  });

  it("states timeout and incomplete outcomes explicitly", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        calibration: {
          ...REPORT.calibration,
          status: "timed-out",
          timed_out: true,
          incomplete: true,
        },
      }),
    );
    renderPanel();
    await openPanel();

    expect(screen.getByRole("alert")).toHaveTextContent("Calibration stage timed out");
    expect(screen.getByRole("alert")).toHaveTextContent("Calibration ended without completing");
    expect(screen.getByRole("button", { name: "Stop calibration" })).toBeEnabled();
  });

  it("does not warn about incompleteness while a run is still going", async () => {
    // `incomplete` is true for every run that has not finished all its stages,
    // so alerting on it alone puts a permanent warning on a healthy run.
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        calibration: { ...REPORT.calibration, status: "active", incomplete: true },
      }),
    );
    renderPanel();
    await openPanel();

    expect(screen.queryByText(/without completing/i)).toBeNull();
  });

  it("does not warn about incompleteness before any run has been started", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        calibration: { ...REPORT.calibration, status: "inactive", stage: null, incomplete: true },
      }),
    );
    renderPanel();
    await openPanel();

    expect(screen.queryByText(/without completing/i)).toBeNull();
  });

  it("shows the calibration status so a stopped run is distinguishable from one never started", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...REPORT,
        calibration: { ...REPORT.calibration, status: "cancelled", incomplete: true },
      }),
    );
    renderPanel();
    await openPanel();

    expect(screen.getByText(/Calibration: cancelled/i)).toBeInTheDocument();
  });

  it("says calibration follows the hold rather than driving one, in the operator's unit", async () => {
    // The removed "maximum calibration temperature" field read like a target.
    // Nothing drives the grill, so the bands are the operator's job.
    renderPanel();
    await openPanel();

    expect(screen.getByText(/does not drive the grill/i)).toBeInTheDocument();
    expect(screen.getByText(/225, 325 and 425 °F/)).toBeInTheDocument();
  });

  it("states the bands in Celsius for a Celsius grill", async () => {
    renderPanel({ units: "C" });
    await openPanel();

    expect(screen.getByText(/107, 163 and 218 °C/)).toBeInTheDocument();
  });
});
