import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { type CommandClient, type CommandResult, createCommand } from "../../helpers/command";
import type { LiveState } from "../../helpers/types";
import { TimerModal } from "./TimerModal";

const OK: CommandResult = { ok: true, message: "" };

type Timer = LiveState["timer"];

const timer = (over: Partial<Timer> = {}): Timer => ({
  start: 0,
  paused: 0,
  end: 0,
  keepWarm: false,
  shutdown: false,
  ...over,
});

/** A command stub that records the order in which timer commands were issued. */
function stubCommand() {
  const calls: string[] = [];
  const record =
    (name: string) =>
    async (...args: unknown[]) => {
      calls.push(args.length ? `${name}:${String(args[0])}` : name);
      return OK;
    };
  const command: CommandClient = {
    setMode: rs.fn(async () => OK),
    hold: rs.fn(async () => OK),
    setSmokePlus: rs.fn(async () => OK),
    setPMode: rs.fn(async () => OK),
    prime: rs.fn(async () => OK),
    timerStart: rs.fn(record("start")),
    timerStartWithOptions: rs.fn(
      async (seconds: number, options: { shutdown: boolean; keepWarm: boolean }) => {
        calls.push(`start:${seconds}:shutdown=${options.shutdown}:keep_warm=${options.keepWarm}`);
        return OK;
      },
    ),
    timerPause: rs.fn(record("pause")),
    timerStop: rs.fn(record("stop")),
    timerShutdown: rs.fn(record("shutdown")),
    timerKeepWarm: rs.fn(record("keep_warm")),
    system: rs.fn(async () => OK),
    setUnits: rs.fn(async () => OK),
    manualOutput: rs.fn(async () => OK),
    manualPwm: rs.fn(async () => OK),
  };
  return { command, calls };
}

function open(over: Partial<Timer> = {}) {
  const { command, calls } = stubCommand();
  const onClose = rs.fn();
  render(<TimerModal timer={timer(over)} command={command} onClose={onClose} />);
  return { command, calls, onClose };
}

const hours = () => screen.getByLabelText("Hours") as HTMLInputElement;
const minutes = () => screen.getByLabelText("Minutes") as HTMLInputElement;
const startButton = () => screen.getByRole("button", { name: "Start" });

describe("TimerModal", () => {
  it("renders as a dialog with both sliders at zero", () => {
    open();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(hours().value).toBe("0");
    expect(minutes().value).toBe("0");
  });

  it("bounds the sliders to the ranges base.html uses", () => {
    open();
    expect(hours().min).toBe("0");
    expect(hours().max).toBe("23");
    expect(minutes().min).toBe("0");
    expect(minutes().max).toBe("59");
  });

  it("shows the slider values as they move", () => {
    open();
    fireEvent.change(hours(), { target: { value: "2" } });
    fireEvent.change(minutes(), { target: { value: "30" } });
    expect(hours().value).toBe("2");
    expect(minutes().value).toBe("30");
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("30")).toBeTruthy();
  });

  // The flags cannot be sent as their own requests -- a later control write
  // clobbers them (see the cross-process pin at the bottom of this file), so
  // they must travel with the start.
  it("sends the expiry flags WITH the start, in a single command", async () => {
    const { command, calls, onClose } = open();
    fireEvent.change(hours(), { target: { value: "1" } });
    fireEvent.change(minutes(), { target: { value: "30" } });
    fireEvent.click(screen.getByLabelText("Shutdown Grill"));

    await fireEvent.click(startButton());
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(calls).toEqual(["start:5400:shutdown=true:keep_warm=false"]);
    expect(command.timerStartWithOptions).toHaveBeenCalledWith(90 * 60, {
      shutdown: true,
      keepWarm: false,
    });
    expect(command.timerShutdown).not.toHaveBeenCalled();
    expect(command.timerKeepWarm).not.toHaveBeenCalled();
    expect(command.timerStart).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it("converts hours and minutes to seconds", async () => {
    const { command } = open();
    fireEvent.change(minutes(), { target: { value: "1" } });
    await fireEvent.click(startButton());
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(command.timerStartWithOptions).toHaveBeenCalledWith(60, {
      shutdown: false,
      keepWarm: false,
    });
  });

  it("sends keep_warm true when the box is checked", async () => {
    const { calls } = open();
    fireEvent.change(minutes(), { target: { value: "5" } });
    fireEvent.click(screen.getByLabelText("Start Keep Warm"));
    await fireEvent.click(startButton());
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(calls).toEqual(["start:300:shutdown=false:keep_warm=true"]);
  });

  it("seeds the checkboxes from the flags already set on the control process", () => {
    open({ shutdown: true, keepWarm: true });
    expect((screen.getByLabelText("Shutdown Grill") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Start Keep Warm") as HTMLInputElement).checked).toBe(true);
  });

  // A 0h0m submission must never reach the backend: /api/set/timer/start/0
  // parses as a float, so the backend would happily arm a 0-second timer, and
  // a missing/non-numeric segment silently becomes 60 seconds. Either way the
  // user gets a timer they did not ask for.
  it("refuses a 0h0m submission instead of sending it", async () => {
    const { command, calls, onClose } = open();
    await fireEvent.click(startButton());
    await Promise.resolve();

    expect(calls).toEqual([]);
    expect(command.timerStartWithOptions).not.toHaveBeenCalled();
    expect(command.timerStart).not.toHaveBeenCalled();
    expect(command.timerShutdown).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/longer than zero|at least/i);
  });

  it("clears the zero-duration complaint once a real duration is chosen", async () => {
    open();
    await fireEvent.click(startButton());
    await Promise.resolve();
    expect(screen.queryByRole("alert")).toBeTruthy();

    fireEvent.change(minutes(), { target: { value: "1" } });

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("cancels without issuing any command", () => {
    const { calls, onClose } = open();
    fireEvent.change(minutes(), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls).toEqual([]);
    expect(onClose).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Cross-process pin: what actually reaches the control process.
//
// The expiry flags (notify_data[timer].shutdown / .keep_warm) and the countdown
// (control.timer) live in ONE control blob, and every MERGE write queues the
// WHOLE blob to be applied with SQLite json_patch -- RFC 7396, which REPLACES
// arrays rather than merging their elements. read_control() also never sees the
// pending queue, and only the control loop drains it. So two writes issued
// inside one control cycle both read the same stale blob and the second one's
// notify_data array wins outright.
//
// That makes "how many requests did this submission produce" the property worth
// pinning, not the order of client-side calls: the flags and the start must
// arrive in the SAME control write or they are silently discarded. The single
// request is /api/set/timer/start/{seconds}/{options}, which does exactly one
// write_control() on the server (common/api_commands.py _cmd_set_timer).
//
// It carries a DURATION. The control process compares control.timer.end against
// its OWN time.time(), so the end must be computed from that same clock: a
// browser running behind the Pi would otherwise arm an already-expired timer,
// and an expired timer with "Shutdown Grill" ticked shuts the grill down
// mid-cook. Nothing in this request is a timestamp, so nothing can skew.
// ---------------------------------------------------------------------------
describe("TimerModal over the real command client", () => {
  let requests: { url: string; init: RequestInit | undefined }[];

  beforeEach(() => {
    requests = [];
    rs.stubGlobal(
      "fetch",
      rs.fn(async (url: string, init?: RequestInit) => {
        requests.push({ url, init });
        return {
          ok: true,
          headers: new Headers(),
          json: async () => ({ result: "OK", message: "Command was accepted successfully." }),
        };
      }),
    );
  });
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  function start(over: Partial<Timer>, h: number, m: number, flags: string[]) {
    const onClose = rs.fn();
    render(<TimerModal timer={timer(over)} command={createCommand("")} onClose={onClose} />);
    fireEvent.change(hours(), { target: { value: String(h) } });
    fireEvent.change(minutes(), { target: { value: String(m) } });
    for (const flag of flags) fireEvent.click(screen.getByLabelText(flag));
    fireEvent.click(startButton());
    return onClose;
  }

  it("sends the expiry flags and the countdown as ONE request", async () => {
    const onClose = start({}, 1, 30, ["Shutdown Grill", "Start Keep Warm"]);
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });

    // One request. Three is the bug: each control write reads the same stale
    // blob and the last one's notify_data array replaces the flags the earlier
    // ones set.
    expect(requests).toHaveLength(1);
    expect(requests[0].url).toBe("/api/set/timer/start/5400/shutdown,keep_warm");
    expect(requests[0].init?.method).toBe("POST");
  });

  it("names only the flags that are ticked", async () => {
    const onClose = start({}, 0, 5, ["Start Keep Warm"]);
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
    expect(requests).toHaveLength(1);
    expect(requests[0].url).toBe("/api/set/timer/start/300/keep_warm");
  });

  it("says so explicitly when neither flag is ticked", async () => {
    const onClose = start({}, 0, 5, []);
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
    // 'none' rather than an empty segment: an empty path segment collapses the
    // URL to the 3-argument form, which leaves both flags at whatever a
    // previous cook left behind.
    expect(requests[0].url).toBe("/api/set/timer/start/300/none");
  });

  // The whole point of the server-side form: the request contains a duration
  // and nothing that came off the browser clock.
  it("sends a duration, never a timestamp", async () => {
    const onClose = start({}, 2, 0, []);
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
    expect(requests[0].url).toBe("/api/set/timer/start/7200/none");
    expect(requests[0].init?.body).toBeUndefined();
    const nowSeconds = String(Math.floor(Date.now() / 1000)).slice(0, 6);
    expect(requests[0].url).not.toContain(nowSeconds);
  });

  it("still refuses 0h0m without issuing any request", async () => {
    const onClose = start({}, 0, 0, []);
    await waitFor(() => {
      expect(screen.getByRole("alert")).toBeTruthy();
    });
    expect(requests).toEqual([]);
    expect(onClose).not.toHaveBeenCalled();
  });
});
