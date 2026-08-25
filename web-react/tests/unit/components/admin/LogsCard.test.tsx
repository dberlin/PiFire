import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as actualAdminApi from "../../../../src/helpers/admin/adminApi" with {
  rstest: "importActual",
};

const deleteLogsMock = rs.fn();
//  logsDownloadUrl stays real -- the href is built here, so it is worth pinning.
rs.mock("../../../../src/helpers/admin/adminApi", () => ({
  ...actualAdminApi,
  deleteLogs: (...a: unknown[]) => deleteLogsMock(...a),
}));

const { LogsCard } = await import("../../../../src/components/admin/LogsCard");

const ok = (removed: string[]) => ({
  ok: true,
  status: 200,
  message: "",
  data: { removed },
});

let onChanged: ReturnType<typeof rs.fn>;

beforeEach(() => {
  deleteLogsMock.mockReset();
  deleteLogsMock.mockResolvedValue(ok(["events.log", "control.log"]));
  onChanged = rs.fn();
});

const mount = (logs: string[] = ["control.log", "events.log"]) =>
  render(<LogsCard logs={logs} onChanged={onChanged} />);

describe("LogsCard listing", () => {
  it("names every log file", () => {
    mount();
    expect(screen.getByText("control.log")).toBeTruthy();
    expect(screen.getByText("events.log")).toBeTruthy();
  });

  it("says so when there are none", () => {
    mount([]);
    expect(screen.getByText("No log files.")).toBeTruthy();
  });

  it("offers one archive rather than a per-file download", () => {
    //  Matching the server: /logs/download takes no filename at all, which is
    //  the same path rule the rest of the page follows. The card's other link
    //  is the diagnostics bundle, likewise filename-free -- what must never
    //  appear here is a link per log file.
    mount();
    const hrefs = screen.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/api/admin/logs/download", "/api/admin/diagnostics/download"]);
    expect(hrefs.some((href) => href?.includes(".log"))).toBe(false);
  });

  it("disables the delete when there is nothing to delete", () => {
    mount([]);
    expect(
      (screen.getByRole("button", { name: "Delete All Logs" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe("LogsCard diagnostics bundle", () => {
  it("offers the database and logs as one download", () => {
    mount();
    const link = screen.getByRole("link", { name: "Download Diagnostics" });
    expect(link.getAttribute("href")).toBe("/api/admin/diagnostics/download");
  });

  it("sits in the wrapping action row, not the scroll area", () => {
    //  .pf-admin-card is overflow-hidden with .pf-admin-scroll as its flex:1
    //  child. Inside that scroll area the button falls below the fold on a
    //  height-constrained card; .pf-admin-actions wraps instead, so the action
    //  cannot be pushed out of reach. This lived in BackupsCard first and hit
    //  both failure modes there.
    const { container } = mount();
    const link = screen.getByRole("link", { name: "Download Diagnostics" });
    expect(link.closest(".pf-admin-actions")).not.toBe(null);
    expect(link.closest(".pf-admin-scroll")).toBe(null);
    expect(container.querySelector(".pf-admin-actions")).toBeTruthy();
  });

  it("is offered even when there are no log files", () => {
    //  The database half of the bundle is the valuable half, and it is there
    //  whether or not any .log survives.
    mount([]);
    expect(screen.getByRole("link", { name: "Download Diagnostics" })).toBeTruthy();
  });
});

describe("LogsCard delete", () => {
  it("deletes nothing until confirmed", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Delete All Logs" }));
    expect(deleteLogsMock).not.toHaveBeenCalled();
    expect(screen.getByText("Delete every log file?")).toBeTruthy();
  });

  it("deletes nothing when cancelled", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Delete All Logs" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteLogsMock).not.toHaveBeenCalled();
  });

  it("reports the names the SERVER said went, not the ones it listed", async () => {
    //  The whole reason the endpoint answers with a list: Flask's `rm` inside a
    //  bare except made a partial failure look identical to success. Showing
    //  the page's own list back would reintroduce exactly that lie.
    deleteLogsMock.mockResolvedValue(ok(["events.log"]));
    mount(["control.log", "events.log"]);
    fireEvent.click(screen.getByRole("button", { name: "Delete All Logs" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect((await screen.findByRole("status")).textContent).toBe("Cleared events.log.");
  });

  it("distinguishes an empty sweep from a successful one", async () => {
    deleteLogsMock.mockResolvedValue(ok([]));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Delete All Logs" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect((await screen.findByRole("status")).textContent).toBe("There was nothing to clear.");
  });

  it("does not promise the log files disappear", async () => {
    //  They deliberately stay: the running processes hold them open, so the
    //  server empties them in place rather than unlinking. Copy that said they
    //  were removed described the bug, not the behaviour.
    mount(["events.log"]);
    fireEvent.click(screen.getByRole("button", { name: "Delete All Logs" }));

    const message = (await screen.findByText(/logs start again from empty/i)).textContent ?? "";
    expect(message).not.toMatch(/removed from the server/i);
    expect(message).toMatch(/emptied/i);
  });

  it("refetches so the list matches the server", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Delete All Logs" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });

  it("reports a failure and leaves the page unrefetched", async () => {
    deleteLogsMock.mockResolvedValue({
      ok: false,
      status: 500,
      message: "boom",
      data: null,
    });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Delete All Logs" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect((await screen.findByRole("alert")).textContent).toBe("boom");
    expect(onChanged).not.toHaveBeenCalled();
  });
});
