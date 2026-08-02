import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import * as actualLogsApi from "../../../../src/helpers/logs/logsApi" with {
  rstest: "importActual",
};
import { renderRoute } from "../../test-utils";

const fetchLogFamiliesMock = rs.fn();
rs.mock("../../../../src/helpers/logs/logsApi", () => ({
  ...actualLogsApi,
  fetchLogFamilies: (...a: unknown[]) => fetchLogFamiliesMock(...a),
  fetchLogWhole: () => Promise.resolve({ text: "alpha\n", total: 6 }),
  fetchLogDelta: () => Promise.resolve({ kind: "unchanged", nextOffset: 6, total: 6 }),
}));

const { EventsPage } = await import("../../../../src/components/logs/EventsPage");

beforeEach(() => {
  fetchLogFamiliesMock.mockReset();
  fetchLogFamiliesMock.mockResolvedValue([
    { stem: "events", members: ["events.log"], bytes: 6 },
    { stem: "mqtt", members: ["mqtt.log"], bytes: 9 },
  ]);
});

describe("EventsPage", () => {
  it("opens on the Events tab", async () => {
    renderRoute(<EventsPage />, {});
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Events" }).getAttribute("aria-selected")).toBe(
        "true",
      ),
    );
  });

  it("switches to Log Files", async () => {
    renderRoute(<EventsPage />, {});
    fireEvent.click(screen.getByRole("tab", { name: "Log Files" }));
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "Log Files" }).getAttribute("aria-selected")).toBe(
        "true",
      ),
    );
  });

  it("offers a follow toggle on the Events tab", async () => {
    renderRoute(<EventsPage />, {});
    expect(await screen.findByRole("checkbox", { name: /follow/i })).toBeTruthy();
  });

  it("offers a follow toggle on the Log Files tab too", async () => {
    //  A family's current file -- control.log, webapp.log -- is being written
    //  right now; only its rotated backups are history.
    renderRoute(<EventsPage />, {});
    fireEvent.click(screen.getByRole("tab", { name: "Log Files" }));
    await waitFor(() => expect(screen.queryByRole("checkbox", { name: /follow/i })).not.toBeNull());
  });

  it("hands the Log Files tab the families it read", async () => {
    renderRoute(<EventsPage />, {});
    await waitFor(() => expect(fetchLogFamiliesMock).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("tab", { name: "Log Files" }));
    expect(await screen.findByRole("option", { name: /mqtt/ })).toBeTruthy();
  });
});
