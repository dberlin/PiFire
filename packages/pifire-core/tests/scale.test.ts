import { describe, expect, it } from "@rstest/core";
import {
  CSS_TOKEN_NAME,
  GAUGE_MODE_BADGE,
  type ModeBadgeMetrics,
  PROBE_GAP,
  SCALE,
  gaugeModeBadge,
  probeGrid,
  probeMetrics,
} from "../src/dashboard/scale";
import { FIXTURE_DASH, FIXTURE_DASH_MONITOR } from "../src/fixture";

describe("SCALE tiers", () => {
  it("composes phone as desktop + tablet + phone, matching the CSS cascade", () => {
    // Both media blocks match at <=719px, so a phone inherits the tablet
    // override for anything the phone block does not itself restate. btnH is
    // restated by phone (56); probecolW is restated by neither, so it falls
    // all the way through to desktop.
    expect(SCALE.phone.btnH).toBe(56);
    expect(SCALE.phone.probecolW).toBe(SCALE.desktop.probecolW);
  });

  it("lets the phone block win over the tablet block for a token both set", () => {
    // Both blocks set gaugeSize. Composed in the wrong order the phone would
    // render the tablet's 320, which is the bug this ordering prevents.
    expect(SCALE.tablet.gaugeSize).toBe(320);
    expect(SCALE.phone.gaugeSize).toBe(260);
  });

  it("leaves tokens neither media block touches at their desktop value", () => {
    for (const key of ["headerH", "probecolW", "colW", "probeName"] as const) {
      expect(SCALE.tablet[key]).toBe(SCALE.desktop[key]);
      expect(SCALE.phone[key]).toBe(SCALE.desktop[key]);
    }
  });

  it("shrinks monotonically from desktop to phone", () => {
    for (const key of ["gaugeSize", "gaugeNum", "probeTemp", "btnFont", "hopperVal"] as const) {
      expect(SCALE.phone[key]).toBeLessThanOrEqual(SCALE.desktop[key]);
    }
  });

  it("names every token for the CSS drift check", () => {
    const tokens = Object.keys(SCALE.desktop).sort();
    expect(Object.keys(CSS_TOKEN_NAME).sort()).toEqual(tokens);
  });
});

describe("probeGrid", () => {
  it("gives a lone probe the whole row, so it lines up with the hopper", () => {
    expect(probeGrid(1, 360)).toEqual({ columns: 1, width: 360 });
  });

  it("splits the row evenly and accounts for the gaps", () => {
    expect(probeGrid(2, 360)).toEqual({ columns: 2, width: (360 - PROBE_GAP) / 2 });
    expect(probeGrid(3, 360)).toEqual({ columns: 3, width: (360 - PROBE_GAP * 2) / 3 });
  });

  it("balances rows instead of filling greedily", () => {
    // 4 probes are 2x2, NOT 3+1: a greedy fill leaves a lone card on row two
    // at a different width from its neighbours, which is the ragged layout
    // this function exists to prevent.
    expect(probeGrid(4, 360).columns).toBe(2);
    expect(probeGrid(5, 360).columns).toBe(3);
    expect(probeGrid(6, 360).columns).toBe(3);
  });

  it("never exceeds three across, however many probes there are", () => {
    for (let n = 1; n <= 12; n++) {
      expect(probeGrid(n, 360).columns).toBeLessThanOrEqual(3);
    }
  });

  it("returns no columns for no probes", () => {
    expect(probeGrid(0, 360).columns).toBe(0);
  });
});

describe("probeMetrics", () => {
  it("caps a full-width probe at the tier's probeTemp, below the gauge's hero", () => {
    const lone = probeMetrics(360);
    expect(lone.temp).toBe(SCALE.phone.probeTemp);
    expect(lone.temp).toBeLessThan(SCALE.phone.gaugeNum);
  });

  it("shrinks the reading once the column gets tight, and not before", () => {
    const one = probeMetrics(probeGrid(1, 360).width).temp;
    const two = probeMetrics(probeGrid(2, 360).width).temp;
    const three = probeMetrics(probeGrid(3, 360).width).temp;
    // Never grows as the row fills...
    expect(two).toBeLessThanOrEqual(one);
    expect(three).toBeLessThan(two);
    // ...but two across still has 174pt per card, which holds the full-size
    // reading. Shrinking there would be gratuitous, so it stays at the cap.
    expect(two).toBe(SCALE.phone.probeTemp);
  });

  it("keeps the reading inside its column at every column count", () => {
    for (let n = 1; n <= 6; n++) {
      const { width } = probeGrid(n, 360);
      const m = probeMetrics(width);
      // Three digits at ~0.62em each, plus the unit, plus both paddings.
      const needed = m.temp * 0.62 * 3 + m.unit * 1.4 + m.padding * 2;
      expect(needed).toBeLessThanOrEqual(width);
    }
  });

  it("keeps a three-across reading legible", () => {
    expect(probeMetrics(probeGrid(3, 360).width).temp).toBeGreaterThanOrEqual(22);
  });

  it("holds the unit in proportion to the reading", () => {
    const ratio = SCALE.phone.probeUnit / SCALE.phone.probeTemp;
    const m = probeMetrics(360);
    expect(m.unit).toBe(Math.round(m.temp * ratio));
  });
});

describe("FIXTURE_DASH_MONITOR", () => {
  it("carries the widest mode label the UI draws", () => {
    // The reason this fixture exists: "Stop" is 4 characters and "Monitor" is
    // 7, and mobile's mode badge has to fit inside the gauge arc. Asserting
    // the length keeps a future edit from quietly shortening the one label
    // that exercises the fit.
    expect(FIXTURE_DASH_MONITOR.currentMode).toBe("Monitor");
    expect(FIXTURE_DASH_MONITOR.currentMode.length).toBeGreaterThan(FIXTURE_DASH.currentMode.length);
  });

  it("is running, unlike the idle capture it derives from", () => {
    expect(FIXTURE_DASH.status).toBe("inactive");
    expect(FIXTURE_DASH_MONITOR.status).toBe("active");
    expect(FIXTURE_DASH_MONITOR.primaryProbe.temp).toBeGreaterThan(0);
  });

  it("keeps the payload shape identical to the captured fixture", () => {
    expect(Object.keys(FIXTURE_DASH_MONITOR).sort()).toEqual(Object.keys(FIXTURE_DASH).sort());
  });

  it("mixes one targeted probe with ambient ones", () => {
    const armed = FIXTURE_DASH_MONITOR.foodProbes.filter((p) => p.targetReq && p.target > 0);
    expect(armed).toHaveLength(1);
    expect(FIXTURE_DASH_MONITOR.foodProbes).toHaveLength(3);
  });
});


describe("gaugeModeBadge", () => {
  /** Rough advance width of one uppercase Barlow glyph at a given size. */
  const glyph = (fontSize: number) => fontSize * 0.509;
  const pillWidth = (m: ModeBadgeMetrics, chars: number) =>
    m.paddingHorizontal * 2 + chars * glyph(m.fontSize) + chars * m.letterSpacing + m.borderWidth * 2;

  it("is the identity at the desktop gauge it was authored for", () => {
    const m = gaugeModeBadge(SCALE.desktop.gaugeSize);
    expect(m.fontSize).toBe(GAUGE_MODE_BADGE.fontSize);
    expect(m.letterSpacing).toBe(GAUGE_MODE_BADGE.letterSpacing);
    expect(m.paddingHorizontal).toBe(GAUGE_MODE_BADGE.paddingHorizontal);
    expect(m.marginTop).toBe(GAUGE_MODE_BADGE.marginTop);
  });

  it("holds the same share of the gauge at every tier", () => {
    // The whole point: "MONITOR" occupies the same fraction of the ring on a
    // phone as it does on the desktop the design was drawn against.
    const ratios = [SCALE.desktop, SCALE.tablet, SCALE.phone].map(
      (t) => pillWidth(gaugeModeBadge(t.gaugeSize), 7) / t.gaugeSize,
    );
    for (const r of ratios) {
      expect(r).toBeCloseTo(ratios[0], 2);
    }
  });

  it("scales the gap above the badge too, not just the pill", () => {
    const k = SCALE.phone.gaugeSize / SCALE.desktop.gaugeSize;
    expect(gaugeModeBadge(SCALE.phone.gaugeSize).marginTop).toBeCloseTo(GAUGE_MODE_BADGE.marginTop * k, 5);
  });

  it("shrinks the phone badge well below the unscaled literal", () => {
    // Guards the regression this replaced: web's single un-mediated rule put a
    // 17px pill on the 260 gauge, making it ~0.49 of the ring instead of ~0.32,
    // where it crowded the arc and competed with the temperature.
    const phone = gaugeModeBadge(SCALE.phone.gaugeSize);
    expect(phone.fontSize).toBeLessThan(GAUGE_MODE_BADGE.fontSize);
    expect(pillWidth(phone, 7) / SCALE.phone.gaugeSize).toBeLessThan(0.4);
    expect(pillWidth(GAUGE_MODE_BADGE, 7) / SCALE.phone.gaugeSize).toBeGreaterThan(0.45);
  });
});
