import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, render, screen } from "@testing-library/react";
import * as React from "react" with { rstest: "importActual" };

// Same double, and for the same reason, as LogViewer.test.tsx: the real LazyLog
// virtualizes through virtua, which drops every ResizeObserver entry whose
// target has no offsetParent -- in jsdom, all of them -- so it renders zero
// rows however much text it is handed. What is testable here is the part that
// is ours: that a poll appends only its delta, and that a `reset` replaces the
// view instead of appending onto a previous run.
const appended: string[][] = [];
rs.mock("@melloware/react-logviewer", () => ({
  LazyLog: class extends React.Component<{ text: string }> {
    appendLines(lines: string[]) {
      appended.push(lines);
    }
    render() {
      return React.createElement("pre", { "data-testid": "lazylog" }, this.props.text);
    }
  },
}));

const { StreamingLogPanel } = await import("../../../../src/components/logs/StreamingLogPanel");

const fetchDelta = rs.fn();
const WAITING = "Waiting for the installer's first line…";

const panel = () => <StreamingLogPanel fetchDelta={fetchDelta} waitingText={WAITING} />;

beforeEach(() => {
  fetchDelta.mockReset();
  appended.length = 0;
});

afterEach(() => {
  rs.useRealTimers();
});

describe("StreamingLogPanel", () => {
  it("reads from offset 0 on mount, then polls from the offset the server returned", async () => {
    rs.useFakeTimers();
    fetchDelta
      .mockResolvedValueOnce({
        text: "2026-08-01 12:00:03 +0000 | INFO | first\n",
        offset: 40,
        reset: true,
      })
      .mockResolvedValue({ text: "", offset: 40, reset: false });

    render(panel());
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });

    expect(fetchDelta).toHaveBeenCalledWith(0);

    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });
    // The second poll must resume where the first ended, or the transcript
    // re-sends the whole run every tick.
    expect(fetchDelta).toHaveBeenLastCalledWith(40);
  });

  it("strips the logger prefix, keeping the clock", async () => {
    rs.useFakeTimers();
    fetchDelta.mockResolvedValue({
      text: "2026-08-01 12:00:03 +0000 | INFO | Resolved 12 packages\n",
      offset: 56,
      reset: true,
    });

    render(panel());
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });

    const shown = screen.getByTestId("lazylog").textContent ?? "";
    expect(shown).toContain("12:00:03  Resolved 12 packages");
    expect(shown).not.toContain("2026-08-01");
    expect(shown).not.toContain("INFO");
  });

  it("appends a delta rather than redrawing the whole log", async () => {
    rs.useFakeTimers();
    fetchDelta
      .mockResolvedValueOnce({ text: "one\n", offset: 4, reset: true })
      .mockResolvedValueOnce({ text: "two\nthree\n", offset: 14, reset: false })
      .mockResolvedValue({ text: "", offset: 14, reset: false });

    render(panel());
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });

    expect(appended).toEqual([["two", "three"]]);
    // The initial text is untouched by the append -- the delta went through the
    // ref, which is what keeps a long run from re-rendering every tick.
    expect(screen.getByTestId("lazylog")).toHaveTextContent("one");
  });

  it("replaces the view when the server reports a reset", async () => {
    /* A second run in the same session. Appending would splice the new run's
       head onto the finished run's tail. */
    rs.useFakeTimers();
    fetchDelta
      .mockResolvedValueOnce({ text: "first run\n", offset: 10, reset: true })
      .mockResolvedValueOnce({ text: "second run\n", offset: 11, reset: true })
      .mockResolvedValue({ text: "", offset: 11, reset: false });

    render(panel());
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });

    expect(appended).toEqual([]);
    const shown = screen.getByTestId("lazylog").textContent ?? "";
    expect(shown).toContain("second run");
    expect(shown).not.toContain("first run");
  });

  it("shows the waiting text, not an empty viewer, until the first line lands", async () => {
    // LazyLog ingests its `text` when it mounts and thereafter owns its rows,
    // so mounting it on placeholder text would bake that placeholder in as row
    // one for the rest of the run -- and swallow the first read with it.
    rs.useFakeTimers();
    fetchDelta
      .mockResolvedValueOnce({ text: "", offset: 0, reset: true })
      .mockResolvedValue({ text: "first line\n", offset: 11, reset: false });

    render(panel());
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByTestId("lazylog")).toBeNull();
    expect(screen.getByText(WAITING)).toBeInTheDocument();

    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByTestId("lazylog")).toHaveTextContent("first line");
  });

  it("discards a read computed from an offset already moved past", async () => {
    /* Two reads overlap -- a slow one plus the next tick, or a remount -- and
       the late one still carries lines the earlier one delivered. Applying it
       duplicated the whole overlap on screen. */
    rs.useFakeTimers();
    let resolveSlow: (v: unknown) => void = () => {};
    fetchDelta
      // Seed, so the reads that follow are appends -- which is where a
      // duplicate actually lands on screen.
      .mockResolvedValueOnce({ text: "one\n", offset: 4, reset: true })
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveSlow = resolve;
        }),
      )
      .mockResolvedValue({ text: "two\n", offset: 8, reset: false });

    render(panel());
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    // The slow read goes out from offset 4 and does not answer.
    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });
    // The next tick reads from offset 4 too, answers, and moves it to 8.
    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });
    expect(appended).toEqual([["two"]]);

    // The slow read now lands with the same delta, computed from offset 4.
    await act(async () => {
      resolveSlow({ text: "two\n", offset: 8, reset: false });
      await Promise.resolve();
    });

    expect(appended).toEqual([["two"]]);
  });

  it("keeps polling after a failed read", async () => {
    rs.useFakeTimers();
    fetchDelta
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue({ text: "recovered\n", offset: 10, reset: true });

    render(panel());
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });

    expect(screen.getByTestId("lazylog")).toHaveTextContent("recovered");
  });

  it("does not restart its polling when the parent re-renders", async () => {
    /* Both callers re-render on their own status poll, four times a second,
       and both pass a fresh closure each time. Depending on that identity
       tore the interval down and rebuilt it on every one of those renders. */
    rs.useFakeTimers();
    fetchDelta.mockResolvedValue({ text: "", offset: 0, reset: false });

    const { rerender } = render(
      <StreamingLogPanel fetchDelta={(o) => fetchDelta(o)} waitingText={WAITING} />,
    );
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    fetchDelta.mockClear();

    // Four renders inside one poll interval. A restarted effect reads
    // immediately, so each would show up as its own call.
    for (let i = 0; i < 4; i++) {
      rerender(<StreamingLogPanel fetchDelta={(o) => fetchDelta(o)} waitingText={WAITING} />);
      await act(async () => {
        await rs.advanceTimersByTimeAsync(100);
      });
    }

    expect(fetchDelta).not.toHaveBeenCalled();
  });

  it("calls the newest fetcher, not the one it mounted with", async () => {
    // The ref that stops the restart above must still be updated, or a caller
    // whose base URL or credentials change would poll the old one forever.
    rs.useFakeTimers();
    const first = rs.fn().mockResolvedValue({ text: "", offset: 0, reset: false });
    const second = rs.fn().mockResolvedValue({ text: "", offset: 0, reset: false });

    const { rerender } = render(<StreamingLogPanel fetchDelta={first} waitingText={WAITING} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });

    rerender(<StreamingLogPanel fetchDelta={second} waitingText={WAITING} />);
    await act(async () => {
      await rs.advanceTimersByTimeAsync(1000);
    });

    expect(second).toHaveBeenCalled();
  });

  it("stops polling on unmount", async () => {
    rs.useFakeTimers();
    fetchDelta.mockResolvedValue({ text: "", offset: 0, reset: false });

    const { unmount } = render(panel());
    await act(async () => {
      await rs.advanceTimersByTimeAsync(0);
    });
    unmount();
    fetchDelta.mockClear();

    await act(async () => {
      await rs.advanceTimersByTimeAsync(5000);
    });

    expect(fetchDelta).not.toHaveBeenCalled();
  });
});
