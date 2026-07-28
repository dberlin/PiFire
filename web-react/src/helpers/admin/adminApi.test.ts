import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  adminErrorText,
  backupDownloadUrl,
  createBackup,
  deleteLogs,
  factoryReset,
  fetchAdminState,
  fetchBackups,
  logsDownloadUrl,
  maintenanceAction,
  restoreBackup,
  saveAdminSettings,
  systemAction,
  uploadBackup,
} from "./adminApi";

const fetchMock = rs.fn();
rs.stubGlobal("fetch", fetchMock);

function envelope(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body };
}

const OK = (data: unknown = null) => envelope(200, { result: "OK", message: null, data });

beforeEach(() => {
  fetchMock.mockReset();
});

describe("adminApi transport", () => {
  it("reads state with a GET and unwraps the envelope", async () => {
    fetchMock.mockResolvedValue(OK({ mode: "Stop", logs: ["events.log"] }));
    const result = await fetchAdminState("");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/state");
    expect(fetchMock.mock.calls[0][1]).toBeUndefined();
    expect(result.ok).toBe(true);
    expect(result.data).toEqual({ mode: "Stop", logs: ["events.log"] });
  });

  it("posts JSON for every write", async () => {
    fetchMock.mockResolvedValue(OK({ action: "reboot" }));
    await systemAction("reboot", "");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/system");
    expect(init.method).toBe("POST");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body)).toEqual({ action: "reboot" });
  });

  it("treats an HTTP 200 with result Error as a failure", async () => {
    //  The envelope, not the status, is the contract -- a 200 body that says
    //  Error must never read as success.
    fetchMock.mockResolvedValue(envelope(200, { result: "Error", message: "nope", data: null }));
    expect((await maintenanceAction("clear_history", "")).ok).toBe(false);
  });

  it("reports a body that is not JSON as the status, not a crash", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error("not json");
      },
    });
    expect(await deleteLogs("")).toEqual({
      ok: false,
      status: 502,
      message: "HTTP 502",
      data: null,
      field: undefined,
      mode: undefined,
    });
  });

  it("reports an unreachable server as status 0 rather than throwing", async () => {
    fetchMock.mockRejectedValue(new Error("Failed to fetch"));
    const result = await factoryReset("");
    expect(result).toEqual({ ok: false, status: 0, message: "Failed to fetch", data: null });
  });
});

describe("refusals", () => {
  it("surfaces the 409 not_stopped mode", async () => {
    fetchMock.mockResolvedValue(
      envelope(409, { result: "Error", message: "not_stopped", data: { mode: "Hold" } }),
    );
    const result = await systemAction("shutdown", "");
    expect(result.ok).toBe(false);
    expect(result.status).toBe(409);
    expect(result.mode).toBe("Hold");
    expect(adminErrorText(result)).toBe(
      "The grill must be stopped first — it is currently in Hold mode.",
    );
  });

  it("surfaces the 400 field", async () => {
    fetchMock.mockResolvedValue(
      envelope(400, { result: "Error", message: "bad_request", data: { field: "kind" } }),
    );
    const result = await createBackup("settings", "");
    expect(result.field).toBe("kind");
    expect(adminErrorText(result)).toBe("The server refused that request: kind.");
  });

  it("names a missing backup without echoing a path", async () => {
    fetchMock.mockResolvedValue(
      envelope(404, { result: "Error", message: "not_found", data: null }),
    );
    const result = await restoreBackup("pelletdb", "PelletDB_gone.json", "");
    expect(adminErrorText(result)).toBe("That file is no longer on the server.");
  });

  it("passes an unrecognised message through untranslated", async () => {
    fetchMock.mockResolvedValue(envelope(500, { result: "Error", message: "boom", data: null }));
    expect(adminErrorText(await deleteLogs(""))).toBe("boom");
  });
});

describe("adminApi calls", () => {
  it("factory reset posts an empty body to its own endpoint", async () => {
    fetchMock.mockResolvedValue(OK({ action: "factory_reset" }));
    await factoryReset("");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/factory-reset");
    expect(JSON.parse(init.body)).toEqual({});
  });

  it("saves only the toggles it was given", async () => {
    //  The server refuses any key outside {debug_mode, boot_to_monitor}, so a
    //  partial patch must stay partial rather than being padded out.
    fetchMock.mockResolvedValue(OK({ debug_mode: true }));
    await saveAdminSettings({ debug_mode: true }, "");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ debug_mode: true });
  });

  it("lists both backup kinds", async () => {
    fetchMock.mockResolvedValue(OK({ settings: ["PiFire_a.json"], pelletdb: [] }));
    const result = await fetchBackups("");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/backups");
    expect(result.data).toEqual({ settings: ["PiFire_a.json"], pelletdb: [] });
  });

  it("restores by bare filename", async () => {
    fetchMock.mockResolvedValue(OK({ kind: "settings", file: "PiFire_a.json" }));
    await restoreBackup("settings", "PiFire_a.json", "");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      kind: "settings",
      file: "PiFire_a.json",
    });
  });

  it("reports the log names that actually went", async () => {
    fetchMock.mockResolvedValue(OK({ removed: ["events.log"] }));
    const result = await deleteLogs("");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/admin/logs/delete");
    expect(result.data).toEqual({ removed: ["events.log"] });
  });
});

describe("uploadBackup", () => {
  it("sends multipart without a JSON Content-Type", async () => {
    //  Setting one would strip the multipart boundary and the server would see
    //  no file at all.
    fetchMock.mockResolvedValue(OK({ filename: "PiFire_up.json" }));
    const file = new File(["{}"], "PiFire_up.json", { type: "application/json" });
    await uploadBackup("settings", file, "");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/admin/backups/upload");
    expect(init.headers).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
    expect(init.body.get("kind")).toBe("settings");
    expect((init.body.get("backup") as File).name).toBe("PiFire_up.json");
  });

  it("reports an unreachable server as status 0", async () => {
    fetchMock.mockRejectedValue(new Error("Failed to fetch"));
    const file = new File(["{}"], "PiFire_up.json");
    expect(await uploadBackup("settings", file, "")).toEqual({
      ok: false,
      status: 0,
      message: "Failed to fetch",
      data: null,
    });
  });
});

describe("download hrefs", () => {
  it("percent-encodes the backup name", () => {
    //  The server contains the name anyway, but an unencoded one would let a
    //  `&` or a `#` in a filename truncate the query.
    expect(backupDownloadUrl("settings", "PiFire_a&b.json", "")).toBe(
      "/api/admin/backups/download?kind=settings&file=PiFire_a%26b.json",
    );
  });

  it("builds the log archive href", () => {
    expect(logsDownloadUrl("")).toBe("/api/admin/logs/download");
  });
});
