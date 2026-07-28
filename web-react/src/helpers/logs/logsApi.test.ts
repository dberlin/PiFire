import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { byteLength, fetchLogDelta, fetchLogFamilies, logDownloadUrl, logViewUrl } from "./logsApi";

const fetchMock = rs.fn();
beforeEach(() => {
  fetchMock.mockReset();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

const res = (status: number, body: string, headers: Record<string, string> = {}) =>
  new Response(status === 416 ? null : body, { status, headers });

describe("logsApi", () => {
  it("encodes the stem into the view url", () => {
    expect(logViewUrl("events", "")).toBe("/api/admin/logs/view?log=events");
    expect(logViewUrl("a b", "")).toBe("/api/admin/logs/view?log=a%20b");
  });

  it("adds the download flag without a second question mark", () => {
    expect(logDownloadUrl("events", "")).toBe("/api/admin/logs/view?log=events&download=1");
  });

  it("counts BYTES, not characters", () => {
    //  Range offsets are byte offsets. A multi-byte line would desync the
    //  cursor forever if this used String.length.
    expect(byteLength("é")).toBe(2);
    expect(byteLength("abc")).toBe(3);
  });

  it("advances the offset by the delta's byte length", async () => {
    fetchMock.mockResolvedValue(res(206, "new\n", { "Content-Range": "bytes 4-7/8" }));
    const d = await fetchLogDelta("events", 4, 8, "");
    expect(d).toEqual({ kind: "appended", text: "new\n", nextOffset: 8, total: 8 });
  });

  it("reports nothing new on a 416 whose total still covers the cursor", async () => {
    fetchMock.mockResolvedValue(res(416, "", { "Content-Range": "bytes */8" }));
    const d = await fetchLogDelta("events", 8, 8, "");
    expect(d.kind).toBe("unchanged");
  });

  it("refetches from zero when a 416 shows the family shrank", async () => {
    //  Rotation drops the oldest member, so the stitched total falls BELOW the
    //  cursor. Without this the tail silently stops and reads as a dead grill.
    fetchMock
      .mockResolvedValueOnce(res(416, "", { "Content-Range": "bytes */3" }))
      .mockResolvedValueOnce(res(200, "abc"));
    const d = await fetchLogDelta("events", 99, 200, "");
    expect(d).toEqual({ kind: "rotated", text: "abc", nextOffset: 3, total: 3 });
  });

  it("refetches from zero when a 206 total drops below the last known total", async () => {
    //  Rotation can leave the new total still ABOVE the cursor, so the 416
    //  branch never fires and the 206 body would be misaligned content.
    fetchMock
      .mockResolvedValueOnce(res(206, "xx", { "Content-Range": "bytes 10-11/40" }))
      .mockResolvedValueOnce(res(200, "whole"));
    const d = await fetchLogDelta("events", 10, 900, "");
    expect(d).toEqual({ kind: "rotated", text: "whole", nextOffset: 5, total: 5 });
  });

  it("unpacks the families member", async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          result: "OK",
          message: "",
          data: { logs: [], families: [{ stem: "events", members: ["events.log"], bytes: 4 }] },
        }),
        { status: 200 },
      ),
    );
    expect(await fetchLogFamilies("")).toEqual([
      { stem: "events", members: ["events.log"], bytes: 4 },
    ]);
  });

  it("reports no families rather than throwing when the listing fails", async () => {
    //  The page renders the picker from this list. A throw here would take out
    //  the events tab too, which does not depend on the listing at all.
    fetchMock.mockResolvedValue(new Response("nope", { status: 500 }));
    expect(await fetchLogFamilies("")).toEqual([]);
  });
});
