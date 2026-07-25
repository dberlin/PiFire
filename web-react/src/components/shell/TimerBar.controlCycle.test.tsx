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
// The three properties modelled here are the ones that make a second write
// destructive, and all three are real (verified against the Python in a
// throwaway pytest run before this file was written):
//
//  1. A web-process write queues the WHOLE control dict, not a diff
//     (common/datastore_accessors.py write_control, WriteKind.MERGE).
//  2. read_control() serves the PERSISTED blob and never the pending queue,
//     so two commands issued inside one control cycle both read pre-write state.
//  3. execute_control_writes applies each queued dict with SQLite json_patch --
//     RFC 7396, which merges objects key-by-key but REPLACES arrays wholesale.
//
// Together: whichever write is queued LAST wins outright, including for keys it
// never meant to touch. `notify_data` is an array, so the timer's expiry flags
// are replaced; `timer` is an object, but every partial carries all three of its
// keys, so it is effectively replaced too.
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

/** json_patch(blob, partial) for the two keys the timer commands write.
 *  `timer` is a JSON object -> merged key by key. `notify_data` is a JSON ARRAY
 *  -> replaced outright, which is the whole reason a second write clobbers. */
function applyPatch(blob: Control, partial: Control): Control {
  return {
    timer: { ...blob.timer, ...partial.timer },
    notify_data: structuredClone(partial.notify_data),
  };
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

  /** One turn of the control loop. */
  drain(): void {
    for (const partial of this.queue) this.blob = applyPatch(this.blob, partial);
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
  // carries the timer's OLD start/end and the OLD expiry flags, and lands last.
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
