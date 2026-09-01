import { describe, expect, it, rs } from "@rstest/core";
import { act, fireEvent, render, screen } from "@testing-library/react";

import * as actualLogsApi from "../../../../src/helpers/logs/logsApi" with {
  rstest: "importActual",
};

//  The viewer beneath the picker is the real LogViewer, so its fetches have to
//  go somewhere. What this file asserts is the picker, the download link, and
//  that the follow toggle reaches the viewer -- LogViewer.test.tsx owns what
//  following then does.
const fetchLogDeltaMock = rs.fn((..._a: unknown[]) =>
  Promise.resolve({ kind: "unchanged", nextOffset: 5, total: 5 }),
);
rs.mock("../../../../src/helpers/logs/logsApi", () => ({
  ...actualLogsApi,
  fetchLogWhole: () => Promise.resolve({ text: "body\n", total: 5 }),
  fetchLogDelta: (...a: unknown[]) => fetchLogDeltaMock(...a),
}));

const { LogFilesTab } = await import("../../../../src/components/logs/LogFilesTab");

const FAMILIES = [
  { stem: "events", members: ["events.log.1", "events.log"], bytes: 2048 },
  { stem: "mqtt", members: ["mqtt.log"], bytes: 1024 },
];

describe("LogFilesTab", () => {
  it("lists every family with its member count", () => {
    render(<LogFilesTab families={FAMILIES} />);
    expect(screen.getByRole("option", { name: "events (2 files, 2.0 KiB)" })).toBeTruthy();
    expect(screen.getByRole("option", { name: "mqtt (1 file, 1.0 KiB)" })).toBeTruthy();
  });

  it("downloads the whole family, not one member", () => {
    //  The bytes offered must be the bytes displayed, so the link points at the
    //  same view endpoint with download=1 rather than at a single file. A link
    //  to events.log would silently hand over the newest slice of a family
    //  whose history is mostly in events.log.1.
    render(<LogFilesTab families={FAMILIES} />);
    expect(screen.getByRole("link", { name: /download/i }).getAttribute("href")).toBe(
      "/api/admin/logs/view?log=events&download=1",
    );
  });

  it("switches the viewer when another family is picked", () => {
    render(<LogFilesTab families={FAMILIES} />);
    fireEvent.change(screen.getByRole("combobox", { name: /log file/i }), {
      target: { value: "mqtt" },
    });
    expect(screen.getByRole("link", { name: /download/i }).getAttribute("href")).toBe(
      "/api/admin/logs/view?log=mqtt&download=1",
    );
  });

  it("says so when there are no logs at all", () => {
    render(<LogFilesTab families={[]} />);
    expect(screen.getByText(/no log files/i)).toBeTruthy();
  });

  // A family's current file is live -- control.log while the control loop runs,
  // webapp.log while gunicorn serves. Only its rotated backups are history, so
  // this tab had a viewer that could tail and no way to ask it to.
  it("tails the selected family by default", async () => {
    fetchLogDeltaMock.mockClear();
    rs.useFakeTimers();
    try {
      render(<LogFilesTab families={FAMILIES} />);
      expect((screen.getByRole("checkbox", { name: /follow/i }) as HTMLInputElement).checked).toBe(
        true,
      );
      //  The poll interval is only created once the initial whole-family read
      //  has landed, so it has to settle before the clock is advanced past one
      //  tick -- otherwise the interval does not exist yet to fire.
      await act(async () => {
        await rs.advanceTimersByTimeAsync(0);
      });
      await act(async () => {
        await rs.advanceTimersByTimeAsync(3000);
      });
      expect(fetchLogDeltaMock.mock.calls.length).toBeGreaterThan(0);
    } finally {
      rs.useRealTimers();
    }
  });

  it("stops tailing when the toggle is cleared", async () => {
    rs.useFakeTimers();
    try {
      render(<LogFilesTab families={FAMILIES} />);
      await act(async () => {
        await rs.advanceTimersByTimeAsync(0);
      });
      fireEvent.click(screen.getByRole("checkbox", { name: /follow/i }));

      fetchLogDeltaMock.mockClear();
      await act(async () => {
        await rs.advanceTimersByTimeAsync(9000); // three poll intervals
      });

      expect(fetchLogDeltaMock.mock.calls.length).toBe(0);
    } finally {
      rs.useRealTimers();
    }
  });
});
