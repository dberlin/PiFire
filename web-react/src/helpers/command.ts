import { postControl } from "./notify/notifyApi";

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
  //  - timerStop clears the timer AND resets shutdown/keep_warm to False. Do
  //    NOT "restore" them afterwards with timerShutdown/timerKeepWarm: nothing
  //    needs restoring -- the next arm carries both flags itself -- and a
  //    standalone flag write only arms an expiry action on a stopped timer.
  //  - A non-numeric `seconds` makes the backend silently substitute 60s.
  //  - Each of these is ONE control write, and two of them in one control cycle
  //    compose. They queue an intent OP the drain evaluates against live state
  //    rather than a timer value computed from a read that cannot see the
  //    queue (common/control_delta.py), so stop-then-pause leaves the timer
  //    stopped instead of resurrecting the countdown
  //    (tests/characterization/test_control_delta_seam.py::
  //    test_stop_then_pause_in_one_cycle_leaves_the_timer_stopped). TimerBar
  //    used to guard its buttons against that pair; it no longer needs to.
  timerStart(seconds: number): Promise<CommandResult>;
  // Arms a NEW timer for a DURATION, together with its expiry flags, in a
  // single request -- see the block above createCommand for the two properties
  // that buys (a server-computed end, and rejections the bare form does not
  // make). Unlike timerStart this one does NOT unpause: the server rejects a
  // paused timer rather than silently ignoring the duration. Resuming is still
  // timerStart(), which carries no flags.
  timerStartWithOptions(seconds: number, options: TimerOptions): Promise<CommandResult>;
  timerPause(): Promise<CommandResult>;
  timerStop(): Promise<CommandResult>;
  // Standalone flag writes. NOTHING in this app calls them, and nothing should:
  // on their own they set an expiry action on a timer that is not armed. They
  // used to be actively destructive next to another timer write --
  // /api/set/timer/start/600 followed by /api/set/timer/shutdown/true inside one
  // control cycle left start/paused/end all ZERO -- which is now fixed at the
  // SOURCE: timer writes queue an intent OP the server evaluates against live
  // state (common/control_delta.py), so two of them in one cycle compose
  // (tests/characterization/test_process_command_golden.py::
  // test_a_flag_write_after_a_start_in_one_cycle_no_longer_destroys_the_timer).
  // Use timerStartWithOptions anyway, because the server computes the end and
  // rejects durations the bare form would silently substitute. They stay on the
  // interface because the REST paths stay (the Flask dashboard and mobile still
  // use them) and command.ts is this app's map of that grammar.
  timerShutdown(on: boolean): Promise<CommandResult>;
  timerKeepWarm(on: boolean): Promise<CommandResult>;
  system(cmd: SystemCmd): Promise<CommandResult>;
  setUnits(units: "F" | "C"): Promise<CommandResult>;
  manualOutput(output: ManualOutput, action?: "toggle" | "true" | "false"): Promise<CommandResult>;
  manualPwm(duty: number): Promise<CommandResult>;
  // The one write with no /api/set/... grammar behind it. It goes through
  // POST /api/control, which answers lowercase "success" rather than the "OK"
  // this module's post() tests for -- see controlPatch below.
  /** Advance a running recipe to its next step (control_panel.js:530). */
  recipeNextStep(): Promise<CommandResult>;
}

/** Bridge POST /api/control into this module's CommandResult envelope.
 *  The fetch itself lives in helpers/notify/notifyApi.ts, which already carries
 *  the two landmines that path has: the response says lowercase "success", and
 *  the server queues the WHOLE posted object as a merge patch. One
 *  implementation, not two. */
async function controlPatch(
  baseUrl: string,
  patch: Record<string, unknown>,
): Promise<CommandResult> {
  try {
    await postControl(baseUrl, patch);
    return { ok: true, message: "" };
  } catch (e) {
    return { ok: false, message: e instanceof Error ? e.message : "network error" };
  }
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
// Arming a timer: /api/set/timer/start/{seconds}/{options}
//
// Two properties this shape buys, neither of which the obvious alternative
// (read control, patch it, write it back) does:
//
//  1. The DURATION travels, never an absolute end time. The control process
//     decides a timer has expired by comparing control.timer.end against its
//     OWN time.time(), so an end computed here would be a value from a
//     different clock -- a browser running behind the Pi would arm an
//     already-expired timer, and an expired timer with "Shutdown Grill" ticked
//     shuts the grill down mid-cook. The server does the arithmetic instead,
//     which also removes the need to learn the server's clock from a response
//     header (Date is not CORS-safelisted, so cross-origin it is unreadable).
//
//  2. The server validates what the bare `start` form does not: a non-numeric
//     duration is rejected rather than silently substituted with 60s, zero and
//     negative durations are rejected (an already-expired timer fires its
//     expiry action the moment it is armed), and a paused timer is rejected
//     rather than unpaused with the duration thrown away.
//
// It was ALSO built to force ONE write_control() on the server, because the
// countdown (control.timer) and the flags (control.notify_data) split across
// two requests in one control cycle used to lose the first. That reason is
// gone, at both ends: every timer write now queues a named OP that the server
// evaluates at DRAIN time against live state rather than a timer state computed
// from a stale read (common/control_delta.py), so two timer writes in one cycle
// compose instead of racing. The form is kept on the two reasons above, which
// have nothing to do with the write seam.
// --------------------------------------------------------------------------

/** Name the ticked flags for the option segment of the start command; 'none'
 *  when neither is ticked. A path segment cannot be empty -- an empty one
 *  collapses the URL to the 3-argument form, which leaves both flags at
 *  whatever the previous cook set (see common/api_commands.py
 *  _parse_timer_expiry_options). */
function timerExpirySegment(options: TimerOptions): string {
  const named: string[] = [];
  if (options.shutdown) named.push("shutdown");
  if (options.keepWarm) named.push("keep_warm");
  return named.length > 0 ? named.join(",") : "none";
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
    // Whole seconds: the backend parses this segment with is_float() and, on
    // this form, rejects anything non-numeric or not greater than zero rather
    // than substituting 60s the way the 3-argument form does.
    timerStartWithOptions: (seconds, options) =>
      post(baseUrl, ["set", "timer", "start", Math.round(seconds), timerExpirySegment(options)]),
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
    // `updated: true` alone is what Flask's "Goto Next Step" sends
    // (control_panel.js:530); the control loop reads it as "re-evaluate the
    // recipe now". Deliberately nothing else in the patch -- POST /api/control
    // merges the whole posted object, so any extra key is a value patched back
    // over whatever the control loop set meanwhile.
    recipeNextStep: () => controlPatch(baseUrl, { updated: true }),
    // No hopperCheck here on purpose. Flask posts hopper_check and re-reads
    // GET /api/hopper 500ms later (dash_default.js:878-897); this UI has no
    // Refresh Status button to post it from, because the control loop refreshes
    // the level every ~10s and the socket pushes hopperLevel with every frame
    // (socket_io.py:258). The backend flag itself still exists for the attached
    // display and the Flask pages -- see HopperGauge.tsx.
  };
}
