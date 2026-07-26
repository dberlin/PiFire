import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PelletDb } from "../../helpers/pellets/pelletTypes";

const useShellStateMock = rs.fn();
rs.mock("../../helpers/shellContext", () => ({
  useShellState: () => useShellStateMock(),
}));

const hopperCheckMock = rs.fn();
const editBrandsMock = rs.fn();
rs.mock("../../helpers/pellets/pelletsApi", () => ({
  loadProfile: rs.fn(),
  hopperCheck: (...a: unknown[]) => hopperCheckMock(...a),
  editBrands: (...a: unknown[]) => editBrandsMock(...a),
  editWoods: rs.fn(),
  addProfile: rs.fn(),
  editProfile: rs.fn(),
  deleteProfile: rs.fn(),
  deleteLog: rs.fn(),
}));

const { PelletsPage } = await import("./PelletsPage");

const DB: PelletDb = {
  current: {
    pelletid: "p1",
    hopper_level: 62,
    date_loaded: "2026-07-25 09:00:00",
    est_usage: 1000,
  },
  brands: ["Generic"],
  woods: ["Alder"],
  archive: {
    p1: { id: "p1", brand: "Generic", wood: "Alder", rating: 4, comments: "c" },
  },
  log: { "2026-07-25 09:00:00": "p1" },
  lastupdated: { time: 1785000000 },
};

function mount(pellets: PelletDb | null) {
  useShellStateMock.mockReturnValue({
    live: { hopperLevel: 62, tempUnits: "F" },
    pellets,
  });
  render(<PelletsPage />);
}

beforeEach(() => {
  hopperCheckMock.mockReset();
  editBrandsMock.mockReset();
  hopperCheckMock.mockResolvedValue({ ok: true, message: "" });
  editBrandsMock.mockResolvedValue({ ok: true, message: "" });
});

describe("PelletsPage", () => {
  it("renders a loading state and no cards until the socket delivers a database", () => {
    mount(null);
    expect(screen.getByText("Loading pellet database…")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Current Load Out" })).toBeNull();
  });

  it("renders all five regions once the database arrives", () => {
    mount(DB);
    for (const name of [
      "Current Load Out",
      "Brands",
      "Wood Types",
      "Pellet Profiles",
      "Pellet Log",
    ]) {
      expect(screen.getByRole("region", { name })).toBeTruthy();
    }
  });

  it("calls hopperCheck with the same-origin base url", async () => {
    mount(DB);
    fireEvent.click(screen.getByRole("button", { name: "Refresh Status" }));
    await waitFor(() => expect(hopperCheckMock).toHaveBeenCalledWith(""));
  });

  it("surfaces a rejected action's message, then clears it on a later success", async () => {
    editBrandsMock.mockResolvedValue({
      ok: false,
      message: "Error: Cannot delete current profile",
    });
    mount(DB);

    fireEvent.change(screen.getByLabelText("New brand"), { target: { value: "Acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Add brand" }));

    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe("Error: Cannot delete current profile"),
    );

    fireEvent.click(screen.getByRole("button", { name: "Refresh Status" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("marks every card busy while an action is in flight", async () => {
    let release: (v: { ok: boolean; message: string }) => void = () => {};
    hopperCheckMock.mockReturnValue(
      new Promise<{ ok: boolean; message: string }>((r) => {
        release = r;
      }),
    );
    mount(DB);

    const refresh = screen.getByRole("button", { name: "Refresh Status" });
    fireEvent.click(refresh);

    await waitFor(() => expect(refresh.hasAttribute("disabled")).toBe(true));

    release({ ok: true, message: "" });
    await waitFor(() => expect(refresh.hasAttribute("disabled")).toBe(false));
  });
});
