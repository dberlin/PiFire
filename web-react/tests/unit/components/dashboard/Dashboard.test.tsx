import type { CommandClient, CommandResult } from "@pifire/core/command";
import type { NotifyUpdate } from "@pifire/core/contracts/control";
import type { DashSocketPayload, ThermocoupleHealthView } from "@pifire/core/contracts/core";
import type {
  ModelEvidenceReport,
  ModelEvidenceStatus,
  PidSpLearningReport,
} from "@pifire/core/contracts/learning";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type ReactElement, useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { TimerBar } from "../../../../src/components/shell/TimerBar";
import { queryKeys } from "../../../../src/helpers/query/keys";
import { createQueryClient } from "../../../../src/helpers/query/queryClient";
import * as actualSettingsApi from "../../../../src/helpers/settings/settingsApi" with {
  rstest: "importActual",
};

// renderRoute() mounts AppPrefsProvider, which now reads settings itself
// (AppPrefs.tsx) -- unmocked, that call would land on the same global `fetch`
// stub the notify tests below install and inflate their addressed-POST call
// counts. Stubbed through a lazy wrapper so the hoisted mock factory never
// captures an uninitialised binding, same idiom MetricsPage.test.tsx uses.
const getSettingsMock = rs.fn().mockResolvedValue({});
rs.mock("../../../../src/helpers/settings/settingsApi", () => ({
  ...actualSettingsApi,
  getSettings: (...a: unknown[]) => getSettingsMock(...a),
}));

const { Dashboard } = await import("../../../../src/components/dashboard/Dashboard");
const { renderRoute, testQueryClient } = await import("../../test-utils");

afterEach(cleanup);

const OK: CommandResult = { ok: true, message: "" };

function dashboardLearningReport(
  status: ModelEvidenceStatus,
  roleGeneration: number,
): ModelEvidenceReport {
  const activeDigest = `${roleGeneration}`.padEnd(64, "a");
  const candidateDigest = `${roleGeneration + 1}`.padEnd(64, "b");
  return {
    schema_version: 3,
    status,
    mode: "passive-online",
    decision_id: `decision-${roleGeneration}`,
    evidence: {
      count: 0,
      audit_count: 0,
      high_water: null,
      retired_excluded: 0,
    },
    fit: {
      status: "idle",
      request_id: null,
      fit_corpus_digest: null,
      error: null,
    },
    checks: {},
    candidate: {
      challenger_id: `challenger-${roleGeneration}`,
      phase: status === "active" ? "qualified" : "evaluating",
      digest: candidateDigest,
      origin: "passive-online",
      policy: "causal-auto",
      role_generation: roleGeneration,
      candidate_generation: roleGeneration + 1,
      parameters: null,
      parameter_deltas: null,
      fit_quality: null,
      identifiability: null,
      assessment: null,
      lineage: {
        request_id: `fit-${roleGeneration}`,
        parent_incumbent_digest: activeDigest,
        parent_incumbent_generation: roleGeneration,
        candidate_generation: roleGeneration + 1,
        fit_corpus_digest: "c".repeat(64),
        trigger_origin: "passive-online",
        result_status: "succeeded",
        candidate_digest: candidateDigest,
      },
    },
    evaluation: null,
    corpus: {
      digest: "c".repeat(64),
      revision: roleGeneration,
      fit_partition_digest: "d".repeat(64),
      slices: [
        {
          segment_id: `segment-${roleGeneration}`,
          through_ordinal: 0,
          prefix_digest: "f".repeat(64),
          pre_roll_count: 0,
          scored_count: 1,
        },
      ],
    },
    activation: {
      phase: status === "active" ? "active" : "aborted",
      origin: "passive-online",
      policy: "causal-auto",
      reason: null,
      pending_persistence: false,
      pending_frame_boundary_swap: false,
    },
    active_model: {
      digest: activeDigest,
      role_generation: roleGeneration,
    },
    identities: {
      active_digest: activeDigest,
      active_generation: roleGeneration,
      candidate_digest: candidateDigest,
      candidate_generation: roleGeneration + 1,
      rollback_digest: null,
      rollback_generation: null,
    },
    calibration: {
      revision: 0,
      command_high_water: 0,
    },
    latest_lifecycle: null,
    failure: null,
    gates: [],
    blockers: [],
    errors: [],
    revision: `${roleGeneration}`.padStart(64, "e"),
  };
}

const DASHBOARD_PID_SP_REPORT: PidSpLearningReport = {
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

function makeCommand(): CommandClient {
  return {
    setMode: rs.fn(async () => OK),
    hold: rs.fn(async () => OK),
    setSmokePlus: rs.fn(async () => OK),
    setPMode: rs.fn(async () => OK),
    prime: rs.fn(async () => OK),
    timerStart: rs.fn(async () => OK),
    timerStartWithOptions: rs.fn(async () => OK),
    timerPause: rs.fn(async () => OK),
    timerStop: rs.fn(async () => OK),
    timerShutdown: rs.fn(async () => OK),
    timerKeepWarm: rs.fn(async () => OK),
    system: rs.fn(async () => OK),
    setUnits: rs.fn(async () => OK),
    manualOutput: rs.fn(async () => OK),
    manualPwm: rs.fn(async () => OK),
    recipeNextStep: rs.fn(async () => OK),
    recipeUnpause: rs.fn(async () => OK),
  };
}

function renderDashboard(
  dash: DashSocketPayload,
  overrides: Partial<Parameters<typeof Dashboard>[0]> = {},
) {
  return renderRoute(
    <Dashboard
      dash={dash}
      command={makeCommand()}
      apiBase=""
      phase="live"
      controlAlive={true}
      accent="ember"
      setAccent={rs.fn()}
      animate={false}
      setAnimate={rs.fn()}
      {...overrides}
    />,
    undefined,
  );
}

function dashboardProbeHealth(
  role: ThermocoupleHealthView["role"],
  label: string,
  displayName: string,
  outcome: ThermocoupleHealthView["outcome"],
): ThermocoupleHealthView {
  return {
    device: `${displayName} device`,
    port: "KTT0",
    label,
    displayName,
    role,
    report: {
      state: "confirmed",
      faults: ["open"],
      evidence: ["hardware"],
      temperatureValid: outcome === "notify_only",
      detail: {},
    },
    detector: { source: "hardware", policy: "observe" },
    outcome,
    freshness: { current: true, lastReportedAgeS: 0 },
  };
}

function renderInQueryRouter(ui: ReactElement, client = createQueryClient()) {
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

function dashboardAt(apiBase: string, dash: DashSocketPayload = FIXTURE_DASH) {
  return (
    <Dashboard
      dash={dash}
      command={makeCommand()}
      apiBase={apiBase}
      phase="live"
      controlAlive={true}
      accent="ember"
      setAccent={rs.fn()}
      animate={false}
      setAnimate={rs.fn()}
    />
  );
}

function DashboardApiHarness() {
  const [apiBase, setApiBase] = useState("/a");
  return (
    <>
      <button type="button" onClick={() => setApiBase("/a")}>
        Use API A
      </button>
      <button type="button" onClick={() => setApiBase("/b")}>
        Use API B
      </button>
      <Dashboard
        dash={FIXTURE_DASH}
        command={makeCommand()}
        apiBase={apiBase}
        phase="live"
        controlAlive={true}
        accent="ember"
        setAccent={rs.fn()}
        animate={false}
        setAnimate={rs.fn()}
      />
    </>
  );
}

function DashboardLearningRevisionHarness() {
  const [modelLearningRevision, setModelLearningRevision] = useState("raw-revision:001");
  return (
    <>
      <button type="button" onClick={() => setModelLearningRevision("raw-revision:1")}>
        Publish raw learning revision
      </button>
      <Dashboard
        dash={{ ...FIXTURE_DASH, modelLearningRevision }}
        command={makeCommand()}
        apiBase=""
        phase="live"
        controlAlive={true}
        accent="ember"
        setAccent={rs.fn()}
        animate={false}
        setAnimate={rs.fn()}
      />
    </>
  );
}

describe("Dashboard MPC settings authority", () => {
  afterEach(() => {
    getSettingsMock.mockReset();
    getSettingsMock.mockResolvedValue({});
    rs.unstubAllGlobals();
  });

  async function renderMpcDashboard(hasDistanceSensor: boolean) {
    getSettingsMock.mockResolvedValue({
      controller: { selected: "mpc", config: { mpc: { T_amb: 20 } } },
    });
    const pendingReport = new Promise<Response>(() => {});
    rs.stubGlobal(
      "fetch",
      rs.fn(() => pendingReport),
    );
    renderDashboard({ ...FIXTURE_DASH, hasDistanceSensor });
    return screen.findByRole("button", { name: "MPC learning: loading" });
  }

  it.each([true, false])(
    "keeps MPC learning in the right column when hopper sensor is %s",
    async (hasDistanceSensor) => {
      const trigger = await renderMpcDashboard(hasDistanceSensor);
      expect(trigger.closest('[data-pf="rightCol"]')).not.toBeNull();
      expect(trigger.closest('[data-pf="controls"]')).toBeNull();
    },
  );

  it.each([
    {
      selectedController: "mpc",
      expectedPill: "MPC learning: idle",
      report: dashboardLearningReport("collecting", 31),
    },
    {
      selectedController: "pid_sp",
      expectedPill: "PID-SP learning: idle",
      report: DASHBOARD_PID_SP_REPORT,
    },
  ])(
    "uses settings-selected $selectedController as learning-panel authority",
    async ({ selectedController, expectedPill, report }) => {
      getSettingsMock.mockResolvedValue({
        controller: {
          selected: selectedController,
          config: { mpc: { T_amb: 20 } },
        },
      });
      rs.stubGlobal(
        "fetch",
        rs.fn(
          async () =>
            new Response(JSON.stringify(report), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
        ),
      );

      renderDashboard(FIXTURE_DASH);

      expect(await screen.findByRole("button", { name: expectedPill })).toBeVisible();
    },
  );

  it("places MPC learning after Hopper when Hopper exists", async () => {
    const trigger = await renderMpcDashboard(true);
    const hopper = screen.getByText("Hopper").closest(".pf-dash-hopper");
    expect(hopper?.nextElementSibling).toBe(trigger);
  });

  it("uses a primed shared settings entry without issuing a second settings transport", async () => {
    const client = createQueryClient();
    client.setQueryData(queryKeys.settings("/a"), {
      controller: { selected: "mpc", config: { mpc: { T_amb: 14.5 } } },
    });
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => {
        return new Response(JSON.stringify(dashboardLearningReport("collecting", 31)), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    renderInQueryRouter(dashboardAt("/a/"), client);

    expect(await screen.findByRole("button", { name: "MPC learning: idle" })).toBeVisible();
    expect(getSettingsMock).not.toHaveBeenCalled();
  });

  it("updates controller authority when the shared settings result changes", async () => {
    const client = createQueryClient();
    client.setQueryData(queryKeys.settings("/a"), {
      controller: { selected: "pid_sp", config: { mpc: { T_amb: 20 } } },
    });
    rs.stubGlobal(
      "fetch",
      rs.fn(async (input: string | URL | Request) => {
        const report = String(input).includes("/api/pid-sp-learning/report")
          ? DASHBOARD_PID_SP_REPORT
          : dashboardLearningReport("collecting", 32);
        return new Response(JSON.stringify(report), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );
    renderInQueryRouter(dashboardAt("/a"), client);
    expect(await screen.findByRole("button", { name: "PID-SP learning: idle" })).toBeVisible();

    act(() => {
      client.setQueryData(queryKeys.settings("/a"), {
        controller: { selected: "mpc", config: { mpc: { T_amb: 16 } } },
      });
    });

    expect(await screen.findByRole("button", { name: "MPC learning: idle" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /PID-SP learning:/i })).not.toBeInTheDocument();
  });

  it.each([
    { configuredAmbient: 13.5, expectedAmbient: 13.5, caseName: "configured" },
    { configuredAmbient: Number.NaN, expectedAmbient: 20, caseName: "non-finite" },
  ])(
    "sends the $caseName settings ambient value as $expectedAmbient for MPC calibration",
    async ({ configuredAmbient, expectedAmbient }) => {
      const user = userEvent.setup();
      getSettingsMock.mockResolvedValue({
        controller: { selected: "mpc", config: { mpc: { T_amb: configuredAmbient } } },
      });
      let calibrationBody: Record<string, unknown> | undefined;
      rs.stubGlobal(
        "fetch",
        rs.fn(async (input: string | URL | Request, init?: RequestInit) => {
          if (String(input).endsWith("/api/set_mpc_calibration")) {
            calibrationBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
            return new Response(
              JSON.stringify({
                result: "OK",
                message: "accepted",
                data: { mpc_calibration: calibrationBody },
              }),
              { status: 200, headers: { "Content-Type": "application/json" } },
            );
          }
          return new Response(JSON.stringify(dashboardLearningReport("collecting", 31)), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }),
      );
      renderInQueryRouter(dashboardAt("/a"));
      await user.click(await screen.findByRole("button", { name: "MPC learning: idle" }));
      await user.click(
        screen.getByLabelText("The grill is empty, with normal grates and drip tray installed."),
      );
      await user.click(
        screen.getByLabelText("Sufficient pellets are loaded for the calibration run."),
      );
      await user.click(screen.getByRole("button", { name: "Start calibration" }));

      await waitFor(() => expect(calibrationBody?.ambient_c).toBe(expectedAmbient));
    },
  );

  it("shows no learning controls when the shared settings read fails", async () => {
    getSettingsMock.mockRejectedValue(new Error("offline"));
    rs.stubGlobal(
      "fetch",
      rs.fn(() => new Promise<Response>(() => {})),
    );
    const { client } = renderInQueryRouter(dashboardAt("/a"));

    await waitFor(() =>
      expect(client.getQueryState(queryKeys.settings("/a"))?.status).toBe("error"),
    );
    expect(screen.queryByRole("button", { name: /learning:/i })).not.toBeInTheDocument();
  });

  it("fences A settings while B loads and reuses A's valid cache on return", async () => {
    const user = userEvent.setup();
    const pendingSettings = new Promise<Record<string, never>>(() => {});
    getSettingsMock.mockImplementation((baseUrl: string) => {
      if (baseUrl === "/a") {
        return Promise.resolve({
          controller: { selected: "mpc", config: { mpc: { T_amb: 20 } } },
        });
      }
      return pendingSettings;
    });
    const pendingReport = new Promise<Response>(() => {});
    rs.stubGlobal(
      "fetch",
      rs.fn(() => pendingReport),
    );
    renderInQueryRouter(<DashboardApiHarness />);

    expect(
      await screen.findByRole("button", { name: "MPC learning: loading" }),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Use API B" }));
    await waitFor(() => expect(getSettingsMock).toHaveBeenCalledWith("/b"));
    expect(screen.queryByRole("button", { name: /MPC learning:/i })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Use API A" }));
    expect(
      await screen.findByRole("button", { name: "MPC learning: loading" }),
    ).toBeInTheDocument();
    expect(getSettingsMock.mock.calls.filter(([baseUrl]) => baseUrl === "/a")).toHaveLength(1);
  });

  it("uses the live revision only to invalidate one shared pill and panel report immediately", async () => {
    const user = userEvent.setup();
    getSettingsMock.mockResolvedValue({
      controller: { selected: "mpc", config: { mpc: { T_amb: 20 } } },
    });
    let reportRequests = 0;
    const fetchMock = rs.fn(async () => {
      reportRequests += 1;
      const report =
        reportRequests === 1
          ? dashboardLearningReport("collecting", 21)
          : dashboardLearningReport("active", 22);
      return new Response(JSON.stringify(report), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    rs.stubGlobal("fetch", fetchMock);
    renderRoute(<DashboardLearningRevisionHarness />, undefined);

    expect(await screen.findByRole("button", { name: "MPC learning: idle" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /MPC learning:/i })).toHaveLength(1);

    await user.click(screen.getByRole("button", { name: "Publish raw learning revision" }));

    const trigger = await screen.findByRole("button", {
      name: "MPC learning: active",
    });
    expect(reportRequests).toBe(2);
    await user.click(trigger);

    expect(screen.getByRole("dialog", { name: "MPC model learning" })).toBeInTheDocument();
    expect(screen.getByText("Role generation: 22")).toBeInTheDocument();
    expect(screen.getByText("Candidate generation: 23")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /MPC learning:/i })).toHaveLength(1);
    expect(reportRequests).toBe(2);
  });
});

describe("Dashboard", () => {
  it("renders the header with the grill name and a LIVE label when connected", () => {
    renderDashboard(FIXTURE_DASH, { phase: "live", controlAlive: true });
    expect(screen.getByText(FIXTURE_DASH.grillName)).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
  });

  it("shows a DEMO label when running against the demo backend", () => {
    renderDashboard(FIXTURE_DASH, { phase: "demo" });
    expect(screen.getByText("DEMO")).toBeInTheDocument();
  });

  it("shows the uppercased mode badge and the cook-time counter", () => {
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Stop" });
    expect(screen.getByText("STOP")).toBeInTheDocument();
    expect(screen.getByText("Cook Time")).toBeInTheDocument();
    // No cook running: startupTimestamp is 0, which Flask renders as "--"
    // (dash_default.js:410). Not "00:00" -- that claimed a cook of zero length.
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("shares one whole-second clock tick with the running timer bar", () => {
    rs.useFakeTimers();
    rs.setSystemTime(new Date(2024, 0, 1, 12, 34, 59));

    try {
      const nowSeconds = Math.floor(Date.now() / 1000);
      const timer = {
        ...FIXTURE_DASH.timer,
        start: nowSeconds - 10,
        end: nowSeconds + 2,
      };
      const dash = {
        ...FIXTURE_DASH,
        currentMode: "Hold",
        startupTimestamp: nowSeconds - 59,
        timer,
      };

      const view = renderInQueryRouter(
        <>
          {dashboardAt("", dash)}
          <TimerBar timer={timer} command={makeCommand()} />
        </>,
      );
      const headerClock = view.container.querySelector('[data-pf="clock"]');

      expect(headerClock).toHaveTextContent("12:34");
      expect(screen.getByText("59s")).toBeInTheDocument();
      expect(screen.getByText("00:00:02")).toBeInTheDocument();
      expect(rs.getTimerCount()).toBe(1);

      act(() => rs.advanceTimersByTime(1_000));

      expect(headerClock).toHaveTextContent("12:35");
      expect(screen.getByText("01:00")).toBeInTheDocument();
      expect(screen.getByText("00:00:01")).toBeInTheDocument();
      expect(rs.getTimerCount()).toBe(1);
    } finally {
      cleanup();
      rs.useRealTimers();
    }
  });

  it("renders the food-probe column when foodProbes are present", () => {
    renderDashboard(FIXTURE_DASH);
    expect(screen.getByText("Food Probes")).toBeInTheDocument();
    expect(screen.getAllByText("AMBIENT")).toHaveLength(FIXTURE_DASH.foodProbes.length);
  });

  it("hides the food-probe column when there are no foodProbes", () => {
    renderDashboard({ ...FIXTURE_DASH, foodProbes: [] });
    expect(screen.queryByText("Food Probes")).not.toBeInTheDocument();
  });

  // Both pin currentMode: the pair is shown in Smoke and nowhere else, so a
  // fixture default would decide the outcome instead of the assertion.
  it("shows the P-mode and an ON smoke+ pill when smokePlus is set", () => {
    renderDashboard({
      ...FIXTURE_DASH,
      currentMode: "Smoke",
      pMode: 2,
      smokePlus: true,
    });
    expect(screen.getByText("P-MODE")).toBeInTheDocument();
    expect(screen.getByText("P-2")).toBeInTheDocument();
    expect(screen.getByText("SMOKE+")).toBeInTheDocument();
    expect(screen.getByText("ON")).toBeInTheDocument();
  });

  it("shows an OFF smoke+ pill when smokePlus is unset", () => {
    renderDashboard({
      ...FIXTURE_DASH,
      currentMode: "Smoke",
      smokePlus: false,
    });
    expect(screen.getByText("OFF")).toBeInTheDocument();
  });

  it("shows the MONITOR mode badge and an inactive cook-time counter", () => {
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Monitor" });
    expect(screen.getByText("MONITOR")).toBeInTheDocument();
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  it("shows the SHUTDOWN mode badge and an inactive cook-time counter", () => {
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Shutdown" });
    expect(screen.getByText("SHUTDOWN")).toBeInTheDocument();
    expect(screen.getByText("--")).toBeInTheDocument();
  });

  // C3: the counter is a pure function of the CONTROLLER's startup_timestamp,
  // so it survives a reload and two browsers watching one cook agree. It used
  // to be seeded from `new Date()` at mount, which reported 00:00 four hours
  // into a brisket.
  it("counts from the controller's startup_timestamp, not from mount", () => {
    const started = Math.floor(Date.now() / 1000) - 3723;
    renderDashboard({
      ...FIXTURE_DASH,
      currentMode: "Hold",
      startupTimestamp: started,
    });
    expect(screen.getByText("HOLD")).toBeInTheDocument();
    expect(screen.getByText(/^01:02:0\d$/)).toBeInTheDocument();
  });

  it("does not restart the counter when a fresh instance mounts mid-cook", () => {
    const started = Math.floor(Date.now() / 1000) - 754;
    const dash = {
      ...FIXTURE_DASH,
      currentMode: "Smoke",
      startupTimestamp: started,
    };
    renderDashboard(dash);
    expect(screen.getByText(/^12:3\d$/)).toBeInTheDocument();
    cleanup();
    renderDashboard(dash);
    expect(screen.getByText(/^12:3\d$/)).toBeInTheDocument();
  });

  // Reignite deliberately does not rewrite startup_timestamp
  // (controller/runtime/modes/reignite.py:17-18), so the elapsed time keeps
  // running from the ORIGINAL ignition -- Flask's behaviour, reproduced.
  it("keeps counting from the original ignition through a Reignite", () => {
    const started = Math.floor(Date.now() / 1000) - 7;
    renderDashboard({
      ...FIXTURE_DASH,
      currentMode: "Reignite",
      startupTimestamp: started,
    });
    expect(screen.getByText(/^0\ds$/)).toBeInTheDocument();
  });

  it("clicking an accent swatch calls setAccent with that accent", async () => {
    const user = userEvent.setup();
    const setAccent = rs.fn();
    renderDashboard(FIXTURE_DASH, { setAccent });
    await user.click(screen.getByRole("button", { name: "ice" }));
    expect(setAccent).toHaveBeenCalledWith("ice");
  });

  it("clicking the ANIM toggle calls setAnimate with the flipped value", async () => {
    const user = userEvent.setup();
    const setAnimate = rs.fn();
    renderDashboard(FIXTURE_DASH, { animate: false, setAnimate });
    await user.click(screen.getByText("ANIM"));
    expect(setAnimate).toHaveBeenCalledWith(true);
  });

  it("clicking the settings gear navigates to /settings", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={testQueryClient()}>
        <MemoryRouter initialEntries={["/"]}>
          <Routes>
            <Route
              path="/"
              element={
                <Dashboard
                  dash={FIXTURE_DASH}
                  command={makeCommand()}
                  apiBase=""
                  phase="live"
                  controlAlive={true}
                  accent="ember"
                  setAccent={rs.fn()}
                  animate={false}
                  setAnimate={rs.fn()}
                />
              }
            />
            <Route path="/settings" element={<div data-testid="settings-route" />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.click(screen.getByRole("button", { name: "settings" }));
    expect(screen.getByTestId("settings-route")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Per-probe target notifications.
//
// These drive the real notify helpers and stub `fetch`, rather than mocking
// helpers/notify/notifyState: the thing worth pinning is the request the
// dashboard actually puts on the wire -- a single POST that ADDRESSES one
// notify entry by (label, type) and names only the fields it changes. No read
// first, and no whole array: an array posted from a queue-blind read is applied
// as a replace and reverts anything another writer changed in the same control
// cycle.
// --------------------------------------------------------------------------

function stubNotifyFetch(postBody: unknown = { result: "success" }, postOk = true) {
  const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => ({
    ok: postOk,
    status: postOk ? 201 : 500,
    json: async () => postBody,
  }));
  rs.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const fetchCallsTo = (fetchMock: ReturnType<typeof rs.fn>, suffix: string) =>
  fetchMock.mock.calls.filter((call) => String(call[0]).endsWith(suffix));

const postedNotifyUpdates = (fetchMock: ReturnType<typeof rs.fn>): NotifyUpdate[] => {
  const post = fetchCallsTo(fetchMock, "/api/control")[0];
  if (post === undefined) throw new Error("no POST /api/control was issued");
  const body = JSON.parse(String((post[1] as RequestInit).body)) as {
    notify_updates: NotifyUpdate[];
  };
  return body.notify_updates;
};

describe("Dashboard target notifications", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("opens the modal for a food probe, seeded from that probe's live fields", async () => {
    const user = userEvent.setup();
    renderDashboard({
      ...FIXTURE_DASH,
      foodProbes: [
        {
          ...FIXTURE_DASH.foodProbes[0],
          title: "Brisket",
          label: "Probe1",
          target: 203,
          targetReq: true,
          targetKeepWarm: true,
        },
      ],
    });
    await user.click(screen.getByRole("button", { name: "Notifications for Brisket" }));
    expect(screen.getByText("Brisket Notifications")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /target temperature/i })).toBeChecked();
    expect(screen.getByRole("spinbutton", { name: /^target/i })).toHaveValue(203);
    expect(screen.getByRole("radio", { name: /keep warm/i })).toBeChecked();
  });

  // dash_default.html:36,53 renders the notify modal for probe_status['P'] as
  // well as ['F'], so a target on the grill probe is not a food-probe-only
  // feature. The Primary probe gets no TARGET action choice (:188-198) -- and is
  // the only probe that gets the LIMIT action choices (:238-244, :284-308) --
  // plus the wider 0-600F range (:174-186).
  it("exposes a bell for the primary probe, opening it as primary", async () => {
    const user = userEvent.setup();
    renderDashboard(FIXTURE_DASH);
    await user.click(
      screen.getByRole("button", {
        name: `Notifications for ${FIXTURE_DASH.primaryProbe.title}`,
      }),
    );
    expect(
      screen.getByText(`${FIXTURE_DASH.primaryProbe.title} Notifications`),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("group", { name: /when it is reached/i })).toHaveLength(0);
    expect(screen.getByRole("group", { name: /above the high limit/i })).toBeInTheDocument();
    expect(screen.getByRole("slider", { name: /^target temperature/i })).toHaveAttribute(
      "max",
      "600",
    );
  });

  it("saves a food probe's target as ONE addressed post", async () => {
    const user = userEvent.setup();
    const fetchMock = stubNotifyFetch();
    renderDashboard({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], title: "Brisket", label: "Probe1" }],
    });
    await user.click(screen.getByRole("button", { name: "Notifications for Brisket" }));
    await user.click(screen.getByRole("checkbox", { name: /target temperature/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /^target/i }));
    await user.type(screen.getByRole("spinbutton", { name: /^target/i }), "203");
    await user.click(screen.getByRole("radio", { name: /keep warm/i }));
    await user.click(screen.getByRole("button", { name: "Set" }));

    await waitFor(() =>
      expect(screen.queryByText("Brisket Notifications")).not.toBeInTheDocument(),
    );
    // ONE request, not a read followed by a write, and not one request per
    // entry: the three entries this modal owns travel as three ADDRESSED
    // updates in a single POST.
    expect(fetchCallsTo(fetchMock, "/api/control")).toHaveLength(1);
    // The (label, type) pair is what tells the three entries sharing this label
    // apart, and naming only these four fields on the `probe` entry is what
    // leaves every other field of it to whatever the control loop holds when the
    // queue drains.
    expect(postedNotifyUpdates(fetchMock).map((u) => [u.label, u.type])).toEqual([
      ["Probe1", "probe"],
      ["Probe1", "probe_limit_high"],
      ["Probe1", "probe_limit_low"],
    ]);
    expect(postedNotifyUpdates(fetchMock)[0].fields).toEqual({
      req: true,
      target: 203,
      keep_warm: true,
      shutdown: false,
    });
  });

  // The cross-process rule: the probe's LIVE reading decides the `triggered`
  // latch the backend then reads. Saved without pre-arming, a limit the
  // temperature has already passed sounds its alarm on the very next control
  // pass (notify/notifications.py:112) -- and the REST grammar cannot set
  // `triggered` at all (common/api_commands.py:544-551), which is why this
  // write is a POST /api/control.
  it("pre-arms a limit against the probe's live temperature", async () => {
    const user = userEvent.setup();
    const fetchMock = stubNotifyFetch();
    renderDashboard({
      ...FIXTURE_DASH,
      foodProbes: [
        {
          ...FIXTURE_DASH.foodProbes[0],
          title: "Brisket",
          label: "Probe1",
          temp: 250,
        },
      ],
    });
    await user.click(screen.getByRole("button", { name: "Notifications for Brisket" }));
    await user.click(screen.getByRole("checkbox", { name: /high limit/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /^high limit/i }));
    await user.type(screen.getByRole("spinbutton", { name: /^high limit/i }), "200");
    await user.click(screen.getByRole("button", { name: "Set" }));

    await waitFor(() => expect(fetchCallsTo(fetchMock, "/api/control")).toHaveLength(1));
    expect(
      postedNotifyUpdates(fetchMock).find((u) => u.type === "probe_limit_high")?.fields,
    ).toEqual({
      req: true,
      target: 200,
      triggered: true, // already at 250 -- stay quiet until it comes back down
      shutdown: false,
      keep_warm: false,
      reignite: false,
      condition: "equal_above",
    });
  });

  it("saves the primary probe's target against its own label", async () => {
    const user = userEvent.setup();
    const fetchMock = stubNotifyFetch();
    renderDashboard(FIXTURE_DASH);
    await user.click(
      screen.getByRole("button", {
        name: `Notifications for ${FIXTURE_DASH.primaryProbe.title}`,
      }),
    );
    await user.click(screen.getByRole("checkbox", { name: /target temperature/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /^target/i }));
    await user.type(screen.getByRole("spinbutton", { name: /^target/i }), "225");
    await user.click(screen.getByRole("button", { name: "Set" }));

    await waitFor(() => expect(fetchCallsTo(fetchMock, "/api/control")).toHaveLength(1));
    expect(
      postedNotifyUpdates(fetchMock).find(
        (u) => u.label === FIXTURE_DASH.primaryProbe.label && u.type === "probe",
      )?.fields,
    ).toMatchObject({ req: true, target: 225 });
  });

  it("keeps the modal open and shows the error when the save is rejected", async () => {
    const user = userEvent.setup();
    stubNotifyFetch({ result: "error", message: "Settings update failed." }, true);
    renderDashboard({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], title: "Brisket", label: "Probe1" }],
    });
    await user.click(screen.getByRole("button", { name: "Notifications for Brisket" }));
    await user.click(screen.getByRole("checkbox", { name: /target temperature/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /^target/i }));
    await user.type(screen.getByRole("spinbutton", { name: /^target/i }), "203");
    await user.click(screen.getByRole("button", { name: "Set" }));

    // Closing on failure would be indistinguishable from success: the write is
    // queued and not echoed back over the socket for ~110ms either way.
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Settings update"));
    expect(screen.getByText("Brisket Notifications")).toBeInTheDocument();
  });

  // The card must keep rendering from `dash`, so the new target appears only
  // when the socket echoes it back. The backend clears req/target/eta on its own
  // as soon as the target is reached (notify/notifications.py:109-111), so any
  // locally mirrored value would end up fighting the truth.
  it("does not mirror the saved target locally -- the card still reads from dash", async () => {
    const user = userEvent.setup();
    stubNotifyFetch();
    renderDashboard({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], title: "Brisket", label: "Probe1" }],
    });
    await user.click(screen.getByRole("button", { name: "Notifications for Brisket" }));
    await user.click(screen.getByRole("checkbox", { name: /target temperature/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /^target/i }));
    await user.type(screen.getByRole("spinbutton", { name: /^target/i }), "203");
    await user.click(screen.getByRole("button", { name: "Set" }));

    await waitFor(() =>
      expect(screen.queryByText("Brisket Notifications")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("AMBIENT")).toBeInTheDocument();
    expect(screen.queryByText("→ 203°")).not.toBeInTheDocument();
  });

  it("cancels without writing anything", async () => {
    const user = userEvent.setup();
    const fetchMock = stubNotifyFetch();
    renderDashboard({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], title: "Brisket", label: "Probe1" }],
    });
    await user.click(screen.getByRole("button", { name: "Notifications for Brisket" }));
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByText("Brisket Notifications")).not.toBeInTheDocument();
    // Flask's Cancel POSTs a wipe of the target AND both limit alerts
    // (dash_default.js:803-831). This one writes nothing at all.
    expect(fetchCallsTo(fetchMock, "/api/control")).toHaveLength(0);
  });
});

// D1: the CTRL OFFLINE signal comes from the errors blob, which never clears
// without a control.py restart (common/datastore_accessors.py:126-132) and can
// be written on a healthy system by a queue race (common/app.py:31-44). The
// frontend cannot clear the blob -- no route does -- so it offers to ask the
// same question directly instead.
describe("Dashboard control-health recheck", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("offers a Recheck beside CTRL OFFLINE when the payload says the control process is down", () => {
    renderDashboard(FIXTURE_DASH, { phase: "live", controlAlive: false });
    expect(screen.getByText("CTRL OFFLINE")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Recheck" })).toBeInTheDocument();
  });

  it("offers no Recheck while the control process is reported alive", () => {
    renderDashboard(FIXTURE_DASH, { phase: "live", controlAlive: true });
    expect(screen.queryByRole("button", { name: "Recheck" })).not.toBeInTheDocument();
  });

  it("offers no Recheck in demo mode, where there is no backend to ask", () => {
    renderDashboard(FIXTURE_DASH, { phase: "demo", controlAlive: false });
    expect(screen.queryByRole("button", { name: "Recheck" })).not.toBeInTheDocument();
  });

  it("asks /api/sys/check_alive and believes an OK over the stale blob", async () => {
    const user = userEvent.setup();
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => ({
      ok: true,
      json: async () => ({ result: "OK" }),
    }));
    rs.stubGlobal("fetch", fetchMock);
    renderDashboard(FIXTURE_DASH, { phase: "live", controlAlive: false });

    await user.click(screen.getByRole("button", { name: "Recheck" }));
    await waitFor(() => expect(screen.getByText("LIVE")).toBeInTheDocument());
    expect(fetchCallsTo(fetchMock, "/api/sys/check_alive")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Recheck" })).not.toBeInTheDocument();
  });

  it("stays offline when the recheck says the control process really is down", async () => {
    const user = userEvent.setup();
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({
        ok: true,
        json: async () => ({ result: "ERROR" }),
      })),
    );
    renderDashboard(FIXTURE_DASH, { phase: "live", controlAlive: false });

    await user.click(screen.getByRole("button", { name: "Recheck" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Recheck" })).toBeEnabled());
    expect(screen.getByText("CTRL OFFLINE")).toBeInTheDocument();
  });

  it("leaves Stop reachable while the control process is reported down", () => {
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Hold" }, { controlAlive: false });
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Smoke" })).toBeDisabled();
  });
});

// I4b: three readouts Flask has always carried and this port dropped. Each
// renders INSIDE an existing box -- no new rows -- so the 1280x720 geometry is
// unchanged whenever they are absent.
describe("Dashboard status readouts", () => {
  const secondsAgo = (n: number) => Math.floor(Date.now() / 1000) - n;
  const secondsAhead = (n: number) => Math.floor(Date.now() / 1000) + n;

  it("shows the time left in a timed mode, with Flask's literal wording", () => {
    renderDashboard({
      ...FIXTURE_DASH,
      currentMode: "Startup",
      startDuration: 240,
      modeStartTime: secondsAgo(60),
    });
    expect(screen.getByText(/Time Left in Mode: 1(79|80)s/)).toBeInTheDocument();
  });

  it("shows no mode countdown in a mode that has no duration", () => {
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Hold" });
    expect(screen.queryByText(/Time Left in Mode/)).not.toBeInTheDocument();
  });

  it("shows the PID-paused countdown beside LID OPEN, in Hold", () => {
    renderDashboard({
      ...FIXTURE_DASH,
      currentMode: "Hold",
      lidOpenDetected: true,
      lidOpenEndTime: secondsAhead(45),
    });
    expect(screen.getByText("LID OPEN")).toBeInTheDocument();
    expect(screen.getByText(/PID Paused 4[45]s/)).toBeInTheDocument();
  });

  it("shows no lid readout when no lid is open", () => {
    renderDashboard({
      ...FIXTURE_DASH,
      currentMode: "Hold",
      lidOpenDetected: false,
    });
    expect(screen.queryByText("LID OPEN")).not.toBeInTheDocument();
    expect(screen.queryByText(/PID Paused/)).not.toBeInTheDocument();
  });

  it("replaces the gauge's mode badge with the recipe step while a recipe runs", () => {
    renderDashboard({
      ...FIXTURE_DASH,
      currentMode: "Recipe",
      displayMode: "Hold",
      recipeStatus: { ...FIXTURE_DASH.recipeStatus, recipeMode: true },
    });
    expect(screen.getByText("Recipe | Hold")).toBeInTheDocument();
    expect(screen.queryByText("RECIPE")).not.toBeInTheDocument();
  });

  it("keeps the plain mode badge when no recipe is running", () => {
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Smoke" });
    expect(screen.getByText("SMOKE")).toBeInTheDocument();
    expect(screen.queryByText(/Recipe \|/)).not.toBeInTheDocument();
  });
});

// I1: the P-Mode value was displayed and could not be changed, while
// command.setPMode sat there with no caller. Flask showed the badge in every
// mode and offered the control in five (dash_default.js:248-293); here both
// the badge and the control are Smoke-only, because that is the mode whose
// cycle a P-number describes. Settings > Work Mode still edits it anywhere.
describe("Dashboard P-Mode control", () => {
  it("is not shown outside Smoke, where the pills carry the actuator duties", () => {
    for (const mode of ["Hold", "Stop", "Startup", "Prime", "Shutdown", "Reignite", "Monitor"]) {
      const { unmount } = renderDashboard({
        ...FIXTURE_DASH,
        currentMode: mode,
        pMode: 2,
      });
      expect(screen.queryByText("P-2"), mode).not.toBeInTheDocument();
      expect(screen.queryByText("P-MODE"), mode).not.toBeInTheDocument();
      expect(screen.queryByText("SMOKE+"), mode).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /P-MODE/ }), mode).not.toBeInTheDocument();
      expect(screen.getByText("AUGER DUTY"), mode).toBeInTheDocument();
      unmount();
    }
  });

  it("becomes a button in Smoke and opens a ten-item menu", async () => {
    const user = userEvent.setup();
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Smoke", pMode: 2 });
    const pill = screen.getByRole("button", { name: /P-MODE/ });
    await user.click(pill);

    expect(screen.getByText("P-Mode")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "0 - Off" })).toBeInTheDocument();
    for (let n = 1; n <= 9; n++) {
      expect(screen.getByRole("button", { name: String(n) })).toBeInTheDocument();
    }
  });

  it("sends the picked value through setPMode", async () => {
    const user = userEvent.setup();
    const command = makeCommand();
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Smoke", pMode: 2 }, { command });
    await user.click(screen.getByRole("button", { name: /P-MODE/ }));
    await user.click(screen.getByRole("button", { name: "7" }));
    await waitFor(() => expect(command.setPMode).toHaveBeenCalledWith(7));
  });

  // pMode is settings["cycle_data"]["PMode"] on the wire (socket_io.py:257) and
  // comes back on the next frame. A local mirror would fight it.
  it("keeps rendering the payload's value after a pick, with no local mirror", async () => {
    const user = userEvent.setup();
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Smoke", pMode: 2 });
    await user.click(screen.getByRole("button", { name: /P-MODE/ }));
    await user.click(screen.getByRole("button", { name: "7" }));
    await waitFor(() => expect(screen.queryByText("P-Mode")).not.toBeInTheDocument());
    expect(screen.getByText("P-2")).toBeInTheDocument();
    expect(screen.queryByText("P-7")).not.toBeInTheDocument();
  });
});

// M8: hasDistanceSensor is settings["modules"]["dist"] != "none"
// (socket_io.py:270) and had zero consumers, so React rendered a pellet gauge --
// reading a hard-coded level -- on grills with no distance sensor at all.
describe("Dashboard hopper card", () => {
  it("renders the hopper card when the grill has a distance sensor", () => {
    renderDashboard({ ...FIXTURE_DASH, hasDistanceSensor: true });
    expect(screen.getByText("Hopper")).toBeInTheDocument();
    expect(screen.getByText(`${FIXTURE_DASH.hopperLevel}%`)).toBeInTheDocument();
  });

  it("hides the whole card when the grill has none, exactly as Flask does", () => {
    renderDashboard({ ...FIXTURE_DASH, hasDistanceSensor: false });
    expect(screen.queryByText("Hopper")).not.toBeInTheDocument();
  });

  // The card used to carry a Refresh Status button wired to command.hopperCheck.
  // Both are gone: the control loop refreshes the level every ~10s on its own
  // (distance/intervals.py) and the socket pushes it with every frame. A
  // deliberate divergence from Flask, which keeps its button.
  it("carries no refresh control, because the level refreshes itself", () => {
    renderDashboard({ ...FIXTURE_DASH, hasDistanceSensor: true });
    expect(screen.queryByRole("button", { name: "Refresh Status" })).not.toBeInTheDocument();
  });
});

// Smoke+ is a toggle rather than a picker, so the pill writes straight through.
// Gated the same way the P-MODE pill is: outside Smoke the same pill reads FAN
// DUTY, which toggles nothing.
describe("Dashboard Smoke+ control", () => {
  it("toggles Smoke+ on from the pill in Smoke", async () => {
    const user = userEvent.setup();
    const command = makeCommand();
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Smoke", smokePlus: false }, { command });
    await user.click(screen.getByRole("button", { name: /SMOKE\+/ }));
    await waitFor(() => expect(command.setSmokePlus).toHaveBeenCalledWith(true));
  });

  it("toggles Smoke+ off again from the same pill", async () => {
    const user = userEvent.setup();
    const command = makeCommand();
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Smoke", smokePlus: true }, { command });
    await user.click(screen.getByRole("button", { name: /SMOKE\+/ }));
    await waitFor(() => expect(command.setSmokePlus).toHaveBeenCalledWith(false));
  });

  it("is not a button where the pill reads the fan duty", () => {
    for (const mode of ["Hold", "Stop", "Startup", "Shutdown"]) {
      const { unmount } = renderDashboard({
        ...FIXTURE_DASH,
        currentMode: mode,
      });
      expect(screen.queryByRole("button", { name: /SMOKE\+/ }), mode).not.toBeInTheDocument();
      expect(screen.getByText("FAN DUTY"), mode).toBeInTheDocument();
      unmount();
    }
  });

  it("stays a readout during a recipe, as the P-MODE pill does", () => {
    renderDashboard({
      ...FIXTURE_DASH,
      currentMode: "Smoke",
      recipeStatus: { ...FIXTURE_DASH.recipeStatus, recipeMode: true },
    });
    expect(screen.queryByRole("button", { name: /SMOKE\+/ })).not.toBeInTheDocument();
    expect(screen.getByText("SMOKE+")).toBeInTheDocument();
  });
});

describe("Dashboard thermocouple health wiring", () => {
  it("passes retained health to the primary gauge and food card without changing their outcomes", () => {
    const food = FIXTURE_DASH.foodProbes[0];
    renderDashboard(
      {
        ...FIXTURE_DASH,
        primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 225 },
        foodProbes: [{ ...food, temp: 147 }],
        thermocoupleHealth: [
          dashboardProbeHealth(
            "Primary",
            FIXTURE_DASH.primaryProbe.label,
            FIXTURE_DASH.primaryProbe.title,
            "notify_only",
          ),
          dashboardProbeHealth("Food", food.label, food.title, "unavailable"),
        ],
      },
      { phase: "unreachable" },
    );

    expect(screen.getByText("225")).toBeInTheDocument();
    expect(screen.getByText("Last reported: FAULT")).toBeInTheDocument();
    expect(
      screen.getByText("Fault detected — Observe mode did not stop heating."),
    ).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("Last reported: PROBE UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText("Grill control continues.")).toBeInTheDocument();
  });
});
