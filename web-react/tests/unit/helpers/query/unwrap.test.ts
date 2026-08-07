import { describe, expect, it } from "@rstest/core";
import { ApiError, unwrap } from "../../../../src/helpers/query/unwrap";

describe("unwrap", () => {
  it("returns the payload when the envelope reports success", async () => {
    await expect(
      unwrap(Promise.resolve({ ok: true, status: 200, message: "", data: { a: 1 } })),
    ).resolves.toEqual({ a: 1 });
  });

  it("rejects with the server's message when the envelope reports failure", async () => {
    const failing = unwrap(
      Promise.resolve({ ok: false, status: 503, message: "not_stopped", data: null }),
    );
    await expect(failing).rejects.toThrow("not_stopped");
  });

  it("carries the status so a caller can branch on it", async () => {
    const err = await unwrap(
      Promise.resolve({ ok: false, status: 404, message: "not_found", data: null }),
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
  });

  it("rejects on ok-with-null-data, which is a broken read contract", async () => {
    await expect(
      unwrap(Promise.resolve({ ok: true, status: 200, message: "", data: null })),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
