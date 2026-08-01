import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import {
  closeSession,
  computeCoefficients,
  fetchAutoStatus,
  fetchTr,
  openSession,
  saveProfile,
  tunerErrorText,
} from "../../../../src/helpers/tuner/tunerApi";

const fetchMock = rs.fn();
rs.stubGlobal("fetch", fetchMock);

const envelope = (status: number, body: unknown) => ({
  ok: status < 400,
  status,
  json: async () => body,
});

const OK = (data: unknown = null) => envelope(200, { result: "OK", message: null, data });

beforeEach(() => {
  fetchMock.mockReset();
});

describe("session", () => {
  it("opens with an explicit boolean", async () => {
    fetchMock.mockResolvedValue(OK({ open: true, mode: "Monitor", restored: true }));
    const result = await openSession("");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/tuner/session");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ open: true });
    expect(result.data?.mode).toBe("Monitor");
  });

  it("closes with the same endpoint and the opposite flag", async () => {
    fetchMock.mockResolvedValue(OK({ open: false, mode: "Stop", restored: true }));
    await closeSession("");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ open: false });
  });

  it("surfaces a 409 refusal rather than throwing", async () => {
    fetchMock.mockResolvedValue(
      envelope(409, { result: "Error", message: "not_tunable", data: { mode: "Hold" } }),
    );
    const result = await openSession("");
    expect(result.ok).toBe(false);
    expect(result.message).toBe("not_tunable");
    expect(result.mode).toBe("Hold");
  });
});

describe("fetchTr", () => {
  it("encodes the probe label", async () => {
    fetchMock.mockResolvedValue(OK({ probe: "Probe 1", trohms: 51234, tuning: true }));
    await fetchTr("Probe 1", "");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/tuner/tr?probe=Probe%201");
  });

  it("keeps a null reading null", async () => {
    //  null means "not reporting". Coercing it to 0 would render as a real
    //  zero-ohm reading, which is what a shorted probe looks like.
    fetchMock.mockResolvedValue(OK({ probe: "Grill", trohms: null, tuning: true }));
    expect((await fetchTr("Grill", "")).data?.trohms).toBeNull();
  });
});

describe("computeCoefficients", () => {
  it("posts the three points", async () => {
    fetchMock.mockResolvedValue(OK({ a: 1, b: 2, c: 3, chart: [{ x: 0, y: 9 }], chart_ok: true }));
    const points = [
      { segment: "High" as const, temp: 400, trohms: 1200 },
      { segment: "Medium" as const, temp: 250, trohms: 6000 },
      { segment: "Low" as const, temp: 100, trohms: 40000 },
    ];
    const result = await computeCoefficients(points, "");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ points });
    expect(result.data?.chart_ok).toBe(true);
  });

  it("surfaces a 422 as an uncomputable result", async () => {
    fetchMock.mockResolvedValue(
      envelope(422, { result: "Error", message: "uncomputable", data: null }),
    );
    const result = await computeCoefficients([], "");
    expect(result.ok).toBe(false);
    expect(result.message).toBe("uncomputable");
  });
});

describe("saveProfile", () => {
  it("sends null apply_to when saving only", async () => {
    fetchMock.mockResolvedValue(OK({ id: "abc", applied: null }));
    await saveProfile({ name: "P", a: 1, b: 2, c: 3, apply_to: null }, "");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).apply_to).toBeNull();
  });
});

describe("tunerErrorText", () => {
  it("explains a refusal in the operator's terms", () => {
    expect(
      tunerErrorText({ ok: false, status: 409, message: "not_tunable", data: null, mode: "Hold" }),
    ).toContain("Hold");
    expect(tunerErrorText({ ok: false, status: 422, message: "uncomputable", data: null })).toMatch(
      /could not be calculated/i,
    );
  });

  it("passes an unrecognised message through unchanged", () => {
    expect(tunerErrorText({ ok: false, status: 500, message: "kaboom", data: null })).toBe(
      "kaboom",
    );
  });
});

describe("fetchAutoStatus", () => {
  const status = (over = {}) => ({
    current_tr: 41000,
    current_temp: 225,
    high_tr: 0,
    high_temp: 0,
    medium_tr: 0,
    medium_temp: 0,
    low_tr: 0,
    low_temp: 0,
    samples: 1,
    ready: false,
    ...over,
  });

  it("posts both probe labels", async () => {
    fetchMock.mockResolvedValue(OK(status()));
    await fetchAutoStatus("Grill", "Probe1", "");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/tuner/auto-status");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      probe: "Grill",
      reference: "Probe1",
    });
  });

  it("keeps a null reading null", async () => {
    fetchMock.mockResolvedValue(OK(status({ current_temp: null, samples: 0 })));
    const result = await fetchAutoStatus("Grill", "Missing", "");
    expect(result.data?.current_temp).toBeNull();
  });

  it("surfaces the ready selection", async () => {
    fetchMock.mockResolvedValue(
      OK(
        status({
          high_tr: 30000,
          high_temp: 240,
          low_tr: 50000,
          low_temp: 100,
          samples: 12,
          ready: true,
        }),
      ),
    );
    const result = await fetchAutoStatus("Grill", "Probe1", "");
    expect(result.data?.ready).toBe(true);
    expect(result.data?.high_temp).toBe(240);
  });
});
