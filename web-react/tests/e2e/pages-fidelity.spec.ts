import { expect, type Page, test } from "@playwright/test";
import { stubApi } from "./apiFixtures";
import {
  baselinePath,
  CAPTURING,
  compareToBaseline,
  freezeDate,
  measureSelectors,
  requireBaseline,
  writeBaseline,
} from "./layoutBaseline";
import { DESKTOP, PAGE_SPECS, PHONE } from "./pageSpecs";

/**
 * Put the page in its RESTING state before anything is measured.
 *
 * Playwright leaves the pointer wherever it last clicked, so after a wizard
 * spec's Next chain it is sitting on the Next button -- and `.pf-btn` carries
 * `transition: background 120ms ease` while
 * `.pf-wizard .pf-btn-primary:hover` swaps var(--accent) for var(--accent-1).
 * Measuring straight after the click therefore samples that transition
 * wherever it happens to have got to. It is not theoretical: the first capture
 * recorded FIVE different backgrounds for the same button across the six
 * wizard steps -- rgb(255,138,43) on welcome, (255,178,66) on grillplatform,
 * (255,193,74) on probes, (255,194,75) on display/distance -- purely as a
 * function of how long each step's click chain took, and re-running the
 * capture moved grillplatform's value again.
 *
 * (0, 0) is where Playwright's pointer starts for a spec that never clicks, so
 * parking it there makes the clicked and unclicked specs agree on one state
 * rather than inventing a third. Then wait out the un-hover.
 *
 * CSSTransition only, NOT every entry `getAnimations()` returns: `pf-blink`,
 * `header-pulse` and `pf-install-stripes` are `infinite`, so awaiting their
 * `finished` would hang until the test timed out. (None of the three is on a
 * measured landmark, which is why the recorded `opacity` values are stable
 * without further help.)
 */
async function settle(page: Page): Promise<void> {
  await page.mouse.move(0, 0);
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((a) => a instanceof CSSTransition)
        .map((a) => a.finished.catch(() => undefined)),
    ),
  );
}

// The fidelity gate for every REST-fed surface, at both viewports.
//
// Deliberately NOT a toHaveScreenshot() gate, and it must not be "upgraded"
// into one: pixels still depend on the host's rasteriser and on the fallback
// stack behind Barlow, and masking the volatile regions would mask exactly the
// typography the gate exists to protect. (Barlow itself is self-hosted via
// @fontsource -- see index.html -- so it no longer depends on the network. What
// remains host-dependent is how glyphs rasterise, not which font loads.)
//
// What it records instead, per landmark: the box (x/y/w/h relative to the
// page's root, +/-2px), the type (fontSize/fontWeight, exact), and 32 computed
// visual properties (colour, border, radius, shadow, padding, margin, flex,
// position -- exact). Geometry alone cannot see a wrong @apply bg-*, which is
// the entire class of defect this migration can introduce.

for (const viewport of [DESKTOP, PHONE]) {
  test.describe(`${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    for (const spec of PAGE_SPECS) {
      test(`${spec.name} matches the committed baseline`, async ({ page }) => {
        await freezeDate(page);
        await stubApi(page);
        // Per-spec fixtures on top of the shared set. Registered AFTER stubApi
        // so a spec can override a shared route: Playwright runs handlers in
        // reverse registration order.
        await spec.stubs?.(page);
        await page.goto(spec.path);
        // The measurement root, NOT `ready`, is what proves the page arrived:
        // a wizard spec's `ready` names the DESTINATION step's own heading, and
        // /wizard always opens on Welcome, so asserting it here would fail
        // every step past the first before a single Next had been clicked.
        await expect(page.locator(spec.root).first()).toBeVisible({ timeout: 15000 });

        for (const click of spec.clicks ?? []) {
          await page.locator(click).first().click();
        }
        // `ready` after the clicks: it names the step's own heading, so this is
        // what proves the wizard actually advanced before measuring. For a spec
        // with no clicks it is simply the page's own readiness check.
        await expect(page.locator(spec.ready).first()).toBeVisible({ timeout: 15000 });
        await settle(page);

        const actual = await measureSelectors(page, spec);
        // A spec whose selectors match nothing produces an empty, cheerful
        // baseline. Refuse to record one.
        expect(
          Object.keys(actual).length,
          `${spec.name} measured no landmarks -- check pageSpecs.ts`,
        ).toBeGreaterThan(5);

        const path = baselinePath(spec.name, viewport);
        if (CAPTURING) {
          writeBaseline(path, actual);
          console.log(`[fidelity] captured ${path} (${Object.keys(actual).length} landmarks)`);
          return;
        }
        const problems = compareToBaseline(
          actual,
          requireBaseline(path),
          spec.exact,
          spec.textSized,
        );
        expect(
          problems,
          `${spec.name} @ ${viewport.width}x${viewport.height}\n${problems.join("\n")}`,
        ).toEqual([]);
      });
    }
  });
}

// Not a landmark check: a page that fits in a desktop window and scrolls
// sideways on a phone is broken in a way no per-element baseline can see,
// because every element is individually where the baseline says it should be.
// dashboard-fidelity.spec.ts learned this the hard way -- the mode-button row
// was sliced across the bottom edge while the landmark gate passed
// byte-for-byte.
for (const viewport of [DESKTOP, PHONE]) {
  test.describe(`${viewport.width}x${viewport.height} overflow`, () => {
    test.use({ viewport });
    for (const spec of PAGE_SPECS) {
      test(`${spec.name} does not scroll sideways`, async ({ page }) => {
        await freezeDate(page);
        await stubApi(page);
        await spec.stubs?.(page);
        await page.goto(spec.path);
        await expect(page.locator(spec.root).first()).toBeVisible({ timeout: 15000 });
        for (const click of spec.clicks ?? []) {
          await page.locator(click).first().click();
        }
        await expect(page.locator(spec.ready).first()).toBeVisible({ timeout: 15000 });
        const doc = await page.evaluate(() => ({
          scrollW: document.documentElement.scrollWidth,
          innerW: window.innerWidth,
        }));
        expect(doc.scrollW, `${spec.name} scrolls sideways`).toBeLessThanOrEqual(doc.innerW);
      });
    }
  });
}
