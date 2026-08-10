import { FIXTURE_DASH } from "./fixture";
import type { DashSocketPayload } from "./contracts/core.gen"

// Live demo simulator: produces an evolving LiveState so the UI animates without
// a real PiFire. Models a "Hold at 225°F" cook — primary eases up to setpoint
// then wobbles, the food probe climbs toward its target, and the auger pulses.
// Pure function of elapsed seconds so it's deterministic and testable.
export function demoDashAt(elapsedSec: number): DashSocketPayload {
  const t = elapsedSec;

  // Primary: climb 180 -> 225 with exponential ease-in, then oscillate ±3.
  const primaryTemp = Math.round(225 - 45 * Math.exp(-t / 35) + 3 * Math.sin(t / 6));

  // Food probe: slow climb 120 -> ~205 (target 203), then hold.
  const foodTemp = Math.round(Math.min(205, 120 + t * 0.55));

  // Auger pulses on for 15s of each 20s cycle; fan steady; igniter off in Hold.
  const augerOn = t % 20 < 15;

  // Hopper slowly drains from 70%.
  const hopperLevel = Math.max(12, Math.round(70 - t / 30));

  return {
    ...FIXTURE_DASH,
    currentMode: "Hold",
    // The simulated grill HAS a distance sensor -- it is the thing draining the
    // hopper level below. Without this the dashboard now hides the hopper card
    // (Flask hides it when settings.modules.dist == "none"), and the demo would
    // be modelling a grill it is not simulating.
    hasDistanceSensor: true,
    displayMode: "Hold",
    hopperLevel,
    outputs: { fan: true, auger: augerOn, igniter: false, power: true },
    primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: primaryTemp, setTemp: 225 },
    foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: foodTemp, target: 203, targetReq: true }],
  };
}
