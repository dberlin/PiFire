// The dashboard's size tokens, shared by web-react and mobile.
//
// These names and values originate in web-react's dashboard.css, which
// extracted them from inline styles so a @media query could reach them. That
// file remains the web's presentation layer -- media queries are the right
// tool there, and nothing here changes how the web renders. What moved here
// is the *source of truth* for the numbers, because mobile had no way to
// consume them and hand-copied instead: it matched the phone tier on
// probeTemp/probeUnit/probeName/hopperVal and silently drifted on gaugeNum
// (56 vs 64), gaugeUnit (20 vs 24), btnFont (14 vs 17) and gaugeSize
// (220 vs 260). Nobody chose those four. web-react's dashboardStyles test
// now asserts dashboard.css against this table, so the next drift fails a
// test instead of shipping.
//
// Deliberately NOT a single global scale multiplier. dashboard.css records
// why: uniform scaling is what the reflow decision rejected, and per-element
// tokens are what let a phone take relatively larger type in some places and
// smaller in others. This table preserves that -- it is the same 15
// per-element names, not a ratio.

/** A breakpoint tier. `phone` is what the native app renders at. */
export type ScaleTier = "desktop" | "tablet" | "phone";

export interface SizeTokens {
  /** Dashboard header height. Web-only: mobile uses native navigation. */
  headerH: number;
  /** Probe column width. Web-only: the web stacks probes in a vertical
   *  column, where mobile lays them out in a row -- see probeGrid(). */
  probecolW: number;
  /** Right-hand column width. Web-only, same reason as probecolW. */
  colW: number;
  /** The gauge SVG's width and height. */
  gaugeSize: number;
  /** The gauge's glow disc, smaller than the SVG box so the glow reads as a
   *  ring bleeding past the arc rather than a disc filling the card. */
  gaugeRing: number;
  /** The big grill temperature inside the gauge. */
  gaugeNum: number;
  /** The degree unit beside gaugeNum. */
  gaugeUnit: number;
  /** A probe card's big temperature, at its widest. Mobile scales BELOW this
   *  when probes share a row -- see probeMetrics(). */
  probeTemp: number;
  /** The degree unit beside probeTemp. */
  probeUnit: number;
  /** A probe card's name caption. */
  probeName: number;
  /** Control button label. */
  btnFont: number;
  /** Control button row height. */
  btnH: number;
  /** Cook-time value. Web-only today; mobile has no cook-time readout yet. */
  cookVal: number;
  /** Status pill value. Web-only today, same reason as cookVal. */
  pillVal: number;
  /** The hopper percentage. */
  hopperVal: number;
}

// dashboard.css's `.pf-dash` rule -- the unmediated values, which is what a
// desktop browser gets.
const DESKTOP: SizeTokens = {
  headerH: 58,
  probecolW: 298,
  colW: 300,
  gaugeSize: 392,
  gaugeRing: 360,
  gaugeNum: 112,
  gaugeUnit: 40,
  probeTemp: 66,
  probeUnit: 26,
  probeName: 15,
  btnFont: 25,
  btnH: 82,
  cookVal: 26,
  pillVal: 24,
  hopperVal: 34,
};

// dashboard.css's `@media (max-width: 1279px)` overrides.
const TABLET_OVERRIDES: Partial<SizeTokens> = {
  gaugeSize: 320,
  gaugeRing: 292,
  gaugeNum: 88,
  btnFont: 21,
};

// dashboard.css's `@media (max-width: 719px)` overrides. Both media blocks
// match at <=719px and the phone block comes later in the file, so the phone
// tier is desktop + tablet + phone, in that order -- exactly the cascade a
// browser applies. Getting this wrong is why the tiers are composed here
// rather than written out three times.
const PHONE_OVERRIDES: Partial<SizeTokens> = {
  gaugeSize: 260,
  gaugeRing: 236,
  gaugeNum: 64,
  gaugeUnit: 24,
  probeTemp: 44,
  probeUnit: 18,
  btnFont: 17,
  btnH: 56,
  cookVal: 20,
  pillVal: 20,
  hopperVal: 26,
};

export const SCALE: Record<ScaleTier, SizeTokens> = {
  desktop: DESKTOP,
  tablet: { ...DESKTOP, ...TABLET_OVERRIDES },
  phone: { ...DESKTOP, ...TABLET_OVERRIDES, ...PHONE_OVERRIDES },
};

/** The token names dashboard.css declares, paired with their CSS custom
 *  property spelling. web-react's dashboardStyles test walks this to assert
 *  the stylesheet still agrees with the table above. */
export const CSS_TOKEN_NAME: Record<keyof SizeTokens, string> = {
  headerH: "--pf-header-h",
  probecolW: "--pf-probecol-w",
  colW: "--pf-col-w",
  gaugeSize: "--pf-gauge-size",
  gaugeRing: "--pf-gauge-ring",
  gaugeNum: "--pf-gauge-num",
  gaugeUnit: "--pf-gauge-unit",
  probeTemp: "--pf-probe-temp",
  probeUnit: "--pf-probe-unit",
  probeName: "--pf-probe-name",
  btnFont: "--pf-btn-font",
  btnH: "--pf-btn-h",
  cookVal: "--pf-cook-val",
  pillVal: "--pf-pill-val",
  hopperVal: "--pf-hopper-val",
};

// --- Probe row layout -------------------------------------------------------
//
// Mobile-only today: the web stacks probes in a fixed-width vertical column
// (probecolW), so it has no column-count problem to solve. It lives here
// rather than inline in the screen because it is layout logic with real edge
// cases, and it is worth testing once rather than eyeballing on a simulator.

/** The gap between probe cards, and between rows of them. */
export const PROBE_GAP = 12;

/** Three across is the cap. A 4th column on a phone leaves roughly 80pt per
 *  card, which cannot hold a three-digit reading and its unit. */
const MAX_PROBE_COLUMNS = 3;

/**
 * How many probe cards share a row, and how wide each one is.
 *
 * Rows are balanced rather than greedily filled: 4 probes lay out 2x2, not
 * 3+1, so no row is left with a lone card and every card in the row is the
 * same width as its neighbours. The row itself always spans `available`,
 * which is what keeps it flush with the full-width hopper below it.
 *
 * @param count     how many probes are being shown
 * @param available the row's total width, i.e. the content width already
 *                  less the screen's horizontal padding
 */
export function probeGrid(count: number, available: number): { columns: number; width: number } {
  if (count <= 0) {
    return { columns: 0, width: available };
  }
  const rows = Math.ceil(count / MAX_PROBE_COLUMNS);
  const columns = Math.ceil(count / rows);
  return { columns, width: (available - PROBE_GAP * (columns - 1)) / columns };
}

/**
 * A probe card's type sizes for the column width it was given.
 *
 * A card sizes from its column, never from its own text. Before this existed
 * each card took its intrinsic content width, so a probe named "BRISKET FLAT"
 * rendered wider than one named "PROBE 2" and three probes wrapped into a
 * ragged 2+1. The reading scales down as the row fills, and `probeTemp` is
 * the ceiling: a lone full-width probe is a peer of the hopper, never a
 * second hero competing with the gauge.
 */
export function probeMetrics(
  width: number,
  tier: SizeTokens = SCALE.phone,
): { temp: number; unit: number; name: number; padding: number } {
  const temp = Math.max(22, Math.min(tier.probeTemp, Math.round(width * 0.26)));
  return {
    temp,
    unit: Math.max(11, Math.round(temp * (tier.probeUnit / tier.probeTemp))),
    name: Math.max(10, Math.min(tier.probeName, Math.round(width * 0.09))),
    padding: width < 130 ? 10 : 16,
  };
}


// --- The gauge's mode badge -------------------------------------------------
//
// The one dashboard element that scales UNIFORMLY with its container rather
// than through a per-element token, and deliberately so.
//
// dashboard.css declares .pf-dash-gauge-mode exactly once and no media query
// overrides it, so on web the badge stays 17px/3px-tracking/20px-padding at
// every breakpoint while --pf-gauge-size drops 392 -> 320 -> 260. It is
// therefore ~0.32 of the gauge on desktop but ~0.49 of it at web's own phone
// tier -- the badge does not shrink when the ring it sits inside does, and at
// 260 it crowds the arc on both sides and competes with the temperature.
//
// The badge is drawn INSIDE the arc, so its size is a fact about the gauge's
// geometry rather than an independent type choice: it has to hold the same
// share of the ring at every size. That is exactly the case per-element tokens
// do not serve, which is why this is a function of gaugeSize.
//
// Values below are dashboard.css's literals, authored against the desktop
// gauge -- the size the web fidelity gate pins at 1280x720.

/** .pf-dash-gauge-mode's literals, authored for SCALE.desktop.gaugeSize. */
export const GAUGE_MODE_BADGE = {
  fontSize: 17,
  letterSpacing: 3,
  paddingHorizontal: 20,
  paddingVertical: 6,
  /** The gap between the reading above and the badge (CSS margin-top). */
  marginTop: 12,
  borderWidth: 1.5,
} as const;

export interface ModeBadgeMetrics {
  fontSize: number;
  letterSpacing: number;
  paddingHorizontal: number;
  paddingVertical: number;
  marginTop: number;
  borderWidth: number;
}

/**
 * The mode badge's metrics for a gauge of `gaugeSize`, scaled uniformly from
 * the desktop reference so the badge holds the same proportion of the ring at
 * any gauge size -- the gap above it included.
 *
 * Only `fontSize` is rounded, to keep glyph rasterisation crisp; everything
 * else stays exact so the pill's proportions do not drift from the reference.
 */
export function gaugeModeBadge(gaugeSize: number, tier: SizeTokens = SCALE.desktop): ModeBadgeMetrics {
  const k = gaugeSize / tier.gaugeSize;
  return {
    fontSize: Math.round(GAUGE_MODE_BADGE.fontSize * k),
    letterSpacing: GAUGE_MODE_BADGE.letterSpacing * k,
    paddingHorizontal: GAUGE_MODE_BADGE.paddingHorizontal * k,
    paddingVertical: GAUGE_MODE_BADGE.paddingVertical * k,
    marginTop: GAUGE_MODE_BADGE.marginTop * k,
    borderWidth: GAUGE_MODE_BADGE.borderWidth * k,
  };
}
