import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { dismissWarnings } from "./warningsApi";

afterEach(() => {
  rs.restoreAllMocks();
});

describe("dismissWarnings", () => {
  it("posts the high-water mark and resolves true on success", async () => {
    const fetchMock = rs
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ result: "OK" }), { status: 200 }));
    await expect(dismissWarnings(7)).resolves.toBe(true);
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(String(init?.body))).toEqual({ through_id: 7 });
  });

  it("resolves false on a refusal rather than throwing", async () => {
    rs.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ result: "ERROR" }), { status: 400 }),
    );
    await expect(dismissWarnings(7)).resolves.toBe(false);
  });

  it("resolves false when the request fails outright", async () => {
    rs.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    await expect(dismissWarnings(7)).resolves.toBe(false);
  });
});
