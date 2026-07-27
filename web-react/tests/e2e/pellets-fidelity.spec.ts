import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import {
  baselinePath,
  CAPTURING,
  compareToBaseline,
  measureSelectors,
  readBaseline,
  writeBaseline,
} from "./layoutBaseline";
import { DESKTOP, PELLETS_SPEC, PHONE } from "./pageSpecs";

// PRECONDITIONS: a real backend -- control.py plus gunicorn -- and, in a jj
// workspace, PIFIRE_BACKEND_URL pointing at the origin that backend serves.
// (Only the HTTP API is read here, so no PIFIRE_DB_PATH is needed: the node
// side never opens pifire.db, it asks the same server the browser does.)

const FINGERPRINT = "tests/e2e/baselines/pellets-fingerprint.json";

interface PelletDbShape {
  brands: string[];
  woods: string[];
  archive: Record<string, unknown>;
  current: { pelletid: string };
  /** timestamp -> profile id. An OBJECT, not an array: see
   *  src/helpers/pellets/pelletTypes.ts and common/defaults.py. Counting it
   *  with `.length` would silently record `undefined` on both sides of the
   *  comparison and drop the load log from the fingerprint entirely. */
  log: Record<string, string>;
}

/** What the layout of /pellets actually depends on: how many rows each table
 *  renders, and which profile is loaded. Not the contents -- a renamed brand
 *  moves no box, and pinning names would make the gate unusable on any machine
 *  but the one that captured it. */
function fingerprint(db: PelletDbShape): Record<string, number | string> {
  return {
    brands: db.brands.length,
    woods: db.woods.length,
    archive: Object.keys(db.archive).length,
    log: Object.keys(db.log).length,
    current: db.current.pelletid,
  };
}

for (const viewport of [DESKTOP, PHONE]) {
  test.describe(`${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport });

    test("pellets matches the committed baseline", async ({ page }) => {
      const res = await page.request.get("/api/pellets");
      expect(res.ok(), "GET /api/pellets failed -- is control.py + gunicorn running?").toBeTruthy();
      const db = ((await res.json()) as { data: { pellets: PelletDbShape } }).data.pellets;
      const fp = fingerprint(db);

      if (!CAPTURING) {
        const want = existsSync(FINGERPRINT)
          ? (JSON.parse(readFileSync(FINGERPRINT, "utf8")) as Record<string, number | string>)
          : null;
        expect(want, `${FINGERPRINT} is missing. Run \`bun run baseline:capture\`.`).not.toBeNull();
        // A skip, not a failure. This machine's pellet store is a different
        // shape from the one the reference was captured on, so its boxes are
        // legitimately different and there is nothing to compare. The gate is
        // silent here BY DESIGN -- which is exactly why Task 15 requires this
        // test to have RUN, not skipped, on the reviewing machine.
        test.skip(
          JSON.stringify(want) !== JSON.stringify(fp),
          `pellet store differs from the baseline's: ${JSON.stringify(want)} vs ${JSON.stringify(fp)}. ` +
            `Re-capture on this machine, or run the gate where the reference was taken.`,
        );
      }

      await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
      await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
      await page.goto("/pellets");
      await expect(page.locator(PELLETS_SPEC.ready)).toBeVisible({ timeout: 20000 });

      const actual = await measureSelectors(page, PELLETS_SPEC);
      expect(Object.keys(actual).length).toBeGreaterThan(10);

      const path = baselinePath(PELLETS_SPEC.name, viewport);
      if (CAPTURING) {
        writeBaseline(path, actual);
        writeFileSync(FINGERPRINT, `${JSON.stringify(fp, null, 2)}\n`);
        console.log(`[pellets] captured ${path} at ${JSON.stringify(fp)}`);
        return;
      }
      const baseline = readBaseline(path);
      expect(baseline, `${path} is missing. Run \`bun run baseline:capture\`.`).not.toBeNull();
      const problems = compareToBaseline(actual, baseline ?? {}, PELLETS_SPEC.exact);
      expect(problems, problems.join("\n")).toEqual([]);
    });
  });
}
