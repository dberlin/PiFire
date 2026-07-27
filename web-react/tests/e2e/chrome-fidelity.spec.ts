import { expect, test } from "@playwright/test";
import { stubApi } from "./apiFixtures";
import {
  CAPTURING,
  compareStyles,
  measureProbes,
  readStyleBaseline,
  writeStyleBaseline,
} from "./layoutBaseline";
import { CHROME_PROBES, DESKTOP, PHONE } from "./pageSpecs";

// Chrome that never renders under a fixed fixture, pinned synthetically.
//
// Banners returns null with no errors and no warnings (shell/Banners.tsx:22);
// TimerBar renders only while useTimerVisibility says so (shell/AppShell.tsx:39);
// the wizard's three role="dialog" surfaces need a running grill or a finished
// installer. Under the demo fixture none of them exist, so a page baseline
// covers none of them -- and they are ~40 of shell.css's 315 lines.
//
// This attaches a detached element carrying the class, reads its computed
// style, and removes it. tests/e2e/wizard-layout.spec.ts already does exactly
// this for the modal and the install stripe; this generalises it and gives it a
// committed baseline. It proves the rules resolve and to WHAT. It proves
// nothing about how they look in situ -- that is Task 15's human checkpoint.

for (const viewport of [DESKTOP, PHONE]) {
  test.describe(`${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    test("conditional chrome resolves to the committed styles", async ({ page }) => {
      await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
      await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
      await stubApi(page);
      // The shell (not the wizard) so shell.css, dashboard.css, settings.css and
      // pellets.css are all loaded; wizard.css arrives through the /wizard
      // route's own import, so the wizard probes get their own host below.
      await page.goto("/");
      await expect(page.locator(".pf-shell")).toBeVisible({ timeout: 15000 });

      const shellProbes = CHROME_PROBES.filter(
        (p) => !p.className.includes("pf-wizard-modal") && !p.className.includes("pf-install"),
      );
      const shell = await measureProbes(page, ".pf-shell", shellProbes);

      await page.goto("/wizard");
      await expect(page.locator(".pf-wizard-content")).toBeVisible({ timeout: 15000 });
      const wizardProbes = CHROME_PROBES.filter(
        (p) => p.className.includes("pf-wizard-modal") || p.className.includes("pf-install"),
      );
      const wizard = await measureProbes(page, ".pf-wizard-content", wizardProbes);

      const actual = { ...shell, ...wizard };
      expect(Object.keys(actual).length).toBe(CHROME_PROBES.length);

      const path = `tests/e2e/baselines/chrome-${viewport.width}x${viewport.height}.json`;
      if (CAPTURING) {
        writeStyleBaseline(path, actual);
        console.log(`[chrome] captured ${path}`);
        return;
      }
      const baseline = readStyleBaseline(path);
      if (baseline === null) {
        throw new Error(
          `${path} is missing. Run \`bun run baseline:capture\` and review the result.`,
        );
      }
      const problems = compareStyles(actual, baseline);
      expect(problems, problems.join("\n")).toEqual([]);
    });
  });
}

// Guard the guard. A synthetic probe that reads a rule which does not exist
// returns the browser's initial values for every property and looks perfectly
// healthy, so the mechanism has to be shown able to say "no".
test("an unstyled probe is distinguishable from a styled one", async ({ page }) => {
  await stubApi(page);
  await page.goto("/");
  await expect(page.locator(".pf-shell")).toBeVisible({ timeout: 15000 });
  const out = await measureProbes(page, ".pf-shell", [
    { name: "real", className: "pf-banner pf-banner--error" },
    { name: "ghost", className: "pf-banner-does-not-exist" },
  ]);
  // If these two agree, the probe is measuring the browser's defaults and every
  // assertion above it is vacuous.
  expect(out.real["background-color"]).not.toBe(out.ghost["background-color"]);
});
