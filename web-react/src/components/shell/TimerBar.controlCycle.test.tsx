import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { createCommand } from "../../helpers/command";
import type { LiveState } from "../../helpers/types";
import { TimerBar } from "./TimerBar";

// ---------------------------------------------------------------------------
// A model of the control-write seam, so the bar's BUTTON SEQUENCES can be
// judged by what the control process ends up holding rather than by which
// fetches were issued.
//
// The properties modelled here are real (ported from the Python, and pinned at
// that end in tests/characterization/test_process_command_golden.py and
// tests/characterization/test_control_writes_cross_writer.py):
//
//  1. A web-process write queues the WHOLE control dict, not a diff
//     (common/datastore_accessors.py write_control, WriteKind.MERGE).
//  2. read_control() serves the PERSISTED blob and never the pending queue,
//     so two commands issued inside one control cycle both read pre-write state.
//  3. execute_control_writes drains the queue against the blob as it stood when
//     the drain began -- the ancestor every writer in that cycle read. Each
//     patch is REDUCED against it, so a member the writer left identical to the
//     ancestor is dropped rather than imposed (common/common.py
//     reduce_control_patch), and `notify_data` is additionally merged
//     element-wise, field by field (::merge_notify_data).
//  4. `timer` is the exception: it is reduced as ONE COUPLED UNIT
//     (CONTROL_COUPLED_MEMBERS), because start/paused/end describe a single
//     countdown the backend branches on in combination. So two writers that
//     each computed a timer state still resolve LAST-WINS on the whole object.
//
// (3) is why a second write is no longer destructive in general -- the expiry
// flags in particular survive. (4) is why it still is for the timer bar's own
// buttons, and therefore why the bar allows one write per gesture. The
// `seedControl` -> `drain` model below exercises exactly that, in
// "the seam this guard exists for".
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

/** One queued patch through the drain, for the two keys the timer commands
 *  write. `base` is the blob as it stood when the drain began -- the ancestor
 *  every writer in this cycle read.
 *
 *  `timer` is COUPLED: taken whole if the writer computed any part of it,
 *  dropped entirely if identical to the ancestor. `notify_data` is merged
 *  element-wise on (label, type), field by field: a field the writer left equal
 *  to the ancestor expresses no intent and is not imposed. (The Python also
 *  handles entries added or removed relative to the ancestor; the timer
 *  commands never do that, so the model does not.) */
function applyPatch(base: Control, blob: Control, patch: Control): Control {
  const next: Control = {
    timer: { ...blob.timer },
    notify_data: structuredClone(blob.notify_data),
  };
  if (JSON.stringify(patch.timer) !== JSON.stringify(base.timer)) {
    next.timer = { ...patch.timer };
  }
  const find = (entries: TimerNotify[], e: TimerNotify) =>
    entries.find((x) => x.label === e.label && x.type === e.type);
  for (const incoming of patch.notify_data) {
    const ancestor = find(base.notify_data, incoming);
    const target = find(next.notify_data, incoming);
    if (ancestor === undefined || target === undefined) continue;
    for (const key of ["req", "shutdown", "keep_warm"] as const) {
      if (incoming[key] !== ancestor[key]) target[key] = incoming[key];
    }
  }
  return next;
}

class ControlProcess {
  /** The persisted blob: what read_control() returns and what the socket ships. */
  blob: Control;
  /** Queued MERGE partials, drained once per control cycle. */
  private queue: Control[] = [];

  constructor(blob: Control) {
    this.blob = blob;
  }

  /** Port of common/api_commands.py _cmd_set_timer's start/pause/stop branches.
   *  Returns the API envelope common/app.py api_response produces. */
  post(url: string): { result: string; message: string; data: unknown } {
    const [, , action, sub, ...rest] = url.split("/");
    if (action !== "set" || sub !== "timer") throw new Error(`unmodelled command: ${url}`);

    // read_control(): the persisted blob, NOT the queue.
    const control = structuredClone(this.blob);
    const entry = control.notify_data.find((e) => e.type === "timer");
    if (entry === undefined) throw new Error("no timer notify entry");

    switch (rest[0]) {
      case "start":
        if (rest[2] !== undefined) {
          // The 4-argument form: server-computed end + both expiry flags, one write.
          if (control.timer.paused !== 0) {
            return { result: "ERROR", message: "Timer is paused.", data: {} };
          }
          entry.req = true;
          entry.shutdown = rest[2].split(",").includes("shutdown");
          entry.keep_warm = rest[2].split(",").includes("keep_warm");
          control.timer.start = NOW;
          control.timer.end = NOW + Number(rest[1]);
        } else {
          entry.req = true;
          if (control.timer.paused === 0) {
            control.timer.start = NOW;
            control.timer.end = NOW + Number(rest[1] ?? 60);
          } else {
            control.timer.end = control.timer.end - control.timer.paused + NOW;
            control.timer.paused = 0;
          }
        }
        break;
      case "pause":
        entry.req = false;
        if (control.timer.start !== 0) {
          control.timer.paused = NOW;
        } else {
          control.timer = { start: 0, paused: 0, end: 0 };
          entry.shutdown = false;
          entry.keep_warm = false;
        }
        break;
      case "stop":
        entry.req = false;
        control.timer = { start: 0, paused: 0, end: 0 };
        entry.shutdown = false;
        entry.keep_warm = false;
        break;
      default:
        throw new Error(`unmodelled timer command: ${url}`);
    }

    // write_control(control, MERGE): the whole dict goes on the queue.
    this.queue.push(control);
    return { result: "OK", message: "", data: {} };
  }

  /** A writer that reads control and queues the whole dict back without
   *  computing a timer state -- common/system.py gather_system_info's shape. */
  postWholeStaleDict(): void {
    this.queue.push(structuredClone(this.blob));
  }

  /** One turn of the control loop. The ancestor is captured ONCE, before the
   *  loop, exactly as execute_control_writes does. */
  drain(): void {
    const base = structuredClone(this.blob);
    for (const patch of this.queue) this.blob = applyPatch(base, this.blob, patch);
    this.queue = [];
  }

  timerEntry(): TimerNotify {
    const entry = this.blob.notify_data.find((e) => e.type === "timer");
    if (entry === undefined) throw new Error("no timer notify entry");
    return entry;
  }
}

/** The socket payload the shell renders from, derived from the persisted blob. */
function liveTimer(cp: ControlProcess): LiveState["timer"] {
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

  // The bug. Pause and Stop are on screen TOGETHER while a timer runs, and the
  // bar re-renders only when the socket republishes -- which cannot happen until
  // the control loop drains. So a second click inside that window is an ordinary
  // thing for a user to do, and it reads the pre-stop blob: the pause write
  // carries the timer's OLD start/end and lands last. See the seam tests at the
  // bottom of this file for what that costs when the guard is not there.
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

  it("keeps a stopped timer stopped when resume lands in the same cycle", async () => {
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

    expect(cp.blob.timer).toEqual({ start: 0, paused: 0, end: 0 });
    expect(cp.timerEntry().shutdown).toBe(false);
  });

  // The guard waits for the live timer to change, and a rejected command never
  // queues anything for the loop to publish -- so a failure has to release it
  // then and there or the bar is stranded until the next socket change.
  it("releases the guard when the command fails, rather than stranding the bar", async () => {
    const cp = new ControlProcess(seedControl({ start: NOW - 60, end: NOW + 600 }, { req: true }));
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
    );
    render(<TimerBar timer={liveTimer(cp)} command={createCommand("")} />);

    await click(/pause timer/i);
    const pause = screen.getByRole("button", { name: /pause timer/i }) as HTMLButtonElement;
    expect(pause.disabled).toBe(false);

    // ...and a retry is actually issued rather than swallowed.
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

// The guard's justification as an executable fact rather than a comment. These
// drive the model directly, with no UI, because the guard is precisely what
// stops the bar from producing these sequences -- so removing the guard would
// make these the bar's behaviour. Both are pinned against the real backend in
// tests/characterization/test_process_command_golden.py
// (test_a_pause_after_a_stop_in_one_cycle_resurrects_the_timer and
// test_a_resume_after_a_stop_in_one_cycle_resurrects_the_timer).
describe("the control-write seam this guard exists for", () => {
  it("resurrects a stopped countdown when a pause lands in the same cycle", () => {
    const cp = new ControlProcess(
      seedControl({ start: NOW - 60, end: NOW + 600 }, { req: true, shutdown: true }),
    );

    cp.post("/api/set/timer/stop");
    cp.post("/api/set/timer/pause");
    cp.drain();

    // The stop's zeros are gone: `timer` is coupled, so the pause's whole
    // (pre-stop) object is taken and lands last.
    expect(cp.blob.timer).toEqual({ start: NOW - 60, paused: NOW, end: NOW + 600 });
    // What does NOT come back is the expiry action -- the pause never touched
    // shutdown, so its stale copy is not imposed. That half the seam fix closed,
    // and it is the half that would otherwise shut the grill down on expiry.
    expect(cp.timerEntry().shutdown).toBe(false);
  });

  it("resurrects a stopped countdown when a resume lands in the same cycle", () => {
    const cp = new ControlProcess(
      seedControl(
        { start: NOW - 600, paused: NOW - 10, end: NOW + 590 },
        { req: true, shutdown: true },
      ),
    );

    cp.post("/api/set/timer/stop");
    cp.post("/api/set/timer/start/590");
    cp.drain();

    // Resume is the bare start form, which unpauses: it shifts `end` forward
    // from the pre-stop blob and clears `paused`.
    expect(cp.blob.timer).toEqual({ start: NOW - 600, paused: 0, end: NOW + 600 });
    expect(cp.timerEntry().shutdown).toBe(false);
  });

  it("keeps an unrelated writer from reverting a timer it never touched", () => {
    const cp = new ControlProcess(seedControl({ start: NOW - 60, end: NOW + 600 }, { req: true }));

    cp.post("/api/set/timer/pause");
    // A writer that queues the whole control dict without computing a timer
    // state -- the shape of every background write. Its `timer` is identical to
    // the ancestor, so it carries no evidence it touched one and is dropped.
    cp.postWholeStaleDict();
    cp.drain();

    expect(cp.blob.timer.paused).toBe(NOW);
  });
});
