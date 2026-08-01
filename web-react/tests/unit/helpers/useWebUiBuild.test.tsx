/** @jest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, render } from "@testing-library/react";
import { useWebUiBuild } from "../../../src/helpers/useWebUiBuild";

const fetchMock = rs.fn();

function Probe({ reload }: { reload: () => void }) {
  useWebUiBuild("http://x", reload);
  return null;
}

function serves(build: string | null) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve({ build }) });
}

beforeEach(() => {
  rs.useFakeTimers();
  fetchMock.mockReset();
  // biome-ignore lint/suspicious/noExplicitAny: stubbing the global fetch.
  (globalThis as any).fetch = fetchMock;
});

afterEach(() => {
  cleanup();
  rs.useRealTimers();
  // biome-ignore lint/suspicious/noExplicitAny: undo the stub above.
  (globalThis as any).fetch = undefined;
});

describe("useWebUiBuild", () => {
  it("does not reload while the served build is unchanged", async () => {
    /* The explicit requirement: a tab must not reload just because it polled. */
    fetchMock.mockImplementation(() => serves("aaaa"));
    const reload = rs.fn();

    render(<Probe reload={reload} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await rs.advanceTimersByTimeAsync(180_000);
    });

    expect(fetchMock.mock.calls.length).toBeGreaterThan(1);
    expect(reload).not.toHaveBeenCalled();
  });

  it("reloads once the server starts serving a different build", async () => {
    fetchMock.mockImplementationOnce(() => serves("aaaa")).mockImplementation(() => serves("bbbb"));
    const reload = rs.fn();

    render(<Probe reload={reload} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    expect(reload).not.toHaveBeenCalled();

    await act(async () => {
      await rs.advanceTimersByTimeAsync(60_000);
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("treats an unreachable backend as no answer, not as a new build", async () => {
    /* An update restarts the backend, so these failures happen on exactly the
       path that matters. Reloading into a dead server would loop. */
    fetchMock
      .mockImplementationOnce(() => serves("aaaa"))
      .mockImplementation(() => Promise.reject(new Error("down")));
    const reload = rs.fn();

    render(<Probe reload={reload} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await rs.advanceTimersByTimeAsync(180_000);
    });

    expect(reload).not.toHaveBeenCalled();
  });

  it("treats a missing bundle as no answer", async () => {
    fetchMock.mockImplementationOnce(() => serves("aaaa")).mockImplementation(() => serves(null));
    const reload = rs.fn();

    render(<Probe reload={reload} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await rs.advanceTimersByTimeAsync(180_000);
    });

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

    render(<Probe reload={reload} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await rs.advanceTimersByTimeAsync(60_000);
    });
    expect(reload).not.toHaveBeenCalled();

    await act(async () => {
      await rs.advanceTimersByTimeAsync(60_000);
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("checks again when a suspended tab becomes visible", async () => {
    fetchMock.mockImplementation(() => serves("aaaa"));
    render(<Probe reload={rs.fn()} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    const before = fetchMock.mock.calls.length;

    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });

    expect(fetchMock.mock.calls.length).toBe(before + 1);
  });

  it("stops polling on unmount", async () => {
    fetchMock.mockImplementation(() => serves("aaaa"));
    const { unmount } = render(<Probe reload={rs.fn()} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });

    unmount();
    fetchMock.mockClear();
    await act(async () => {
      await rs.advanceTimersByTimeAsync(300_000);
    });

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
