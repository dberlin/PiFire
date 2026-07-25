import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { getNotifyData, type NotifyEntry, postControl, postNotifyData } from "./notifyApi";

const ENTRIES: NotifyEntry[] = [
  {
    label: "Probe1",
    type: "probe",
    req: false,
    shutdown: false,
    keep_warm: false,
    target: 0,
    eta: null,
    condition: "equal_above",
    name: "Probe-1",
  },
];

describe("getNotifyData", () => {
  let fetchMock: ReturnType<typeof rs.fn>;
  const respond = (body: unknown, ok = true, status = 200) => {
    fetchMock = rs.fn(async () => ({ ok, status, json: async () => body }));
    rs.stubGlobal("fetch", fetchMock);
  };
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("GETs /api/get/notify and returns body.data", async () => {
    respond({ result: "OK", message: "", data: ENTRIES });
    await expect(getNotifyData("")).resolves.toEqual(ENTRIES);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/get/notify");
  });

  it("prefixes the base URL", async () => {
    respond({ result: "OK", data: ENTRIES });
    await getNotifyData("http://pi:5000");
    expect(fetchMock.mock.calls[0][0]).toBe("http://pi:5000/api/get/notify");
  });

  // Every one of these MUST throw rather than fall back to []: the caller posts
  // the returned array straight back with POST /api/control, and an empty array
  // would replace notify_data wholesale -- wiping every notification on the
  // grill, including any high/low limit alerts the user is relying on.
  it("throws on an ERROR envelope", async () => {
    respond({ result: "ERROR", message: "nope" });
    await expect(getNotifyData("")).rejects.toThrow(/no notify_data/);
  });

  it("throws when data is not an array", async () => {
    respond({ result: "OK", data: { nope: true } });
    await expect(getNotifyData("")).rejects.toThrow(/no notify_data/);
  });

  it("throws on a non-ok response", async () => {
    respond({}, false, 500);
    await expect(getNotifyData("")).rejects.toThrow(/HTTP 500/);
  });
});

describe("postNotifyData", () => {
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
    await postNotifyData("", ENTRIES);
    const [url, opts] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/control");
    expect(opts.method).toBe("POST");
    expect((opts.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(opts.body).toBe(JSON.stringify({ notify_data: ENTRIES }));
  });

  // The single-key body is what keeps this write from reverting the controller:
  // a MERGE queues the WHOLE posted patch, so any extra key (mode,
  // primary_setpoint, ...) would be patched back over whatever the control loop
  // set in the meantime.
  it("sends ONLY the notify_data key", async () => {
    respond({ result: "success" });
    await postNotifyData("", ENTRIES);
    const opts = fetchMock.mock.calls[0][1] as RequestInit;
    expect(Object.keys(JSON.parse(String(opts.body)))).toEqual(["notify_data"]);
  });

  // blueprints/api/routes.py:211 answers { "result": "success" } with HTTP 201 --
  // lowercase, NOT the "OK" that common/app.py's api_response envelope uses
  // everywhere else. Verified live: POST /api/control ->
  // {"control":"success","message":"Settings updated successfully.","result":"success"}.
  // command.ts's post() tests result === "OK" and would call every successful
  // save a failure, which is why this write has its own helper.
  it("resolves on the lowercase 'success' result", async () => {
    respond({ result: "success" });
    await expect(postNotifyData("", ENTRIES)).resolves.toBeUndefined();
  });

  it("rejects on 'OK' -- this endpoint never says OK, so that is a wrong answer", async () => {
    respond({ result: "OK" });
    await expect(postNotifyData("", ENTRIES)).rejects.toThrow(/control write rejected/);
  });

  it("rejects with the server message on an error result", async () => {
    respond({ result: "error", message: "Settings update failed." });
    await expect(postNotifyData("", ENTRIES)).rejects.toThrow("Settings update failed.");
  });

  it("rejects on a non-ok response", async () => {
    respond({}, false, 500);
    await expect(postNotifyData("", ENTRIES)).rejects.toThrow(/HTTP 500/);
  });
});

describe("postControl", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it("posts an arbitrary single-key control patch", async () => {
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({ result: "success" }),
    }));
    rs.stubGlobal("fetch", fetchMock);
    await postControl("http://pi:5000", { notify_data: ENTRIES });
    expect(fetchMock.mock.calls[0][0]).toBe("http://pi:5000/api/control");
  });
});
