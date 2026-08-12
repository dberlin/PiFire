import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { TimerBar } from "../../../../src/components/shell/TimerBar";
import { createCommand } from "../../../../src/helpers/command";
import type { DashSocketPayload } from "../../../../src/helpers/contracts/core.gen";

// ---------------------------------------------------------------------------
// A model of the control-write seam, so the bar's BUTTON SEQUENCES can be
// judged by what the control process ends up holding rather than by which
// fetches were issued.
//
// The properties modelled here are real (ported from the Python, and pinned at
// that end in tests/characterization/test_control_delta_seam.py and
// tests/characterization/test_process_command_golden.py):
//
//  1. A timer command does NOT queue a computed timer state. It queues a named
//     OP carrying the request's clock as `at` (common/control_delta.py):
//     `timer.clear`, `timer.pause`, `timer.start_or_resume`,
//     `timer.start_with_options`.
//  2. read_control() still serves the PERSISTED blob and never the pending
//     queue, so a command's REQUEST-time answer (the API envelope) is still
//     computed from pre-write state. That is why the paused-timer rejection in
//     the 4-argument form is a request-time answer and stays one.
//  3. execute_control_writes applies each op IN ORDER against the LIVE,
//     evolving blob -- not against a captured ancestor, and with nothing
//     reduced or inferred. So the second op in a cycle sees the first op's
//     result.
//
// (3) is the whole point, and it is what retired this bar's
// one-write-per-gesture guard: a stop followed by a pause pauses a timer that
// the stop already cleared, which is the backend's own start == 0 branch, i.e.
// nothing. See "the control-write seam" at the bottom of this file.
// ---------------------------------------------------------------------------

const NOW = 1_700_000_000;

interface TimerNotify {
  label: string;
  type: string;
  req: boolean;
  shutdown: boolean;
  keep_warm: boolean;
}

interface Control {
  timer: { start: number; paused: number; end: number };
  notify_data: TimerNotify[];
}

function seedControl(
  over: Partial<Control["timer"]> = {},
  flags: Partial<TimerNotify> = {},
): Control {
  return {
    timer: { start: 0, paused: 0, end: 0, ...over },
    notify_data: [
      { label: "Grill", type: "probe", req: true, shutdown: false, keep_warm: false },
      { label: "Timer", type: "timer", req: false, shutdown: false, keep_warm: false, ...flags },
    ],
  };
}

/** The ops a queued delta can carry, mirroring common/control_delta.py's
 *  _OP_FIELDS for the timer family. `at` is the REQUEST's clock: the branch
 *  moves to the drain, the clock does not. */
type Op =
  | { op: "timer.clear" }
  | { op: "timer.pause"; at: number }
  | { op: "timer.start_or_resume"; at: number; seconds: number | null }
  | {
      op: "timer.start_with_options";
      at: number;
      seconds: number;
      shutdown: boolean;
      keep_warm: boolean;
    };

/** Port of common/control_delta.py's timer op appliers. Applied to the LIVE
 *  control dict -- there is no ancestor and nothing is reduced, because an op
 *  is nothing but intent. */
function applyOp(control: Control, op: Op): void {
  const entry = control.notify_data.find((e) => e.type === "timer");
  if (entry === undefined) throw new Error("no timer notify entry");
  switch (op.op) {
    case "timer.clear":
      control.timer = { start: 0, paused: 0, end: 0 };
      entry.req = false;
      entry.shutdown = false;
      entry.keep_warm = false;
      break;
    case "timer.pause":
      if (control.timer.start === 0) {
        // The backend's own start == 0 branch is a full clear, not a pause.
        applyOp(control, { op: "timer.clear" });
        break;
      }
      entry.req = false;
      control.timer.paused = op.at;
      break;
    case "timer.start_or_resume":
      entry.req = true;
      if (control.timer.paused === 0) {
        control.timer.start = op.at;
        control.timer.end = op.at + (op.seconds ?? 60);
      } else {
        control.timer.end = control.timer.end - control.timer.paused + op.at;
        control.timer.paused = 0;
      }
      break;
    case "timer.start_with_options":
      if (control.timer.paused !== 0) {
        // Request time already rejected a paused timer, so reaching the drain
        // paused means another writer paused it inside this cycle. Dropped.
        break;
      }
      entry.req = true;
      entry.shutdown = op.shutdown;
      entry.keep_warm = op.keep_warm;
      control.timer.start = op.at;
      control.timer.end = op.at + op.seconds;
      break;
  }
}

class ControlProcess {
  /** The persisted blob: what read_control() returns and what the socket ships. */
  blob: Control;
  /** Queued delta envelopes, drained once per control cycle. */
  private queue: Op[][] = [];

  constructor(blob: Control) {
    this.blob = blob;
  }

  /** Port of common/api_commands.py _cmd_set_timer's start/pause/stop branches.
   *  Returns the API envelope common/app.py api_response produces.
   *
   *  Note what is NOT here any more: no timer state is computed. The only thing
   *  this reads the blob for is the REQUEST-time rejection in the 4-argument
   *  form, which is a synchronous HTTP answer the queue cannot give. */
  post(url: string): { result: string; message: string; data: unknown } {
    const [, , action, sub, ...rest] = url.split("/");
    if (action !== "set" || sub !== "timer") throw new Error(`unmodelled command: ${url}`);

    switch (rest[0]) {
      case "start":
        if (rest[2] !== undefined) {
          // read_control(): the persisted blob, NOT the queue. See (2) above.
          if (this.blob.timer.paused !== 0) {
            return { result: "ERROR", message: "Timer is paused.", data: {} };
          }
          this.queue.push([
            {
              op: "timer.start_with_options",
              at: NOW,
              seconds: Number(rest[1]),
              shutdown: rest[2].split(",").includes("shutdown"),
              keep_warm: rest[2].split(",").includes("keep_warm"),
            },
          ]);
        } else {
          this.queue.push([
            {
              op: "timer.start_or_resume",
              at: NOW,
              seconds: rest[1] === undefined ? null : Number(rest[1]),
            },
          ]);
        }
        break;
      case "pause":
        this.queue.push([{ op: "timer.pause", at: NOW }]);
        break;
      case "stop":
        this.queue.push([{ op: "timer.clear" }]);
        break;
      default:
        throw new Error(`unmodelled timer command: ${url}`);
    }
    return { result: "OK", message: "", data: {} };
  }

  /** A background writer that names only what it changed -- the shape every
   *  converted writer now has (common/control_delta.py `set`). It cannot revert
   *  a timer because it does not mention one. */
  postUnrelatedDelta(): void {
    this.queue.push([]);
  }

  /** One turn of the control loop. Each envelope's ops are applied IN ORDER
   *  against the live blob, exactly as execute_control_writes does -- no
   *  ancestor is captured, because nothing is inferred. */
  drain(): void {
    for (const ops of this.queue) {
      for (const op of ops) applyOp(this.blob, op);
    }
    this.queue = [];
  }

  timerEntry(): TimerNotify {
    const entry = this.blob.notify_data.find((e) => e.type === "timer");
    if (entry === undefined) throw new Error("no timer notify entry");
    return entry;
  }
}

/** The socket payload the shell renders from, derived from the persisted blob. */
function liveTimer(cp: ControlProcess): DashSocketPayload["timer"] {
  return {
    start: cp.blob.timer.start,
    paused: cp.blob.timer.paused,
    end: cp.blob.timer.end,
    shutdown: cp.timerEntry().shutdown,
    keepWarm: cp.timerEntry().keep_warm,
  };
}

function mount(cp: ControlProcess) {
  const fetchMock = rs.fn(async (url: string) => ({
    ok: true,
    json: async () => cp.post(url),
  }));
  rs.stubGlobal("fetch", fetchMock);
  const view = render(<TimerBar timer={liveTimer(cp)} command={createCommand("")} />);
  // Re-render from the blob, the way the live socket does after the loop runs.
  const republish = () =>
    view.rerender(<TimerBar timer={liveTimer(cp)} command={createCommand("")} />);
  return { view, republish, fetchMock };
}

async function click(name: RegExp) {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name }));
  });
}

beforeEach(() => {
  rs.useFakeTimers();
  rs.setSystemTime(NOW * 1000);
});

afterEach(() => {
  rs.useRealTimers();
  rs.unstubAllGlobals();
});

describe("TimerBar across one control cycle", () => {
  it("arms a timer with its expiry flag in a single write", async () => {
    const cp = new ControlProcess(seedControl());
    const { republish } = mount(cp);

    await click(/start timer/i);
    fireEvent.change(screen.getByLabelText(/hours/i), { target: { value: "1" } });
    fireEvent.click(screen.getByLabelText(/shutdown grill/i));
    await click(/^start$/i);

    cp.drain();
    republish();

    expect(cp.blob.timer.end).toBe(NOW + 3600);
    expect(cp.timerEntry().shutdown).toBe(true);
    expect(cp.timerEntry().req).toBe(true);
  });

  // Pause and Stop are on screen TOGETHER while a timer runs, and the bar
  // re-renders only when the socket republishes -- which cannot happen until
  // the control loop drains. So a second click inside that window is an
  // ordinary thing for a user to do. It used to resurrect the timer, and the
  // bar disabled its own buttons to prevent it; now both clicks queue ops that
  // compose in the drain, so the bar lets them through.
  it("keeps a stopped timer stopped when the next click lands in the same cycle", async () => {
    const cp = new ControlProcess(
      seedControl({ start: NOW - 60, end: NOW + 600 }, { req: true, shutdown: true }),
    );
    mount(cp);

    await click(/stop timer/i);
    await click(/pause timer/i);

    cp.drain();

    expect(cp.blob.timer).toEqual({ start: 0, paused: 0, end: 0 });
    // ...and the expiry action the stop was supposed to disarm stays disarmed.
    // A resurrected timer with `shutdown` still set shuts the grill down when
    // it later expires -- the stop is what the user pressed to prevent that.
    expect(cp.timerEntry().shutdown).toBe(false);
    expect(cp.timerEntry().req).toBe(false);
  });

  // Stop then Resume is the OTHER reachable pair -- both buttons are on screen
  // together while a timer is paused. The guard used to block the second click
  // outright. Now it goes through and the drain composes the two ops: the clear
  // lands first, so the resume sees paused == 0 and arms a FRESH countdown for
  // the time that was left. That is exactly what the same two clicks produce
  // one control cycle apart, which is the property the guard stood in for.
  it("arms a fresh countdown when resume lands in the same cycle as a stop", async () => {
    const cp = new ControlProcess(
      seedControl(
        { start: NOW - 600, paused: NOW - 10, end: NOW + 590 },
        { req: true, shutdown: true },
      ),
    );
    mount(cp);

    await click(/stop timer/i);
    await click(/resume timer/i);

    cp.drain();

    // 600, not 590: the Resume button passes the REMAINING time (end - paused),
    // and with the clear already applied the op uses it instead of ignoring it.
    // The user gets the countdown they had left, which is what the button says.
    expect(cp.blob.timer).toEqual({ start: NOW, paused: 0, end: NOW + 600 });
    // The expiry action the stop disarmed stays disarmed: the resume op sets
    // `req` and nothing re-arms shutdown.
    expect(cp.timerEntry().shutdown).toBe(false);
  });

  // The guard used to make the second gesture wait for the socket to
  // republish. Nothing does now, and that is the user-visible half of this
  // change: a second click is accepted the moment it is made.
  it("lets a second gesture through immediately, without waiting for a republish", async () => {
    const cp = new ControlProcess(seedControl({ start: NOW - 60, end: NOW + 600 }, { req: true }));
    mount(cp);

    await click(/stop timer/i);
    // No drain, no republish -- the bar is still rendering the pre-stop blob.
    expect(
      (screen.getByRole("button", { name: /pause timer/i }) as HTMLButtonElement).disabled,
    ).toBe(false);
  });

  it("issues a retry rather than swallowing it when a command fails", async () => {
    const cp = new ControlProcess(seedControl({ start: NOW - 60, end: NOW + 600 }, { req: true }));
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    render(<TimerBar timer={liveTimer(cp)} command={createCommand("")} />);

    await click(/pause timer/i);

    const stopped = rs.fn(async () => ({
      ok: true,
      json: async () => cp.post("/api/set/timer/stop"),
    }));
    rs.stubGlobal("fetch", stopped);
    await click(/stop timer/i);
    cp.drain();
    expect(cp.blob.timer).toEqual({ start: 0, paused: 0, end: 0 });
  });

  it("still lets the next gesture through once the loop has published the write", async () => {
    const cp = new ControlProcess(seedControl({ start: NOW - 60, end: NOW + 600 }, { req: true }));
    const { republish } = mount(cp);

    await click(/pause timer/i);
    cp.drain();
    republish();
    expect(cp.blob.timer.paused).toBe(NOW);

    await click(/stop timer/i);
    cp.drain();
    republish();
    expect(cp.blob.timer).toEqual({ start: 0, paused: 0, end: 0 });
    expect(screen.getByRole("button", { name: /start timer/i })).toBeTruthy();
  });
});

// The seam itself, driven directly with no UI. These used to be the guard's
// justification -- they showed what the bar would do if the guard were removed.
// They now show why it COULD be removed. Both are pinned against the real
// backend in tests/characterization/test_process_command_golden.py
// (test_a_pause_after_a_stop_in_one_cycle_leaves_the_timer_stopped and
// test_a_resume_after_a_stop_in_one_cycle_arms_a_fresh_timer).
describe("the control-write seam", () => {
  it("keeps a stopped countdown stopped when a pause lands in the same cycle", () => {
    const cp = new ControlProcess(
      seedControl({ start: NOW - 60, end: NOW + 600 }, { req: true, shutdown: true }),
    );

    cp.post("/api/set/timer/stop");
    cp.post("/api/set/timer/pause");
    cp.drain();

    // The pause sees an already-cleared timer and takes the backend's own
    // start == 0 branch, which is a clear -- i.e. nothing.
    expect(cp.blob.timer).toEqual({ start: 0, paused: 0, end: 0 });
    expect(cp.timerEntry().shutdown).toBe(false);
  });

  it("arms a fresh countdown rather than the old one when a resume lands in the same cycle", () => {
    const cp = new ControlProcess(
      seedControl(
        { start: NOW - 600, paused: NOW - 10, end: NOW + 590 },
        { req: true, shutdown: true },
      ),
    );

    cp.post("/api/set/timer/stop");
    cp.post("/api/set/timer/start/590");
    cp.drain();

    // The clear landed first, so the resume sees paused == 0 and starts fresh.
    // The pre-stop end time does NOT come back.
    expect(cp.blob.timer).toEqual({ start: NOW, paused: 0, end: NOW + 590 });
    expect(cp.timerEntry().shutdown).toBe(false);
  });

  it("keeps a start and a stop in one cycle from leaving the timer running", () => {
    // The residual no merge could reach: a stop against an already-zero timer
    // used to be indistinguishable from silence, because the payload carried a
    // value rather than an intent. An op has no value to coincide with.
    const cp = new ControlProcess(seedControl());

    cp.post("/api/set/timer/start/600");
    cp.post("/api/set/timer/stop");
    cp.drain();

    expect(cp.blob.timer).toEqual({ start: 0, paused: 0, end: 0 });
  });

  it("keeps an unrelated writer from reverting a timer it never touched", () => {
    const cp = new ControlProcess(seedControl({ start: NOW - 60, end: NOW + 600 }, { req: true }));

    cp.post("/api/set/timer/pause");
    // A background writer that names only what it changed. It cannot revert a
    // timer because it does not mention one -- no reduction needed.
    cp.postUnrelatedDelta();
    cp.drain();

    expect(cp.blob.timer.paused).toBe(NOW);
  });
});
