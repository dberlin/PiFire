import { useState } from "react";
import type { CommandClient, CommandResult } from "../command";
import { buttonsForMode, type ButtonAction } from "./controlButtons";
import { SetpointEntry } from "./SetpointEntry";
import { ConfirmAction } from "./ConfirmAction";
import type { DashData } from "../types";

// Mode-driven control row, styled to the design's button grid. Each press
// dispatches a `ButtonAction` (command / setpoint / confirm) against the REST
// `CommandClient` (see controlButtons.ts / command.ts).
export function ControlButtons({ dash, command, disabled }: { dash: DashData; command: CommandClient; disabled: boolean }) {
  const buttons = buttonsForMode(dash);
  const [setpointOpen, setSetpointOpen] = useState(false);
  const [confirm, setConfirm] = useState<{ title: string; run(c: CommandClient): Promise<CommandResult> } | null>(null);
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
    else setConfirm({ title: action.title, run: action.run });
  };

  return (
    <div style={{ display: "grid", gridAutoFlow: "column", gridAutoColumns: "1fr", gap: 12, height: 82, flex: "0 0 82px" }}>
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
        return (
          <button
            key={b.label}
            className="pf-btn"
            disabled={disabled || busy}
            style={{ borderColor: border, background: bg, color, opacity: disabled || busy ? 0.5 : 1 }}
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
