import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import type { CommandClient, CommandResult } from "../../helpers/command";
import { FIXTURE_DASH } from "../../helpers/fixture";
import type { LiveState } from "../../helpers/types";
import { Dashboard } from "./Dashboard";

afterEach(cleanup);

const OK: CommandResult = { ok: true, message: "" };

function makeCommand(): CommandClient {
  return {
    setMode: rs.fn(async () => OK),
    hold: rs.fn(async () => OK),
    setSmokePlus: rs.fn(async () => OK),
    setPMode: rs.fn(async () => OK),
    prime: rs.fn(async () => OK),
    timerStart: rs.fn(async () => OK),
    timerStartWithOptions: rs.fn(async () => OK),
    timerPause: rs.fn(async () => OK),
    timerStop: rs.fn(async () => OK),
    timerShutdown: rs.fn(async () => OK),
    timerKeepWarm: rs.fn(async () => OK),
    system: rs.fn(async () => OK),
    setUnits: rs.fn(async () => OK),
    manualOutput: rs.fn(async () => OK),
    manualPwm: rs.fn(async () => OK),
    recipeNextStep: rs.fn(async () => OK),
    hopperCheck: rs.fn(async () => OK),
  };
}

function renderDash(dash: LiveState = FIXTURE_DASH) {
  return render(
    <MemoryRouter>
      <Dashboard
        dash={dash}
        command={makeCommand()}
        apiBase=""
        phase="live"
        controlAlive={true}
        accent="ember"
        setAccent={rs.fn()}
        animate={false}
        setAnimate={rs.fn()}
      />
    </MemoryRouter>,
  );
}

// jsdom does no layout, so this file deliberately asserts NOTHING about
// geometry. What it can assert -- and what the reflow depends on -- is that the
// layout-bearing declarations live somewhere a @media query can reach them. You
// cannot write a breakpoint against a style={{...}} object.
//
// Everything a media query needs to override: box size, spacing, the flex/grid
// model, and type size. Colour is NOT on this list: it is not responsive, and
// most of it is genuinely dynamic (deriveView output).
const LAYOUT_PROPS = [
  "width",
  "height",
  "padding",
  "margin",
  "gap",
  "flex",
  "display",
  "font",
  "grid",
];

const OFFENDING = (styleAttr: string): string[] =>
  styleAttr
    .split(";")
    .map((d) => d.trim())
    .filter((d) => d.length > 0)
    // Custom properties are how DYNAMIC values (a bar percentage, an accent
    // tint) reach the stylesheet. They are the intended escape hatch, not a
    // violation.
    .filter((d) => !d.startsWith("--"))
    .filter((d) => LAYOUT_PROPS.some((p) => d.split(":")[0].trim().startsWith(p)));

describe("dashboard layout lives in CSS, not in style objects", () => {
  it("gives every landmark a class name", () => {
    const { container } = renderDash();
    const landmarks = container.querySelectorAll("[data-pf]");
    expect(landmarks.length).toBeGreaterThan(10);
    const unclassed = [...landmarks]
      .filter((el) => el.getAttribute("class") === null || el.getAttribute("class") === "")
      .map((el) => el.getAttribute("data-pf"));
    expect(unclassed).toEqual([]);
  });

  it("leaves no layout-bearing declaration inline on the dashboard surface", () => {
    const { container } = renderDash({
      ...FIXTURE_DASH,
      // A shape that renders every conditional box: probes, the hopper card,
      // the lid-open block.
      currentMode: "Hold",
      hasDistanceSensor: true,
      lidOpenDetected: true,
    });
    const violations: string[] = [];
    for (const el of container.querySelectorAll("[style]")) {
      // SVG internals are drawing instructions, not layout: their geometry is
      // in attributes and viewBox coordinates, and no breakpoint touches them.
      if (el.closest("svg") !== null) continue;
      const bad = OFFENDING(el.getAttribute("style") ?? "");
      if (bad.length > 0) {
        const tag = el.tagName.toLowerCase();
        const name = el.getAttribute("data-pf") ?? el.getAttribute("class") ?? tag;
        violations.push(`${name}: ${bad.join("; ")}`);
      }
    }
    expect(violations, violations.join("\n")).toEqual([]);
  });
});

describe("dashboard.css carries the extracted rules", () => {
  const css = readFileSync("src/components/dashboard/dashboard.css", "utf8");

  it("declares a rule for each landmark box", () => {
    for (const cls of [
      ".pf-dash-header",
      ".pf-dash-body",
      ".pf-dash-probecol",
      ".pf-dash-rightcol",
      ".pf-dash-centercol",
      ".pf-dash-cookrow",
      ".pf-dash-pills",
      ".pf-dash-controls",
      ".pf-dash-probecard",
      ".pf-dash-gauge",
      ".pf-dash-system",
      ".pf-dash-hopper",
    ]) {
      expect(css).toContain(cls);
    }
  });

  it("keeps the authored 1280x720 constants", () => {
    // The numbers the fidelity gate pins to +/-0.5px. Three of them now live in
    // size tokens (see the token block below); the other two are still literal
    // because nothing needs to override them per breakpoint.
    expect(css).toMatch(/\.pf-dash-cookrow\s*\{[^}]*height:\s*52px/);
    expect(css).toMatch(/\.pf-dash-pills\s*\{[^}]*height:\s*64px/);
    expect(css).toMatch(/\.pf-dash-header\s*\{[^}]*height:\s*var\(--pf-header-h\)/);
    expect(css).toMatch(/\.pf-dash-probecol\s*\{[^}]*width:\s*var\(--pf-probecol-w\)/);
    expect(css).toMatch(/\.pf-dash-rightcol\s*\{[^}]*width:\s*var\(--pf-col-w\)/);
    expect(css).toMatch(/\.pf-dash-controls\s*\{[^}]*height:\s*var\(--pf-btn-h\)/);
  });
});

// Task 12's contract, and the thing Task 13 depends on: every size a
// breakpoint will want to change is behind a named token, and the desktop value
// of each token is the literal it replaced. jsdom resolves neither var() nor
// media queries, so this is a stylesheet-text assertion by necessity -- the
// rendered result is the Playwright fidelity gate's job.
describe("dashboard size tokens", () => {
  const css = readFileSync("src/components/dashboard/dashboard.css", "utf8");
  // The token block specifically -- located by one of its own declarations,
  // because `.pf-stage` also has an earlier rule carrying the stage geometry.
  const tokenStart = css.lastIndexOf("{", css.indexOf("--pf-header-h"));
  const root = css.slice(tokenStart, css.indexOf("}", tokenStart) + 1);

  const TOKENS: Record<string, string> = {
    "--pf-header-h": "58px",
    "--pf-probecol-w": "298px",
    "--pf-col-w": "300px",
    "--pf-gauge-size": "392px",
    "--pf-gauge-ring": "360px",
    "--pf-gauge-num": "112px",
    "--pf-gauge-unit": "40px",
    "--pf-probe-temp": "66px",
    "--pf-probe-unit": "26px",
    "--pf-probe-name": "15px",
    "--pf-btn-font": "25px",
    "--pf-btn-h": "82px",
    "--pf-cook-val": "26px",
    "--pf-pill-val": "24px",
    "--pf-hopper-val": "34px",
  };

  it("declares every token on the dashboard root, at its original value", () => {
    for (const [name, value] of Object.entries(TOKENS)) {
      expect(root, `${name} missing from the root rule`).toContain(`${name}: ${value};`);
    }
  });

  it("consumes each token somewhere outside the root rule", () => {
    const body = css.replace(root, "");
    for (const name of Object.keys(TOKENS)) {
      expect(body, `${name} is declared but never used`).toContain(`var(${name}`);
    }
  });

  it("leaves no raw px font-size on a tokenised element", () => {
    for (const rule of [
      ".pf-dash-gauge-temp",
      ".pf-dash-gauge-unit",
      ".pf-dash-probetemp-int",
      ".pf-dash-probetemp-unit",
    ]) {
      const block = css.slice(css.indexOf(`${rule} {`), css.indexOf("}", css.indexOf(`${rule} {`)));
      expect(block, `${rule} still hard-codes a size`).not.toMatch(/font-size:\s*\d/);
    }
  });

  // .pf-btn is shared with the wizard and settings surfaces, which never see
  // the dashboard root and so never see the token.
  it("gives the shared button font-size a fallback", () => {
    expect(css).toContain("font-size: var(--pf-btn-font, 25px)");
  });
});

// A var() inside a shorthand makes the whole declaration pending-substitution:
// it expands at computed-value time, AFTER the cascade, and resets every
// longhand it owns -- including font-variant-numeric. A tokenised
// `font: 800 var(--x) "..."` followed by `font-variant-numeric: tabular-nums`
// therefore loses the tabular figures, and the digits change width while every
// landmark box stays exactly where it was. Found by the fidelity screenshot,
// which is precisely the class of regression the landmark gate cannot see.
describe("size tokens never sit inside a shorthand", () => {
  it("never combines the font shorthand with font-variant-numeric", () => {
    // Measured, not assumed: the clock computed font-variant-numeric: normal
    // while its rule said tabular-nums, because this build's CSS pipeline folds
    // the following longhand back into the shorthand. Tabular figures are the
    // whole point of a clock and a percentage readout that update in place.
    const css = readFileSync("src/components/dashboard/dashboard.css", "utf8");
    const rules = css.split("}");
    const offenders = rules
      .filter((r) => /^\s*font:\s/m.test(r) && r.includes("font-variant-numeric"))
      .map((r) => r.trim().split("\n")[0]);
    expect(offenders, offenders.join("\n")).toEqual([]);
  });

  it("uses font longhands wherever a font size is tokenised", () => {
    const css = readFileSync("src/components/dashboard/dashboard.css", "utf8");
    const offenders = css
      .split("\n")
      .filter((line) => /^\s*font:\s/.test(line) && line.includes("var(--pf-"));
    expect(offenders, offenders.join("\n")).toEqual([]);
  });
});
