import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as actualAdminApi from "../../../../src/helpers/admin/adminApi" with {
  rstest: "importActual",
};
import type { AdminSettings } from "../../../../src/helpers/contracts/operations.gen";
import { queryKeys } from "../../../../src/helpers/query/keys";
import { testQueryClient } from "../../test-utils";

const maintenanceActionMock = rs.fn();
const saveAdminSettingsMock = rs.fn();
rs.mock("../../../../src/helpers/admin/adminApi", () => ({
  ...actualAdminApi,
  maintenanceAction: (...a: unknown[]) => maintenanceActionMock(...a),
  saveAdminSettings: (...a: unknown[]) => saveAdminSettingsMock(...a),
}));

const { MaintenanceCard } = await import("../../../../src/components/admin/MaintenanceCard");

const ok = () => ({ ok: true, status: 200, message: "", data: null });
const SETTINGS: AdminSettings = { debug_mode: false, boot_to_monitor: false };

const CLEARS = [
  ["Clear History", "clear_history"],
  ["Clear Event Log", "clear_events"],
  ["Clear Pellet Database", "clear_pelletdb"],
  ["Clear Pellet Log", "clear_pelletdb_log"],
] as const;

let onChanged: ReturnType<typeof rs.fn>;

beforeEach(() => {
  maintenanceActionMock.mockReset();
  saveAdminSettingsMock.mockReset();
  maintenanceActionMock.mockResolvedValue(ok());
  saveAdminSettingsMock.mockResolvedValue(ok());
  onChanged = rs.fn();
});

function mount(settings: AdminSettings = SETTINGS, queryClient: QueryClient = testQueryClient()) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MaintenanceCard settings={settings} onChanged={onChanged} />
    </QueryClientProvider>,
  );
}

describe("MaintenanceCard clears", () => {
  it("is available in every mode, matching the server", () => {
    //  The card takes no mode at all: routes.py deliberately does not gate
    //  these, because clearing a pellet log mid-cook is recoverable. A
    //  disabled state here would diverge from what the server accepts.
    mount();
    for (const [label] of CLEARS) {
      expect((screen.getByRole("button", { name: label }) as HTMLButtonElement).disabled).toBe(
        false,
      );
    }
  });

  it.each([
    ["Clear History", "clear_history"],
    ["Clear Event Log", "clear_events"],
    ["Clear Pellet Database", "clear_pelletdb"],
    ["Clear Pellet Log", "clear_pelletdb_log"],
  ])("sends %s as %s, but only after confirmation", (label, action) => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(maintenanceActionMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(maintenanceActionMock).toHaveBeenCalledTimes(1);
    expect(maintenanceActionMock.mock.calls[0][0]).toBe(action);
  });

  it("sends nothing when a clear is cancelled", () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Clear Pellet Database" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(maintenanceActionMock).not.toHaveBeenCalled();
  });

  it("distinguishes the two pellet clears in its copy", () => {
    //  "Clear Pellet Database" and "Clear Pellet Log" differ by everything a
    //  user cares about, and the labels alone do not say which keeps profiles.
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Clear Pellet Database" }));
    expect(screen.getByText(/Every brand, wood, profile and log entry/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByRole("button", { name: "Clear Pellet Log" }));
    expect(screen.getByText(/profiles, brands and woods are kept/)).toBeTruthy();
  });

  it("refetches the page once a clear lands", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Clear Event Log" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });

  it("invalidates the metrics and history caches on a successful Clear History", async () => {
    // clear_history wipes the temperature history, the current readings AND
    // the cook metrics server-side (blueprints/api_admin/routes.py), but
    // `onChanged` only refetches this page's own adminState entry. Seed both
    // caches on a real QueryClient and assert against its cache state
    // directly -- onChanged is a mock and would stay green even if the
    // invalidation this test targets were deleted.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: Number.POSITIVE_INFINITY } },
    });
    queryClient.setQueryData(queryKeys.metrics, { seeded: true });
    queryClient.setQueryData(queryKeys.historyChart(60), { seeded: true });

    mount(SETTINGS, queryClient);
    fireEvent.click(screen.getByRole("button", { name: "Clear History" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(queryClient.getQueryState(queryKeys.metrics)?.isInvalidated).toBe(true);
      expect(queryClient.getQueryState(queryKeys.historyChart(60))?.isInvalidated).toBe(true);
    });
  });

  it("does not invalidate metrics or history for a clear that is not Clear History", async () => {
    // Scoped to clear_history alone: a pellet-log clear has no bearing on
    // either cache, and invalidating them anyway would trigger refetches the
    // action gives no reason for.
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: Number.POSITIVE_INFINITY } },
    });
    queryClient.setQueryData(queryKeys.metrics, { seeded: true });
    queryClient.setQueryData(queryKeys.historyChart(60), { seeded: true });

    mount(SETTINGS, queryClient);
    fireEvent.click(screen.getByRole("button", { name: "Clear Event Log" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(queryClient.getQueryState(queryKeys.metrics)?.isInvalidated).toBe(false);
    expect(queryClient.getQueryState(queryKeys.historyChart(60))?.isInvalidated).toBe(false);
  });

  it("does not refetch when the server refuses", async () => {
    maintenanceActionMock.mockResolvedValue({
      ok: false,
      status: 400,
      message: "bad_request",
      data: null,
      field: "action",
    });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Clear History" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect((await screen.findByRole("alert")).textContent).toContain("action");
    expect(onChanged).not.toHaveBeenCalled();
  });
});

describe("MaintenanceCard toggles", () => {
  it("reads its switches from the server's state", () => {
    mount({ debug_mode: true, boot_to_monitor: false });
    expect(screen.getByRole("button", { name: "Debug Mode" }).getAttribute("aria-pressed")).toBe(
      "true",
    );
    expect(
      screen.getByRole("button", { name: "Boot to Monitor Mode" }).getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it.each([
    ["Debug Mode", "debug_mode"],
    ["Boot to Monitor Mode", "boot_to_monitor"],
  ])("saves %s alone, as a partial patch", (label, key) => {
    //  The server refuses any key outside the two, and sending both would
    //  overwrite the other switch with whatever this page last read.
    mount();
    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(saveAdminSettingsMock).toHaveBeenCalledTimes(1);
    expect(saveAdminSettingsMock.mock.calls[0][0]).toEqual({ [key]: true });
  });

  it("sends the value it is turning ON or OFF, not the one it read", () => {
    mount({ debug_mode: true, boot_to_monitor: false });
    fireEvent.click(screen.getByRole("button", { name: "Debug Mode" }));
    expect(saveAdminSettingsMock.mock.calls[0][0]).toEqual({ debug_mode: false });
  });

  it("refetches so the switch reflects what the server stored", async () => {
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Debug Mode" }));
    await waitFor(() => {
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });

  it("reports a rejected save and leaves the page unrefetched", async () => {
    saveAdminSettingsMock.mockResolvedValue({
      ok: false,
      status: 400,
      message: "bad_request",
      data: null,
      field: "debug_mode",
    });
    mount();
    fireEvent.click(screen.getByRole("button", { name: "Debug Mode" }));
    expect((await screen.findByRole("alert")).textContent).toContain("debug_mode");
    expect(onChanged).not.toHaveBeenCalled();
  });
});
