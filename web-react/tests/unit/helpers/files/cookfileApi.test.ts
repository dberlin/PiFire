import { afterEach, describe, expect, rs, test } from "@rstest/core";

import { FileRequestError } from "../../../../src/helpers/files/apiEnvelope";
import {
  addCookFileComment,
  assetThumbUrl,
  assetUrl,
  cookFileDownloadUrl,
  cookFileExportUrl,
  deleteCookFile,
  fetchCookFileChart,
  fetchCookFileDetail,
  renameCookFileLabel,
  uploadCookFile,
  uploadCookFileAssets,
} from "../../../../src/helpers/files/cookfileApi";

afterEach(() => {
  rs.resetAllMocks();
});

const DETAIL = {
  filename: "Sunday.pifire",
  metadata: {
    title: "Sunday",
    units: "F",
    thumbnail: "",
    id: "abc",
    version: "1.5.0",
    starttime: "12:00:00",
    endtime: "13:00:00",
    starttime_epoch: 1784942370612,
    endtime_epoch: 1784945970612,
  },
  graph_labels: { probes: { grill1: "Grill" }, targets: {}, primarysp: {} },
  events: [],
  event_totals: {},
  comments: [],
  assets: [],
};

function mockFetch(response: unknown): void {
  globalThis.fetch = rs.fn().mockResolvedValue(response) as never;
}

function calls(): unknown[][] {
  return (globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls;
}

describe("cookfileApi reads", () => {
  test("fetchCookFileDetail returns the parsed body and encodes the name", async () => {
    mockFetch({ ok: true, status: 200, json: async () => DETAIL });
    const detail = await fetchCookFileDetail("Sunday Brisket #2.pifire", "");

    expect(String(calls()[0][0])).toBe(
      "/api/files/cookfiles/detail?file=Sunday%20Brisket%20%232.pifire",
    );
    expect(detail.metadata.title).toBe("Sunday");
  });

  test("fetchCookFileChart hits the chart endpoint", async () => {
    mockFetch({
      ok: true,
      status: 200,
      json: async () => ({
        time_labels: [1, 2],
        chart_data: [],
        probe_mapper: { probes: {}, targets: {}, primarysp: {} },
        annotations: {},
      }),
    });
    const chart = await fetchCookFileChart("X.pifire", "");
    expect(String(calls()[0][0])).toContain("/api/files/cookfiles/chart?file=X.pifire");
    expect(chart.time_labels).toEqual([1, 2]);
  });

  test("a 422 carries the errortype the repair prompt needs", async () => {
    mockFetch({
      ok: false,
      status: 422,
      json: async () => ({
        result: "Error",
        message: "WARNING: Older cookfile version format! ",
        data: { errortype: "version" },
      }),
    });
    await expect(fetchCookFileDetail("X.pifire", "")).rejects.toMatchObject({
      detail: { status: 422, errortype: "version" },
    });
  });

  test("a 404 throws with a null errortype", async () => {
    mockFetch({
      ok: false,
      status: 404,
      json: async () => ({ result: "Error", message: "not_found", data: null }),
    });
    await expect(fetchCookFileDetail("Nope.pifire", "")).rejects.toMatchObject({
      detail: { status: 404, message: "not_found", errortype: null },
    });
  });

  test("a non-JSON error body still yields the status, not a parse throw", async () => {
    mockFetch({
      ok: false,
      status: 500,
      json: async () => {
        throw new SyntaxError("Unexpected token <");
      },
    });
    await expect(fetchCookFileDetail("X.pifire", "")).rejects.toMatchObject({
      detail: { status: 500, message: "HTTP 500", errortype: null },
    });
  });
});

describe("cookfileApi writes", () => {
  test("deleteCookFile posts JSON and unwraps the envelope", async () => {
    mockFetch({ ok: true, status: 200, json: async () => ({ result: "OK", data: null }) });
    await deleteCookFile("Old.pifire", "");

    const [url, init] = calls()[0] as [string, RequestInit];
    expect(url).toBe("/api/files/cookfiles/delete");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ file: "Old.pifire" });
  });

  test("renameCookFileLabel returns the server's safe label", async () => {
    mockFetch({
      ok: true,
      status: 200,
      json: async () => ({ result: "OK", data: { new_label_safe: "MainGrill" } }),
    });
    const data = await renameCookFileLabel("X.pifire", "grill1", "Main Grill", "");
    expect(data.new_label_safe).toBe("MainGrill");
    expect(JSON.parse(String((calls()[0] as [string, RequestInit])[1].body))).toEqual({
      file: "X.pifire",
      old_label: "grill1",
      new_label: "Main Grill",
    });
  });

  test("a write throws when the envelope is not OK even on HTTP 200", async () => {
    mockFetch({
      ok: true,
      status: 200,
      json: async () => ({ result: "Error", message: "rejected by server" }),
    });
    await expect(addCookFileComment("X.pifire", "hi", "")).rejects.toThrow("rejected by server");
  });

  test("a write surfaces a 409 as a FileRequestError with its status", async () => {
    mockFetch({
      ok: false,
      status: 409,
      json: async () => ({ result: "Error", message: "label_exists", data: null }),
    });
    const failure = await renameCookFileLabel("X.pifire", "a", "b", "").catch((err) => err);
    expect(failure).toBeInstanceOf(FileRequestError);
    expect((failure as FileRequestError).detail).toEqual({
      status: 409,
      message: "label_exists",
      errortype: null,
    });
  });
});

describe("cookfileApi uploads", () => {
  test("uploadCookFileAssets sends one file field and one assets part per image", async () => {
    mockFetch({
      ok: true,
      status: 200,
      json: async () => ({
        result: "OK",
        data: { assets: [{ id: "a", filename: "a.png", type: "png" }] },
      }),
    });
    const images = [
      new File([new Uint8Array([1])], "one.png", { type: "image/png" }),
      new File([new Uint8Array([2])], "two.png", { type: "image/png" }),
    ];
    const stored = await uploadCookFileAssets("X.pifire", images, "");

    const [url, init] = calls()[0] as [string, RequestInit];
    expect(url).toBe("/api/files/cookfiles/assets/upload");
    const form = init.body as FormData;
    expect(form.get("file")).toBe("X.pifire");
    expect(form.getAll("assets")).toHaveLength(2);
    //  No JSON Content-Type: the browser must set the multipart boundary.
    expect(init.headers).toBeUndefined();
    expect(stored).toHaveLength(1);
  });

  test("uploadCookFile returns the name the server actually stored", async () => {
    mockFetch({
      ok: true,
      status: 200,
      json: async () => ({ result: "OK", data: { filename: "sanitised.pifire" } }),
    });
    const archive = new File([new Uint8Array([1])], "../../hostile.pifire");
    expect(await uploadCookFile(archive, "")).toBe("sanitised.pifire");
  });

  test("a rejected upload throws rather than reporting a filename", async () => {
    mockFetch({
      ok: false,
      status: 400,
      json: async () => ({ result: "Error", message: "disallowed_file", data: { field: "file" } }),
    });
    const archive = new File([new Uint8Array([1])], "payload.sh");
    await expect(uploadCookFile(archive, "")).rejects.toThrow("disallowed_file");
  });
});

describe("cookfileApi urls", () => {
  test("download and export urls percent-encode names with spaces and hashes", () => {
    expect(cookFileDownloadUrl("Sunday Brisket #2.pifire", "")).toBe(
      "/api/files/cookfiles/download?file=Sunday%20Brisket%20%232.pifire",
    );
    expect(cookFileExportUrl("Sunday Brisket #2.pifire", "events", "")).toBe(
      "/api/files/cookfiles/export?file=Sunday%20Brisket%20%232.pifire&kind=events",
    );
  });

  test("asset urls follow the layout read_json_file_data creates", () => {
    expect(assetUrl("parent-id", "a.png", "")).toBe("/static/img/tmp/parent-id/a.png");
    expect(assetThumbUrl("parent-id", "a.png", "")).toBe("/static/img/tmp/parent-id/thumbs/a.png");
  });
});
