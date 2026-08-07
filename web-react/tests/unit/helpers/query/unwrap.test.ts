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

  // adminApi.ts's unpack() lifts these off `data` for every call, GETs
  // included (adminApi.ts:35-49), and adminErrorText() branches on both.
  // Dropping them here is invisible from the useQuery side: `message` and
  // `status` still arrive, so the caller renders the DEGRADED copy without
  // anything reporting a loss.
  it("carries field and mode, the rest of what a refusal envelope says", async () => {
    const err = await unwrap(
      Promise.resolve({
        ok: false,
        status: 409,
        message: "not_stopped",
        data: null,
        field: "history",
        mode: "Smoke",
      }),
    ).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).field).toBe("history");
    expect((err as ApiError).mode).toBe("Smoke");
  });

  // An envelope that never had them must not turn them into the string
  // "undefined" somewhere downstream -- adminErrorText's `result.mode ||
  // "another"` fallback depends on absent staying falsy.
  it("leaves field and mode undefined when the envelope carried neither", async () => {
    const err = await unwrap(
      Promise.resolve({ ok: false, status: 500, message: "boom", data: null }),
    ).catch((e: unknown) => e);
    expect((err as ApiError).field).toBeUndefined();
    expect((err as ApiError).mode).toBeUndefined();
  });

  it("rejects on ok-with-null-data, which is a broken read contract", async () => {
    await expect(
      unwrap(Promise.resolve({ ok: true, status: 200, message: "", data: null })),
    ).rejects.toBeInstanceOf(ApiError);
  });
});
