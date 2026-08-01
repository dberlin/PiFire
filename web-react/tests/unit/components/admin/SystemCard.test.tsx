import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import * as actualAdminApi from "../../../../src/helpers/admin/adminApi" with {
  rstest: "importActual",
};

// EVERY door out of this component is stubbed. These four actions power the
// machine off, and the assertions below are only worth anything because no
// request can leave the test -- adminErrorText stays real so a wrong refusal
// token would show up as wrong copy.
const systemActionMock = rs.fn();
const factoryResetMock = rs.fn();
rs.mock("../../../../src/helpers/admin/adminApi", () => ({
  ...actualAdminApi,
  systemAction: (...a: unknown[]) => systemActionMock(...a),
  factoryReset: (...a: unknown[]) => factoryResetMock(...a),
}));

const { SystemCard } = await import("../../../../src/components/admin/SystemCard");

const ok = () => ({ ok: true, status: 200, message: "", data: null });

const LABELS = ["Reboot", "Shut Down", "Restart PiFire", "Restore Factory Defaults"];

beforeEach(() => {
  systemActionMock.mockReset();
  factoryResetMock.mockReset();
  systemActionMock.mockResolvedValue(ok());
  factoryResetMock.mockResolvedValue(ok());
});

/** Open a button's confirm dialog and press Confirm. */
function confirmAction(label: string) {
  fireEvent.click(screen.getByRole("button", { name: label }));
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
}

describe("SystemCard availability", () => {
  it("disables all four unless the grill is stopped", () => {
    render(<SystemCard mode="Hold" />);
    for (const label of LABELS) {
      expect((screen.getByRole("button", { name: label }) as HTMLButtonElement).disabled).toBe(
        true,
      );
    }
  });

  it("says why they are unavailable, naming the mode", () => {
    render(<SystemCard mode="Smoke" />);
    expect(screen.getByText(/only while the grill is stopped/i).textContent).toContain("Smoke");
  });

  it("enables them at Stop", () => {
    render(<SystemCard mode="Stop" />);
    for (const label of LABELS) {
      expect((screen.getByRole("button", { name: label }) as HTMLButtonElement).disabled).toBe(
        false,
      );
    }
    expect(screen.queryByText(/only while the grill is stopped/i)).toBeNull();
  });
});

describe("SystemCard confirmation", () => {
  it("fires nothing on the first click — the button only opens the dialog", () => {
    render(<SystemCard mode="Stop" />);
    fireEvent.click(screen.getByRole("button", { name: "Reboot" }));
    expect(systemActionMock).not.toHaveBeenCalled();
    expect(factoryResetMock).not.toHaveBeenCalled();
    expect(screen.getByText("Reboot the system?")).toBeTruthy();
  });

  it("fires nothing when the dialog is cancelled", () => {
    render(<SystemCard mode="Stop" />);
    fireEvent.click(screen.getByRole("button", { name: "Shut Down" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(systemActionMock).not.toHaveBeenCalled();
    expect(screen.queryByText("Shut the system down?")).toBeNull();
  });

  it("names the consequence rather than asking 'are you sure'", () => {
    render(<SystemCard mode="Stop" />);

    fireEvent.click(screen.getByRole("button", { name: "Shut Down" }));
    //  The one thing a user must know before shutting a headless machine down.
    expect(screen.getByText(/STAY off/).textContent).toMatch(/power cycle it by hand/);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByRole("button", { name: "Restore Factory Defaults" }));
    //  The pellet database goes too, which is the least recoverable part and
    //  the part a user is least likely to expect from the words "factory
    //  defaults". It is named explicitly.
    const copy = screen.getByText(/pellet database/i).textContent ?? "";
    expect(copy).toMatch(/every profile and every log entry/i);
    expect(copy).toMatch(/cannot be undone|Nothing here can be undone/i);
  });
});

describe("SystemCard dispatch", () => {
  it.each([
    ["Reboot", "reboot"],
    ["Shut Down", "shutdown"],
    ["Restart PiFire", "restart"],
  ])("sends %s exactly once, as %s", (label, action) => {
    render(<SystemCard mode="Stop" />);
    confirmAction(label);
    expect(systemActionMock).toHaveBeenCalledTimes(1);
    expect(systemActionMock.mock.calls[0][0]).toBe(action);
    expect(factoryResetMock).not.toHaveBeenCalled();
  });

  it("routes factory reset to its own endpoint, not through systemAction", () => {
    render(<SystemCard mode="Stop" />);
    confirmAction("Restore Factory Defaults");
    expect(factoryResetMock).toHaveBeenCalledTimes(1);
    expect(systemActionMock).not.toHaveBeenCalled();
  });

  it("closes the dialog on confirm so a second click cannot re-fire it", () => {
    render(<SystemCard mode="Stop" />);
    confirmAction("Reboot");
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
    expect(systemActionMock).toHaveBeenCalledTimes(1);
  });
});

describe("SystemCard outcomes", () => {
  it("reports an accepted request rather than pretending to refetch", async () => {
    render(<SystemCard mode="Stop" />);
    confirmAction("Reboot");
    expect((await screen.findByRole("status")).textContent).toBe(
      "Reboot requested. The machine is going down now.",
    );
  });

  it("surfaces the server's 409 if the mode changed under the page", async () => {
    //  The disabled state above is a courtesy; the server re-reads control at
    //  request time and is the thing that actually refuses. A lit grill that
    //  the page has not heard about yet must not read as a successful reboot.
    systemActionMock.mockResolvedValue({
      ok: false,
      status: 409,
      message: "not_stopped",
      data: null,
      mode: "Hold",
    });
    render(<SystemCard mode="Stop" />);
    confirmAction("Reboot");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("must be stopped first");
    expect(alert.textContent).toContain("Hold");
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("clears a previous failure when a later request is accepted", async () => {
    systemActionMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      message: "not_stopped",
      data: null,
      mode: "Hold",
    });
    render(<SystemCard mode="Stop" />);
    confirmAction("Reboot");
    await screen.findByRole("alert");

    confirmAction("Reboot");
    await screen.findByRole("status");
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
