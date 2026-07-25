import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import type { CommandClient, CommandResult } from "../../helpers/command";
import { FIXTURE_DASH } from "../../helpers/fixture";
import type { NotifyEntry } from "../../helpers/notify/notifyApi";
import type { LiveState } from "../../helpers/types";
import { renderRoute } from "../../test-utils";
import { Dashboard } from "./Dashboard";

afterEach(cleanup);

const OK: CommandResult = { ok: true, message: "" };

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
  };
}

function renderDashboard(
  dash: LiveState,
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

  it("renders the food-probe column when foodProbes are present", () => {
    renderDashboard(FIXTURE_DASH);
    expect(screen.getByText("Food Probes")).toBeInTheDocument();
    expect(screen.getAllByText("AMBIENT")).toHaveLength(FIXTURE_DASH.foodProbes.length);
  });

  it("hides the food-probe column when there are no foodProbes", () => {
    renderDashboard({ ...FIXTURE_DASH, foodProbes: [] });
    expect(screen.queryByText("Food Probes")).not.toBeInTheDocument();
  });

  it("shows the P-mode and an ON smoke+ pill when smokePlus is set", () => {
    renderDashboard({ ...FIXTURE_DASH, pMode: 2, smokePlus: true });
    expect(screen.getByText("P-MODE")).toBeInTheDocument();
    expect(screen.getByText("P-2")).toBeInTheDocument();
    expect(screen.getByText("SMOKE+")).toBeInTheDocument();
    expect(screen.getByText("ON")).toBeInTheDocument();
  });

  it("shows an OFF smoke+ pill when smokePlus is unset", () => {
    renderDashboard({ ...FIXTURE_DASH, smokePlus: false });
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
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Hold", startupTimestamp: started });
    expect(screen.getByText("HOLD")).toBeInTheDocument();
    expect(screen.getByText(/^01:02:0\d$/)).toBeInTheDocument();
  });

  it("does not restart the counter when a fresh instance mounts mid-cook", () => {
    const started = Math.floor(Date.now() / 1000) - 754;
    const dash = { ...FIXTURE_DASH, currentMode: "Smoke", startupTimestamp: started };
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
    renderDashboard({ ...FIXTURE_DASH, currentMode: "Reignite", startupTimestamp: started });
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
      </MemoryRouter>,
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
// dashboard actually puts on the wire -- one GET, then one POST whose body
// carries the WHOLE notify_data array with a single entry edited.
// --------------------------------------------------------------------------

const NOTIFY_ENTRIES: NotifyEntry[] = [
  { label: "Grill", type: "probe", req: false, shutdown: false, keep_warm: false, target: 0 },
  {
    label: "Grill",
    type: "probe_limit_high",
    req: true,
    shutdown: false,
    keep_warm: false,
    target: 500,
    triggered: false,
  },
  { label: "Probe1", type: "probe", req: false, shutdown: false, keep_warm: false, target: 0 },
  {
    label: "Probe1",
    type: "probe_limit_high",
    req: true,
    shutdown: false,
    keep_warm: false,
    target: 350,
    triggered: false,
  },
];

function stubNotifyFetch(postBody: unknown = { result: "success" }, postOk = true) {
  const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async (url: string) =>
    String(url).endsWith("/api/get/notify")
      ? { ok: true, status: 200, json: async () => ({ result: "OK", data: NOTIFY_ENTRIES }) }
      : { ok: postOk, status: postOk ? 201 : 500, json: async () => postBody },
  );
  rs.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const postedNotifyData = (fetchMock: ReturnType<typeof rs.fn>): NotifyEntry[] => {
  const post = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/api/control"));
  if (post === undefined) throw new Error("no POST /api/control was issued");
  const body = JSON.parse(String((post[1] as RequestInit).body)) as {
    notify_data: NotifyEntry[];
  };
  return body.notify_data;
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
    expect(screen.getByRole("checkbox", { name: /notify/i })).toBeChecked();
    expect(screen.getByRole("spinbutton", { name: /target/i })).toHaveValue(203);
    expect(screen.getByRole("radio", { name: /keep warm/i })).toBeChecked();
  });

  // dash_default.html:36,53 renders the notify modal for probe_status['P'] as
  // well as ['F'], so a target on the grill probe is not a food-probe-only
  // feature. The Primary probe gets no action checkboxes (:188-198) and the
  // wider 0-600F range (:174-186).
  it("exposes a bell for the primary probe, opening it as primary", async () => {
    const user = userEvent.setup();
    renderDashboard(FIXTURE_DASH);
    await user.click(
      screen.getByRole("button", { name: `Notifications for ${FIXTURE_DASH.primaryProbe.title}` }),
    );
    expect(
      screen.getByText(`${FIXTURE_DASH.primaryProbe.title} Notifications`),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("radio")).toHaveLength(0);
    expect(screen.getByRole("slider", { name: /target/i })).toHaveAttribute("max", "600");
  });

  it("saves a food probe's target as ONE post of the whole array", async () => {
    const user = userEvent.setup();
    const fetchMock = stubNotifyFetch();
    renderDashboard({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], title: "Brisket", label: "Probe1" }],
    });
    await user.click(screen.getByRole("button", { name: "Notifications for Brisket" }));
    await user.click(screen.getByRole("checkbox", { name: /notify/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /target/i }));
    await user.type(screen.getByRole("spinbutton", { name: /target/i }), "203");
    await user.click(screen.getByRole("radio", { name: /keep warm/i }));
    await user.click(screen.getByRole("button", { name: "Set" }));

    await waitFor(() =>
      expect(screen.queryByText("Brisket Notifications")).not.toBeInTheDocument(),
    );
    expect(fetchMock.mock.calls).toHaveLength(2);
    const posted = postedNotifyData(fetchMock);
    expect(posted).toHaveLength(NOTIFY_ENTRIES.length);
    expect(posted.find((e) => e.label === "Probe1" && e.type === "probe")).toMatchObject({
      req: true,
      target: 203,
      keep_warm: true,
      shutdown: false,
    });
    // The limit entry sharing that label survives untouched -- the property the
    // per-field REST grammar could not have given us.
    expect(posted.find((e) => e.label === "Probe1" && e.type === "probe_limit_high")).toEqual(
      NOTIFY_ENTRIES.find((e) => e.label === "Probe1" && e.type === "probe_limit_high"),
    );
  });

  it("saves the primary probe's target against its own label", async () => {
    const user = userEvent.setup();
    const fetchMock = stubNotifyFetch();
    renderDashboard(FIXTURE_DASH);
    await user.click(
      screen.getByRole("button", { name: `Notifications for ${FIXTURE_DASH.primaryProbe.title}` }),
    );
    await user.click(screen.getByRole("checkbox", { name: /notify/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /target/i }));
    await user.type(screen.getByRole("spinbutton", { name: /target/i }), "225");
    await user.click(screen.getByRole("button", { name: "Set" }));

    await waitFor(() => expect(fetchMock.mock.calls).toHaveLength(2));
    expect(
      postedNotifyData(fetchMock).find(
        (e) => e.label === FIXTURE_DASH.primaryProbe.label && e.type === "probe",
      ),
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
    await user.click(screen.getByRole("checkbox", { name: /notify/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /target/i }));
    await user.type(screen.getByRole("spinbutton", { name: /target/i }), "203");
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
    await user.click(screen.getByRole("checkbox", { name: /notify/i }));
    await user.clear(screen.getByRole("spinbutton", { name: /target/i }));
    await user.type(screen.getByRole("spinbutton", { name: /target/i }), "203");
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
    expect(fetchMock.mock.calls).toHaveLength(0);
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
    expect(fetchMock.mock.calls[0][0]).toBe("/api/sys/check_alive");
    expect(screen.queryByRole("button", { name: "Recheck" })).not.toBeInTheDocument();
  });

  it("stays offline when the recheck says the control process really is down", async () => {
    const user = userEvent.setup();
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: true, json: async () => ({ result: "ERROR" }) })),
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
