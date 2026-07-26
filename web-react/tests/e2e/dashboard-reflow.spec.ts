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

// A transform does not touch an element's LAYOUT box. `offsetWidth` is the
// layout width; `getBoundingClientRect()` is what the user actually sees, after
// every ancestor transform. The ratio between them is the rendered scale, and
// it is the ONLY thing that distinguishes the two states this spec exists to
// tell apart:
//
//   reflowed      -- a 390px-wide board, scale 1
//   letterboxed   -- a 1280px-wide board shrunk to 0.30, which also PRESENTS a
//                    390px rect, reports no page overflow, and keeps reporting
//                    its authored 66px font-size
//
// Measured, not theorised: with the fixed scaled board forced on at 390px,
// three of this file's five assertions passed. They were reading the visual
// rect and the computed font-size, both of which the old build satisfied while
// rendering ~20px type. Everything below measures layout width or an
// effective, scale-multiplied size for that reason.

test("the dashboard fills a phone viewport instead of letterboxing into it", async ({ page }) => {
  const viewport = page.viewportSize();
  expect(viewport).not.toBeNull();
  const width = viewport?.width ?? 0;

  const board = await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>('[data-pf="stage"]');
    if (el === null) throw new Error("no [data-pf=stage] on the page");
    const layoutWidth = el.offsetWidth;
    return {
      layoutWidth,
      renderedWidth: el.getBoundingClientRect().width,
      scale: layoutWidth === 0 ? 1 : el.getBoundingClientRect().width / layoutWidth,
    };
  });

  // The board is LAID OUT at the phone's width -- not laid out at 1280 and
  // shrunk into it. This is the assertion the old build slipped past.
  expect(Math.abs(board.layoutWidth - width)).toBeLessThanOrEqual(1);
  expect(Math.abs(board.renderedWidth - width)).toBeLessThanOrEqual(1);
  // No uniform scale is in play at this width at all.
  expect(board.scale).toBeGreaterThan(0.99);
  expect(board.scale).toBeLessThan(1.01);

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

  // scrollWidth alone is satisfied by the old build too: a 1280px board scaled
  // to 0.30 contributes a 390px visual rect, and `overflow: hidden` hides the
  // rest regardless. So also require that no landmark is LAID OUT wider than
  // the phone -- which the fixed board, at its unscaled 1280, is.
  const tooWide = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("[data-pf]")]
      .filter((el) => el.offsetWidth > window.innerWidth + 1)
      .map(
        (el) =>
          `${el.dataset.pf}: laid out at ${el.offsetWidth}px in a ${window.innerWidth}px viewport`,
      ),
  );
  expect(tooWide, tooWide.join("\n")).toEqual([]);
});

test("the probe temperature stays readable", async ({ page }) => {
  // Under the old uniform scale this rendered at roughly 20px -- the audit's
  // headline number. 36px is the floor for the number you read across a garden.
  //
  // It has to be the EFFECTIVE size. `getComputedStyle().fontSize` reports the
  // authored value and is completely blind to an ancestor's scale(): on the
  // letterboxed build it answers the same 66px it answers on a desktop, while
  // the glyphs on screen are 20px tall. Multiplying by the element's own
  // rendered scale is what turns this into the assertion its name claims.
  const size = await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>(".pf-dash-probetemp-int");
    if (el === null) throw new Error("no probe temperature on the page");
    const layout = el.offsetWidth;
    const scale = layout === 0 ? 1 : el.getBoundingClientRect().width / layout;
    return Number.parseFloat(getComputedStyle(el).fontSize) * scale;
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
