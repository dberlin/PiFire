/** @jest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createQueryClient } from "../../../src/helpers/query/queryClient";
import { useWebUiBuild } from "../../../src/helpers/useWebUiBuild";

const fetchMock = rs.fn();

function Probe({ reload }: { reload: () => void }) {
  useWebUiBuild("http://x", reload);
  return null;
}

function serves(build: string | null) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ build }) });
}

// This hook overrides refetchOnWindowFocus and staleTime on its own query
// (useWebUiBuild.ts) and several tests below exist specifically to exercise
// those overrides. test-utils.tsx's testQueryClient() sets staleTime: 0
// CLIENT-WIDE, which makes queryObserver's shouldFetchOn (query-core) refetch
// on focus even for an UNSET refetchOnWindowFocus -- so against that client
// either override could be deleted from the hook and every test here would
// still pass. Render through the production client (queryClient.ts) instead,
// so it is genuinely the hook's own overrides making these tests pass, not
// the test harness's defaults standing in for them.
function renderProbe(ui: ReactElement) {
  return render(<QueryClientProvider client={createQueryClient()}>{ui}</QueryClientProvider>);
}

// react-query settles a refetch in two hops: the fetch promise resolves (a
// microtask), then notifyManager re-renders observers via a REAL setTimeout(0)
// (see node_modules/@tanstack/query-core's notifyManager.js, so React can
// batch the update. advanceTimersByTimeAsync stopping exactly on the poll
// boundary flushes the first hop but leaves that second one still pending --
// nothing downstream of `data` (this hook's useEffect, and so `reload`) has
// run yet. waitFor can't be used to paper over this either: it polls on a
// timer that fake timers freeze (see HistoryPage.test.tsx's auto-refresh
// describe block).
async function tick(ms: number) {
  await act(async () => {
    await rs.advanceTimersByTimeAsync(ms);
  });
}

// Ticking one more virtual millisecond past a settle is what lets notifyManager's
// setTimeout(0) hop fire. Kept separate from tick() so a call site's intent --
// "advance the poll clock by exactly this much" vs. "let the pending render
// flush" -- reads correctly instead of being hidden inside an off-by-one.
async function settle() {
  await act(async () => {
    await rs.advanceTimersByTimeAsync(1);
  });
}

beforeEach(() => {
  rs.useFakeTimers();
  fetchMock.mockReset();
  (globalThis as any).fetch = fetchMock;
});

afterEach(() => {
  cleanup();
  rs.useRealTimers();
  (globalThis as any).fetch = undefined;
});

describe("useWebUiBuild", () => {
  it("does not reload while the served build is unchanged", async () => {
    /* The explicit requirement: a tab must not reload just because it polled. */
    fetchMock.mockImplementation(() => serves("aaaa"));
    const reload = rs.fn();

    renderProbe(<Probe reload={reload} />);
    await tick(0);
    await settle();
    await tick(180_000);
    await settle();

    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    expect(reload).not.toHaveBeenCalled();
  });

  it("reloads once the server starts serving a different build", async () => {
    fetchMock.mockImplementationOnce(() => serves("aaaa")).mockImplementation(() => serves("bbbb"));
    const reload = rs.fn();

    renderProbe(<Probe reload={reload} />);
    await tick(0);
    await settle();
    expect(reload).not.toHaveBeenCalled();

    await tick(60_000);
    await settle();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("treats an unreachable backend as no answer, not as a new build", async () => {
    /* An update restarts the backend, so these failures happen on exactly the
       path that matters. Reloading into a dead server would loop. */
    fetchMock
      .mockImplementationOnce(() => serves("aaaa"))
      .mockImplementation(() => Promise.reject(new Error("down")));
    const reload = rs.fn();

    renderProbe(<Probe reload={reload} />);
    await tick(0);
    await settle();
    await tick(180_000);
    await settle();

    expect(reload).not.toHaveBeenCalled();
  });

  it("treats a missing bundle as no answer", async () => {
    fetchMock.mockImplementationOnce(() => serves("aaaa")).mockImplementation(() => serves(null));
    const reload = rs.fn();

    renderProbe(<Probe reload={reload} />);
    await tick(0);
    await settle();
    await tick(180_000);
    await settle();

    expect(reload).not.toHaveBeenCalled();
  });

  it("adopts the first build it can read, even if early reads failed", async () => {
    /* Opening a tab while the backend is restarting must not leave the tab
       permanently unable to notice a later change. */
    fetchMock
      .mockImplementationOnce(() => Promise.reject(new Error("down")))
      .mockImplementationOnce(() => serves("aaaa"))
      .mockImplementation(() => serves("bbbb"));
    const reload = rs.fn();

    renderProbe(<Probe reload={reload} />);
    await tick(0);
    await settle();
    await tick(60_000);
    await settle();
    expect(reload).not.toHaveBeenCalled();

    await tick(60_000);
    await settle();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("checks again when a suspended tab becomes visible", async () => {
    fetchMock.mockImplementation(() => serves("aaaa"));
    renderProbe(<Probe reload={rs.fn()} />);
    await tick(0);
    await settle();
    const before = fetchMock.mock.calls.length;

    // react-query's focusManager listens on `window`, not `document` (see
    // node_modules/@tanstack/query-core's focusManager.js) -- this is what
    // refetchOnWindowFocus: true (useWebUiBuild.ts) actually subscribes to.
    await act(async () => {
      window.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    await tick(0);
    await settle();

    expect(fetchMock.mock.calls.length).toBe(before + 1);
  });

  it("stops polling on unmount", async () => {
    fetchMock.mockImplementation(() => serves("aaaa"));
    const { unmount } = renderProbe(<Probe reload={rs.fn()} />);
    await tick(0);
    await settle();

    unmount();
    fetchMock.mockClear();
    await tick(300_000);
    await settle();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("re-reads on the poll cadence without a hand-rolled interval", async () => {
    const reload = rs.fn();
    fetchMock
      .mockResolvedValueOnce({ ok: true, json: async () => ({ build: "abc" }) })
      .mockResolvedValue({ ok: true, json: async () => ({ build: "def" }) });
    renderProbe(<Probe reload={reload} />);
    await tick(0);
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await tick(60_000);
    await settle();
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
