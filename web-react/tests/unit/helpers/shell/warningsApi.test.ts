import type { DismissWarningsRequest, DismissWarningsResponse } from "@pifire/core/contracts/core";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { dismissWarnings } from "../../../../src/helpers/shell/warningsApi";

afterEach(() => {
  rs.restoreAllMocks();
});

describe("dismissWarnings", () => {
  it("posts the high-water mark and resolves true on success", async () => {
    const fetchMock = rs.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          result: "OK",
          message: "Warnings dismissed.",
          data: null,
        } satisfies DismissWarningsResponse),
        { status: 200 },
      ),
    );
    await expect(dismissWarnings(7)).resolves.toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    // Pin the seam: a wrong path or verb answers 404/405, which this client maps
    // to a plain false, so the banner would just never dismiss with no error.
    expect(url).toBe("/api/dismiss_warnings");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      through_id: 7,
    } satisfies DismissWarningsRequest);
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
