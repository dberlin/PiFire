import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, renderHook } from "@testing-library/react";

import { recheckControl, useControlHealth } from "../../../../src/helpers/dashboard/controlHealth";

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
  const downErrors = ["The control process did not respond to a request and may be stopped."];
  const healthyErrors: string[] = [];
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
    const { result } = renderHook(() => useControlHealth(true, "", healthyErrors));
    expect(result.current.alive).toBe(true);
    expect(result.current.stale).toBe(false);
    expect(result.current.rechecking).toBe(false);
  });

  it("is not alive and is stale when the current payload reports control down", () => {
    const { result } = renderHook(() => useControlHealth(false, "", downErrors));
    expect(result.current.alive).toBe(false);
    expect(result.current.stale).toBe(true);
  });

  it("keeps a successful recheck through local renders but expires it on the next authoritative snapshot", async () => {
    const { result, rerender } = renderHook(({ errors }) => useControlHealth(false, "", errors), {
      initialProps: { errors: downErrors },
    });
    await act(async () => {
      await result.current.recheck();
    });
    expect(result.current.alive).toBe(true);
    expect(result.current.stale).toBe(true);
    rerender({ errors: downErrors });
    expect(result.current.alive).toBe(true);
    rerender({ errors: [...downErrors] });
    expect(result.current.alive).toBe(false);
    expect(result.current.stale).toBe(true);
  });

  it("does not let an in-flight successful recheck override a newer control-down snapshot", async () => {
    let resolveFetch!: (value: unknown) => void;
    const promise = new Promise<unknown>((resolve) => {
      resolveFetch = resolve;
    });
    rs.stubGlobal(
      "fetch",
      rs.fn(() => promise),
    );
    const { result, rerender } = renderHook(({ errors }) => useControlHealth(false, "", errors), {
      initialProps: { errors: downErrors },
    });
    let pending!: Promise<void>;
    act(() => {
      pending = result.current.recheck();
    });
    rerender({ errors: [...downErrors] });
    await act(async () => {
      resolveFetch({ ok: true, json: async () => ({ result: "OK" }) });
      await pending;
    });
    expect(result.current.alive).toBe(false);
    expect(result.current.rechecking).toBe(false);
  });

  it("stays not-alive when the recheck fails", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: true, json: async () => ({ result: "ERROR" }) })),
    );
    const { result } = renderHook(() => useControlHealth(false, "", downErrors));
    await act(async () => {
      await result.current.recheck();
    });
    expect(result.current.alive).toBe(false);
  });
});
