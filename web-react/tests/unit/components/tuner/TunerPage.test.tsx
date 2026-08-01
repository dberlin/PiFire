import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import * as actualTunerApi from "../../../../src/helpers/tuner/tunerApi" with {
  rstest: "importActual",
};

const openMock = rs.fn();
const closeMock = rs.fn();
const fetchTrMock = rs.fn();
const fetchAutoStatusMock = rs.fn();
const computeMock = rs.fn();
rs.mock("../../../../src/helpers/tuner/tunerApi", () => ({
  ...actualTunerApi,
  openSession: (...a: unknown[]) => openMock(...a),
  closeSession: (...a: unknown[]) => closeMock(...a),
  fetchTr: (...a: unknown[]) => fetchTrMock(...a),
  fetchAutoStatus: (...a: unknown[]) => fetchAutoStatusMock(...a),
  computeCoefficients: (...a: unknown[]) => computeMock(...a),
}));

const getSettingsMock = rs.fn();
rs.mock("../../../../src/helpers/settings/settingsApi", () => ({
  getSettings: (...a: unknown[]) => getSettingsMock(...a),
}));

const { TunerPage } = await import("../../../../src/components/tuner/TunerPage");

const SETTINGS = {
  probe_settings: {
    probe_map: { probe_info: [{ label: "Grill" }, { label: "Probe1" }] },
  },
};

const OK = (data: unknown) => ({ ok: true, status: 200, message: "", data });
const READING = OK({ probe: "Grill", trohms: 51234, tuning: true });

const autoStatus = (over = {}) =>
  OK({
    current_tr: 41000,
    current_temp: 225,
    high_tr: 0,
    high_temp: 0,
    medium_tr: 0,
    medium_temp: 0,
    low_tr: 0,
    low_temp: 0,
    samples: 3,
    ready: false,
    ...over,
  });

// Fake timers must be installed BEFORE render, or the poll interval runs on the
// real clock -- the mistake that cost the events slice four failing tests.
const installFakeClock = rs.useFakeTimers.bind(rs);

beforeEach(() => {
  openMock.mockReset();
  closeMock.mockReset();
  fetchTrMock.mockReset();
  computeMock.mockReset();
  getSettingsMock.mockReset();
  fetchAutoStatusMock.mockReset();
  getSettingsMock.mockResolvedValue(SETTINGS);
  openMock.mockResolvedValue(OK({ open: true, mode: "Monitor", restored: true }));
  closeMock.mockResolvedValue(OK({ open: false, mode: "Stop", restored: true }));
  fetchTrMock.mockResolvedValue(READING);
  fetchAutoStatusMock.mockResolvedValue(autoStatus());
});

afterEach(() => {
  cleanup();
  rs.useRealTimers();
});

async function startTuning() {
  await userEvent.click(screen.getByRole("button", { name: "Start tuning" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Stop tuning" })).toBeVisible());
}

// Record all three points. Each recording removes that card's own input and
// Record button (it switches to its recorded view), so the FIRST remaining
// input/button is always the next unrecorded segment -- High, then Medium,
// then Low.
async function recordAllSegments() {
  for (let i = 0; i < 3; i++) {
    const input = screen.getAllByRole("spinbutton", { name: /temperature/i })[0];
    await userEvent.type(input, String(400 - i * 100));
    await userEvent.click(screen.getAllByRole("button", { name: "Record" })[0]);
  }
}

describe("TunerPage", () => {
  it("renders the three segments in High, Medium, Low order", async () => {
    render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    const titles = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(titles).toEqual(["High", "Medium", "Low"]);
  });

  it("does not open a session on mount", async () => {
    render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    expect(openMock).not.toHaveBeenCalled();
  });

  it("Start opens the session and begins polling the selected probe", async () => {
    render(<TunerPage />);
    await startTuning();
    await waitFor(() => expect(fetchTrMock).toHaveBeenCalled());
    expect(fetchTrMock.mock.calls[0][0]).toBe("Grill");
  });

  it("a 409 refusal shows the mode and offers no Stop", async () => {
    openMock.mockResolvedValue({
      ok: false,
      status: 409,
      message: "not_tunable",
      data: null,
      mode: "Hold",
    });
    render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await userEvent.click(screen.getByRole("button", { name: "Start tuning" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Hold");
    expect(screen.queryByRole("button", { name: "Stop tuning" })).toBeNull();
  });

  it("Finish is disabled until all three segments are recorded", async () => {
    render(<TunerPage />);
    await startTuning();
    await waitFor(() => expect(screen.getAllByText("51234 Ω")).toHaveLength(3));

    expect(screen.getByRole("button", { name: "Finish" })).toBeDisabled();
    await recordAllSegments();
    expect(screen.getByRole("button", { name: "Finish" })).toBeEnabled();
  });

  it("Finish computes, closes the session, and shows the chart and the form", async () => {
    computeMock.mockResolvedValue(
      OK({ a: 1, b: 2, c: 3, chart: [{ x: 0, y: 9 }], chart_ok: true }),
    );
    render(<TunerPage />);
    await startTuning();
    await recordAllSegments();
    await userEvent.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(screen.getByRole("img", { name: /resistance/i })).toBeVisible());
    expect(screen.getByRole("button", { name: "Save & Apply" })).toBeVisible();
    //  Finish hands the grill back to Stop.
    expect(closeMock).toHaveBeenCalled();
  });

  it("an uncomputable result shows the error and no save form", async () => {
    computeMock.mockResolvedValue({
      ok: false,
      status: 422,
      message: "uncomputable",
      data: null,
    });
    render(<TunerPage />);
    await startTuning();
    await recordAllSegments();
    await userEvent.click(screen.getByRole("button", { name: "Finish" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be calculated/i);
    expect(screen.queryByRole("button", { name: "Save & Apply" })).toBeNull();
  });

  it("polls once per second while open and stops when the session closes", async () => {
    installFakeClock();
    try {
      render(<TunerPage />);
      // Let the mount settings fetch resolve under fake timers.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      // Open the session directly through the button's handler path.
      act(() => screen.getByRole("button", { name: "Start tuning" }).click());
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      const afterOpen = fetchTrMock.mock.calls.length;
      await act(async () => {
        rs.advanceTimersByTime(1000);
        await Promise.resolve();
      });
      expect(fetchTrMock.mock.calls.length).toBeGreaterThan(afterOpen);

      // Close, then confirm no further polls land.
      act(() => screen.getByRole("button", { name: "Stop tuning" }).click());
      await act(async () => {
        await Promise.resolve();
      });
      const afterStop = fetchTrMock.mock.calls.length;
      await act(async () => {
        rs.advanceTimersByTime(3000);
        await Promise.resolve();
      });
      expect(fetchTrMock.mock.calls.length).toBe(afterStop);
    } finally {
      rs.useRealTimers();
    }
  });

  it("closes the session when the page is left", async () => {
    const view = render(<TunerPage />);
    await startTuning();
    view.unmount();
    await waitFor(() => expect(closeMock).toHaveBeenCalled());
  });
});

describe("TunerPage — auto mode", () => {
  async function switchToAuto() {
    await userEvent.click(screen.getByRole("button", { name: "Auto" }));
  }

  it("defaults to Manual with the three segment cards shown", async () => {
    render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    expect(screen.getByRole("button", { name: "Manual" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Auto" })).toHaveAttribute("aria-pressed", "false");
  });

  it("switching to Auto shows the reference selector and hides the segments", async () => {
    render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await switchToAuto();
    expect(screen.getByRole("combobox", { name: /reference/i })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "High" })).toBeNull();
  });

  it("disables the toggle while a session is open", async () => {
    render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await switchToAuto();
    await startTuning();
    expect(screen.getByRole("button", { name: "Manual" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Auto" })).toBeDisabled();
  });

  it("Auto Start opens the session and polls auto-status with the tune and reference probes", async () => {
    render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await switchToAuto();
    await startTuning();
    await waitFor(() => expect(fetchAutoStatusMock).toHaveBeenCalled());
    //  The tuned probe defaults to the first label, the reference to the first
    //  OTHER label.
    expect(fetchAutoStatusMock.mock.calls[0][0]).toBe("Grill");
    expect(fetchAutoStatusMock.mock.calls[0][1]).toBe("Probe1");
    //  Manual's Tr poll must NOT run in auto mode.
    expect(fetchTrMock).not.toHaveBeenCalled();
  });

  it("Auto Finish is disabled until the status is ready", async () => {
    fetchAutoStatusMock.mockResolvedValue(autoStatus({ ready: false }));
    render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await switchToAuto();
    await startTuning();
    await waitFor(() => expect(fetchAutoStatusMock).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Finish" })).toBeDisabled();
  });

  it("Auto Finish sends the derived high/medium/low points and closes the session", async () => {
    fetchAutoStatusMock.mockResolvedValue(
      autoStatus({
        ready: true,
        high_temp: 240,
        high_tr: 30000,
        medium_temp: 170,
        medium_tr: 40000,
        low_temp: 100,
        low_tr: 50000,
        samples: 14,
      }),
    );
    computeMock.mockResolvedValue(
      OK({ a: 1, b: 2, c: 3, chart: [{ x: 0, y: 9 }], chart_ok: true }),
    );
    render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await switchToAuto();
    await startTuning();
    await waitFor(() => expect(screen.getByRole("button", { name: "Finish" })).toBeEnabled());

    await userEvent.click(screen.getByRole("button", { name: "Finish" }));

    await waitFor(() => expect(computeMock).toHaveBeenCalled());
    expect(computeMock.mock.calls[0][0]).toEqual([
      { segment: "High", temp: 240, trohms: 30000 },
      { segment: "Medium", temp: 170, trohms: 40000 },
      { segment: "Low", temp: 100, trohms: 50000 },
    ]);
    await waitFor(() => expect(screen.getByRole("img", { name: /resistance/i })).toBeVisible());
    expect(closeMock).toHaveBeenCalled();
  });

  it("closes the session when the page is left in auto mode", async () => {
    const view = render(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await switchToAuto();
    await startTuning();
    view.unmount();
    await waitFor(() => expect(closeMock).toHaveBeenCalled());
  });
});
