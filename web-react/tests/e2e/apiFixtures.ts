import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { Page, Route } from "@playwright/test";

const DIR = join("tests", "e2e", "fixtures");
const body = (file: string): string => readFileSync(join(DIR, file), "utf8");

const json = (route: Route, text: string): Promise<void> =>
  route.fulfill({ status: 200, contentType: "application/json", body: text });

/**
 * Serve every REST read the styled pages make from a committed fixture, and
 * acknowledge every write without letting it reach the backend.
 *
 * Two things this buys, both load-bearing for a fidelity baseline:
 *
 *   - The DOM shape stops depending on the machine's pifire.db. A settings tab
 *     renders a different number of probe cards, range rows and device rows on
 *     every install, and a landmark baseline is a list of boxes -- so without
 *     this the reference is only meaningful on the machine that captured it.
 *   - The capture stops MUTATING shared state. Stepping the wizard forward
 *     POSTs a draft (helpers/wizard/wizardApi.ts saveDraft), which
 *     wizard-layout.spec.ts has to clear afterwards. Here it never lands.
 *
 * Demo mode does NOT cover this: PUBLIC_DEMO is read only by
 * helpers/useLiveState.ts and only suppresses the socket. Every loader below
 * fetches over REST on the demo server exactly as on the app server.
 */
export async function stubApi(page: Page): Promise<void> {
  await page.route("**/api/settings", (r) => json(r, body("settings.json")));
  await page.route("**/api/controller_metadata", (r) => json(r, body("controller-metadata.json")));
  await page.route("**/api/get/mode", (r) => json(r, body("mode.json")));
  await page.route("**/api/wizard/state", (r) => json(r, body("wizard-state.json")));
  await page.route("**/api/history/chart*", (r) => json(r, body("history-chart.json")));
  // Writes: acknowledged, never forwarded. `**` globs match the query string
  // too, so /api/wizard/draft and /api/settings_update are covered whatever the
  // caller appends.
  await page.route("**/api/wizard/draft", (r) => json(r, '{"ok":true}'));
  await page.route("**/api/settings_update", (r) => json(r, '{"ok":true}'));
  await page.route("**/api/wizard/module-values", (r) => json(r, '{"values":{}}'));
}

/** An 8x8 opaque PNG, inline so no fixture file has to be a binary blob.
 *
 * Every cook-file <img> is boxed to a fixed square by cookfiles.css
 * (.pf-cf-thumb 48, .pf-cf-comment-thumb 64, .pf-cf-media-img 96,
 * .pf-cf-meta-thumb 128) with object-fit: cover, so the intrinsic size cannot
 * move the layout -- but a BROKEN image can, and every one of these URLs 404s
 * on the demo server. /static/img IS proxied to Flask (rsbuild.config.ts), so
 * without this route the boxes would be filled by whatever happens to be in
 * the developer's own history folder. */
const PNG_8x8 = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAEklEQVR42mNISCv4jw8zjAwFAJ1gjUG5Vml9AAAAAElFTkSuQmCC",
  "base64",
);

/**
 * The cook-file surfaces' fixtures, deliberately NOT part of stubApi().
 *
 * /history is measured by TWO specs. The `history` one predates the cook-file
 * list and its baseline is one of the 43 immutable pre-Tailwind references;
 * stubbing the listing inside stubApi() would change how many rows the second
 * section renders and move `.pf-section#1` in a file that must not move. So
 * these routes are installed per-spec, through PageSpec.stubs.
 *
 * Route patterns, and why they do not collide: Playwright anchors a glob at
 * both ends and `*` stops at "/", so the listing pattern -- which ends in a
 * single star straight after "cookfiles" -- matches the listing and its query
 * string only. `/api/files/cookfiles/detail?...` has a slash where that
 * pattern wants none, so it cannot be swallowed by the listing route.
 */
export async function stubCookFiles(page: Page): Promise<void> {
  await page.route("**/api/files/cookfiles/detail*", (r) => json(r, body("cookfile-detail.json")));
  // The cook chart payload is structurally a subset of the history one
  // (cookfileApi.ts: no graph_labels, no minutes), and history-chart.json
  // already carries numeric epoch time_labels -- which is what cookfileAdapter
  // requires to plot anything at all -- so it is reused rather than forked.
  await page.route("**/api/files/cookfiles/chart*", (r) => json(r, body("history-chart.json")));
  await page.route("**/api/files/cookfiles*", (r) => json(r, body("cookfile-listing.json")));
  await page.route("**/static/img/tmp/**", (r) =>
    r.fulfill({ status: 200, contentType: "image/png", body: PNG_8x8 }),
  );
}

/**
 * The module catalog behind /settings/probes.
 *
 * Its route loader (probeModulesLoader) is the only settings child with one,
 * and stubApi() does not cover /api/probe_modules -- so without this the tab
 * renders its errorElement rather than the surface being measured. Installed
 * per-spec, like the cook-file routes, so no other baseline can be affected by
 * its presence.
 *
 * The fixture is the real catalog as the running backend reports it: 18
 * modules with their descriptions and install requirements. Which modules
 * exist changes what DevicesCard offers, so a hand-trimmed list would measure
 * a surface no install actually has.
 */
export async function stubProbeModules(page: Page): Promise<void> {
  await page.route("**/api/probe_modules", (r) => json(r, body("probe-modules.json")));
}
