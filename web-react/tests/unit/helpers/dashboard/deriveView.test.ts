import { describe, expect, it } from "@rstest/core";
import { deriveView } from "../../../../src/helpers/dashboard/deriveView";
import { FIXTURE_DASH } from "../../../../src/helpers/fixture";
import type { LiveState, ProbeData } from "../../../../src/helpers/types";

// probeCard()'s existing fields (targetStr / tgtColor / barPct / barColor) are
// covered through components/dashboard/ProbeCard.test.tsx, which renders them.
// These cases cover the fields the card cannot show by itself: the write
// identity and the ETA readout.
const card = (over: Partial<ProbeData>) =>
  deriveView({ ...FIXTURE_DASH, foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], ...over }] })
    .probes[0];

describe("probeCard identity", () => {
  // `label` is the key every notify write is addressed by
  // (common/api_commands.py:441-449). ProbeCardView previously exposed only
  // `name` (the display title), so there was no way to say which probe a card
  // meant.
  it("carries the probe's label, distinct from its display title", () => {
    const v = card({ title: "Brisket", label: "Probe1" });
    expect(v.label).toBe("Probe1");
    expect(v.name).toBe("Brisket");
  });
});

describe("probeCard notifyOn", () => {
  // hasNotifications, NOT targetReq. blueprints/mobile/socket_io.py:770-795
  // sets it when ANY of the probe's three notify entries is armed, and since
  // slice 2 the bell opens a modal that edits all three -- so a probe carrying
  // only a high-limit alert used to show a struck-through bell for a
  // notification it really had.
  it("is set by an armed target", () => {
    expect(card({ targetReq: true, hasNotifications: true, target: 203 }).notifyOn).toBe(true);
    expect(card({ targetReq: false, hasNotifications: false, target: 203 }).notifyOn).toBe(false);
  });

  it("is set by a limit alert with no target armed", () => {
    expect(card({ targetReq: false, hasNotifications: true, highLimitReq: true }).notifyOn).toBe(
      true,
    );
  });
});

describe("probeCard etaStr", () => {
  // The Flask ETA button is rendered only while the probe notification is
  // requested (_macro_dash_default.html:123-131) and shows a spinner until the
  // backend has computed one (dash_default.js:632-636); null here is that
  // "nothing to show" state.
  it("formats a numeric eta while the notification is armed", () => {
    expect(card({ targetReq: true, target: 203, eta: 3661 }).etaStr).toBe("1:01:01");
    expect(card({ targetReq: true, target: 203, eta: 65 }).etaStr).toBe("01:05");
  });

  it("is null when the notification is not armed", () => {
    expect(card({ targetReq: false, target: 203, eta: 3661 }).etaStr).toBeNull();
  });

  it("is null when the backend has no eta yet", () => {
    expect(card({ targetReq: true, target: 203, eta: null }).etaStr).toBeNull();
  });

  // types.ts:17 types eta as `number | string | null` because the real capture
  // has been seen carrying a string; only a number is formattable.
  it("is null for a non-numeric eta", () => {
    expect(card({ targetReq: true, target: 203, eta: "--" }).etaStr).toBeNull();
  });
});

// The P-Mode pill is a control only where it is shown at all, which is Smoke.
// Flask offered the control in five modes (dash_default.js:248-293), but it
// also showed the badge in all of them; here the badge is Smoke-only, and the
// left pill reads AUGER DUTY everywhere else -- a pill that must never open a
// P-Mode picker.
describe("deriveView.pModeEditable", () => {
  const at = (over: Partial<LiveState>): LiveState => ({ ...FIXTURE_DASH, ...over });

  it("is editable in Smoke", () => {
    expect(deriveView(at({ currentMode: "Smoke" })).pModeEditable).toBe(true);
  });

  it("is NOT editable in Hold, where the PID owns the cycle", () => {
    expect(deriveView(at({ currentMode: "Hold" })).pModeEditable).toBe(false);
  });

  it("is NOT editable wherever the left pill is the auger duty", () => {
    for (const mode of [
      "Prime",
      "Shutdown",
      "Startup",
      "Reignite",
      "Stop",
      "Monitor",
      "Manual",
      "Error",
      "",
    ]) {
      const view = deriveView(at({ currentMode: mode }));
      expect(view.pillL.label, mode).toBe("AUGER DUTY");
      expect(view.pModeEditable, mode).toBe(false);
    }
  });

  it("is NOT editable during a recipe, whatever the sub-mode reads", () => {
    expect(
      deriveView(
        at({
          currentMode: "Smoke",
          recipeStatus: { ...FIXTURE_DASH.recipeStatus, recipeMode: true },
        }),
      ).pModeEditable,
    ).toBe(false);
  });
});

// The two pills under the system card. P-mode and Smoke+ describe the smoke
// cycle, so they appear only in Smoke; in every other mode they reported
// settings that governed nothing running, and the pills carry the actuator
// duties instead. The attached display makes the same swap on the same
// condition (display/qml/screens/DashScreen.qml).
describe("duty pills", () => {
  const at = (over: Partial<LiveState>): LiveState => ({ ...FIXTURE_DASH, ...over });

  it("shows P-mode and Smoke+ in Smoke, and only there", () => {
    const view = deriveView(at({ currentMode: "Smoke", pMode: 3, smokePlus: true }));
    expect(view.pillL.label).toBe("P-MODE");
    expect(view.pillL.value).toBe("P-3");
    expect(view.pillR.label).toBe("SMOKE+");
    expect(view.pillR.value).toBe("ON");
  });

  it("reports Smoke+ off in Smoke when it is off", () => {
    const view = deriveView(at({ currentMode: "Smoke", smokePlus: false }));
    expect(view.pillR.value).toBe("OFF");
  });

  it("shows the actuator duties in every mode but Smoke", () => {
    // Smoke+ is deliberately left ON here: outside Smoke it must not reach the
    // pill at all, so a passing run proves the mode gate and not a false value.
    for (const mode of [
      "Hold",
      "Startup",
      "Stop",
      "Shutdown",
      "Prime",
      "Reignite",
      "Monitor",
      "Manual",
      "Error",
      "",
    ]) {
      const view = deriveView(
        at({ currentMode: mode, cycleRatio: 0.42, fanDuty: 65, pMode: 3, smokePlus: true }),
      );
      expect(view.pillL.label, mode).toBe("AUGER DUTY");
      expect(view.pillL.value, mode).toBe("42%");
      expect(view.pillR.label, mode).toBe("FAN DUTY");
      expect(view.pillR.value, mode).toBe("65%");
    }
  });

  it("rounds the auger's cycle share to whole percent", () => {
    const view = deriveView(at({ currentMode: "Hold", cycleRatio: 0.335, fanDuty: 0 }));
    expect(view.pillL.value).toBe("34%");
    expect(view.pillR.value).toBe("0%");
  });

  it("survives a payload from a backend too old to send the duties", () => {
    const { cycleRatio, fanDuty, ...older } = at({ currentMode: "Hold" });
    void cycleRatio;
    void fanDuty;
    const view = deriveView(older as LiveState);
    expect(view.pillL.value).toBe("0%");
    expect(view.pillR.value).toBe("0%");
  });
});
