import type { DashData } from "../types";

// Mode-driven control-button set. Each press maps to post_app_data ->
// write_control, exactly like the Qt bridge's typed slots (qtbackend.py) and
// the current web dashboard. Wiring is unchanged from the original POC
// ControlPanel; only the presentation (ControlButtons.tsx) was restyled to the
// design's button grid.
export interface ControlButton {
  label: string;
  action: string;
  type: string | null;
  data?: unknown;
  variant?: "accent" | "danger";
}

export function buttonsForMode(dash: DashData): ControlButton[] {
  const mode = dash.currentMode;
  if (mode === "Stop" || mode === "Error" || mode === "") {
    return [
      { label: "Startup", action: "update", type: "control", data: { updated: true, mode: "Startup" }, variant: "accent" },
      { label: "Prime", action: "update", type: "control", data: { updated: true, mode: "Prime" } },
      { label: "Monitor", action: "update", type: "control", data: { updated: true, mode: "Monitor" } },
    ];
  }
  if (mode === "Monitor") {
    return [
      { label: "Startup", action: "update", type: "control", data: { updated: true, mode: "Startup" }, variant: "accent" },
      { label: "Stop", action: "update", type: "control", data: { updated: true, mode: "Stop" }, variant: "danger" },
    ];
  }
  // Any active cook mode (Startup / Smoke / Hold / Prime / Reheat / Shutdown).
  return [
    { label: "Hold", action: "update", type: "control", data: { updated: true, mode: "Hold" }, variant: "accent" },
    { label: "Smoke", action: "update", type: "control", data: { updated: true, mode: "Smoke" } },
    { label: dash.smokePlus ? "Smoke+ On" : "Smoke+ Off", action: "update", type: "control", data: { s_plus: !dash.smokePlus } },
    { label: "Shutdown", action: "update", type: "control", data: { updated: true, mode: "Shutdown" } },
    { label: "Stop", action: "update", type: "control", data: { updated: true, mode: "Stop" }, variant: "danger" },
  ];
}
