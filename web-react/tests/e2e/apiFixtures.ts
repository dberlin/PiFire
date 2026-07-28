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

/**
 * The two recipe surfaces (RecipeList, RecipePage), and the only cover
 * components/recipes/recipes.css has -- it landed after the Tailwind plan was
 * written, like cookfiles.css, so nothing else would notice a wrong @apply or
 * preflight reset here either.
 *
 * Installed per-spec rather than in stubApi(): the fidelity-pages project
 * runs against the demo server, which has no backend for either route at all,
 * so an unstubbed fetch would render the error branch and the baseline would
 * encode that instead of the styled surface.
 *
 * Route order matters the same way it does in stubCookFiles: the detail
 * pattern has a "/" right after "recipes" where the listing pattern's `*`
 * cannot follow, so the listing route can never swallow a detail request
 * whatever order they are installed in.
 */
/**
 * The whole admin page, from its one read.
 *
 * The fixture deliberately leaves `backups.pelletdb` empty and fills
 * `backups.settings`: both the populated list and the "no backups yet" note
 * are styled surfaces, and a fixture with two full lists would leave the empty
 * branch uncovered by any baseline.
 *
 * `mode` is "Stop" so the destructive controls render ENABLED. The disabled
 * variant is what a baseline would silently start encoding if this ever
 * changed, which is why admin.spec.ts asserts the mode gate separately rather
 * than leaving it to the fidelity gate.
 *
 * Nothing else on this page is stubbed because nothing else is fetched: every
 * write endpoint is reached only by a click, and admin.spec.ts never confirms
 * one.
 */
export async function stubAdmin(page: Page): Promise<void> {
  await page.route("**/api/admin/state", (r) => json(r, body("admin-state.json")));
}

/** The events page, pinned so a baseline capture does not photograph whatever
 *  the operator's log happened to say -- one real line has a timestamp, a build
 *  number and a debug flag in it, all of which move. Routed BEFORE the listing
 *  because `**\/api/admin/logs` would otherwise also swallow `/logs/view`. */
export async function stubEvents(page: Page): Promise<void> {
  await page.route("**/api/admin/logs/view*", (r) =>
    r.fulfill({
      status: 200,
      contentType: "text/plain",
      headers: { "Accept-Ranges": "bytes" },
      body: "2026-07-28 09:00:00 -0400 [INFO] PiFire Web UI started.\n",
    }),
  );
  await page.route("**/api/admin/logs", (r) =>
    json(
      r,
      JSON.stringify({
        result: "OK",
        message: "",
        data: {
          logs: ["events.log"],
          families: [{ stem: "events", members: ["events.log"], bytes: 42 }],
        },
      }),
    ),
  );
}

/** One finished Smoke record, exactly as GET /api/metrics serves one.
 *
 * Smoke is the widest of the five row sets (ten rows), so a single card
 * exercises every branch of metricRows that a card can show. The values are
 * copied from a real response -- notably `timeinmode` as "1 m 30 s" (the
 * minute branch needs `seconds > 60`, so 60 000 ms would read "60 s") and
 * `fanontime_c` as the STRING "0", which is what a TEXT column returns. */
const METRICS_PAYLOAD = {
  result: "OK",
  message: null,
  data: {
    units: "F",
    augerrate: 0.3,
    metrics: [
      {
        id: "m1",
        starttime: 1700000000000,
        starttime_c: "17:13:20",
        endtime: 1700000090000,
        endtime_c: "17:14:50",
        timeinmode: "1 m 30 s",
        mode: "Smoke",
        augerontime: 100,
        augerontime_c: "100 s",
        estusage_m: "30 grams",
        estusage_i: "0.07 pounds (1.06 ounces)",
        fanontime: 60,
        fanontime_c: "0",
        smokeplus: true,
        primary_setpoint: 225,
        smart_start_profile: 2,
        startup_temp: 160,
        p_mode: 2,
        auger_cycle_time: 20,
        pellet_level_start: 90,
        pellet_level_end: 85,
        pellet_brand_type: "Lumber Jack Hickory",
      },
    ],
  },
};

export async function stubMetrics(page: Page): Promise<void> {
  //  Pinned content, not the live table: a real record carries wall-clock
  //  timestamps and a pellet estimate that both move between captures, and a
  //  baseline photographs geometry, not the grill's afternoon. It also makes
  //  the page renderable on a machine that has never lit the grill.
  //
  //  The export endpoint is NOT stubbed: nothing fetches it, the link only
  //  carries its href, and a baseline never follows a download.
  await page.route("**/api/metrics", (r) => json(r, JSON.stringify(METRICS_PAYLOAD)));
}

export async function stubRecipes(page: Page): Promise<void> {
  await page.route("**/api/files/recipes/detail*", (r) => json(r, body("recipe-detail.json")));
  await page.route("**/api/files/recipes*", (r) => json(r, body("recipe-listing.json")));
  await page.route("**/static/img/tmp/**", (r) =>
    r.fulfill({ status: 200, contentType: "image/png", body: PNG_8x8 }),
  );
}
