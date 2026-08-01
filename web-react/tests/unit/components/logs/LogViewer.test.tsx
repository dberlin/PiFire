import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, render, screen, waitFor } from "@testing-library/react";
import * as React from "react" with { rstest: "importActual" };
import * as actualLogsApi from "../../../../src/helpers/logs/logsApi" with {
  rstest: "importActual",
};

const fetchLogWholeMock = rs.fn();
const fetchLogDeltaMock = rs.fn();
rs.mock("../../../../src/helpers/logs/logsApi", () => ({
  ...actualLogsApi,
  fetchLogWhole: (...a: unknown[]) => fetchLogWholeMock(...a),
  fetchLogDelta: (...a: unknown[]) => fetchLogDeltaMock(...a),
}));

// LazyLog stands in for the real viewer here.
//
// The real one virtualizes through virtua, which discards every ResizeObserver
// entry whose target has no `offsetParent` -- and in jsdom that is every
// element, always. So the real component mounts its search bar, ingests the
// text, and renders exactly zero rows: asserting on displayed log lines under
// jsdom is not possible, and stubbing layout deep enough to fake it would only
// assert the stubs. tests/e2e/events.spec.ts covers real rendering in Chromium.
//
// What this double DOES let us test is the part that is ours and is subtle:
// that a poll appends only the delta rather than redrawing, and that a rotation
// replaces the whole view. The double therefore keeps the two pieces of
// LazyLog's contract LogViewer depends on -- the `text` prop and the
// appendLines() ref method -- and nothing else.
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

const { LogViewer } = await import("../../../../src/components/logs/LogViewer");

beforeEach(() => {
  fetchLogWholeMock.mockReset();
  fetchLogDeltaMock.mockReset();
  appended.length = 0;
  fetchLogWholeMock.mockResolvedValue({ text: "alpha\nbravo\n", total: 12 });
});

afterEach(() => {
  rs.useRealTimers();
});

//  Biome's useHookAtTopLevel rule keys off the `use` prefix and cannot tell
//  rstest's clock control from a React hook, so calling rs.useFakeTimers()
//  inside the plain helper below is an error. Bound rather than wrapped: a
//  wrapper would still contain the offending call site.
const installFakeClock = rs.useFakeTimers.bind(rs);

/** Mount under fake timers and let the initial whole-family read settle.
 *
 * The clock has to be faked BEFORE render: the poll's setInterval is created
 * during the mount effect, and one installed afterwards does not own it -- the
 * timer then runs on the real clock and never fires inside the test. */
async function mountFollowing(follow = true) {
  installFakeClock();
  render(<LogViewer stem="events" follow={follow} />);
  await act(async () => {
    await rs.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByTestId("lazylog")).toBeTruthy();
}

/** One poll interval. */
async function tick(ms = 3000) {
  await act(async () => {
    await rs.advanceTimersByTimeAsync(ms);
  });
}

describe("LogViewer", () => {
  it("reads the whole family once on mount", async () => {
    render(<LogViewer stem="events" follow={false} />);
    await waitFor(() => expect(fetchLogWholeMock).toHaveBeenCalledTimes(1));
    expect(fetchLogWholeMock).toHaveBeenCalledWith("events");
  });

  it("hands the text it read to the viewer", async () => {
    render(<LogViewer stem="events" follow={false} />);
    expect((await screen.findByTestId("lazylog")).textContent).toBe("alpha\nbravo\n");
  });

  it("clears the previous family's content when the stem changes", async () => {
    const { rerender } = render(<LogViewer stem="events" follow={false} />);
    await screen.findByTestId("lazylog");

    //  Never resolves, so the assertion below sees the state between the switch
    //  and the new family's content arriving -- which is the whole window in
    //  which the old log could be shown under the new log's name.
    fetchLogWholeMock.mockReturnValue(new Promise(() => {}));
    rerender(<LogViewer stem="control" follow={false} />);

    expect(screen.queryByTestId("lazylog")).toBeNull();
    await waitFor(() => expect(fetchLogWholeMock).toHaveBeenCalledWith("control"));
  });

  it("reports a failed read instead of rendering an empty frame", async () => {
    fetchLogWholeMock.mockRejectedValue(new Error("Failed to fetch"));
    render(<LogViewer stem="events" follow={false} />);
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("Failed to fetch"));
  });

  it("does not poll when follow is off", async () => {
    render(<LogViewer stem="events" follow={false} />);
    await waitFor(() => expect(fetchLogWholeMock).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 50));
    expect(fetchLogDeltaMock).not.toHaveBeenCalled();
  });

  it("polls from the cursor the whole read ended at", async () => {
    fetchLogDeltaMock.mockResolvedValue({ kind: "unchanged", nextOffset: 12, total: 12 });
    await mountFollowing();
    await tick();
    expect(fetchLogDeltaMock).toHaveBeenCalledWith("events", 12, 12);
  });

  it("appends only the delta rather than redrawing the view", async () => {
    fetchLogDeltaMock.mockResolvedValue({
      kind: "appended",
      text: "charlie\ndelta\n",
      nextOffset: 26,
      total: 26,
    });
    await mountFollowing();
    await tick();
    //  The trailing newline must not become an empty final line, and the text
    //  already displayed must not be re-sent.
    expect(appended).toEqual([["charlie", "delta"]]);
    expect(screen.getByTestId("lazylog").textContent).toBe("alpha\nbravo\n");
  });

  it("replaces the whole view when the family rotates", async () => {
    fetchLogDeltaMock.mockResolvedValue({
      kind: "rotated",
      text: "fresh\n",
      nextOffset: 6,
      total: 6,
    });
    await mountFollowing();
    await tick();
    //  Appending here would weld new lines onto content that no longer exists
    //  on the server.
    expect(appended).toEqual([]);
    expect(screen.getByTestId("lazylog").textContent).toBe("fresh\n");
  });

  it("keeps polling after a failed poll", async () => {
    fetchLogDeltaMock
      .mockRejectedValueOnce(new Error("Failed to fetch"))
      .mockResolvedValue({ kind: "unchanged", nextOffset: 12, total: 12 });
    await mountFollowing();
    await tick(9000);
    //  A grill that drops off the network for one tick must not silently stop
    //  the tail; the viewer would then read as a grill with nothing to say.
    expect(fetchLogDeltaMock.mock.calls.length).toBeGreaterThan(1);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
