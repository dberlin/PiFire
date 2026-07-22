import type { DashData, SendCommand } from "../types";
import { buttonsForMode } from "./controlButtons";

// Mode-driven control row, styled to the design's button grid. Each press goes
// straight to post_app_data via `send` (see controlButtons.ts).
export function ControlButtons({ dash, send }: { dash: DashData; send: SendCommand }) {
  const buttons = buttonsForMode(dash);
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
            style={{ borderColor: border, background: bg, color }}
            onClick={() => send(b.action, b.type, b.data)}
          >
            {b.label}
          </button>
        );
      })}
    </div>
  );
}
