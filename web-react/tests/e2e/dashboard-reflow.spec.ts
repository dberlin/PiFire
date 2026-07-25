import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";

// The other half of the reflow gate. dashboard-fidelity.spec.ts proves the
// 1280x720 layout did not change; this proves something actually happens below
// it. @media queries prove nothing on their own -- a breakpoint that declares
// the wrong thing, or that no element consumes, is still a breakpoint.
//
// Every assertion here is one of the things the fixed scaled stage got wrong at
// 390px wide, where it rendered at scale ~0.30.
//
// Same demo server as the fidelity project, for the same reason: no socket, so
// this cannot be raced by the shared PiFire instance the rest of the suite
// mutates, and demoDashAt pins the content.

const ARTIFACTS = "tests/e2e/artifacts";

test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
  await page.goto("/");
  await expect(page.locator('[data-pf="stage"]')).toBeVisible();
});

test("the dashboard fills a phone viewport instead of letterboxing into it", async ({ page }) => {
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  const width = viewport?.width ?? 0;

  const stage = await page.locator('[data-pf="stage"]').boundingBox();
  expect(stage).not.toBeNull();
  // Not 1280 scaled down to fit: the board is as wide as the phone.
  expect(Math.abs((stage?.width ?? 0) - width)).toBeLessThanOrEqual(1);

  mkdirSync(ARTIFACTS, { recursive: true });
  // Taller viewport for the capture only: a phone page is expected to scroll,
  // and an artifact cut off at the fold is no use to a human reviewing the
  // layout. Both breakpoints are width-based, so this is the same layout.
  await page.setViewportSize({ width, height: 1600 });
  await page
    .locator('[data-pf="stage"]')
    .screenshot({ path: `${ARTIFACTS}/dashboard-390-full.png`, animations: "disabled" });
});

test("nothing overflows the page horizontally", async ({ page }) => {
  // A single 1280px-wide box inside a 390px viewport is the failure this
  // catches: sideways scrolling on a phone.
  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth);
});

test("the probe temperature stays readable", async ({ page }) => {
  // Under the old uniform scale this rendered at roughly 20px -- the audit's
  // headline number. 36px is the floor for the number you read across a garden.
  const size = await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>(".pf-dash-probetemp-int");
    if (el === null) throw new Error("no probe temperature on the page");
    return Number.parseFloat(getComputedStyle(el).fontSize);
  });
  expect(size).toBeGreaterThanOrEqual(36);
});

test("every control button is a usable touch target", async ({ page }) => {
  const heights = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>(".pf-btn")].map(
      (el) => el.getBoundingClientRect().height,
    ),
  );
  expect(heights.length).toBeGreaterThan(0);
  // 44px is the smallest comfortable touch target; the scaled stage delivered
  // about 25px here.
  for (const h of heights) expect(h).toBeGreaterThanOrEqual(44);
});

test("the control row wraps rather than squeezing five buttons into one line", async ({ page }) => {
  // grid-auto-flow: column would put all five in a single row at any width.
  // Each would be about 70px wide on a phone, with 17px type inside it.
  const rows = await page.evaluate(() => {
    const tops = [...document.querySelectorAll<HTMLElement>(".pf-btn")].map((el) =>
      Math.round(el.getBoundingClientRect().top),
    );
    return new Set(tops).size;
  });
  expect(rows).toBeGreaterThan(1);
});
