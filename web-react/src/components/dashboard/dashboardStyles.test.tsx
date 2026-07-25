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
    // These are the numbers the fidelity gate pins to +/-0.5px. If the
    // extraction changed one, it changed the design.
    expect(css).toMatch(/\.pf-dash-header\s*\{[^}]*height:\s*58px/);
    expect(css).toMatch(/\.pf-dash-probecol\s*\{[^}]*width:\s*298px/);
    expect(css).toMatch(/\.pf-dash-rightcol\s*\{[^}]*width:\s*300px/);
    expect(css).toMatch(/\.pf-dash-cookrow\s*\{[^}]*height:\s*52px/);
    expect(css).toMatch(/\.pf-dash-pills\s*\{[^}]*height:\s*64px/);
    expect(css).toMatch(/\.pf-dash-controls\s*\{[^}]*height:\s*82px/);
  });
});
