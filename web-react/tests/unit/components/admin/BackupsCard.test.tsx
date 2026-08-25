import type { BackupListing } from "@pifire/core/contracts/operations";
import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as actualAdminApi from "../../../../src/helpers/admin/adminApi" with {
  rstest: "importActual",
};

const createBackupMock = rs.fn();
const restoreBackupMock = rs.fn();
const uploadBackupMock = rs.fn();
//  backupDownloadUrl stays REAL: the href is the one place this component
//  builds a request itself, and its encoding is the thing worth pinning.
rs.mock("../../../../src/helpers/admin/adminApi", () => ({
  ...actualAdminApi,
  createBackup: (...a: unknown[]) => createBackupMock(...a),
  restoreBackup: (...a: unknown[]) => restoreBackupMock(...a),
  uploadBackup: (...a: unknown[]) => uploadBackupMock(...a),
}));

const { BackupsCard } = await import("../../../../src/components/admin/BackupsCard");

const ok = (data: unknown = null) => ({ ok: true, status: 200, message: "", data });

const BACKUPS: BackupListing = {
  settings: ["PiFire_01-01-26_120000.json"],
  pelletdb: ["PelletDB_01-01-26_120000.json"],
};

let onChanged: ReturnType<typeof rs.fn>;

beforeEach(() => {
  createBackupMock.mockReset();
  restoreBackupMock.mockReset();
  uploadBackupMock.mockReset();
  createBackupMock.mockResolvedValue(ok({ filename: "PiFire_new.json" }));
  restoreBackupMock.mockResolvedValue(ok());
  uploadBackupMock.mockResolvedValue(ok({ filename: "PiFire_up.json" }));
  onChanged = rs.fn();
});

function mount(mode = "Stop", backups: BackupListing = BACKUPS) {
  render(<BackupsCard backups={backups} mode={mode} onChanged={onChanged} />);
}

/** The row for one backup file, so Download/Restore resolve unambiguously. */
function row(file: string) {
  const name = screen.getByText(file);
  const li = name.closest("li");
  if (!li) throw new Error(`no row for ${file}`);
  return within(li);
}

describe("BackupsCard listing", () => {
  it("lists both kinds under their own headings", () => {
    //  By role, not by text: the upload picker's <option>s carry the same two
    //  words, and a bare getByText matches both.
    mount();
    expect(screen.getByRole("heading", { name: "Settings" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Pellet Database" })).toBeTruthy();
    expect(screen.getByText("PiFire_01-01-26_120000.json")).toBeTruthy();
    expect(screen.getByText("PelletDB_01-01-26_120000.json")).toBeTruthy();
  });

  it("says a kind is empty rather than rendering a bare heading", () => {
    mount("Stop", { settings: [], pelletdb: [] });
    expect(screen.getByText("No settings backups yet.")).toBeTruthy();
    expect(screen.getByText("No pellet database backups yet.")).toBeTruthy();
  });

  it("creates a backup of the kind whose button was pressed", async () => {
    mount();
    const settingsHead = screen.getByRole("heading", { name: "Settings" }).closest("div");
    if (!settingsHead) throw new Error("no settings group");
    fireEvent.click(within(settingsHead).getByRole("button", { name: "Back Up Now" }));

    await waitFor(() => {
      expect(createBackupMock).toHaveBeenCalledTimes(1);
    });
    expect(createBackupMock.mock.calls[0][0]).toBe("settings");
    expect(onChanged).toHaveBeenCalledTimes(1);
  });
});

describe("BackupsCard download", () => {
  it("links by bare filename, percent-encoded", () => {
    mount();
    const link = row("PiFire_01-01-26_120000.json").getByRole("link", { name: "Download" });
    expect(link.getAttribute("href")).toBe(
      "/api/admin/backups/download?kind=settings&file=PiFire_01-01-26_120000.json",
    );
  });

  it("never puts a path in the href, whatever the server listed", () => {
    //  The server answers with basenames, but the href is built here, so this
    //  pins that no separator can appear even if a name ever carried one.
    mount("Stop", { settings: ["../escape.json"], pelletdb: [] });
    const href =
      row("../escape.json").getByRole("link", { name: "Download" }).getAttribute("href") ?? "";
    expect(href).toContain("file=..%2Fescape.json");
    expect(href).not.toContain("../");
  });
});

describe("BackupsCard diagnostics bundle", () => {
  it("offers the database and logs as one download", () => {
    mount();
    const link = screen.getByRole("link", { name: "Download Diagnostics" });
    expect(link.getAttribute("href")).toBe("/api/admin/diagnostics/download");
  });

  it("is offered while the grill is running", () => {
    //  The whole point is capturing a cook that is misbehaving right now, so
    //  unlike a settings restore this must not be mode-gated.
    mount("Hold");
    expect(screen.getByRole("link", { name: "Download Diagnostics" })).toBeTruthy();
  });

  it("is not listed among the restorable backups", () => {
    //  It is a snapshot to send someone, not a file this card can restore from,
    //  and a Restore button beside it would be a lie.
    mount();
    const link = screen.getByRole("link", { name: "Download Diagnostics" });
    expect(link.closest("li")).toBe(null);
  });
});

describe("BackupsCard restore", () => {
  it("restores nothing until confirmed", () => {
    mount();
    fireEvent.click(row("PiFire_01-01-26_120000.json").getByRole("button", { name: "Restore" }));
    expect(restoreBackupMock).not.toHaveBeenCalled();
    expect(screen.getByText("Restore from PiFire_01-01-26_120000.json?")).toBeTruthy();
  });

  it("warns that a settings restore restarts the server", () => {
    mount();
    fireEvent.click(row("PiFire_01-01-26_120000.json").getByRole("button", { name: "Restore" }));
    expect(screen.getByText(/RESTARTS/).textContent).toMatch(/grill must be stopped first/i);
  });

  it("does not claim a pellet restore restarts anything", () => {
    //  It genuinely does not -- the pellet database is re-read on demand -- and
    //  a warning that overstates the cost teaches users to ignore warnings.
    mount();
    fireEvent.click(row("PelletDB_01-01-26_120000.json").getByRole("button", { name: "Restore" }));
    expect(screen.getByText(/Nothing restarts/)).toBeTruthy();
  });

  it("sends the kind and the bare filename", async () => {
    mount();
    fireEvent.click(row("PelletDB_01-01-26_120000.json").getByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await waitFor(() => {
      expect(restoreBackupMock).toHaveBeenCalledTimes(1);
    });
    expect(restoreBackupMock.mock.calls[0].slice(0, 2)).toEqual([
      "pelletdb",
      "PelletDB_01-01-26_120000.json",
    ]);
  });

  it("disables a SETTINGS restore unless the grill is stopped", () => {
    mount("Hold");
    const settingsRestore = row("PiFire_01-01-26_120000.json").getByRole("button", {
      name: "Restore",
    }) as HTMLButtonElement;
    expect(settingsRestore.disabled).toBe(true);
  });

  it("leaves the PELLET restore available in any mode, matching the server", () => {
    mount("Hold");
    const pelletRestore = row("PelletDB_01-01-26_120000.json").getByRole("button", {
      name: "Restore",
    }) as HTMLButtonElement;
    expect(pelletRestore.disabled).toBe(false);
  });

  it("surfaces a 409 if the mode changed under the page", async () => {
    restoreBackupMock.mockResolvedValue({
      ok: false,
      status: 409,
      message: "not_stopped",
      data: null,
      mode: "Smoke",
    });
    mount();
    fireEvent.click(row("PiFire_01-01-26_120000.json").getByRole("button", { name: "Restore" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Smoke");
    expect(onChanged).not.toHaveBeenCalled();
  });
});

describe("BackupsCard upload", () => {
  const file = () => new File(["{}"], "PiFire_up.json", { type: "application/json" });

  it("uploads into the kind the picker names", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("Upload into"), { target: { value: "pelletdb" } });
    fireEvent.change(screen.getByLabelText("Backup file to upload"), {
      target: { files: [file()] },
    });

    await waitFor(() => {
      expect(uploadBackupMock).toHaveBeenCalledTimes(1);
    });
    expect(uploadBackupMock.mock.calls[0][0]).toBe("pelletdb");
    expect((uploadBackupMock.mock.calls[0][1] as File).name).toBe("PiFire_up.json");
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("clears the picker after a FAILED upload too", async () => {
    //  Otherwise the filename sits in the input looking like a queued upload
    //  that will never happen.
    uploadBackupMock.mockResolvedValue({
      ok: false,
      status: 400,
      message: "bad_request",
      data: null,
      field: "backup",
    });
    mount();
    const input = screen.getByLabelText("Backup file to upload") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file()] } });

    await screen.findByRole("alert");
    expect(input.value).toBe("");
    expect(onChanged).not.toHaveBeenCalled();
  });
});
