import { useState } from "react";
import type { CommandClient, CommandResult } from "../../helpers/command";
import {
  type ButtonAction,
  buttonsForMode,
  type MenuItem,
} from "../../helpers/dashboard/buttonsForMode";
import { applySettings } from "../../helpers/settings/settingsApi";
import type { LiveState } from "../../helpers/types";
import { ActionMenu } from "./ActionMenu";
import { ConfirmAction } from "./ConfirmAction";
import { PwmEntry } from "./PwmEntry";
import { SetpointEntry } from "./SetpointEntry";

/** The two ways out of a running cook. These are never withheld. */
const SAFETY_LABELS = new Set(["Stop", "Shutdown"]);

/** Flask's startup hold-prompt slider bounds (_macro_control_panel.html:236,238).
 *  Wider than SETPOINT_RANGE, which governs the Hold setpoint. */
const HOLD_PROMPT_RANGE: Record<"F" | "C", { min: number; max: number }> = {
  F: { min: 125, max: 600 },
  C: { min: 50, max: 260 },
};

/** Which variant of Flask's single #startupModal is on screen, if any. */
type StartupPrompt = "none" | "hold" | "confirm";

/** The row's marked states. `accent` is a lit BORDER and `primary` a FILL, and
 *  the difference is the point: accent says "this is the mode you are in" and
 *  primary says "this is the way in". The fill repeats `.pf-modal-btn.accent`
 *  (dashboard.css:302-306) so the surface has one primary-action look. */
const VARIANT_STYLE = {
  primary: { border: "transparent", bg: "var(--accent)", color: "#1a0f04" },
  accent: {
    border: "var(--accent)",
    bg: "color-mix(in srgb, var(--accent) 16%, transparent)",
    color: "#e8dfd1",
  },
  danger: {
    border: "var(--danger)",
    bg: "color-mix(in srgb, var(--danger) 14%, transparent)",
    color: "var(--danger)",
  },
  plain: { border: "rgba(255,255,255,0.14)", bg: "var(--inset)", color: "#e8dfd1" },
} as const;

// Mode-driven control row, styled to the design's button grid. Each press
// dispatches a `ButtonAction` (command / setpoint / confirm / startup) against
// the REST `CommandClient` (see buttonsForMode.ts / command.ts).
export function ControlButtons({
  dash,
  command,
  disabled,
  apiBase,
}: {
  dash: LiveState;
  command: CommandClient;
  disabled: boolean;
  /** Same-origin API base for the settings write the hold prompt makes. NOT the
   *  human-readable origin ConnectionStatus displays -- fetching that one sends
   *  the browser cross-origin. */
  apiBase: string;
}) {
  const buttons = buttonsForMode(dash);
  const [setpointOpen, setSetpointOpen] = useState(false);
  const [pwmOpen, setPwmOpen] = useState(false);
  const [confirm, setConfirm] = useState<{
    title: string;
    run(c: CommandClient): Promise<CommandResult>;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [menu, setMenu] = useState<{
    title: string;
    items: MenuItem[];
    run(c: CommandClient, value: string): Promise<CommandResult>;
  } | null>(null);
  // Derived when the button is pressed, never mirrored from `dash` in an effect.
  const [startupPrompt, setStartupPrompt] = useState<StartupPrompt>("none");
  const [startupError, setStartupError] = useState<string | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);
  // Which button is waiting for the grill to catch up, and what the mode was
  // when it was pressed. A command's effect is not visible until the control
  // loop has applied it and the next socket frame carries the result, which is
  // most of a second at best and several seconds across a mode change (the
  // transition drives the output relays and can write a cookfile). Without this
  // the press produces no acknowledgement at all and the row simply changes
  // shape later, so the natural read is that nothing happened.
  const [pending, setPending] = useState<{ label: string; mode: string } | null>(null);

  // Render-phase transition, not an effect: the awaited command has landed once
  // the mode moves off what it was at press time.
  if (pending !== null && pending.mode !== dash.currentMode) setPending(null);

  const fire = async (run: (c: CommandClient) => Promise<CommandResult>, label?: string) => {
    setBusy(true);
    setCommandError(null);
    if (label !== undefined) setPending({ label, mode: dash.currentMode });
    try {
      const res = await run(command);
      if (!res.ok) {
        // Previously discarded. A rejected command -- or a 500 from a settings
        // write that cannot validate -- looked exactly like a successful one.
        setCommandError(res.message || "the grill did not accept that command");
        setPending(null);
      }
    } finally {
      setBusy(false);
    }
  };

  const onClick = (action: ButtonAction, label?: string) => {
    if (action.type === "command") fire(action.run, label);
    else if (action.type === "setpoint") setSetpointOpen(true);
    else if (action.type === "pwm") setPwmOpen(true);
    else if (action.type === "menu")
      setMenu({ title: action.title, items: action.items, run: action.run });
    else if (action.type === "startup") {
      // ONE modal with two Jinja-selected variants, and the hold prompt WINS
      // when both are configured (_macro_control_panel.html:214,224 test the
      // same condition twice). There is no path that shows both.
      setStartupError(null);
      setStartupPrompt(
        dash.startToHoldPrompt && dash.startupGotoMode === "Hold" ? "hold" : "confirm",
      );
    } else setConfirm({ title: action.title, run: action.run });
  };

  // Flask fires the settings write and the mode change as two unchained
  // $.ajax calls (control_panel.js:421-427) -- a genuine race: the grill can
  // start before the hold target is recorded. Awaiting the write closes it, and
  // a FAILED write does not ignite. Lighting a grill that just failed to record
  // its hold target is the wrong failure mode.
  const startupWithHold = async (temp: number) => {
    setBusy(true);
    setStartupError(null);
    try {
      const res = await applySettings(
        apiBase,
        {
          startup: {
            start_to_mode: {
              after_startup_mode: "Hold",
              start_to_hold_prompt: true,
              primary_setpoint: temp,
            },
          },
        },
        ["settings_update"],
      );
      if (!res.ok) {
        setStartupError(res.message || "could not save the hold temperature");
        return;
      }
      setStartupPrompt("none");
      await command.setMode("startup");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-pf="controls" className="pf-dash-controls">
      {buttons.map((b) => {
        const look = VARIANT_STYLE[b.variant ?? "plain"];
        // `disabled` means "the control process is unreachable" -- which is
        // exactly when a user most needs to stop the grill, and a state this
        // flag can be stuck in on a HEALTHY system (see
        // helpers/dashboard/controlHealth.ts). Flask never disables anything
        // here: it shows a dismissible banner and leaves every button live
        // (templates/base.html:117-125). Keep the dimming as a signal, never
        // withhold the exits. `busy` still gates everything -- that one is a
        // real in-flight request, not a guess about the backend.
        const off = (disabled && !SAFETY_LABELS.has(b.label)) || busy;
        const waiting = pending?.label === b.label;
        return (
          <button
            key={b.label}
            className="pf-btn"
            data-pending={waiting ? "true" : undefined}
            aria-busy={waiting || undefined}
            disabled={off}
            style={{
              // A primary button is already filled with the accent, so lighting
              // its border in the same colour would say nothing. Its pending cue
              // is the pulse and the ellipsis, which read on either look.
              borderColor: waiting && b.variant !== "primary" ? "var(--accent)" : look.border,
              background: look.bg,
              color: look.color,
              opacity: disabled || busy ? 0.5 : 1,
            }}
            onClick={() => onClick(b.action, b.label)}
          >
            {waiting ? `${b.label}…` : b.label}
          </button>
        );
      })}
      {commandError !== null && (
        <div role="alert" className="pf-dash-control-error">
          {commandError}
        </div>
      )}

      <SetpointEntry
        open={setpointOpen}
        initial={dash.primaryProbe.setTemp || dash.primaryProbe.temp}
        units={dash.tempUnits}
        onCancel={() => setSetpointOpen(false)}
        onSubmit={(temp) => {
          setSetpointOpen(false);
          fire((c) => c.hold(temp));
        }}
      />
      {/* Startup modal, hold-temperature variant. Flask seeds it from
          settings.startup.start_to_mode.primary_setpoint, which is
          dash.startupGotoTemp on the wire. */}
      <SetpointEntry
        open={startupPrompt === "hold"}
        initial={dash.startupGotoTemp}
        units={dash.tempUnits}
        title="Change Hold Temp?"
        submitLabel="Startup"
        min={HOLD_PROMPT_RANGE[dash.tempUnits].min}
        max={HOLD_PROMPT_RANGE[dash.tempUnits].max}
        error={startupError}
        saving={busy}
        onCancel={() => {
          setStartupPrompt("none");
          setStartupError(null);
        }}
        onSubmit={startupWithHold}
      />
      {/* Startup modal, safety-check variant. Nothing editable, exactly as
          Flask renders it (_macro_control_panel.html:246-248). */}
      <ConfirmAction
        open={startupPrompt === "confirm"}
        title="Startup Check"
        message="Confirm Startup Grill?"
        onCancel={() => setStartupPrompt("none")}
        onConfirm={() => {
          setStartupPrompt("none");
          fire((c) => c.setMode("startup"));
        }}
      />
      <ActionMenu
        open={menu !== null}
        title={menu?.title ?? ""}
        items={menu?.items ?? []}
        onCancel={() => setMenu(null)}
        onPick={(value) => {
          const run = menu?.run;
          setMenu(null);
          if (run !== undefined) fire((c) => run(c, value));
        }}
      />
      <PwmEntry
        open={pwmOpen}
        initial={dash.manualPwm}
        onCancel={() => setPwmOpen(false)}
        onSubmit={(duty) => {
          setPwmOpen(false);
          fire((c) => c.manualPwm(duty));
        }}
      />
      <ConfirmAction
        open={confirm !== null}
        title={confirm?.title ?? ""}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          const run = confirm!.run;
          setConfirm(null);
          fire(run);
        }}
      />
    </div>
  );
}
