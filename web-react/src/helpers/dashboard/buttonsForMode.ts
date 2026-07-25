import type { CommandClient, CommandResult, ManualOutput } from "../command";
import type { LiveState } from "../types";

export type ButtonAction =
  | { type: "command"; run(c: CommandClient): Promise<CommandResult> }
  | { type: "setpoint" }
  | { type: "pwm" }
  // Ignition behind Flask's startup modal. WHICH of its two variants to show is
  // a presentation decision, so this carries only the intent.
  | { type: "startup" }
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
// Flask gates ignition behind #startupModal whenever
//   safety.startup_check OR (start_to_hold_prompt AND after_startup_mode == 'Hold')
// (_macro_control_panel.html:89-90). Both halves matter: the hold prompt alone
// is not enough -- it only fires when the configured post-startup mode is Hold.
const startupButton = (dash: LiveState): ControlButton => ({
  label: "Startup",
  variant: "accent",
  action:
    dash.startupCheck || (dash.startToHoldPrompt && dash.startupGotoMode === "Hold")
      ? { type: "startup" }
      : cmd((c) => c.setMode("startup")),
});

export function buttonsForMode(dash: LiveState): ControlButton[] {
  const mode = dash.currentMode;

  // A running recipe drives the mode itself, so the ordinary ladder below would
  // offer Smoke / Hold / Smoke+ -- the exact controls Flask HIDES during a
  // recipe (control_panel.js:181-182 hides #active_group AND #inactive_group),
  // because pressing them breaks out of it. This branch comes first for the
  // same reason Flask's does.
  //
  // Gated on recipeStatus.recipeMode, not on a string compare against
  // currentMode: the boolean is what the controller publishes
  // (controller/runtime/modes/base.py:478) and the mode string is a second-hand
  // copy that reads as the running SUB-mode in some frames.
  if (dash.recipeStatus?.recipeMode) {
    return [
      {
        label: "Next Step",
        // Flask makes this a glowbutton while the recipe is paused waiting on a
        // trigger (control_panel.js:207-217).
        variant: dash.recipeStatus.paused ? "accent" : undefined,
        action: cmd((c) => c.recipeNextStep()),
      },
      { label: "Shutdown", action: confirm("Shut down the grill?", (c) => c.setMode("shutdown")) },
      STOP,
    ];
  }

  if (mode === "Stop" || mode === "Error" || mode === "") {
    return [
      startupButton(dash),
      { label: "Prime", action: cmd((c) => c.prime(dash.primeAmount || 10, "startup")) },
      { label: "Monitor", action: cmd((c) => c.setMode("monitor")) },
      { label: "Manual", action: cmd((c) => c.setMode("manual")) },
    ];
  }

  if (mode === "Monitor") {
    return [startupButton(dash), STOP];
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
