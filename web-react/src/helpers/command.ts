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

export function createCommand(baseUrl: string): CommandClient {
  return {
    setMode: (mode) => post(baseUrl, ["set", "mode", mode]),
    hold: (tempF) => post(baseUrl, ["set", "psp", Math.round(tempF)]),
    setSmokePlus: (on) => post(baseUrl, ["set", "splus", on ? "true" : "false"]),
    setPMode: (n) => post(baseUrl, ["set", "pmode", n]),
    prime: (grams, next) =>
      post(baseUrl, next ? ["set", "mode", "prime", grams, next] : ["set", "mode", "prime", grams]),
    timerStart: (seconds) => post(baseUrl, ["set", "timer", "start", seconds]),
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
