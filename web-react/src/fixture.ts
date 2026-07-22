import type { DashData } from "./types";

// Recorded-shape fallback so the POC renders with no running Pi. Values are a
// representative "Hold at 225°F, primary climbing" snapshot matching the exact
// key layout of socket_io.py `_get_dash_data`. Swap for a real captured payload
// by copying one emitted `socket_dash_data` object here verbatim.
export const FIXTURE_DASH: DashData = {
  uuid: "fixture",
  grillName: "PiFire",
  currentMode: "Hold",
  displayMode: "Hold",
  nextMode: "Hold",
  smokePlus: true,
  pMode: 2,
  hopperLevel: 68,
  tempUnits: "F",
  lidOpenDetected: false,
  outputs: { fan: 1, auger: 1, igniter: 0 },
  timer: { start: 0, paused: 0, end: 0, keepWarm: false, shutdown: false },
  recipeStatus: { recipeMode: false, filename: "", mode: "", paused: false, step: 0 },
  primaryProbe: {
    title: "Grill",
    label: "Grill",
    temp: 198,
    setTemp: 225,
    maxTemp: 600,
    target: 0,
    targetReq: false,
    hasNotifications: false,
    status: {},
  },
  foodProbes: [
    {
      title: "Probe 1",
      label: "Probe1",
      temp: 142,
      setTemp: 0,
      maxTemp: 300,
      target: 203,
      targetReq: true,
      hasNotifications: true,
      status: {},
    },
  ],
  errors: [],
  warnings: [],
};
