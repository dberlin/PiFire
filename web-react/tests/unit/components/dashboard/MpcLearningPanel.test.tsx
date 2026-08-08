import { afterEach, beforeEach, describe, expect, it, type Mock, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MpcLearningPanel } from "../../../../src/components/dashboard/MpcLearningPanel";
import type { ModelEvidenceReport } from "../../../../src/helpers/modelEvidence/types";

const REPORT: ModelEvidenceReport = {
  schema_version: 1,
  status: "evaluating",
  decision_id: "decision-7",
  active_model: { kind: "grey-box", digest: "active-digest-1234567890" },
  default_model: { kind: "grey-box", digest: "default-digest-1234567890" },
  candidate: { kind: "state-space", generation: 7, digest: "candidate-digest-abcdef123456" },
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
  identifiability: {
    available: true,
    accepted: false,
    reason: "gain interval crosses zero",
    full_rank: true,
    finite_diagnostics: true,
    pole_magnitude: 0.97,
    gain: 0.12,
    delay_steps: 4,
    covariance_finite: true,
    alignment_error_c: 0.8,
    snapshot_round_trip: true,
    sequential_wins: 1,
    generation_continuity: true,
    atomic_persistence: true,
    production_prospective: true,
    braking_error_c: 1.1,
    incumbent_braking_error_c: 1.4,
  },
  scores: [
    {
      horizon_steps: 45,
      temperature_band: "low",
      phase: "heating",
      ambient_source: "configured",
      generation: 7,
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
    { name: "calibration-complete", passed: false, reason: "missing high and coast stages" },
    { name: "target-timing", passed: true, reason: null },
  ],
  missing_gates: ["calibration-complete"],
  blockers: ["missing high and coast stages", "gain interval crosses zero"],
  target_timing: {
    available: true,
    sample_count: 420,
    p50_ms: 12.4,
    p95_ms: 26.8,
    p99_ms: 41.7,
    hardware_provenance: "Raspberry Pi 5 / target-hardware",
    gate_passed: true,
  },
  history: [],
  ambient_provenance_limitation:
    "Ambient temperature is configured, not measured; ambient gain is not separately identified.",
  artifact_metadata: {
    schema_version: 1,
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

function renderPanel(props: Partial<React.ComponentProps<typeof MpcLearningPanel>> = {}) {
  return render(<MpcLearningPanel apiBase="" selectedController="mpc" ambientC={20} {...props} />);
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

  it("renders exact evidence, calibration, score, timing, and ambient limitations", async () => {
    renderPanel();
    await openPanel();

    expect(screen.getByText("Evaluating")).toBeInTheDocument();
    expect(screen.getByText("Candidate generation 7")).toBeInTheDocument();
    expect(screen.getByText("candidate-digest-abcdef123456")).toBeInTheDocument();
    expect(screen.getByText("Active model: grey-box")).toBeInTheDocument();
    expect(screen.getByText("Default model: grey-box")).toBeInTheDocument();
    expect(screen.getByText("missing high and coast stages")).toBeInTheDocument();
    expect(screen.getByText("gain interval crosses zero")).toBeInTheDocument();
    expect(screen.getByText("Stage: low")).toBeInTheDocument();
    expect(screen.getByText("Current probe: +0.040 q")).toBeInTheDocument();
    expect(screen.getByText("18 eligible / 4 ineligible")).toBeInTheDocument();
    expect(screen.getByText("Missing stages: middle, high, coast")).toBeInTheDocument();
    expect(screen.getByText("Missing gates: calibration-complete")).toBeInTheDocument();
    expect(screen.getAllByText("45")).not.toHaveLength(0);
    expect(screen.getAllByText("1.20 °C")).not.toHaveLength(0);
    expect(screen.getAllByText("0.91")).not.toHaveLength(0);
    expect(screen.getByText("Raspberry Pi 5 / target-hardware")).toBeInTheDocument();
    expect(screen.getByText(/configured, not measured/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /activate/i })).not.toBeInTheDocument();
  });

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

  it("requires both exact confirmations and sends the reviewed digest and decision ID", async () => {
    const ready = { ...REPORT, status: "ready-for-review" as const, blockers: [] };
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? Promise.resolve(
            jsonResponse({
              accepted: true,
              active_kind: "innovation-state-space",
              candidate_digest: ready.candidate.digest,
              decision_id: ready.decision_id,
              role_generation: 8,
            }),
          )
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
    await waitFor(() => expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3));
  });

  it("keeps grey-box review visible with the backend's exact activation rejection", async () => {
    const ready = { ...REPORT, status: "ready-for-review" as const, blockers: [] };
    fetchMock.mockImplementation((_input: RequestInfo | URL, init?: RequestInit) =>
      init?.method === "POST"
        ? Promise.resolve(
            jsonResponse(
              {
                accepted: false,
                active_kind: "grey-box",
                error: "model-activation-rejected",
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
});
