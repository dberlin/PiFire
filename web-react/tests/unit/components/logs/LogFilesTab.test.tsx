import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import * as actualLogsApi from "../../../../src/helpers/logs/logsApi" with {
  rstest: "importActual",
};

//  The viewer beneath the picker is the real LogViewer, so its fetches have to
//  go somewhere. What this file asserts is the picker and the download link.
rs.mock("../../../../src/helpers/logs/logsApi", () => ({
  ...actualLogsApi,
  fetchLogWhole: () => Promise.resolve({ text: "body\n", total: 5 }),
  fetchLogDelta: () => Promise.resolve({ kind: "unchanged", nextOffset: 5, total: 5 }),
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
});
