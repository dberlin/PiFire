// PiFire accent tokens, ported literally from web-react/src/theme.css's
// @theme static block and its [data-accent="..."] overrides (which in turn
// are ported from display/qml/Theme.qml, the app's ultimate source of truth
// for colour). Do not invent values here — if a colour needs to change,
// change it in theme.css first and re-port it here.
//
// theme.css only swaps --color-accent (plus the gauge gradient stops and
// glow, which this app does not need yet) per accent; --color-page,
// --color-card, --color-text, and --color-danger are declared once in the
// base @theme block and are never overridden per [data-accent]. So
// background/surface/text/danger are identical across all three accents
// here too — only `accent` differs.
export type AccentName = "ember" | "ice" | "crimson";

export interface AccentTokens {
  accent: string;
  background: string;
  surface: string;
  text: string;
  danger: string;
}

// theme.css line 47: --color-page: #0c0a09; (Theme.page)
const BACKGROUND = "#0c0a09";
// theme.css line 48: --color-card: #2c231a; (Theme.card)
const SURFACE = "#2c231a";
// theme.css line 51: --color-text: #f4ede2; (Theme.textColor)
const TEXT = "#f4ede2";
// theme.css line 58: --color-danger: #ff5a4d; (Theme.dangerColor)
export const DANGER = "#ff5a4d";

export const THEME: Record<AccentName, AccentTokens> = {
  // theme.css line 71 / 96: --color-accent-ember / default --color-accent
  ember: {
    accent: "#ff8a2b",
    background: BACKGROUND,
    surface: SURFACE,
    text: TEXT,
    danger: DANGER,
  },
  // theme.css line 72 / 159: --color-accent-ice / [data-accent="ice"] --color-accent
  ice: {
    accent: "#3cc7d0",
    background: BACKGROUND,
    surface: SURFACE,
    text: TEXT,
    danger: DANGER,
  },
  // theme.css line 73 / 166: --color-accent-crimson / [data-accent="crimson"] --color-accent
  crimson: {
    accent: "#ff6a5a",
    background: BACKGROUND,
    surface: SURFACE,
    text: TEXT,
    danger: DANGER,
  },
};

// ---------------------------------------------------------------------------
// Consolidated CSS-variable-to-hex table.
//
// React Native has no var() resolver, so any color web-react expresses as
// var(--color-xxx) has to become a literal hex somewhere on this side. Two
// call sites used to hand-copy that translation independently -- GrillGauge's
// own gauge-only constants, and app/(tabs)/index.tsx's hopper-bar
// CSS_VAR_COLOR -- which is exactly how they could (and did) drift apart from
// theme.css and from each other. This is the one place those values live now.
//
// Every hex below was checked directly against web-react/src/theme.css.
// GrillGauge's old TEXT_DIM_COLOR (#b9ab98) matched nothing in that file --
// it was standing in for two distinct real tokens, --color-text-dim and
// --color-label, which is why both are exported separately below instead of
// being collapsed back into one fabricated value.
export const TRACK_COLOR = "#4a4034"; // theme.css: --color-track (Theme.trackColor)
export const SETPOINT_COLOR = "#6cc8ff"; // theme.css: --color-setpoint (Theme.setpoint)
export const TEXT_COLOR = "#f4ede2"; // theme.css: --color-text (Theme.textColor), same as THEME.*.text
export const TEXT_DIM_COLOR = "#8a7f70"; // theme.css: --color-text-dim (Theme.dim)
export const LABEL_COLOR = "#7d7264"; // theme.css: --color-label (Theme.label)
export const PROBE_LABEL_COLOR = "#b7ac9c"; // theme.css: --color-probe-label (Theme.probeLabel)
export const WARN_COLOR = "#ffb020"; // theme.css: --color-warn (Theme.warn)
export const OK_COLOR = "#5ec96f"; // theme.css: --color-ok (Theme.okColor)
export const COOKING_COLOR = "#ffd23f"; // theme.css: --color-cooking (Theme.cookingColor)
export const INSET_COLOR = "#1c1712"; // theme.css: --color-inset (Theme.inset)
// theme.css: --color-card-border (Theme.cardBorder), already an rgba() literal there.
export const CARD_BORDER_COLOR = "rgba(255, 255, 255, 0.13)";
// Ink text painted on an accent-filled surface (ControlButtons.tsx's
// VARIANT_STYLE.primary.color and dashboard.css's .pf-modal-btn.accent). Not
// a Theme.qml token -- both call sites on the web pick this literal
// themselves -- so it is ported as the literal, not as a --color-* alias.
export const ON_ACCENT_INK = "#1a0f04";
// Body text on a dark inset/accent-tinted surface (ControlButtons.tsx's
// VARIANT_STYLE.accent/.plain colors and dashboard.css's .pf-modal-btn).
// Same status as ON_ACCENT_INK above: a literal the web picks at each call
// site, not a Theme.qml token.
export const BODY_TEXT_COLOR = "#e8dfd1";

// CSS var() name -> hex. Only the tokens @pifire/core/dashboard/deriveView's
// HopperView and ProbeCardView actually emit (dashboard.css custom
// properties, on the web) are listed here. "var(--accent)" is deliberately
// absent: it is the one color deriveView emits that depends on the user's
// accent selection, so callers resolve it against THEME[accent].accent
// instead of a fixed hex.
export const CSS_VAR_COLOR: Record<string, string> = {
  "var(--ok)": OK_COLOR,
  "var(--warn)": WARN_COLOR,
  "var(--danger)": DANGER,
  "var(--label)": LABEL_COLOR,
  "var(--cooking)": COOKING_COLOR,
};

// CSS's `color-mix(in srgb, <hex> <pct>%, transparent)` has no RN equivalent,
// so call sites that used it on the web (ControlButtons.tsx's VARIANT_STYLE,
// GrillGauge.tsx's .pf-dash-gauge-mode background/border) convert the same
// hex through this instead of hand-picking a separate rgba() literal that
// could drift from the theme color it is supposed to be a tint of.
export function withAlpha(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export interface GaugeAccentTokens {
  arcStop0: string;
  arcStop1: string;
  arcStop2: string;
  glow: string;
}

// The grill gauge's three arc-gradient stops, in drawing order (Theme.arcStop0
// -> arcStop1 -> arcStop2), plus its glow color -- the one part of the UI
// that DOES vary by accent beyond the flat `accent` swatch in THEME above.
// Ported literally from web-react/src/theme.css: the base @theme block
// (lines 96-100) for ember's values, and the [data-accent="ice"/"crimson"]
// overrides (lines 158-170) for the other two.
export const GAUGE_ACCENT: Record<AccentName, GaugeAccentTokens> = {
  ember: { arcStop0: "#ff5e1a", arcStop1: "#ff8a2b", arcStop2: "#ffc24b", glow: "#ff7a1a" },
  ice: { arcStop0: "#1f9fb8", arcStop1: "#35c7d0", arcStop2: "#7ef0d2", glow: "#2ec5d3" },
  crimson: { arcStop0: "#e11d48", arcStop1: "#ff5a4d", arcStop2: "#ff9f43", glow: "#ff5a4d" },
};
