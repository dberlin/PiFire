import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";
import {
  compareToBaseline,
  measureLandmarks,
  measureStageScale,
  readBaseline,
  writeBaseline,
} from "./layoutBaseline";

// The 1280x720 fidelity gate. This is the regression target for the reflow:
// "make it reflow, but do not change how it looks at 1280x720".
//
// It runs against the DEMO server (port 5174). Demo mode is load-bearing:
// useLiveState's demo branch opens no socket at all, so this spec cannot be
// raced by the shared PiFire instance the rest of the suite mutates, and
// demoDashAt pins the structure to mode Hold, exactly one food probe, hopper
// visible, no lid-open block and no banners -- a fixed DOM shape on every
// machine.
//
// It is deliberately NOT a toHaveScreenshot() gate: index.html loads Barlow
// from fonts.googleapis.com, so pixels depend on the network and the host font
// stack, and masking the volatile regions would mask exactly the typography the
// gate exists to protect. The PNG below is a human artifact, not an assertion.

const BASELINE = "tests/e2e/dashboard-layout-1280x720.json";
const ARTIFACTS = "tests/e2e/artifacts";

test("dashboard layout at 1280x720 matches the committed baseline", async ({ page }) => {
  // Freeze the clock BEFORE navigating so helpers/clock.ts's shared interval,
  // useClock and demoDashAt's elapsed-seconds argument are all pinned.
  await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
  await page.goto("/");
  await expect(page.locator('[data-pf="stage"]')).toBeVisible();

  const actual = await measureLandmarks(page);
  const scale = await measureStageScale(page);
  console.log(`[fidelity] stage scale = ${scale.toFixed(4)}`);

  mkdirSync(ARTIFACTS, { recursive: true });
  // What a user actually sees in a 1280x720 window, scrolled to the top.
  await page.locator('[data-pf="stage"]').screenshot({
    path: `${ARTIFACTS}/dashboard-1280x720.png`,
    animations: "disabled",
  });

  // The authored constants, asserted directly so a wholesale baseline
  // replacement cannot quietly relax them.
  expect(actual.stage.w).toBe(1280);
  expect(actual.stage.h).toBe(720);
  expect(actual.controls.h).toBe(82);

  const baseline = readBaseline(BASELINE);
  if (baseline === null) {
    // Recording run: the file is absent, so there is nothing to compare
    // against. Write it, then inspect it by hand and commit it.
    writeBaseline(BASELINE, actual);
    console.log(`[fidelity] wrote a fresh baseline to ${BASELINE} -- review it before committing`);
    return;
  }

  const problems = compareToBaseline(actual, baseline);
  expect(problems, problems.join("\n")).toEqual([]);
});

// THE OVERFLOW GATE.
//
// The landmark gate above cannot catch this, by construction: it measures
// stage-relative, scale-normalised geometry, so it reports the DESIGN as
// unchanged no matter what size that design is rendered at. When the fixed
// scaled board was first removed it passed byte-for-byte while the mode-button
// row -- Smoke / Hold / Smoke+ / Shutdown / Stop, the primary controls for a
// live fire -- was sliced horizontally across the bottom edge of a 1280x720
// window, and the hopper card lost its "LEVEL OK" footer. A human caught it.
//
// So this asserts the thing the human was actually looking at: at the reference
// resolution the whole dashboard is on screen, nothing scrolls, and the bottom
// row of controls is fully visible rather than clipped.
test("nothing is clipped or scrolled off a 1280x720 window", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
  await page.goto("/");
  await expect(page.locator('[data-pf="stage"]')).toBeVisible();

  const doc = await page.evaluate(() => ({
    scrollW: document.documentElement.scrollWidth,
    scrollH: document.documentElement.scrollHeight,
    innerW: window.innerWidth,
    innerH: window.innerHeight,
  }));
  expect(doc.scrollW, "the page scrolls sideways at 1280x720").toBeLessThanOrEqual(doc.innerW);
  expect(doc.scrollH, "the page scrolls vertically at 1280x720").toBeLessThanOrEqual(doc.innerH);

  // Every landmark, and every control button, inside the viewport. Checking the
  // controls specifically because they are the row that went off the edge, and
  // checking each button rather than the row because a clipped row still
  // reports its full height.
  const clipped = await page.evaluate(() => {
    const bad: string[] = [];
    const fits = (el: Element, name: string) => {
      const r = el.getBoundingClientRect();
      // 0.5px of tolerance for sub-pixel rounding of a scaled box.
      if (r.bottom > window.innerHeight + 0.5) {
        bad.push(`${name}: bottom ${Math.round(r.bottom)} > viewport ${window.innerHeight}`);
      }
      if (r.right > window.innerWidth + 0.5) {
        bad.push(`${name}: right ${Math.round(r.right)} > viewport ${window.innerWidth}`);
      }
    };
    for (const el of document.querySelectorAll<HTMLElement>("[data-pf]")) {
      fits(el, el.dataset.pf ?? "?");
    }
    for (const [i, el] of [...document.querySelectorAll(".pf-btn")].entries()) {
      fits(el, `control button ${i} (${el.textContent?.trim()})`);
    }
    return bad;
  });
  expect(clipped, clipped.join("\n")).toEqual([]);
});
