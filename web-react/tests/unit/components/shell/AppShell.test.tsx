import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { createMemoryRouter, MemoryRouter, Route, RouterProvider, Routes } from "react-router";
import type { CommandClient, CommandResult } from "../../../../src/helpers/command";
import { FIXTURE_DASH } from "../../../../src/helpers/fixture";
import { queryKeys } from "../../../../src/helpers/query/keys";
import { useShellState } from "../../../../src/helpers/shellContext";
import type { DashSocketPayload } from "../../../../src/helpers/contracts/core.gen"

// The shell owns the app's ONE live-state subscription (useLiveState opens a
// socket per call), so it is mocked here rather than connected: these tests are
// about what the shell does with that data, not about the socket.
const useLiveStateMock = rs.fn();
rs.mock("../../../../src/helpers/useLiveState", () => ({
  useLiveState: () => useLiveStateMock(),
}));

const { AppShell } = await import("../../../../src/components/shell/AppShell");

const OK: CommandResult = { ok: true, message: "" };
const NOW = 1_700_000_000;

type Timer = DashSocketPayload["timer"];

const timerBlock = (over: Partial<Timer> = {}): Timer => ({
  start: 0,
  paused: 0,
  end: 0,
  keepWarm: false,
  shutdown: false,
  ...over,
});

function stubCommand(): CommandClient {
  const ok = async () => OK;
  return {
    setMode: rs.fn(ok),
    hold: rs.fn(ok),
    setSmokePlus: rs.fn(ok),
    setPMode: rs.fn(ok),
    prime: rs.fn(ok),
    timerStart: rs.fn(ok),
    timerStartWithOptions: rs.fn(ok),
    timerPause: rs.fn(ok),
    timerStop: rs.fn(ok),
    timerShutdown: rs.fn(ok),
    timerKeepWarm: rs.fn(ok),
    system: rs.fn(ok),
    setUnits: rs.fn(ok),
    manualOutput: rs.fn(ok),
    manualPwm: rs.fn(ok),
    recipeNextStep: rs.fn(ok),
    recipeUnpause: rs.fn(ok),
  };
}

// A child route that reports what the shell handed it, so "the page gets the
// shell's live state" is asserted through the real Outlet context rather than
// by inspecting the shell.
function ContextProbe() {
  const { live, phase, controlAlive, targetUrl, command } = useShellState();
  return (
    <div>
      <span data-testid="probe-mode">{live.currentMode}</span>
      <span data-testid="probe-phase">{phase}</span>
      <span data-testid="probe-control">{controlAlive ? "alive" : "down"}</span>
      <span data-testid="probe-url">{targetUrl}</span>
      <button type="button" onClick={() => command.timerStop()}>
        stop from the page
      </button>
    </div>
  );
}

function mountShell(over: Partial<DashSocketPayload> = {}) {
  useLiveStateMock.mockReturnValue({
    live: { ...FIXTURE_DASH, timer: timerBlock(), ...over },
    phase: "live",
    controlAlive: true,
    targetUrl: "http://pifire.local:5000",
    command: stubCommand(),
    // The shell forwards its whole useLiveState() result on Outlet context,
    // so a stub must carry every field the real hook returns. null is what a
    // client that has not yet received socket_pellet_data sees.
    pellets: null,
  });
  const router = createMemoryRouter(
    [{ path: "/", element: <AppShell />, children: [{ index: true, element: <ContextProbe /> }] }],
    { initialEntries: ["/"] },
  );
  // App.tsx wraps every route in QueryClientProvider; AppShell's own
  // useQueryClient() call needs one present even in tests that don't care
  // about the settings cache.
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

// A full live-state frame to vary uiHash on, one socket tick at a time --
// App.tsx wraps every route in QueryClientProvider, so AppShell's
// useQueryClient() needs one here too.
const FRAME: DashSocketPayload = { ...FIXTURE_DASH, timer: timerBlock() };

function frameRenderer(client: QueryClient) {
  let utils: ReturnType<typeof render> | null = null;
  return (frame: DashSocketPayload) => {
    useLiveStateMock.mockReturnValue({
      live: frame,
      phase: "live",
      controlAlive: true,
      targetUrl: "http://pifire.local:5000",
      command: stubCommand(),
      pellets: null,
    });
    // A declarative MemoryRouter, not createMemoryRouter/RouterProvider: the
    // data router memoizes its rendered tree off router state, so a rerender()
    // that changes only the useLiveState() mock (router state untouched)
    // never reaches AppShell -- verified by instrumentation, the component
    // rendered exactly once across two rerenderWithFrame calls.
    const ui = (
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/"]}>
          <Routes>
            <Route path="/" element={<AppShell />}>
              <Route index element={<ContextProbe />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    );
    if (utils === null) {
      utils = render(ui);
    } else {
      utils.rerender(ui);
    }
  };
}

const stopwatch = () => screen.getByRole("button", { name: /toggle timer bar/i });
const timerBar = (container: HTMLElement) => container.querySelector(".pf-timer-bar");

// The timer bar reads the shared clock, and one case counts live intervals, so
// the fake clock is installed before any render. Clicks go through fireEvent
// rather than userEvent: userEvent's own scheduling deadlocks against fake
// timers, and nothing here needs its richer event sequence.
beforeEach(() => {
  useLiveStateMock.mockReset();
  rs.useFakeTimers();
  rs.setSystemTime(NOW * 1000);
});

afterEach(() => {
  rs.useRealTimers();
  cleanup();
});

describe("AppShell chrome", () => {
  it("renders the navbar above the routed page", () => {
    mountShell({ currentMode: "Hold" });

    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "History" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByTestId("probe-mode")).toHaveTextContent("Hold");
  });

  it("names the grill in the navbar from the live payload", () => {
    mountShell({ grillName: "Backyard Smoker" });

    expect(screen.getByText("Backyard Smoker")).toBeInTheDocument();
  });

  it("renders the global alert strip on every page, not just the dashboard", () => {
    mountShell({
      errors: ["control down"],
      warnings: ["lid open"],
      warningsMaxId: 1,
      criticalError: false,
    });

    expect(screen.getByText("control down")).toHaveClass("pf-banner--error");
    expect(screen.getByText("lid open")).toHaveClass("pf-banner--warning");
  });

  it("renders no banner strip when there is nothing to report", () => {
    const { container } = mountShell({ errors: [], warnings: [], criticalError: false });

    expect(container.querySelector(".pf-banners")).toBeNull();
  });
});

describe("AppShell live-state context", () => {
  it("hands its single subscription to the routed page", () => {
    mountShell({ currentMode: "Smoke" });

    expect(screen.getByTestId("probe-mode")).toHaveTextContent("Smoke");
    expect(screen.getByTestId("probe-phase")).toHaveTextContent("live");
    expect(screen.getByTestId("probe-control")).toHaveTextContent("alive");
    expect(screen.getByTestId("probe-url")).toHaveTextContent("http://pifire.local:5000");
  });

  it("passes the same command client the shell itself uses", () => {
    mountShell();
    const { command } = useLiveStateMock.mock.results[0].value;

    fireEvent.click(screen.getByRole("button", { name: "stop from the page" }));

    expect(command.timerStop).toHaveBeenCalled();
  });
});

// Folded in from the former shell/timerToggle.test.tsx, which mirrored this
// wiring by hand before AppShell existed. Now that the real shell owns the
// composition these drive it directly.
describe("AppShell navbar stopwatch and timer bar", () => {
  it("hides the bar until the stopwatch is pressed", () => {
    const { container } = mountShell();
    expect(timerBar(container)).toBeNull();

    fireEvent.click(stopwatch());

    expect(timerBar(container)).toBeTruthy();
    expect(stopwatch().getAttribute("aria-pressed")).toBe("true");
  });

  it("hides the bar again on a second press", () => {
    const { container } = mountShell();

    fireEvent.click(stopwatch());
    fireEvent.click(stopwatch());

    expect(timerBar(container)).toBeNull();
    expect(stopwatch().getAttribute("aria-pressed")).toBe("false");
  });

  it("shows the bar unprompted when a timer is already running", () => {
    const { container } = mountShell({ timer: timerBlock({ start: NOW - 60, end: NOW + 600 }) });

    expect(timerBar(container)).toBeTruthy();
    expect(stopwatch().className).toContain("running");
  });

  it("arms no clock interval while the bar is hidden, even mid-cook", () => {
    mountShell({ timer: timerBlock({ start: NOW - 60, end: NOW + 600 }) });
    expect(rs.getTimerCount()).toBeGreaterThan(0);

    fireEvent.click(stopwatch());

    // Hiding unmounts the bar, which detaches the shell's last subscriber from
    // the shared clock -- nothing is left ticking for an invisible display.
    expect(rs.getTimerCount()).toBe(0);
  });

  it("drives the timer with the shell's own command client", () => {
    const { container } = mountShell({ timer: timerBlock({ start: NOW - 60, end: NOW + 600 }) });
    const { command } = useLiveStateMock.mock.results[0].value;
    expect(timerBar(container)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /pause timer/i }));

    expect(command.timerPause).toHaveBeenCalled();
  });
});

describe("AppShell settings invalidation on uiHash change", () => {
  it("refetches settings when the probe map changes under it", async () => {
    // A changed uiHash means set_probe_map() ran somewhere else: hidden_cards,
    // notify_data and probe_config are all stale in cache even though the
    // readings on screen are live.
    const client = new QueryClient();
    const spy = rs.spyOn(client, "invalidateQueries");
    const rerenderWithFrame = frameRenderer(client);
    rerenderWithFrame({ ...FRAME, uiHash: 111 });
    rerenderWithFrame({ ...FRAME, uiHash: 222 });
    expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.settings });
  });

  it("does not refetch when the hash is unchanged", async () => {
    const client = new QueryClient();
    const spy = rs.spyOn(client, "invalidateQueries");
    const rerenderWithFrame = frameRenderer(client);
    rerenderWithFrame({ ...FRAME, uiHash: 111 });
    rerenderWithFrame({ ...FRAME, uiHash: 111 });
    expect(spy).not.toHaveBeenCalled();
  });
});
