import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as actualAdminApi from "../../helpers/admin/adminApi" with { rstest: "importActual" };
import type { AdminState } from "../../helpers/admin/adminTypes";

// Stubbed through a lazy wrapper so the hoisted mock factory never captures an
// uninitialised binding. adminErrorText stays REAL: the page's whole job on a
// failure is to render its output, and a stub would let a wrong token pass
// unnoticed.
const fetchAdminStateMock = rs.fn();
rs.mock("../../helpers/admin/adminApi", () => ({
  ...actualAdminApi,
  fetchAdminState: (...a: unknown[]) => fetchAdminStateMock(...a),
}));

const { AdminPage } = await import("./AdminPage");

const STATE: AdminState = {
  system: {
    uptime: " 14:02:11 up 3 days,  1:14,  1 user\n",
    os_info: "Debian GNU/Linux 12 (bookworm)",
    network_info: { eth0: { ip_address: "10.0.0.9", mac_address: "de:ad:be:ef:00:01" } },
    hardware_info: {
      total_ram: "8GB",
      available_ram: "6GB",
      cpu_info: {
        hardware: "BCM2712",
        model: "Raspberry Pi 5",
        model_name: "Cortex-A76",
        cores: "4",
        frequency: "2400MHz",
      },
    },
  },
  settings: { debug_mode: false, boot_to_monitor: false },
  backups: { settings: ["PiFire_a.json"], pelletdb: [] },
  logs: ["events.log"],
  mode: "Stop",
};

const ok = (data: AdminState = STATE) => ({ ok: true, status: 200, message: "", data });

beforeEach(() => {
  fetchAdminStateMock.mockReset();
  fetchAdminStateMock.mockResolvedValue(ok());
});

describe("AdminPage", () => {
  it("reads the whole page from one request", async () => {
    render(<AdminPage />);
    await screen.findByText("Admin");
    expect(fetchAdminStateMock).toHaveBeenCalledTimes(1);
  });

  it("shows a loading state before the read lands", () => {
    let resolve: (v: unknown) => void = () => {};
    fetchAdminStateMock.mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    render(<AdminPage />);
    expect(screen.getByText("Loading system information…")).toBeTruthy();
    resolve(ok());
  });

  it("renders the system readings", async () => {
    render(<AdminPage />);
    //  The trailing newline uptime(1) emits must not reach the DOM. (Testing
    //  Library collapses the interior runs of spaces before matching, so this
    //  is the single-spaced form of the fixture's raw line.)
    expect(await screen.findByText("14:02:11 up 3 days, 1:14, 1 user")).toBeTruthy();
    expect(screen.getByText("Debian GNU/Linux 12 (bookworm)")).toBeTruthy();
    expect(screen.getByText("Cortex-A76")).toBeTruthy();
    expect(screen.getByText("8GB")).toBeTruthy();
  });

  it("names each network interface with its address pair", async () => {
    render(<AdminPage />);
    expect(await screen.findByText("eth0")).toBeTruthy();
    expect(screen.getByText("10.0.0.9 · de:ad:be:ef:00:01")).toBeTruthy();
  });

  it("shows the grill mode the server judged the request against", async () => {
    fetchAdminStateMock.mockResolvedValue(ok({ ...STATE, mode: "Hold" }));
    render(<AdminPage />);
    expect(await screen.findByText("Grill mode: Hold")).toBeTruthy();
  });

  it("renders an unprobed reading as the server's Unknown, not as a blank", async () => {
    //  gather_system_info() falls back to the literal string rather than null,
    //  and a stale-looking blank would be worse than an honest Unknown.
    const unknown: AdminState = {
      ...STATE,
      system: {
        ...STATE.system,
        hardware_info: {
          total_ram: "Unknown",
          available_ram: "Unknown",
          cpu_info: {
            hardware: "Unknown",
            model: "Unknown",
            model_name: "Unknown",
            cores: "Unknown",
            frequency: "Unknown",
          },
        },
      },
    };
    fetchAdminStateMock.mockResolvedValue(ok(unknown));
    render(<AdminPage />);
    await screen.findByText("Admin");
    expect(screen.getAllByText("Unknown").length).toBe(7);
  });

  it("refetches on demand, since nothing pushes a new reading", async () => {
    fetchAdminStateMock.mockResolvedValueOnce(ok());
    render(<AdminPage />);
    await screen.findByText("Admin");

    fetchAdminStateMock.mockResolvedValue(ok({ ...STATE, mode: "Hold" }));
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Grill mode: Hold")).toBeTruthy();
    expect(fetchAdminStateMock).toHaveBeenCalledTimes(2);
  });

  it("clears a stale error once a later read succeeds", async () => {
    //  Otherwise a transient network blip leaves a permanent alert over a page
    //  that is now showing good data.
    fetchAdminStateMock.mockResolvedValueOnce(ok());
    render(<AdminPage />);
    await screen.findByText("Admin");

    fetchAdminStateMock.mockResolvedValueOnce({
      ok: false,
      status: 0,
      message: "Failed to fetch",
      data: null,
    });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByRole("alert");

    fetchAdminStateMock.mockResolvedValue(ok());
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => {
      expect(screen.queryByRole("alert")).toBeNull();
    });
  });

  it("reports a failed read instead of rendering an empty page", async () => {
    fetchAdminStateMock.mockResolvedValue({
      ok: false,
      status: 0,
      message: "Failed to fetch",
      data: null,
    });
    render(<AdminPage />);
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toBe("Failed to fetch");
    });
    expect(screen.queryByText("Admin")).toBeNull();
  });

  it("translates a refusal through adminErrorText rather than showing the token", async () => {
    fetchAdminStateMock.mockResolvedValue({
      ok: false,
      status: 409,
      message: "not_stopped",
      data: null,
      mode: "Hold",
    });
    render(<AdminPage />);
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("must be stopped first");
    });
  });
});
