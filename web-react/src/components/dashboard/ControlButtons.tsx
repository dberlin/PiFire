import { useState } from "react";
import type { CommandClient, CommandResult } from "../../helpers/command";
import { type ButtonAction, buttonsForMode } from "../../helpers/dashboard/buttonsForMode";
import type { LiveState } from "../../helpers/types";
import { ConfirmAction } from "./ConfirmAction";
import { PwmEntry } from "./PwmEntry";
import { SetpointEntry } from "./SetpointEntry";

/** The two ways out of a running cook. These are never withheld. */
const SAFETY_LABELS = new Set(["Stop", "Shutdown"]);

// Mode-driven control row, styled to the design's button grid. Each press
// dispatches a `ButtonAction` (command / setpoint / confirm) against the REST
// `CommandClient` (see buttonsForMode.ts / command.ts).
export function ControlButtons({
  dash,
  command,
  disabled,
}: {
  dash: LiveState;
  command: CommandClient;
  disabled: boolean;
}) {
  const buttons = buttonsForMode(dash);
  const [setpointOpen, setSetpointOpen] = useState(false);
  const [pwmOpen, setPwmOpen] = useState(false);
  const [confirm, setConfirm] = useState<{
    title: string;
    run(c: CommandClient): Promise<CommandResult>;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const fire = async (run: (c: CommandClient) => Promise<CommandResult>) => {
    setBusy(true);
    try {
      await run(command);
    } finally {
      setBusy(false);
    }
  };

  const onClick = (action: ButtonAction) => {
    if (action.type === "command") fire(action.run);
    else if (action.type === "setpoint") setSetpointOpen(true);
    else if (action.type === "pwm") setPwmOpen(true);
    else setConfirm({ title: action.title, run: action.run });
  };

  return (
    <div
      data-pf="controls"
      style={{
        display: "grid",
        gridAutoFlow: "column",
        gridAutoColumns: "1fr",
        gap: 12,
        height: 82,
        flex: "0 0 82px",
      }}
    >
      {buttons.map((b) => {
        const danger = b.variant === "danger";
        const accent = b.variant === "accent";
        const border = danger ? "#ff5a4d" : accent ? "var(--accent)" : "rgba(255,255,255,0.14)";
        const bg = danger
          ? "rgba(255,90,77,0.14)"
          : accent
            ? "color-mix(in srgb, var(--accent) 16%, transparent)"
            : "#1d1813";
        const color = danger ? "#ff8b82" : "#e8dfd1";
        // `disabled` means "the control process is unreachable" -- which is
        // exactly when a user most needs to stop the grill, and a state this
        // flag can be stuck in on a HEALTHY system (see
        // helpers/dashboard/controlHealth.ts). Flask never disables anything
        // here: it shows a dismissible banner and leaves every button live
        // (templates/base.html:117-125). Keep the dimming as a signal, never
        // withhold the exits. `busy` still gates everything -- that one is a
        // real in-flight request, not a guess about the backend.
        const off = (disabled && !SAFETY_LABELS.has(b.label)) || busy;
        return (
          <button
            key={b.label}
            className="pf-btn"
            disabled={off}
            style={{
              borderColor: border,
              background: bg,
              color,
              opacity: disabled || busy ? 0.5 : 1,
            }}
            onClick={() => onClick(b.action)}
          >
            {b.label}
          </button>
        );
      })}

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
