import type { CommandClient, CommandResult, ManualOutput } from "../command";
import type { LiveState } from "../types";

export type ButtonAction =
  | { type: "command"; run(c: CommandClient): Promise<CommandResult> }
  | { type: "setpoint" }
  | { type: "pwm" }
  | { type: "confirm"; title: string; run(c: CommandClient): Promise<CommandResult> };

export interface ControlButton {
  label: string;
  variant?: "accent" | "danger";
  action: ButtonAction;
}

const cmd = (run: (c: CommandClient) => Promise<CommandResult>): ButtonAction => ({
  type: "command",
  run,
});
const confirm = (
  title: string,
  run: (c: CommandClient) => Promise<CommandResult>,
): ButtonAction => ({ type: "confirm", title, run });

const STOP: ControlButton = {
  label: "Stop",
  variant: "danger",
  action: confirm("Stop the cook?", (c) => c.setMode("stop")),
};
const STARTUP: ControlButton = {
  label: "Startup",
  variant: "accent",
  action: cmd((c) => c.setMode("startup")),
};

export function buttonsForMode(dash: LiveState): ControlButton[] {
  const mode = dash.currentMode;

  if (mode === "Stop" || mode === "Error" || mode === "") {
    return [
      STARTUP,
      { label: "Prime", action: cmd((c) => c.prime(dash.primeAmount || 10, "startup")) },
      { label: "Monitor", action: cmd((c) => c.setMode("monitor")) },
      { label: "Manual", action: cmd((c) => c.setMode("manual")) },
    ];
  }

  if (mode === "Monitor") {
    return [STARTUP, STOP];
  }

  // Manual mode: the button row becomes the output control panel, mirroring
  // legacy's control-panel buttons (accent == relay energised, matching its
  // btn-primary vs btn-outline-primary styling). Shown ONLY in Manual mode --
  // legacy hides these outside it even when safety.allow_manual_changes is set.
  if (mode === "Manual") {
    const toggle = (label: string, output: ManualOutput, live: boolean): ControlButton => ({
      label,
      variant: live ? "accent" : undefined,
      action: cmd((c) => c.manualOutput(output)),
    });
    return [
      toggle("Power", "power", dash.outputs.power),
      toggle("Igniter", "igniter", dash.outputs.igniter),
      toggle("Auger", "auger", dash.outputs.auger),
      toggle("Fan", "fan", dash.outputs.fan),
      ...(dash.hasDcFan ? [{ label: "Fan %", action: { type: "pwm" } as ButtonAction }] : []),
      STOP,
    ];
  }

  // Active cook modes (Startup / Smoke / Hold / Prime / Reignite / Shutdown).
  return [
    { label: "Smoke", action: cmd((c) => c.setMode("smoke")) },
    { label: "Hold", variant: "accent", action: { type: "setpoint" } },
    {
      label: "Smoke+",
      variant: dash.smokePlus ? "accent" : undefined,
      action: cmd((c) => c.setSmokePlus(!dash.smokePlus)),
    },
    { label: "Shutdown", action: confirm("Shut down the grill?", (c) => c.setMode("shutdown")) },
    STOP,
  ];
}
