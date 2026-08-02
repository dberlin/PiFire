import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PelletDb } from "../../../../src/helpers/pellets/pelletTypes";

const useShellStateMock = rs.fn();
rs.mock("../../../../src/helpers/shellContext", () => ({
  useShellState: () => useShellStateMock(),
}));

// Every helper is stubbed through a lazy wrapper so the mock factory (hoisted
// above these declarations) never captures an uninitialised binding. The page
// is nothing BUT the wiring between a child's callback and one of these eight,
// so each one has to be observable by name and by payload.
const loadProfileMock = rs.fn();
const hopperCheckMock = rs.fn();
const editBrandsMock = rs.fn();
const editWoodsMock = rs.fn();
const addProfileMock = rs.fn();
const editProfileMock = rs.fn();
const deleteProfileMock = rs.fn();
const deleteLogMock = rs.fn();
const allMocks = [
  loadProfileMock,
  hopperCheckMock,
  editBrandsMock,
  editWoodsMock,
  addProfileMock,
  editProfileMock,
  deleteProfileMock,
  deleteLogMock,
];
rs.mock("../../../../src/helpers/pellets/pelletsApi", () => ({
  loadProfile: (...a: unknown[]) => loadProfileMock(...a),
  hopperCheck: (...a: unknown[]) => hopperCheckMock(...a),
  editBrands: (...a: unknown[]) => editBrandsMock(...a),
  editWoods: (...a: unknown[]) => editWoodsMock(...a),
  addProfile: (...a: unknown[]) => addProfileMock(...a),
  editProfile: (...a: unknown[]) => editProfileMock(...a),
  deleteProfile: (...a: unknown[]) => deleteProfileMock(...a),
  deleteLog: (...a: unknown[]) => deleteLogMock(...a),
}));

const { PelletsPage } = await import("../../../../src/components/pellets/PelletsPage");

const LOG_KEY = "1785013200000";
const LOG_LABEL = `Delete log entry ${new Date(Number(LOG_KEY)).toLocaleString()}`;

const DB: PelletDb = {
  schema_version: 2,
  current: {
    pelletid: "p1",
    hopper_level: 62,
    date_loaded: "2026-07-25 09:00:00",
    est_usage: 1000,
  },
  brands: ["Generic"],
  woods: ["Alder"],
  archive: {
    p1: { brand: "Generic", wood: "Alder", rating: 4, comments: "c" },
    p2: { brand: "Custom", wood: "Oak", rating: 5, comments: "second" },
  },
  log: { [LOG_KEY]: { pelletid: "p1", deleted: false } },
  lastupdated: { time: 1785000000 },
};

function mount(pellets: PelletDb | null, live: { hopperLevel: number; tempUnits: "F" | "C" }) {
  useShellStateMock.mockReturnValue({ live, pellets });
  render(<PelletsPage />);
}

const mountDb = (pellets: PelletDb | null = DB) =>
  mount(pellets, { hopperLevel: 62, tempUnits: "F" });

/** Expand a profile's collapse so its Save/Delete buttons exist. */
function expandProfile(label: string) {
  fireEvent.click(screen.getByRole("button", { name: label }));
}

beforeEach(() => {
  for (const m of allMocks) {
    m.mockReset();
    m.mockResolvedValue({ ok: true, message: "" });
  }
});

describe("PelletsPage", () => {
  it("renders a loading state and no cards until the socket delivers a database", () => {
    mountDb(null);
    expect(screen.getByText("Loading pellet database…")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Current Load Out" })).toBeNull();
  });

  it("renders all five regions once the database arrives", () => {
    mountDb();
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
    mountDb();
    fireEvent.click(screen.getByRole("button", { name: "Refresh Status" }));
    await waitFor(() => expect(hopperCheckMock).toHaveBeenCalledWith(""));
  });

  it("surfaces a rejected action's message, then clears it on a later success", async () => {
    editBrandsMock.mockResolvedValue({
      ok: false,
      message: "Error: Cannot delete current profile",
    });
    mountDb();

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
    mountDb();

    // One in-flight flag feeds all five cards. A card that lost its `busy`
    // prop would still accept clicks mid-write, and the pellet blob is a
    // whole-blob overwrite, so a concurrent second write loses the first.
    const refresh = screen.getByRole("button", { name: "Refresh Status" });
    fireEvent.click(refresh);

    const gated = [
      "Refresh Status",
      "Load New Pellets",
      "Add brand",
      "Add wood type",
      "Delete Generic",
      "Delete Alder",
      LOG_LABEL,
    ];
    for (const name of gated) {
      await waitFor(() =>
        expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(true),
      );
    }

    release({ ok: true, message: "" });
    for (const name of gated) {
      await waitFor(() =>
        expect(screen.getByRole("button", { name }).hasAttribute("disabled")).toBe(false),
      );
    }
  });

  it("reads hopper level and temp units from the live socket state, not the stale database", () => {
    // db.current.hopper_level is 62 and is only rewritten when the pellet blob
    // is flushed; live.hopperLevel arrives on every socket tick. Wiring the
    // card to the database would show a level minutes out of date.
    mount(DB, { hopperLevel: 12, tempUnits: "C" });

    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("12");
    // tempUnits C puts metric in the primary slot (helpers/pellets/usage.ts).
    expect(screen.getByLabelText("Estimated usage since reload").textContent).toBe("1 kg");
  });
});

// One test per action arrow. The page has no logic beyond these eight bindings,
// so the only regressions available here are calling the wrong helper, dropping
// the base url, or posting the wrong payload key -- and the server reads those
// keys by name (common/pellets_actions.py), silently no-opping on a miss.
describe("PelletsPage action wiring", () => {
  it("loads the profile chosen in the picker", async () => {
    mountDb();
    fireEvent.click(screen.getByRole("button", { name: "Load New Pellets" }));
    fireEvent.change(screen.getByLabelText("Profile to load"), { target: { value: "p2" } });
    fireEvent.click(screen.getByRole("button", { name: "Load Profile" }));

    await waitFor(() => expect(loadProfileMock).toHaveBeenCalledWith("", "p2"));
  });

  it("distinguishes adding a brand from deleting one by payload key", async () => {
    mountDb();

    fireEvent.change(screen.getByLabelText("New brand"), { target: { value: "Acme" } });
    fireEvent.click(screen.getByRole("button", { name: "Add brand" }));
    await waitFor(() => expect(editBrandsMock).toHaveBeenCalledWith("", { new_brand: "Acme" }));

    fireEvent.click(screen.getByRole("button", { name: "Delete Generic" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    // Swapping these keys makes a delete silently re-add the brand:
    // pellets_edit_brands checks for "delete_brand" first, then "new_brand".
    await waitFor(() =>
      expect(editBrandsMock).toHaveBeenLastCalledWith("", { delete_brand: "Generic" }),
    );
  });

  it("routes the Wood Types card to editWoods, never to editBrands", async () => {
    // The two vocabulary cards are the same component with different handlers,
    // so a copy-paste in either direction typechecks and edits the wrong list.
    mountDb();

    fireEvent.change(screen.getByLabelText("New wood type"), { target: { value: "Hickory" } });
    fireEvent.click(screen.getByRole("button", { name: "Add wood type" }));
    await waitFor(() => expect(editWoodsMock).toHaveBeenCalledWith("", { new_wood: "Hickory" }));

    fireEvent.click(screen.getByRole("button", { name: "Delete Alder" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() =>
      expect(editWoodsMock).toHaveBeenLastCalledWith("", { delete_wood: "Alder" }),
    );

    expect(editBrandsMock).not.toHaveBeenCalled();
  });

  it("sends add_and_load false for Add", async () => {
    mountDb();
    fireEvent.click(screen.getByRole("button", { name: "Add Profile" }));
    fireEvent.click(screen.getByRole("button", { name: "Add" }));

    await waitFor(() =>
      expect(addProfileMock).toHaveBeenCalledWith("", {
        brand_name: "Generic",
        wood_type: "Alder",
        rating: 5,
        comments: "Enter comments here.",
        add_and_load: false,
      }),
    );
  });

  it("sends add_and_load true for Add & Load", async () => {
    // Losing the flag is invisible in the UI -- the profile is still created,
    // it just never becomes the loaded one (pellets_add_profile:115).
    mountDb();
    fireEvent.click(screen.getByRole("button", { name: "Add Profile" }));
    fireEvent.click(screen.getByRole("button", { name: "Add & Load" }));

    await waitFor(() =>
      expect(addProfileMock).toHaveBeenCalledWith(
        "",
        expect.objectContaining({ add_and_load: true }),
      ),
    );
  });

  it("edits a profile with its id alongside the drafted fields", async () => {
    mountDb();
    expandProfile("Custom Oak");
    fireEvent.change(screen.getByLabelText("Comments for Custom Oak"), {
      target: { value: "edited" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save Custom Oak" }));

    // Without `profile`, pellets_edit_profile answers "Profile not included in
    // request" rather than saving.
    await waitFor(() =>
      expect(editProfileMock).toHaveBeenCalledWith("", {
        profile: "p2",
        brand_name: "Custom",
        wood_type: "Oak",
        rating: 5,
        comments: "edited",
      }),
    );
  });

  it("deletes a profile by id, and only after the cascade is confirmed", async () => {
    mountDb();
    expandProfile("Custom Oak");
    fireEvent.click(screen.getByRole("button", { name: "Delete Custom Oak" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(deleteProfileMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Delete Custom Oak" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(deleteProfileMock).toHaveBeenCalledWith("", "p2"));
  });

  it("refuses to delete the profile the shell reports as loaded", () => {
    // currentId comes from pellets.current.pelletid; wiring it anywhere else
    // (or dropping it) re-enables a delete the server answers with
    // "Error: Cannot delete current profile" (pellets_delete_profile:147-148).
    mountDb();
    expandProfile("Generic Alder");
    expandProfile("Custom Oak");

    expect(
      screen.getByRole("button", { name: "Delete Generic Alder" }).hasAttribute("disabled"),
    ).toBe(true);
    expect(screen.getByRole("button", { name: "Delete Custom Oak" }).hasAttribute("disabled")).toBe(
      false,
    );
  });

  it("deletes a log entry by its timestamp key", async () => {
    mountDb();
    fireEvent.click(screen.getByRole("button", { name: LOG_LABEL }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    // The log is keyed by load time, and the archive by profile id; passing the
    // row's profile id instead would match nothing and no-op.
    await waitFor(() => expect(deleteLogMock).toHaveBeenCalledWith("", LOG_KEY));
  });
});
