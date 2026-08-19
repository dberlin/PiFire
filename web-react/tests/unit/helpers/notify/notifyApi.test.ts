import type { NotifyUpdate } from "@pifire/core/contracts/control";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { postControl, postNotifyUpdates } from "../../../../src/helpers/notify/notifyApi";

const UPDATES: NotifyUpdate[] = [
  {
    label: "Probe1",
    type: "probe",
    fields: { req: true, target: 165, shutdown: false, keep_warm: false },
  },
];

describe("postNotifyUpdates", () => {
  let fetchMock: ReturnType<typeof rs.fn>;
  const respond = (body: unknown, ok = true, status = 201) => {
    fetchMock = rs.fn(async () => ({ ok, status, json: async () => body }));
    rs.stubGlobal("fetch", fetchMock);
  };
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("POSTs /api/control as JSON", async () => {
    respond({ control: "success", message: "Settings updated successfully.", result: "success" });
    await postNotifyUpdates("", UPDATES);
    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/control");
    expect(opts.method).toBe("POST");
    expect((opts.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(opts.body).toBe(JSON.stringify({ notify_updates: UPDATES }));
  });

  // The single-key body is the request contract: this helper owns only
  // notify_updates and must not send unrelated control members.
  it("sends ONLY the notify_updates key", async () => {
    respond({ result: "success" });
    await postNotifyUpdates("", UPDATES);
    const opts = fetchMock.mock.calls[0][1] as RequestInit;
    expect(Object.keys(JSON.parse(String(opts.body)))).toEqual(["notify_updates"]);
  });

  // The whole point of the key: each update ADDRESSES one entry by (label,
  // type) and names only the fields it changes, so the drain can apply it
  // against live state. Posting the whole notify_data array instead is a
  // replace -- it would revert every entry the poster did not mean to touch,
  // including a timer armed by another writer in the same control cycle.
  it("addresses one entry per update and carries no other entry", async () => {
    respond({ result: "success" });
    await postNotifyUpdates("", UPDATES);
    const opts = fetchMock.mock.calls[0][1] as RequestInit;
    const sent = JSON.parse(String(opts.body)) as { notify_updates: NotifyUpdate[] };
    expect(sent.notify_updates).toHaveLength(1);
    expect(Object.keys(sent.notify_updates[0]).sort()).toEqual(["fields", "label", "type"]);
  });

  // blueprints/api/routes.py:211 answers { "result": "success" } with HTTP 201 --
  // lowercase, NOT the "OK" that common/app.py's api_response envelope uses
  // everywhere else. Verified live: POST /api/control ->
  // {"control":"success","message":"Settings updated successfully.","result":"success"}.
  // command.ts's post() tests result === "OK" and would call every successful
  // save a failure, which is why this write has its own helper.
  it("resolves on the lowercase 'success' result", async () => {
    respond({ result: "success" });
    await expect(postNotifyUpdates("", UPDATES)).resolves.toBeUndefined();
  });

  it("rejects on 'OK' -- this endpoint never says OK, so that is a wrong answer", async () => {
    respond({ result: "OK" });
    await expect(postNotifyUpdates("", UPDATES)).rejects.toThrow(/control write rejected/);
  });

  it("rejects with the server message on an error result", async () => {
    respond({ result: "error", message: "Settings update failed." });
    await expect(postNotifyUpdates("", UPDATES)).rejects.toThrow("Settings update failed.");
  });

  it("rejects on a non-ok response", async () => {
    respond({}, false, 500);
    await expect(postNotifyUpdates("", UPDATES)).rejects.toThrow(/HTTP 500/);
  });
});

describe("postControl", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("prefixes the base URL", async () => {
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({ result: "success" }),
    }));
    rs.stubGlobal("fetch", fetchMock);
    await postControl("http://pi:5000", { notify_updates: UPDATES });
    expect(fetchMock.mock.calls[0][0]).toBe("http://pi:5000/api/control");
  });
});
