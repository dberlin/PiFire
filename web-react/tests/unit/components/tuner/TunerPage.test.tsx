import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { queryKeys } from "../../../../src/helpers/query/keys";
import * as actualTunerApi from "../../../../src/helpers/tuner/tunerApi" with {
  rstest: "importActual",
};
import { flushObservers, renderWithQuery, testQueryClient } from "../../test-utils";

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
  it("takes the probe list from the shared settings entry", async () => {
    getSettingsMock.mockResolvedValue(SETTINGS);
    renderWithQuery(<TunerPage />);
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /probe/i })).toHaveValue("Grill"),
    );
    expect(getSettingsMock).toHaveBeenCalledTimes(1);
  });

  it("does not overwrite the operator's probe selection when settings refetch", async () => {
    // A settings save invalidates the settings key, so a refetch WILL happen
    // while the operator is sitting on their own selection. Seeding is a
    // first-list-only event for exactly this reason (see AppPrefs.test.tsx for
    // the same case on the accent seed).
    getSettingsMock.mockResolvedValue(SETTINGS);
    const client = testQueryClient();
    render(
      <QueryClientProvider client={client}>
        <TunerPage />
      </QueryClientProvider>,
    );
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: /probe/i })).toHaveValue("Grill"),
    );

    await userEvent.selectOptions(screen.getByRole("combobox", { name: /probe/i }), "Probe1");
    expect(screen.getByRole("combobox", { name: /probe/i })).toHaveValue("Probe1");

    // The refetch's FIRST probe ("Ambient") is deliberately NOT the operator's
    // current selection ("Probe1"): an unguarded seed would overwrite it with
    // "Ambient", so this is the payload that actually discriminates a working
    // seed-once from a broken one. (Reusing SETTINGS unchanged, or reordering
    // it so "Probe1" landed first, would have let an unguarded seed produce
    // the SAME value as the guarded one -- which is exactly the mistake the
    // first version of this test made.)
    getSettingsMock.mockResolvedValue({
      probe_settings: {
        probe_map: {
          probe_info: [{ label: "Ambient" }, { label: "Grill" }, { label: "Probe1" }],
        },
      },
    });
    await act(() =>
      client.invalidateQueries({
        queryKey: queryKeys.settings(import.meta.env.PUBLIC_PIFIRE_URL || ""),
      }),
    );
    await flushObservers();

    expect(screen.getByRole("combobox", { name: /probe/i })).toHaveValue("Probe1");
  });

  it("renders the three segments in High, Medium, Low order", async () => {
    renderWithQuery(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    const titles = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(titles).toEqual(["High", "Medium", "Low"]);
  });

  it("does not open a session on mount", async () => {
    renderWithQuery(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    expect(openMock).not.toHaveBeenCalled();
  });

  it("Start opens the session and begins polling the selected probe", async () => {
    renderWithQuery(<TunerPage />);
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
    renderWithQuery(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await userEvent.click(screen.getByRole("button", { name: "Start tuning" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Hold");
    expect(screen.queryByRole("button", { name: "Stop tuning" })).toBeNull();
  });

  it("Finish is disabled until all three segments are recorded", async () => {
    renderWithQuery(<TunerPage />);
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
    renderWithQuery(<TunerPage />);
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
    renderWithQuery(<TunerPage />);
    await startTuning();
    await recordAllSegments();
    await userEvent.click(screen.getByRole("button", { name: "Finish" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not be calculated/i);
    expect(screen.queryByRole("button", { name: "Save & Apply" })).toBeNull();
  });

  it("polls once per second while open and stops when the session closes", async () => {
    installFakeClock();
    try {
      renderWithQuery(<TunerPage />);
      // Let the mount settings fetch resolve under fake timers. react-query
      // notifies observers via a real setTimeout(0) (notifyManager), which
      // fake timers intercept same as the poll's own setInterval, so it needs
      // an explicit tick alongside the promise flush.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
        rs.advanceTimersByTime(0);
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
    const view = renderWithQuery(<TunerPage />);
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
    renderWithQuery(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    expect(screen.getByRole("button", { name: "Manual" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Auto" })).toHaveAttribute("aria-pressed", "false");
  });

  it("switching to Auto shows the reference selector and hides the segments", async () => {
    renderWithQuery(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await switchToAuto();
    expect(screen.getByRole("combobox", { name: /reference/i })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "High" })).toBeNull();
  });

  it("disables the toggle while a session is open", async () => {
    renderWithQuery(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await switchToAuto();
    await startTuning();
    expect(screen.getByRole("button", { name: "Manual" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Auto" })).toBeDisabled();
  });

  it("Auto Start opens the session and polls auto-status with the tune and reference probes", async () => {
    renderWithQuery(<TunerPage />);
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
    renderWithQuery(<TunerPage />);
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
    renderWithQuery(<TunerPage />);
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
    const view = renderWithQuery(<TunerPage />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "High" })).toBeVisible());
    await switchToAuto();
    await startTuning();
    view.unmount();
    await waitFor(() => expect(closeMock).toHaveBeenCalled());
  });
});
