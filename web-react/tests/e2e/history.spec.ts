import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, type Page, test } from "@playwright/test";

// Requires the prototype backend running (control.py + gunicorn on :5000).
//
// GRILL MODE: deliberately NOT pinned with `ensureStopped`, unlike
// roundtrip.spec.ts. This page is mode-independent -- it only reads history --
// so a mode change would buy nothing here, while moving the single global
// grill mode is exactly the kind of side effect that makes this suite's specs
// interfere with each other. If the grill happens to be running, all it does
// to us is let control.py append a few real rows at the tail of the history
// store, so the assertions below are sized to tolerate that (see MID_MINUTES)
// rather than reaching for the mode to prevent it.
//
// STATE: `GET /api/history/chart` is READ-ONLY -- unlike the legacy
// `POST /history/refresh`, which persists settings["history_page"]["minutes"]
// as a side effect of merely being asked for a window, this endpoint never
// writes. So nothing the page itself does here needs restoring: driving the
// Minutes control and dragging the chart leave the backend exactly as found.
//
// The one thing that DOES need restoring is the history rows this file seeds
// (see below), which afterAll deletes by the exact id range it created.

// ---------------------------------------------------------------------------
// Seeding
// ---------------------------------------------------------------------------
// An empty history store makes HistoryPage render its "No history yet" empty
// state instead of mounting a chart, and there is no API a browser test could
// use to put rows in (the endpoint is read-only, by design). Everything this
// file exists to prove -- a window change resetting the x-scale, and the
// cursor tooltip rendering real values -- can only be exercised against a
// REAL uPlot canvas with real data. The unit suite stubs HistoryChart out
// entirely, because jsdom has no canvas, so these are the only tests in the
// project that ever touch the actual plot.
//
// seed_history.py inserts a known 30-minute, 600-row curve with backdated
// timestamps and prints the id range it created; afterAll deletes exactly
// that range.

// Both windows are wider than the 30 minutes of seeded history. The endpoint
// windows by ROW COUNT (`num_items = minutes * 20`), not by wall clock, so
// each of these selects every seeded row and the two payloads are IDENTICAL.
// That is what makes the scale-reset test below decisive: switching between
// them changes the chart's React `key` (and so remounts it) without changing
// a single data point, so any change in what a given pixel maps to can only
// have come from the reset.
const WIDE_MINUTES = 40;
const WIDER_MINUTES = 50;
// Selects the last 300 rows -- the back HALF of the seeded curve, so its left
// edge sits at the curve's midpoint rather than its start.
//
// Deliberately not something tiny like 1 minute (20 rows): the grill mode is
// NOT pinned here (see above), so control.py may be appending real rows at the
// tail while this runs, and a 20-row window could end up composed entirely of
// them. 300 rows is wide enough that a few dozen foreign rows move the reading
// by single digits, while the seeded midpoint/start gap is ~150 degrees.
const MID_MINUTES = 15;

// The seeded Grill series sweeps 100 -> 400 degrees across the full window,
// so the value under the cursor is a direct, numeric read-out of where that
// pixel sits on the x-axis. That makes it a far better probe of the x-scale
// than the tooltip's locale-formatted timestamp.
const SEEDED_GRILL_MIN = 100;
const SEEDED_GRILL_MAX = 400;
const SEEDED_GRILL_SPAN = SEEDED_GRILL_MAX - SEEDED_GRILL_MIN;

interface SeedResult {
  first_id: number;
  last_id: number;
  rows: number;
}

let seeded: SeedResult | null = null;

function runSeedScript(args: string[]): string {
  // `test.info().file` is this spec's own absolute path, so both the script
  // beside it and the repo root three levels up (where `uv run` finds the
  // project venv) resolve no matter what cwd `playwright test` was launched
  // from. Read via test.info() rather than a hook parameter because Playwright
  // requires a hook's first argument to be an object-destructuring pattern,
  // and these hooks want no fixtures at all.
  const here = path.dirname(test.info().file);
  const repoRoot = path.resolve(here, "..", "..", "..");
  const script = path.join(here, "seed_history.py");
  return execFileSync("uv", ["run", "python", script, ...args], {
    cwd: repoRoot,
    encoding: "utf8",
  });
}

test.beforeAll(async ({ request }) => {
  seeded = JSON.parse(runSeedScript(["seed"]).trim()) as SeedResult;

  // The seed script writes through common/datastore.py, whose DB_PATH defaults
  // to `<repo root>/pifire.db` -- the root of whatever checkout the script runs
  // from. Run this spec from a jj workspace while the backend serves the main
  // checkout and the rows land in a different database entirely: every test
  // below then fails on a missing chart, which reads like a broken chart rather
  // than a misdirected write. Fail here instead, with the fix.
  const res = await request.get("http://localhost:5000/api/history/chart?minutes=40");
  const body = (await res.json()) as { time_labels?: unknown[] };
  expect(
    body.time_labels?.length ?? 0,
    "Seeded history is not visible to the backend. The seed script and the running " +
      "server are using different pifire.db files -- set PIFIRE_DB_PATH to the " +
      "database the backend serves, or run this spec from that checkout.",
  ).toBeGreaterThan(0);
});

test.afterAll(async () => {
  if (!seeded) return;
  runSeedScript(["clean", String(seeded.first_id), String(seeded.last_id)]);
  seeded = null;
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** uPlot's mouse-event overlay -- the element the cursor and drag-zoom live on. */
function overlay(page: Page) {
  return page.locator(".pf-history-chart .u-over");
}

async function overlayBox(page: Page) {
  const box = await overlay(page).boundingBox();
  if (!box) throw new Error("uPlot overlay has no bounding box -- did the chart mount?");
  return box;
}

/**
 * Hovers the plot at `fraction` of its width and returns the first series'
 * (Grill's) value as the tooltip renders it.
 *
 * Always moves to a corner first: uPlot updates its cursor from `mousemove`,
 * so moving straight to a coordinate the mouse is already at would fire no
 * event at all and silently re-read the previous position's value.
 */
async function grillValueAt(page: Page, fraction: number): Promise<number> {
  const box = await overlayBox(page);
  await page.mouse.move(box.x + 2, box.y + 2);
  await page.mouse.move(box.x + box.width * fraction, box.y + box.height / 2, { steps: 4 });

  const tip = page.locator(".pf-history-tip");
  await expect(tip).toBeVisible();
  const text = await tip.locator(".r").first().locator("b").innerText();
  const value = Number.parseFloat(text);
  expect(Number.isNaN(value), `tooltip value "${text}" did not parse as a number`).toBe(false);
  return value;
}

/** Drag-zooms the x-axis between two fractions of the plot width. */
async function dragZoom(page: Page, fromFraction: number, toFraction: number) {
  const box = await overlayBox(page);
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width * fromFraction, y);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * toFraction, y, { steps: 12 });
  await page.mouse.up();
}

/** Sets the Minutes control and waits for the resulting refetch to settle. */
async function setMinutes(page: Page, minutes: number) {
  await page.getByLabel("Minutes").fill(String(minutes));
  await expect(page.getByText("Loading history…")).toHaveCount(0, { timeout: 15_000 });
  await expect(overlay(page)).toBeVisible();
}

/** Opens /history at a known window with a mounted chart. */
async function openHistory(page: Page, minutes: number) {
  await page.goto("/history");
  await setMinutes(page, minutes);
  // The seeded rows mean the empty state is WRONG here; asserting its absence
  // keeps every test below from passing vacuously against an unseeded store.
  await expect(page.getByText(/No history yet/)).toHaveCount(0);
  await expect(page.getByText(/Couldn't load history/)).toHaveCount(0);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

test("history page mounts a real uPlot chart and links Export CSV at /history/export", async ({
  page,
}) => {
  await openHistory(page, WIDE_MINUTES);

  // A real canvas, not just the host div -- the host renders even when uPlot
  // fails to construct.
  await expect(page.locator(".pf-history-chart canvas").first()).toBeVisible();
  await expect(overlay(page)).toBeVisible();

  // Served by the legacy Flask route, which streams a CSV attachment, so it's
  // a plain link rather than a fetch. Matched by suffix because BASE_URL is
  // absolute whenever PUBLIC_PIFIRE_URL is set.
  await expect(page.getByRole("link", { name: "Export CSV" })).toHaveAttribute(
    "href",
    /\/history\/export$/,
  );
});

test("changing the minutes window refetches and redraws the narrower slice", async ({ page }) => {
  await openHistory(page, WIDE_MINUTES);

  // Near the left edge of the full window the seeded Grill curve is at the
  // bottom of its sweep.
  const wideLeft = await grillValueAt(page, 0.05);
  expect(wideLeft).toBeLessThan(SEEDED_GRILL_MIN + 0.2 * SEEDED_GRILL_SPAN);

  await setMinutes(page, MID_MINUTES);
  await expect(page.getByText(/Couldn't load history/)).toHaveCount(0);

  // The narrower window drops the first half of the curve, so the same pixel
  // now reads from the curve's MIDDLE. A refetch that silently failed, or one
  // that redrew the old payload, would leave this near `wideLeft`.
  const midLeft = await grillValueAt(page, 0.05);
  expect(midLeft).toBeGreaterThan(SEEDED_GRILL_MIN + 0.35 * SEEDED_GRILL_SPAN);
  expect(midLeft).toBeLessThan(SEEDED_GRILL_MIN + 0.75 * SEEDED_GRILL_SPAN);
});

test("a window change resets the x-scale, discarding a drag-zoom", async ({ page }) => {
  await openHistory(page, WIDE_MINUTES);

  // Where a quarter of the way across the plot lands while fully zoomed out.
  const baseline = await grillValueAt(page, 0.25);

  // Zoom into a narrow band on the right-hand side of the plot.
  await dragZoom(page, 0.7, 0.85);

  // Same pixel, much later in the cook: this is what proves the drag actually
  // took -- without it the assertion below could pass against a chart that
  // simply never zoomed.
  const zoomed = await grillValueAt(page, 0.25);
  expect(
    zoomed - baseline,
    "drag-zoom did not change the x-scale, so the reset assertion below would be vacuous",
  ).toBeGreaterThan(0.3 * SEEDED_GRILL_SPAN);

  // Now change the window. WIDE_MINUTES and WIDER_MINUTES select the exact
  // same rows, so the payload is identical and the only thing that changes is
  // HistoryPage's chart `key` -- which remounts HistoryChart, and a fresh
  // uPlot autoscales. shouldResetScales cannot detect this on its own (it
  // compares the x-scale against the data the plot already holds, and a zoom
  // dragged BEFORE the window changed looks exactly like "not zoomed yet"),
  // so the remount is the entire mechanism. If it regresses, the view stays
  // on the stale zoomed range and `afterChange` reads like `zoomed`.
  await setMinutes(page, WIDER_MINUTES);

  const afterChange = await grillValueAt(page, 0.25);
  expect(
    Math.abs(afterChange - baseline),
    `x-scale was not reset: pixel 0.25 still reads ${afterChange} (zoomed) rather than ${baseline} (full range)`,
  ).toBeLessThan(0.07 * SEEDED_GRILL_SPAN);
});

test("Reset zoom restores the full x-scale", async ({ page }) => {
  await openHistory(page, WIDE_MINUTES);

  const baseline = await grillValueAt(page, 0.25);
  await dragZoom(page, 0.7, 0.85);
  const zoomed = await grillValueAt(page, 0.25);
  expect(zoomed - baseline).toBeGreaterThan(0.3 * SEEDED_GRILL_SPAN);

  // Rides the same remount mechanism as a window change, via resetNonce.
  await page.getByRole("button", { name: "Reset zoom" }).click();
  await expect(overlay(page)).toBeVisible();

  const afterReset = await grillValueAt(page, 0.25);
  expect(Math.abs(afterReset - baseline)).toBeLessThan(0.07 * SEEDED_GRILL_SPAN);
});

test("the cursor tooltip renders a row per visible series with real values", async ({
  page,
  request,
}) => {
  const res = await request.get(`http://localhost:5000/api/history/chart?minutes=${WIDE_MINUTES}`);
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as {
    chart_data: { label: string; hidden: boolean; data: unknown[] }[];
  };
  const visible = body.chart_data.filter((ds) => !ds.hidden);
  expect(visible.length).toBeGreaterThan(0);

  await openHistory(page, WIDE_MINUTES);

  const box = await overlayBox(page);
  await page.mouse.move(box.x + 2, box.y + 2);
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height / 2, { steps: 4 });

  const tip = page.locator(".pf-history-tip");
  await expect(tip).toBeVisible();

  // Timestamp header -- toLocaleTimeString, so h:mm:ss with an optional AM/PM.
  await expect(tip.locator(".t")).toHaveText(/\d{1,2}:\d{2}:\d{2}/);

  // One row per series the adapter keeps (hidden datasets are dropped).
  await expect(tip.locator(".r")).toHaveCount(visible.length);

  const firstRow = tip.locator(".r").first();
  await expect(firstRow).toContainText(visible[0].label);

  // A real formatted reading, and -- because the seeded Grill curve sweeps
  // 100 -> 400 across the window -- one that actually corresponds to the
  // middle of the plot. A placeholder or a stale zero would fail this.
  const valueText = await firstRow.locator("b").innerText();
  expect(valueText).toMatch(/^-?\d+\.\d°$/);
  const value = Number.parseFloat(valueText);
  expect(value).toBeGreaterThan(SEEDED_GRILL_MIN + 0.3 * SEEDED_GRILL_SPAN);
  expect(value).toBeLessThan(SEEDED_GRILL_MIN + 0.7 * SEEDED_GRILL_SPAN);

  // The swatch carries the series colour. uPlot normalizes every series'
  // `stroke` to a FUNCTION during init, so an implementation that read the
  // colour back off the uPlot instance would set `background` to the wrapper
  // function's source text -- invalid CSS, which computes to a transparent
  // black. Asserting a real, opaque colour is what catches that regression;
  // the exact value is deliberately not pinned, because settings.spec.ts
  // mutates the first probe's chart colour concurrently.
  const swatch = await firstRow.locator("i").evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(swatch).toMatch(/^rgba?\(\d+, \d+, \d+/);
  expect(swatch).not.toBe("rgba(0, 0, 0, 0)");
});
