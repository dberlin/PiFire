import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { onlineManager, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import * as actualAdminApi from "../../../../src/helpers/admin/adminApi" with {
  rstest: "importActual",
};
import type { AdminState } from "../../../../src/helpers/admin/adminTypes";
import { testQueryClient } from "../../test-utils";

// Stubbed through a lazy wrapper so the hoisted mock factory never captures an
// uninitialised binding. adminErrorText stays REAL: the page's whole job on a
// failure is to render its output, and a stub would let a wrong token pass
// unnoticed.
const fetchAdminStateMock = rs.fn();
//  The cards render inside the page, so every write door they own is stubbed
//  here too even though no test below clicks one. Leaving them live would put
//  this file one future assertion away from a real power-off or a real clear
//  against whatever backend the runner can see.
const refuseCall = (name: string) => () => {
  throw new Error(`${name} must never be reached from AdminPage.test`);
};
rs.mock("../../../../src/helpers/admin/adminApi", () => ({
  ...actualAdminApi,
  fetchAdminState: (...a: unknown[]) => fetchAdminStateMock(...a),
  systemAction: refuseCall("systemAction"),
  factoryReset: refuseCall("factoryReset"),
  maintenanceAction: refuseCall("maintenanceAction"),
  saveAdminSettings: refuseCall("saveAdminSettings"),
  createBackup: refuseCall("createBackup"),
  restoreBackup: refuseCall("restoreBackup"),
  uploadBackup: refuseCall("uploadBackup"),
  deleteLogs: refuseCall("deleteLogs"),
}));

const { AdminPage } = await import("../../../../src/components/admin/AdminPage");

// SystemUpdateCard renders a react-router <Link>, which throws outside a
// Router context -- every render below needs one even though none of these
// tests navigate. Each render gets its own client (gcTime: 0, staleTime: 0)
// so no test's resolved admin state leaks into the next one's assertions.
const renderPage = () =>
  render(
    <QueryClientProvider client={testQueryClient()}>
      <AdminPage />
    </QueryClientProvider>,
    { wrapper: MemoryRouter },
  );

//  Copied from a REAL /api/admin/state response, not composed from the type.
//  The first version of this fixture had os_info as a display string and the
//  RAM readings as strings, matching a type that was also wrong; every test
//  passed and the page crashed on a live machine. A fixture written from the
//  same assumption as the code under test proves only that they agree.
const STATE: AdminState = {
  system: {
    uptime: " 14:02:11 up 3 days,  1:14,  1 user\n",
    os_info: {
      PRETTY_NAME: "Debian GNU/Linux 12 (bookworm)",
      NAME: "Debian GNU/Linux",
      VERSION: "12 (bookworm)",
      VERSION_ID: "12",
      VERSION_CODENAME: "bookworm",
      ARCHITECTURE: "aarch64",
      BITS: "64-Bit",
    },
    network_info: { eth0: { ip_address: "10.0.0.9", mac_address: "de:ad:be:ef:00:01" } },
    hardware_info: {
      total_ram: 8232370176,
      available_ram: 6442450944,
      cpu_info: {
        hardware: "BCM2712",
        model: "Raspberry Pi 5 Model B Rev 1.0",
        model_name: "Cortex-A76",
        cores: 4,
        frequency: 2400.0,
      },
    },
  },
  settings: { debug_mode: false, boot_to_monitor: false },
  backups: { settings: ["PiFire_a.json"], pelletdb: [] },
  logs: ["events.log"],
  mode: "Stop",
};

const ok = (data: AdminState = STATE) => ({ ok: true, status: 200, message: "", data });

const refuse = (status: number, message: string) => ({ ok: false, status, message, data: null });

beforeEach(() => {
  fetchAdminStateMock.mockReset();
  fetchAdminStateMock.mockResolvedValue(ok());
});

describe("AdminPage", () => {
  it("reads the whole page from one request", async () => {
    renderPage();
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
    renderPage();
    expect(screen.getByText("Loading system information…")).toBeTruthy();
    resolve(ok());
  });

  it("renders the system readings", async () => {
    renderPage();
    //  The trailing newline uptime(1) emits must not reach the DOM. (Testing
    //  Library collapses the interior runs of spaces before matching, so this
    //  is the single-spaced form of the fixture's raw line.)
    expect(await screen.findByText("14:02:11 up 3 days, 1:14, 1 user")).toBeTruthy();
    //  PRETTY_NAME, not the os_info map -- rendering the map itself throws.
    expect(screen.getByText("Debian GNU/Linux 12 (bookworm)")).toBeTruthy();
    expect(screen.getByText("aarch64 (64-Bit)")).toBeTruthy();
    expect(screen.getByText("Cortex-A76")).toBeTruthy();
  });

  it("renders the numeric readings as units, not as raw bytes and floats", async () => {
    //  The live platform module answers with byte counts and an unrounded
    //  megahertz float; only gather_system_info()'s FALLBACKS are strings.
    renderPage();
    expect(await screen.findByText("7.7 GiB")).toBeTruthy();
    expect(screen.getByText("6.0 GiB")).toBeTruthy();
    expect(screen.getByText("2400 MHz")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
  });

  it("names each network interface with its address pair", async () => {
    renderPage();
    expect(await screen.findByText("eth0")).toBeTruthy();
    expect(screen.getByText("10.0.0.9 · de:ad:be:ef:00:01")).toBeTruthy();
  });

  it("shows the grill mode the server judged the request against", async () => {
    fetchAdminStateMock.mockResolvedValue(ok({ ...STATE, mode: "Hold" }));
    renderPage();
    expect(await screen.findByText("Grill mode: Hold")).toBeTruthy();
  });

  it("passes the server's Unknown through every formatter untouched", async () => {
    //  gather_system_info() falls back to the literal string rather than null,
    //  and a stale-looking blank would be worse than an honest Unknown. The
    //  byte and megahertz formatters have to survive it, not produce "NaN GiB".
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
    renderPage();
    await screen.findByText("Admin");
    expect(screen.getAllByText("Unknown").length).toBe(7);
    expect(screen.queryByText(/NaN/)).toBeNull();
  });

  it("refetches on demand, since nothing pushes a new reading", async () => {
    fetchAdminStateMock.mockResolvedValueOnce(ok());
    renderPage();
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
    renderPage();
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
    renderPage();
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
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain("must be stopped first");
    });
  });

  //  The envelope's `mode` has to survive TWO conversions to get here:
  //  unwrap() turning it into an ApiError, and queryErrorText() turning that
  //  back into an AdminResult. Asserting the full sentence rather than a
  //  substring is what makes the loss visible -- dropping `mode` at either
  //  step still produces a plausible alert ("...in another mode."), which is
  //  how it went unnoticed.
  it("keeps the refused mode in the copy across the query boundary", async () => {
    fetchAdminStateMock.mockResolvedValue({
      ok: false,
      status: 409,
      message: "not_stopped",
      data: null,
      mode: "Smoke",
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toBe(
        "The grill must be stopped first — it is currently in Smoke mode.",
      );
    });
  });

  it("renders the server's refusal message when the envelope reports failure", async () => {
    fetchAdminStateMock.mockResolvedValue(refuse(409, "not_stopped"));
    renderPage();
    await waitFor(() => expect(screen.getByRole("alert")).toBeVisible());
  });

  it("never re-reads on a timer: the read probes hardware and writes to control", async () => {
    rs.useFakeTimers();
    fetchAdminStateMock.mockResolvedValue(ok(STATE));
    renderPage();
    //  Not waitFor(): under fake timers it never settles here (confirmed by
    //  running it -- the test times out even though the assertion inside it
    //  is already true). Same constraint HistoryPage.test.tsx's `settle()`
    //  documents -- waitFor polls on a timer fake timers freeze. The mount
    //  read fires synchronously off render() (React flushes the query's
    //  effect inside render()'s own act()), so no wait is needed at all.
    expect(fetchAdminStateMock).toHaveBeenCalledTimes(1);

    //  advanceTimersByTimeAsync, not the sync advanceTimersByTime the brief's
    //  snippet showed: react-query reschedules refetchInterval's next timer
    //  only after the in-flight fetch's promise settles, and a synchronous
    //  advance never yields for that microtask, so it fires at most the
    //  first tick and this assertion would pass even with polling turned ON.
    //  Confirmed by deliberately setting refetchInterval: 5_000 -- the sync
    //  form left this test green; only the async form caught it (61 calls).
    await act(async () => {
      await rs.advanceTimersByTimeAsync(300_000);
    });
    expect(fetchAdminStateMock).toHaveBeenCalledTimes(1);
    rs.useRealTimers();
  });

  it("does not re-probe on a network reconnect: refetchOnReconnect must be false, not merely unset", async () => {
    //  testQueryClient() sets staleTime: 0, so the read is stale the instant
    //  it lands -- the one condition under which react-query's default
    //  refetchOnReconnect (refetch-if-stale) would fire. No fake timers
    //  needed: onlineManager's offline->online edge is what triggers the
    //  refetch check, not the clock.
    fetchAdminStateMock.mockResolvedValue(ok());
    renderPage();
    await screen.findByText("Admin");
    expect(fetchAdminStateMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    });

    expect(fetchAdminStateMock).toHaveBeenCalledTimes(1);
  });
});
