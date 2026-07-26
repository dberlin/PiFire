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
