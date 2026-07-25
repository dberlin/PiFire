import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";
import { recheckControl, useControlHealth } from "./controlHealth";

describe("recheckControl", () => {
  let fetchMock: ReturnType<typeof rs.fn>;
  const stub = (impl: () => Promise<unknown>) => {
    fetchMock = rs.fn(impl);
    rs.stubGlobal("fetch", fetchMock);
  };
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  it('GETs /api/sys/check_alive and returns true for result "OK"', async () => {
    stub(async () => ({ ok: true, json: async () => ({ result: "OK" }) }));
    await expect(recheckControl("")).resolves.toBe(true);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/sys/check_alive");
  });

  it("prefixes the base URL when there is one", async () => {
    stub(async () => ({ ok: true, json: async () => ({ result: "OK" }) }));
    await recheckControl("http://pi:5000");
    expect(fetchMock.mock.calls[0][0]).toBe("http://pi:5000/api/sys/check_alive");
  });

  it("returns false for any other result", async () => {
    stub(async () => ({ ok: true, json: async () => ({ result: "ERROR" }) }));
    await expect(recheckControl("")).resolves.toBe(false);
  });

  it("returns false on a non-ok HTTP status", async () => {
    stub(async () => ({ ok: false, status: 500, json: async () => ({ result: "OK" }) }));
    await expect(recheckControl("")).resolves.toBe(false);
  });

  it("returns false when fetch throws", async () => {
    stub(async () => {
      throw new Error("network down");
    });
    await expect(recheckControl("")).resolves.toBe(false);
  });
});

describe("useControlHealth", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });
  beforeEach(() => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: true, json: async () => ({ result: "OK" }) })),
    );
  });

  it("is alive and not stale when the payload says the control process is up", () => {
    const { result } = renderHook(() => useControlHealth(true, ""));
    expect(result.current.alive).toBe(true);
    expect(result.current.stale).toBe(false);
    expect(result.current.rechecking).toBe(false);
  });

  it("is not alive and is stale when the payload carries the sticky error", () => {
    const { result } = renderHook(() => useControlHealth(false, ""));
    expect(result.current.alive).toBe(false);
    expect(result.current.stale).toBe(true);
  });

  it("believes a successful recheck over the payload, and keeps believing it", async () => {
    const { result, rerender } = renderHook(({ live }) => useControlHealth(live, ""), {
      initialProps: { live: false },
    });
    await act(async () => {
      await result.current.recheck();
    });
    expect(result.current.alive).toBe(true);
    // The errors blob NEVER clears without a control.py restart, so the next
    // frame still says false. A live probe that just succeeded is better
    // evidence than a blob written up to 30s ago that nothing can clear.
    rerender({ live: false });
    expect(result.current.alive).toBe(true);
    expect(result.current.stale).toBe(true);
  });

  it("stays not-alive when the recheck fails", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: true, json: async () => ({ result: "ERROR" }) })),
    );
    const { result } = renderHook(() => useControlHealth(false, ""));
    await act(async () => {
      await result.current.recheck();
    });
    expect(result.current.alive).toBe(false);
  });
});
