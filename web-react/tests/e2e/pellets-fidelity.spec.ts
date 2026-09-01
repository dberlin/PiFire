import { existsSync, readFileSync, writeFileSync } from "node:fs";

import type { APIRequestContext } from "@playwright/test";
import { expect, test } from "@playwright/test";

import {
  baselinePath,
  CAPTURING,
  compareToBaseline,
  freezeDate,
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

// The grill name is part of this page's LAYOUT, and until now nothing pinned it.
//
// `.pf-nav-brand` renders `PiFire | <grill name>`, and `.pf-nav-actions` is
// pushed right by `margin-left: auto` (shell.css) -- a computed style this
// harness compares EXACTLY. The used value of that auto margin is the leftover
// space in the navbar, so it moves one-for-one with the rendered width of the
// grill name. This is the only fidelity project pointed at the live backend
// (the rest run on the demo server, whose name comes from demoData and is
// therefore fixed), so it is the only one exposed to it -- and settings.spec.ts
// writes a randomised `E2E Grill <4 digits>` into that live backend, leaving it
// there. Whichever digits it happened to draw moved this margin by a few px and
// failed the gate, or did not, depending on spec order.
//
// The pin has to reproduce the width the committed baseline encodes, because
// tests/e2e/baselines/*.json are the untouched pre-Tailwind reference and are
// not regenerable to suit the page. Deduced from the measurement itself:
// pellets-1280x720.json records `.pf-nav-grill` w:83 and `.pf-nav-brand` w:169,
// and the navbar arithmetic (16 padding + 169 brand + 16 gap + 552 list + 16
// gap, out to `.pf-nav-actions` at x:1226) gives exactly the 457px margin it
// also records -- so the baseline was captured with one of settings.spec.ts's
// own names, 83px wide in Barlow 600 13px. In that family a bare digit costs a
// fixed width, '1' costs 2px less and '7' 1px less, which puts "E2E Grill 7000"
// at exactly 83px; measured, it reproduces w:83 / w:169 / margin-left:457px at
// 1280 and the 89px this file's 390x844 baseline records. Any other string of
// that width would do; none of them is any less arbitrary, because the width is
// what the baseline fixed.
const PINNED_GRILL_NAME = "E2E Grill 7000";

interface PelletDbShape {
  brands: string[];
  woods: string[];
  archive: Record<string, unknown>;
  current: { pelletid: string };
  /** timestamp -> profile id. An OBJECT, not an array: see
   *  src/helpers/contracts/control.gen.ts and common/defaults.py. Counting it
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

let previousGrillName: string | null = null;

async function setGrillName(request: APIRequestContext, name: string): Promise<void> {
  const res = await request.post("/api/settings_update", {
    data: { settings: { globals: { grill_name: name } } },
  });
  expect(res.ok(), `setting the grill name to "${name}" failed`).toBeTruthy();
}

test.beforeAll(async ({ request }) => {
  const res = await request.get("/api/settings");
  expect(res.ok(), "GET /api/settings failed -- is control.py + gunicorn running?").toBeTruthy();
  const body = (await res.json()) as { settings?: { globals?: { grill_name?: string } } };
  previousGrillName = body.settings?.globals?.grill_name ?? "";
  await setGrillName(request, PINNED_GRILL_NAME);
});

// Put back whatever this machine had, whether or not the measurements passed.
// The pin is this file's business; leaving it behind would just move the
// order-dependence somewhere else.
test.afterAll(async ({ request }) => {
  if (previousGrillName !== null) await setGrillName(request, previousGrillName);
});

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
        // silent here BY DESIGN -- which is why the gate is only meaningful
        // when this test RAN, rather than skipped, on the reviewing machine.
        test.skip(
          JSON.stringify(want) !== JSON.stringify(fp),
          `pellet store differs from the baseline's: ${JSON.stringify(want)} vs ${JSON.stringify(fp)}. ` +
            `Re-capture on this machine, or run the gate where the reference was taken.`,
        );
      }

      await freezeDate(page);
      await page.goto("/pellets");
      await expect(page.locator(PELLETS_SPEC.ready)).toBeVisible({ timeout: 20000 });
      // The pin, checked in the DOM rather than assumed from the POST: the name
      // reaches this page over the socket, and measuring before it arrives would
      // record the boot fixture's name and fail on a margin nobody would connect
      // to a grill name.
      await expect(page.locator(".pf-nav-grill")).toHaveText(PINNED_GRILL_NAME, {
        timeout: 20000,
      });

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
      const problems = compareToBaseline(
        actual,
        baseline ?? {},
        PELLETS_SPEC.exact,
        PELLETS_SPEC.textSized,
      );
      expect(problems, problems.join("\n")).toEqual([]);
    });
  });
}
