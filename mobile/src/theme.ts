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
const DANGER = "#ff5a4d";

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
