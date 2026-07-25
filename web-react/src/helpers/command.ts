// REST command client using PiFire's command grammar (common/api_commands.py
// _COMMAND_DISPATCH) via blueprints/api/routes.py. Writes only; live reads come
// over the socket. Envelope: { result, message, data } (common/app.py api_response).

export type GrillMode =
  | "startup"
  | "smoke"
  | "shutdown"
  | "stop"
  | "monitor"
  | "reignite"
  | "manual";
export type SystemCmd = "reboot" | "shutdown" | "restart";
export type ManualOutput = "power" | "igniter" | "auger" | "fan";

export interface CommandResult {
  ok: boolean;
  message: string;
  data?: unknown;
}

/** The two "when the timer expires" flags, which live in control.notify_data. */
export interface TimerOptions {
  shutdown: boolean;
  keepWarm: boolean;
}

export interface CommandClient {
  setMode(mode: GrillMode): Promise<CommandResult>;
  hold(tempF: number): Promise<CommandResult>;
  setSmokePlus(on: boolean): Promise<CommandResult>;
  setPMode(n: number): Promise<CommandResult>;
  prime(grams: number, next?: GrillMode): Promise<CommandResult>;
  // NOTE (verified against common/api_commands.py _cmd_set_timer):
  //  - timerStart is ALSO the unpause command: when control.timer.paused != 0
  //    the backend ignores `seconds` and just shifts the existing end time.
  //  - timerPause CLEARS the whole timer (and the shutdown/keep_warm flags)
  //    when the timer was never started (timer.start == 0).
  //  - timerStop clears the timer AND resets shutdown/keep_warm to False, so
  //    those flags must be re-sent after any stop.
  //  - A non-numeric `seconds` makes the backend silently substitute 60s.
  timerStart(seconds: number): Promise<CommandResult>;
  // Arms a NEW timer together with its expiry flags in a single control write.
  // The flags cannot be sent as their own /api/set/timer/... call -- see
  // timerStartWithOptions below for why. Resuming a paused timer is still
  // timerStart(): it carries no flags and is therefore a single write already.
  timerStartWithOptions(seconds: number, options: TimerOptions): Promise<CommandResult>;
  timerPause(): Promise<CommandResult>;
  timerStop(): Promise<CommandResult>;
  timerShutdown(on: boolean): Promise<CommandResult>;
  timerKeepWarm(on: boolean): Promise<CommandResult>;
  system(cmd: SystemCmd): Promise<CommandResult>;
  setUnits(units: "F" | "C"): Promise<CommandResult>;
  manualOutput(output: ManualOutput, action?: "toggle" | "true" | "false"): Promise<CommandResult>;
  manualPwm(duty: number): Promise<CommandResult>;
}

export function buildCommandUrl(baseUrl: string, segments: (string | number)[]): string {
  return `${baseUrl}/api/${segments.map((s) => String(s)).join("/")}`;
}

async function post(baseUrl: string, segments: (string | number)[]): Promise<CommandResult> {
  try {
    const res = await fetch(buildCommandUrl(baseUrl, segments), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
    const body = (await res.json()) as { result?: string; message?: string; data?: unknown };
    return { ok: body.result === "OK", message: body.message ?? "", data: body.data };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "network error" };
  }
}

// --------------------------------------------------------------------------
// Whole-control writes (POST /api/control).
//
// blueprints/api/routes.py answers this route with {"result": "success"} --
// LOWERCASE -- while the /api/set/... command grammar answers {"result": "OK"}.
// Two envelopes from the same blueprint, so the control write gets its own
// success predicate. post() above keeps requiring "OK" because every /api/set/
// caller depends on that, and a route that answered "success" there would be a
// genuine surprise worth reporting as a failure.
// --------------------------------------------------------------------------
const CONTROL_WRITE_RESULT = "success";

interface ControlNotifyEntry {
  type?: string;
  [k: string]: unknown;
}

interface ControlBlob {
  notify_data?: ControlNotifyEntry[];
  [k: string]: unknown;
}

type ControlRead =
  | { ok: true; control: ControlBlob; serverNow: number }
  | { ok: false; message: string };

/**
 * Epoch seconds as the CONTROL PROCESS sees them.
 *
 * The control process decides a timer has expired by comparing control.timer.end
 * against its own time.time(), so an end computed from a browser clock running
 * behind the Pi's would arm an already-expired timer -- and an expired timer
 * with "Shutdown Grill" ticked shuts the grill down mid-cook. The response's
 * Date header is the server's own clock, so it is preferred. It is unreadable
 * cross-origin (Date is not on the CORS-safelist), and then the browser clock is
 * all that is left.
 */
function serverNowSeconds(headers: Headers | undefined): number {
  const raw = headers?.get("Date");
  const parsed = raw === null || raw === undefined ? Number.NaN : Date.parse(raw);
  return Math.floor((Number.isNaN(parsed) ? Date.now() : parsed) / 1000);
}

async function getControl(baseUrl: string): Promise<ControlRead> {
  try {
    const res = await fetch(`${baseUrl}/api/control`, { method: "GET" });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
    const body = (await res.json()) as { control?: ControlBlob };
    if (!body.control) return { ok: false, message: "no control in /api/control response" };
    return { ok: true, control: body.control, serverNow: serverNowSeconds(res.headers) };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "network error" };
  }
}

async function postControl(
  baseUrl: string,
  patch: Record<string, unknown>,
): Promise<CommandResult> {
  try {
    const res = await fetch(`${baseUrl}/api/control`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) return { ok: false, message: `HTTP ${res.status}` };
    const body = (await res.json()) as { result?: string; message?: string };
    return { ok: body.result === CONTROL_WRITE_RESULT, message: body.message ?? "" };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "network error" };
  }
}

/**
 * Arm a new timer and its expiry flags in ONE control write.
 *
 * Why not three tidy /api/set/timer/... calls: the flags live in
 * control.notify_data (an array) and the countdown in control.timer, and every
 * web-process write is a MERGE queued as the WHOLE control dict
 * (common/datastore_accessors.py write_control). read_control() reads only the
 * persisted blob and never the pending queue, and only the control loop drains
 * that queue. So calls issued inside one control cycle all read the same stale
 * blob, and execute_control_writes applies each with SQLite json_patch --
 * RFC 7396, which REPLACES arrays wholesale. The last write's notify_data
 * therefore overwrites the flags the earlier ones set, every time.
 *
 * Hence: read control once, patch the timer's notify entry and the timer block
 * in that snapshot, post it back as a single merge -- the shape the Flask
 * dashboard already uses for notification edits (dash_default.js). notify_data
 * goes back WHOLE for the same json_patch reason: a partial array would delete
 * every probe notification the user has armed.
 */
async function timerStartWithOptions(
  baseUrl: string,
  seconds: number,
  options: TimerOptions,
): Promise<CommandResult> {
  const read = await getControl(baseUrl);
  if (!read.ok) return { ok: false, message: `timer start failed: ${read.message}` };

  const notifyData = read.control.notify_data;
  if (!Array.isArray(notifyData)) {
    return { ok: false, message: "timer start failed: control.notify_data missing" };
  }
  const index = notifyData.findIndex((entry) => entry?.type === "timer");
  if (index < 0) {
    return { ok: false, message: "timer start failed: no timer entry in control.notify_data" };
  }

  const now = read.serverNow;
  return postControl(baseUrl, {
    notify_data: notifyData.map((entry, i) =>
      i === index
        ? { ...entry, req: true, shutdown: options.shutdown, keep_warm: options.keepWarm }
        : entry,
    ),
    // Mirrors common/api_commands.py _cmd_set_timer's start branch. `paused` is
    // reset explicitly: a timer armed from the modal is always a fresh one.
    timer: { start: now, end: now + seconds, paused: 0 },
  });
}

export function createCommand(baseUrl: string): CommandClient {
  return {
    setMode: (mode) => post(baseUrl, ["set", "mode", mode]),
    hold: (tempF) => post(baseUrl, ["set", "psp", Math.round(tempF)]),
    setSmokePlus: (on) => post(baseUrl, ["set", "splus", on ? "true" : "false"]),
    setPMode: (n) => post(baseUrl, ["set", "pmode", n]),
    prime: (grams, next) =>
      post(baseUrl, next ? ["set", "mode", "prime", grams, next] : ["set", "mode", "prime", grams]),
    timerStart: (seconds) => post(baseUrl, ["set", "timer", "start", seconds]),
    timerStartWithOptions: (seconds, options) => timerStartWithOptions(baseUrl, seconds, options),
    timerPause: () => post(baseUrl, ["set", "timer", "pause"]),
    timerStop: () => post(baseUrl, ["set", "timer", "stop"]),
    // The backend compares the raw path segment against the string "true"
    // (arglist[2] == "true"), so booleans must serialize as literal true/false.
    timerShutdown: (on) => post(baseUrl, ["set", "timer", "shutdown", on ? "true" : "false"]),
    timerKeepWarm: (on) => post(baseUrl, ["set", "timer", "keep_warm", on ? "true" : "false"]),
    system: (cmd) => post(baseUrl, ["cmd", cmd]),
    setUnits: (units) => post(baseUrl, ["set", "units", units]),
    manualOutput: (output, action = "toggle") => post(baseUrl, ["set", "manual", output, action]),
    // The backend takes an integer 0-100; clamp here so a slider/entry can't
    // send an out-of-range duty that the API would reject.
    manualPwm: (duty) =>
      post(baseUrl, ["set", "manual", "pwm", Math.min(100, Math.max(0, Math.round(duty)))]),
  };
}
