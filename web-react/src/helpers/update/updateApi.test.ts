import { afterEach, describe, expect, it, rs } from "@rstest/core";
import {
  changeBranch,
  fetchUpdateCheck,
  fetchUpdateLog,
  fetchUpdateState,
  fetchUpdateStatus,
  pullUpdate,
  refreshBranches,
  upgradeDeps,
} from "./updateApi";

afterEach(() => {
  rs.unstubAllGlobals();
});

function stub(status: number, body: unknown) {
  const fetchMock = rs.fn(async (_input: string, _init?: RequestInit) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  }));
  rs.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("updateApi reads", () => {
  it("fetchUpdateState GETs /api/update/state and unwraps data", async () => {
    const fetchMock = stub(200, {
      result: "OK",
      data: {
        version: "v1",
        branch: "main",
        branches: ["main"],
        remote_url: "u",
        remote_version: "v2",
      },
    });
    const r = await fetchUpdateState("");
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/update/state");
    expect(r.ok).toBe(true);
    expect(r.data?.branch).toBe("main");
  });

  it("fetchUpdateCheck maps a 502 Error envelope to ok:false", async () => {
    stub(502, { result: "Error", message: "ERROR Getting Remote" });
    const r = await fetchUpdateCheck("");
    expect(r.ok).toBe(false);
    expect(r.status).toBe(502);
    expect(r.message).toContain("ERROR");
  });

  it("fetchUpdateLog passes commits as a query param", async () => {
    const fetchMock = stub(200, { result: "OK", data: { output: "log" } });
    await fetchUpdateLog(25, "");
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/update/log?commits=25");
  });

  it("fetchUpdateStatus returns the triplet", async () => {
    stub(200, { result: "OK", data: { percent: 142, status: "done", output: "x" } });
    const r = await fetchUpdateStatus("");
    expect(r.data?.percent).toBe(142);
  });
});

describe("updateApi mutations", () => {
  it("changeBranch POSTs the target", async () => {
    const fetchMock = stub(200, { result: "OK", data: { started: true } });
    const r = await changeBranch("dev", "");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/update/branch");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ target: "dev" });
    expect(r.data?.started).toBe(true);
  });

  it("pullUpdate surfaces a 409 as ok:false", async () => {
    stub(409, { result: "Error", message: "system_active" });
    const r = await pullUpdate("");
    expect(r.ok).toBe(false);
    expect(r.status).toBe(409);
    expect(r.message).toBe("system_active");
  });

  it("refreshBranches and upgradeDeps POST their paths", async () => {
    const fetchMock = stub(200, { result: "OK", data: { started: true } });
    await refreshBranches("");
    expect((fetchMock.mock.calls[0] as [string])[0]).toBe("/api/update/branches/refresh");
    await upgradeDeps("");
    expect((fetchMock.mock.calls[1] as [string])[0]).toBe("/api/update/upgrade");
  });
});
