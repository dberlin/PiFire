import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "@rstest/core";

/**
 * display/qml/Theme.qml is the source of truth for every colour in this app.
 *
 * This file PARSES Theme.qml rather than restating it: a second copy of the
 * palette is just a second thing to drift. What is hardcoded here is the
 * MAPPING (which Qt property each CSS custom property follows) and the list of
 * Qt tokens the web app deliberately does not consume -- names and decisions,
 * never values.
 *
 * Three things are checked:
 *   1. every mapped token in theme.css equals its Qt property, per accent;
 *   2. every colour property in Theme.qml is either mapped or explicitly
 *      declared unused, so a new Qt token cannot be added and silently ignored;
 *   3. no file outside theme.css hardcodes a colour that IS a Qt token value --
 *      those must go through the custom property, or they will drift the moment
 *      Qt changes.
 */

const QML = readFileSync("../display/qml/Theme.qml", "utf8");
const CSS = readFileSync("src/theme.css", "utf8");

type Accent = "ember" | "ice" | "crimson";
const ACCENTS: Accent[] = ["ember", "ice", "crimson"];

/** A colour as [r, g, b, a], so "#ff5a4d" and "rgba(255, 90, 77, 1)" compare equal. */
type Rgba = [number, number, number, number];

function parseColor(raw: string): Rgba {
  const v = raw.trim();
  const hex = /^#([0-9a-f]{6})$/i.exec(v);
  if (hex !== null) {
    const n = Number.parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  const rgba = /^rgba?\(([^)]*)\)$/i.exec(v);
  if (rgba !== null) {
    const parts = rgba[1].split(/[,/]/).map((p) => Number.parseFloat(p.trim()));
    return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
  }
  throw new Error(`not a colour literal: ${raw}`);
}

const show = (c: Rgba): string => `rgba(${c.join(", ")})`;
const same = (a: Rgba, b: Rgba): boolean => a.every((n, i) => Math.abs(n - b[i]) < 0.001);

// ---------------------------------------------------------------- Theme.qml

/** One Qt colour property: a single value, or one per accent. */
type QtColor = { kind: "fixed"; value: Rgba } | { kind: "accent"; value: Record<Accent, Rgba> };

function parseThemeQml(src: string): Map<string, QtColor> {
  const out = new Map<string, QtColor>();
  const aliases: [string, string][] = [];

  for (const line of src.split("\n")) {
    const decl = /^\s*readonly\s+property\s+color\s+(\w+):\s*(.+?)\s*$/.exec(line);
    if (decl === null) continue;
    const [, name, expr] = decl;

    // accent === "Ice" ? "#a" : accent === "Crimson" ? "#b" : "#c"
    const ternary =
      /accent\s*===\s*"Ice"\s*\?\s*"(#[0-9a-f]{6})"\s*:\s*accent\s*===\s*"Crimson"\s*\?\s*"(#[0-9a-f]{6})"\s*:\s*"(#[0-9a-f]{6})"/i.exec(
        expr,
      );
    if (ternary !== null) {
      out.set(name, {
        kind: "accent",
        value: {
          ice: parseColor(ternary[1]),
          crimson: parseColor(ternary[2]),
          ember: parseColor(ternary[3]),
        },
      });
      continue;
    }

    // Qt.rgba(1, 1, 1, 0.13) -- channels are 0..1 floats in QML.
    const qtRgba = /^Qt\.rgba\(([^)]*)\)/.exec(expr);
    if (qtRgba !== null) {
      const n = qtRgba[1].split(",").map((p) => Number.parseFloat(p.trim()));
      out.set(name, { kind: "fixed", value: [n[0] * 255, n[1] * 255, n[2] * 255, n[3]] });
      continue;
    }

    const literal = /^"(#[0-9a-f]{6})"/i.exec(expr);
    if (literal !== null) {
      out.set(name, { kind: "fixed", value: parseColor(literal[1]) });
      continue;
    }

    // Back-compat alias: `readonly property color background: page`.
    const alias = /^(\w+)$/.exec(expr);
    if (alias !== null) aliases.push([name, alias[1]]);
  }

  for (const [name, target] of aliases) {
    const resolved = out.get(target);
    if (resolved !== undefined) out.set(name, resolved);
  }
  return out;
}

const QT = parseThemeQml(QML);

// ---------------------------------------------------------------- theme.css

/** Read the declarations of one CSS rule, keyed by custom-property name. */
function ruleBlock(css: string, selector: string): Map<string, string> {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) throw new Error(`theme.css has no ${selector} rule`);
  const body = css.slice(at + selector.length + 2, css.indexOf("}", at));
  const out = new Map<string, string>();
  for (const decl of body.split(";")) {
    const m = /(--[\w-]+)\s*:\s*([^;]+)/.exec(decl.replace(/\/\*[\s\S]*?\*\//g, ""));
    if (m !== null) out.set(m[1], m[2].trim());
  }
  return out;
}

// Three places a token can be declared, in cascade order (most specific first).
// THEME is Tailwind's `@theme static` block, which emits into @layer theme;
// ROOT is the unlayered alias block that maps every legacy --name onto its
// --color-* counterpart, and an unlayered declaration beats a layered one, so
// the [data-accent] overrides below win over @theme's ember defaults.
const THEME = ruleBlock(CSS, "@theme static");
const ROOT = ruleBlock(CSS, ":root");
const OVERRIDES: Record<Accent, Map<string, string>> = {
  ember: new Map(),
  ice: ruleBlock(CSS, ':root[data-accent="ice"]'),
  crimson: ruleBlock(CSS, ':root[data-accent="crimson"]'),
};

/** Resolve a token for one accent, following var() indirection through the
 *  legacy alias into @theme. */
function cssToken(name: string, accent: Accent): Rgba {
  const lookup = (n: string): string => {
    const v = OVERRIDES[accent].get(n) ?? ROOT.get(n) ?? THEME.get(n);
    if (v === undefined) throw new Error(`theme.css declares no ${n}`);
    const indirect = /^var\((--[\w-]+)\)$/.exec(v);
    return indirect !== null ? lookup(indirect[1]) : v;
  };
  return parseColor(lookup(name));
}

// ----------------------------------------------------------------- the map

/** CSS custom property -> the Theme.qml property it must equal. */
const MAPPING: Record<string, string> = {
  "--page": "page",
  "--card": "card",
  "--inset": "inset",
  "--card-border": "cardBorder",
  "--text": "textColor",
  "--text-dim": "dim",
  "--label": "label",
  "--probe-label": "probeLabel",
  "--setpoint": "setpoint",
  "--ok": "okColor",
  "--warn": "warn",
  "--danger": "dangerColor",
  "--track": "trackColor",
  "--cooking": "cookingColor",
  "--igniter": "igniterColor",
  "--icon-idle": "iconIdle",
  "--dot-idle": "dotIdle",
  "--row-label": "rowLabel",
  "--accent": "accentColor",
  "--glow": "glowColor",
  // The gauge arc's three gradient stops, in Qt's numbering. --accent-2 is the
  // DARK end and --accent-1 the light one; see theme.css.
  "--accent-2": "arcStop0",
  "--accent-mid": "arcStop1",
  "--accent-1": "arcStop2",
};

/** The accent picker paints all three swatches at once, so it needs all three
 *  branches of Theme.accentColor as constants rather than the live --accent. */
const FIXED_ACCENT_TOKENS: Record<string, Accent> = {
  "--accent-ember": "ember",
  "--accent-ice": "ice",
  "--accent-crimson": "crimson",
};

/** Qt colour properties this app deliberately does not consume. Each needs a
 *  reason: the point of the list is that a NEW Qt token cannot join it by
 *  accident. */
const UNCONSUMED: Record<string, string> = {
  notify: "the Qt on-device notification banner has no web counterpart",
  // Theme.qml's back-compat aliases for the older menu/input QML components.
  // They resolve to properties already mapped above, so a token for them would
  // be a second name for a colour the web app already has.
  background: "alias of page",
  surface: "alias of card",
  primary: "alias of setpoint",
  text: "alias of textColor",
  subtext: "alias of dim",
  danger: "alias of dangerColor",
  ok: "alias of okColor",
};

describe("theme.css follows display/qml/Theme.qml", () => {
  it("parses a palette out of Theme.qml at all", () => {
    // Guards the parser itself: a Theme.qml rewrite that this regex stops
    // understanding would otherwise make every assertion below vacuous.
    expect(QT.size).toBeGreaterThanOrEqual(25);
    expect(QT.get("dim")).toEqual({ kind: "fixed", value: [138, 127, 112, 1] });
    expect(QT.get("accentColor")?.kind).toBe("accent");
  });

  for (const [token, qtName] of Object.entries(MAPPING)) {
    it(`${token} equals Theme.${qtName}`, () => {
      const qt = QT.get(qtName);
      expect(qt, `Theme.qml has no property ${qtName}`).toBeDefined();
      if (qt === undefined) return;
      for (const accent of ACCENTS) {
        const want = qt.kind === "fixed" ? qt.value : qt.value[accent];
        const got = cssToken(token, accent);
        expect(
          same(got, want),
          `${token} on ${accent}: theme.css has ${show(got)}, Theme.${qtName} is ${show(want)}`,
        ).toBe(true);
      }
    });
  }

  it("keeps the three accent constants equal to Theme.accentColor's branches", () => {
    const qt = QT.get("accentColor");
    expect(qt?.kind).toBe("accent");
    if (qt?.kind !== "accent") return;
    for (const [token, accent] of Object.entries(FIXED_ACCENT_TOKENS)) {
      // Read on ember: these do not vary, which is the property being pinned.
      expect(same(cssToken(token, "ember"), qt.value[accent]), `${token} drifted`).toBe(true);
    }
  });

  it("accounts for every colour property in Theme.qml", () => {
    const mapped = new Set(Object.values(MAPPING));
    const unaccounted = [...QT.keys()].filter((n) => !mapped.has(n) && UNCONSUMED[n] === undefined);
    expect(
      unaccounted,
      `Theme.qml colours neither mapped into theme.css nor listed as unconsumed: ${unaccounted.join(", ")}`,
    ).toEqual([]);
  });
});

// ------------------------------------------------------- the Tailwind bridge

describe("theme.css tokens", () => {
  it("uses @theme static so unreferenced tokens are still emitted", () => {
    // Without `static`, Tailwind tree-shakes theme variables no generated
    // utility mentions. --color-glow and --color-accent-1/-2 are consumed only
    // through the legacy var(--glow) / var(--accent-1) names, which Tailwind
    // cannot see -- they would vanish and the glow would silently disappear.
    expect(CSS).toContain("@theme static {");
  });

  const TOKENS: Record<string, string> = {
    "--color-page": "#0c0a09",
    "--color-card": "#2c231a",
    "--color-inset": "#1c1712",
    "--color-text": "#f4ede2",
    "--color-text-dim": "#8a7f70",
    "--color-accent": "#ff8a2b",
    "--color-accent-mid": "#ff8a2b",
    "--color-accent-1": "#ffc24b",
    "--color-accent-2": "#ff5e1a",
    "--color-glow": "#ff7a1a",
    "--radius-card": "18px",
    "--radius-pill": "999px",
    "--ease-out-cubic": "cubic-bezier(0.33, 1, 0.68, 1)",
  };

  it("declares every token at its original value", () => {
    for (const [name, value] of Object.entries(TOKENS)) {
      expect(CSS, `${name} missing or changed`).toContain(`${name}: ${value};`);
    }
  });

  it("keeps the legacy names resolving, so the seven stylesheets need no edit", () => {
    for (const [legacy, themed] of [
      ["--page", "--color-page"],
      ["--card", "--color-card"],
      ["--inset", "--color-inset"],
      ["--card-border", "--color-card-border"],
      ["--text", "--color-text"],
      ["--text-dim", "--color-text-dim"],
      ["--label", "--color-label"],
      ["--probe-label", "--color-probe-label"],
      ["--setpoint", "--color-setpoint"],
      ["--ok", "--color-ok"],
      ["--warn", "--color-warn"],
      ["--danger", "--color-danger"],
      ["--track", "--color-track"],
      ["--cooking", "--color-cooking"],
      ["--igniter", "--color-igniter"],
      ["--icon-idle", "--color-icon-idle"],
      ["--dot-idle", "--color-dot-idle"],
      ["--row-label", "--color-row-label"],
      ["--accent-ember", "--color-accent-ember"],
      ["--accent-ice", "--color-accent-ice"],
      ["--accent-crimson", "--color-accent-crimson"],
      ["--card-radius", "--radius-card"],
      ["--pill-radius", "--radius-pill"],
      ["--accent", "--color-accent"],
      ["--accent-mid", "--color-accent-mid"],
      ["--accent-1", "--color-accent-1"],
      ["--accent-2", "--color-accent-2"],
      ["--glow", "--color-glow"],
    ]) {
      expect(CSS, `${legacy} is not aliased to ${themed}`).toContain(`${legacy}: var(${themed});`);
    }
    // No Tailwind namespace for durations; stays a plain custom property.
    expect(CSS).toContain("--anim-ms: 250ms;");
    // --ease-* IS a Tailwind namespace, so the themed name and the legacy name
    // are the same string. Aliasing it to itself would be a var() cycle.
    expect(CSS).not.toContain("--ease-out-cubic: var(--ease-out-cubic)");
  });

  it("keeps all three accents overriding the THEMED name", () => {
    // The switcher works by attribute on :root. These rules are unlayered, so
    // they beat @theme's layered value; the --accent alias then follows.
    for (const [attr, accent, a1, a2, glow] of [
      ["ice", "#3cc7d0", "#7ef0d2", "#1f9fb8", "#2ec5d3"],
      ["crimson", "#ff6a5a", "#ff9f43", "#e11d48", "#ff5a4d"],
    ]) {
      const at = CSS.indexOf(`:root[data-accent="${attr}"]`);
      expect(at, `no rule for the ${attr} accent`).toBeGreaterThan(-1);
      const block = CSS.slice(at, CSS.indexOf("}", at));
      expect(block).toContain(`--color-accent: ${accent};`);
      expect(block).toContain(`--color-accent-1: ${a1};`);
      expect(block).toContain(`--color-accent-2: ${a2};`);
      expect(block).toContain(`--color-glow: ${glow};`);
    }
  });
});

// --------------------------------------------------- no second copy anywhere

/** Every source file that could hold a colour, minus theme.css and the tests. */
function sourceFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      sourceFiles(path, acc);
    } else if (
      /\.(css|ts|tsx)$/.test(entry.name) &&
      !/\.test\.tsx?$/.test(entry.name) &&
      entry.name !== "theme.css"
    ) {
      acc.push(path);
    }
  }
  return acc;
}

/** Literals that equal a Qt token value but must NOT become one. Each entry is
 *  a decision, not an exemption to be extended casually. */
const LITERAL_ALLOWLIST: Record<string, string> = {
  // historyAdapter.ts lives in packages/pifire-core/src/history/, outside this
  // scan of web-react's own `src` tree, so it needs no allowlist entry here.
  // Its FALLBACK_COLOR literal is still pinned to Theme.dim below via a direct
  // file read.
  //
  // IgniterIcon's three flame strokes are a fixed gradient (Qt's
  // IgniterIcon.qml hardcodes the same three). The highlight #ff9f43 happens to
  // equal Crimson's arcStop2; tokenising it would make the flame follow the
  // accent, which is exactly what neither UI does.
  "src/components/dashboard/SystemStatus.tsx": "#ff9f43",
};

describe("no colour outside theme.css duplicates a Qt token", () => {
  const qtValues = new Map<string, string>();
  for (const [name, colour] of QT) {
    const values = colour.kind === "fixed" ? [colour.value] : ACCENTS.map((a) => colour.value[a]);
    for (const v of values) {
      if (v[3] === 1) {
        const hex = `#${v
          .slice(0, 3)
          .map((n) => Math.round(n).toString(16).padStart(2, "0"))
          .join("")}`;
        qtValues.set(hex, name);
      }
    }
  }

  it("finds hardcoded Theme.qml values nowhere but the allowlist", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles("src")) {
      const text = readFileSync(file, "utf8");
      const allowed = LITERAL_ALLOWLIST[file.replace(/\\/g, "/")];
      for (const m of text.matchAll(/#[0-9a-fA-F]{6}\b/g)) {
        const hex = m[0].toLowerCase();
        const qtName = qtValues.get(hex);
        if (qtName === undefined || hex === allowed) continue;
        offenders.push(`${file}: ${hex} is Theme.${qtName} -- use the token`);
      }
    }
    expect(offenders, offenders.join("\n")).toEqual([]);
  });

  it("keeps the chart's canvas fallback equal to Theme.dim", () => {
    const src = readFileSync("../packages/pifire-core/src/history/historyAdapter.ts", "utf8");
    const m = /const FALLBACK_COLOR = "(#[0-9a-f]{6})"/i.exec(src);
    expect(m, "historyAdapter no longer declares FALLBACK_COLOR the expected way").not.toBeNull();
    const qt = QT.get("dim");
    if (m === null || qt?.kind !== "fixed") return;
    expect(same(parseColor(m[1]), qt.value), `FALLBACK_COLOR ${m[1]} is not Theme.dim`).toBe(true);
  });
});
