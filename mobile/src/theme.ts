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
export const WARN_COLOR = "#ffb020"; // theme.css: --color-warn (Theme.warn)
export const OK_COLOR = "#5ec96f"; // theme.css: --color-ok (Theme.okColor)

// CSS var() name -> hex. Only the tokens @pifire/core/dashboard/deriveView's
// HopperView actually emits (dashboard.css custom properties, on the web)
// are listed here.
export const CSS_VAR_COLOR: Record<string, string> = {
  "var(--ok)": OK_COLOR,
  "var(--warn)": WARN_COLOR,
  "var(--danger)": DANGER,
  "var(--label)": LABEL_COLOR,
};

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
