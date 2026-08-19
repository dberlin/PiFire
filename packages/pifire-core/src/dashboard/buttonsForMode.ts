import type { CommandClient, CommandResult } from "../command";
import type { ManualOutput } from "../contracts/control.gen";
import type { DashSocketPayload } from "../contracts/core.gen";

export type ButtonAction =
  | { type: "command"; run(c: CommandClient): Promise<CommandResult> }
  | { type: "setpoint" }
  | { type: "pwm" }
  // Ignition behind Flask's startup modal. WHICH of its two variants to show is
  // a presentation decision, so this carries only the intent.
  | { type: "startup" }
  // A pick-one list (ActionMenu). `run` receives the picked item's value, so
  // the menu itself stays a dumb list of labels.
  | {
      type: "menu";
      title: string;
      items: MenuItem[];
      run(c: CommandClient, value: string): Promise<CommandResult>;
    }
  | { type: "confirm"; title: string; run(c: CommandClient): Promise<CommandResult> };

export interface MenuItem {
  label: string;
  value: string;
}

export interface ControlButton {
  label: string;
  /** `accent` is the row's "this is the mode you are in" mark -- the display's
   *  button_active (display/_base_flex.py:523). `primary` is "the way in from
   *  here", which is a different claim and so gets a different look: a fill
   *  rather than a lit border. Sharing one look made an always-primary Startup
   *  read as a second active mode. */
  variant?: "accent" | "danger" | "primary";
  /** Set when the button names a state the grill is already in and pressing it
   *  could only ask for something the controller's transition graph refuses.
   *  Distinct from the row-wide dimming, which tracks whether the control
   *  process is reachable and never withholds an exit. */
  disabled?: boolean;
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

// Flask's Prime dropup: six items, three amounts x two follow-on modes
// (_macro_control_panel.html:80-85, control_panel.js:65-73). React had reduced
// this to a single button that always primed dash.primeAmount and always went
// on to Startup -- so "prime and stop", the variant you use when loading a
// fresh bag, was unreachable.
const PRIME_GRAMS = [10, 25, 50];
const PRIME_MENU: MenuItem[] = [
  ...PRIME_GRAMS.map((g) => ({ label: `Prime ${g}g`, value: `${g}:stop` })),
  ...PRIME_GRAMS.map((g) => ({ label: `Prime ${g}g & Startup`, value: `${g}:startup` })),
];

const PRIME: ControlButton = {
  label: "Prime",
  action: {
    type: "menu",
    title: "Prime",
    items: PRIME_MENU,
    run: (c, value) => {
      const [grams, next] = value.split(":");
      // Omission is the API's explicit "prime then Stop" form. Only Startup
      // and Monitor are recognized next-mode path values.
      return c.prime(Number(grams), next === "startup" ? "startup" : undefined);
    },
  },
};

const STOP: ControlButton = {
  label: "Stop",
  variant: "danger",
  action: confirm("Stop the cook?", (c) => c.setMode("stop")),
};
// Flask gates ignition behind #startupModal whenever
//   safety.startup_check OR (start_to_hold_prompt AND after_startup_mode == 'Hold')
// (_macro_control_panel.html:89-90). Both halves matter: the hold prompt alone
// is not enough -- it only fires when the configured post-startup mode is Hold.
const startupButton = (dash: DashSocketPayload): ControlButton => ({
  label: "Startup",
  // Primary, never accent: lighting a fire is what this row is FOR, but it is
  // not a mode you are in. Carrying accent put a second lit border next to the
  // real one, so Monitor read as "monitoring, and also starting up".
  variant: "primary",
  action:
    dash.startupCheck || (dash.startToHoldPrompt && dash.startupGotoMode === "Hold")
      ? { type: "startup" }
      : cmd((c) => c.setMode("startup")),
});

// Nothing is lit in Monitor, so leaving it has no cook to lose and does not
// earn a confirmation step. The attached display's Stop is unconfirmed in every
// mode (display/_base_flex.py routes cmd_stop straight through); this UI keeps
// the confirmation where it means something -- an active cook.
const STOP_IDLE: ControlButton = {
  label: "Stop",
  variant: "danger",
  action: cmd((c) => c.setMode("stop")),
};

// The idle row: the ways INTO a cook, plus the mode you are in.
//
// Monitor shares this row with Stop rather than collapsing to Startup/Stop. The
// attached display does the same -- display/_base_flex.py:523 falls through to
// one "Prime / Startup / Monitor / Stop" list for Stop, Prime AND Monitor, and
// marks the running mode via `button_active` -- so in both UIs the row reads as
// a mode selector rather than changing shape underneath the press.
const idleRow = (dash: DashSocketPayload, mode: string): ControlButton[] => {
  const monitoring = mode === "Monitor";
  return [
    startupButton(dash),
    { ...PRIME, variant: mode === "Prime" ? "accent" : undefined },
    {
      label: "Monitor",
      // Same "this one is active" idiom the Manual row uses for an energised
      // relay, which is what the display paints green via button_active.
      variant: monitoring ? "accent" : undefined,
      // Pressing the mode you are already in leaves it. The display has no such
      // toggle (its cmd_monitor unconditionally sets Monitor), but the row it
      // draws looks like one, and without it the only way out of Monitor is a
      // Stop button that reads as "stop the cook" for a mode that never lit
      // anything.
      action: cmd((c) => c.setMode(monitoring ? "stop" : "monitor")),
    },
    { label: "Manual", action: cmd((c) => c.setMode("manual")) },
    // Every idle mode that is not already stopped needs a way out of itself:
    // Monitor and Prime are running, and Error is latched until something
    // clears it. Stop is the one mode where the button would say nothing.
    ...(mode === "Stop" || mode === "" ? [] : [STOP_IDLE]),
  ];
};

export function buttonsForMode(dash: DashSocketPayload): ControlButton[] {
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
        // Flask's one button branches the same way (control_panel.js:520-533): a
        // paused step ignores {updated:true}, so it must have its `pause` flag
        // cleared (recipeUnpause) to move on; an unpaused step just advances.
        action: cmd((c) => (dash.recipeStatus?.paused ? c.recipeUnpause() : c.recipeNextStep())),
      },
      { label: "Shutdown", action: confirm("Shut down the grill?", (c) => c.setMode("shutdown")) },
      STOP,
    ];
  }

  // Prime belongs here, not with the cook modes: it runs before there is a
  // fire, so Smoke and Hold would drive a cycle over an unlit firepot, and the
  // cook row hides Startup -- the normal next step out of a prime. Both
  // displays classify it the same way (display/_base_flex.py:524).
  if (
    mode === "Stop" ||
    mode === "Error" ||
    mode === "" ||
    mode === "Monitor" ||
    mode === "Prime"
  ) {
    return idleRow(dash, mode);
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
  //
  // Smoke and Hold each carry accent only in their own mode, the same way the
  // idle row marks Monitor. Smoke additionally goes inert while smoking: the
  // controller's graph has no Smoke -> Smoke edge, so the press could only ask
  // for a transition the controller refuses. Hold stays live while holding,
  // because it opens the setpoint entry rather than commanding a mode.
  return [
    {
      label: "Smoke",
      variant: mode === "Smoke" ? "accent" : undefined,
      disabled: mode === "Smoke" ? true : undefined,
      action: cmd((c) => c.setMode("smoke")),
    },
    {
      label: "Hold",
      variant: mode === "Hold" ? "accent" : undefined,
      action: { type: "setpoint" },
    },
    {
      label: "Smoke+",
      variant: dash.smokePlus ? "accent" : undefined,
      action: cmd((c) => c.setSmokePlus(!dash.smokePlus)),
    },
    { label: "Shutdown", action: confirm("Shut down the grill?", (c) => c.setMode("shutdown")) },
    STOP,
  ];
}
