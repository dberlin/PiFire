// End-to-end coverage for the Probes settings tab at /settings/probes.
//
// This spec REWRITES THE LIVE PROBE MAP of whatever backend it reaches. It
// snapshots the map first and restores it in afterAll, but a crash mid-run
// leaves the grill with the test's map. Never point it at a real cooker.
//
// Preconditions: control.py + gunicorn running, gunicorn restarted since
// POST /api/probe_map landed, and PIFIRE_DB_PATH pointing at the DB the
// backend serves if you are in a jj workspace.
//
// LOCATOR DISCIPLINE: every getByRole below is scoped by getByRole("region")
// or carries exact: true. "Save probe configuration" and "Save" are different
// buttons on this page; do not shorten the name.
import { expect, test } from "@playwright/test";

import { ensureStopped, expectCurrentMatchesProbeMap } from "./helpers";

let original: unknown;

test.beforeAll(async ({ request }) => {
  const res = await request.get("/api/settings");
  original = (await res.json()).settings.probe_settings.probe_map;
  expect(original).toBeTruthy();
});

// Putting the map back in SETTINGS is only half the restore -- and the missing
// half used to take the rest of the suite down with it.
//
// control.py republishes the `current` blob -- the label -> temperature map the
// socket payload is assembled from -- only while it is in a RUNNING mode; in
// Stop it keeps the labels the last running mode left. The socket payload
// builder indexes `current` by the labels the probe map declares
// (blueprints/mobile/socket_io.py::_get_probe_data), so once the two disagree it
// raises KeyError inside the `connect` handler, python-socketio refuses every
// connection, and roundtrip.spec.ts and settings.spec.ts -- which run next and
// both need the dashboard's live data -- sit on "Connecting to PiFire…" until
// they time out. Three failures, none of them in the file that caused them.
//
// The fix is the test ORDER below, not a repair here: as long as this file
// never runs the grill while the map is renamed, `current` stays on the
// original labels the whole time and the restore genuinely restores. The check
// is what keeps that true -- reintroduce a running mode after the rename and it
// fails HERE, naming the invariant, instead of two files later. Repairing it
// instead (a Monitor pass after the restore) is what the earlier version did,
// and it was worse: that ends a cook, and a cook end whose history store spans
// a probe rename fails create_cookfile and plants a permanent error banner on
// every page, which then breaks the layout gates.
test.afterAll(async ({ request }) => {
  const res = await request.post("/api/probe_map", { data: { probe_map: original } });
  expect(res.ok(), "restoring the original probe map failed").toBeTruthy();
  await expectCurrentMatchesProbeMap(request);
});

// FIRST, deliberately: this is the only test in the file that runs the
// controller, and running it after the rename below is what froze `current` on
// the renamed labels. See the afterAll.
test("the tab refuses to save while the grill is running", async ({ page, request }) => {
  await request.post("/api/set/mode/monitor");
  try {
    await page.goto("/settings/probes");
    await expect(page.getByRole("alert").filter({ hasText: "Stop it before" })).toBeVisible({
      timeout: 15000,
    });
    await expect(
      page.getByRole("button", { name: "Save probe configuration", exact: true }),
    ).toBeDisabled();
  } finally {
    await ensureStopped(request);
  }
});

test("renaming a probe round-trips to live settings", async ({ page, request }) => {
  // The tab refuses to save while the grill runs; make sure it is stopped.
  // ensureStopped (helpers.ts) polls rather than fire-and-forgetting, because
  // control.py applies the mode change on its own tick.
  await ensureStopped(request);

  await page.goto("/settings/probes");
  const ports = page.getByRole("region", { name: "Probe ports" });
  await expect(ports).toBeVisible({ timeout: 15000 });

  const renamed = `E2E${Date.now().toString().slice(-6)}`;
  await ports.getByRole("button", { name: "Edit", exact: true }).first().click();
  const form = page.getByRole("dialog", { name: "edit probe" });
  await form.getByLabel("Probe Name").fill(renamed);
  await form.getByRole("button", { name: "Save", exact: true }).click();

  const save = page.getByRole("button", { name: "Save probe configuration", exact: true });
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText("Applied ✓", { exact: true })).toBeVisible({ timeout: 10000 });

  const after = (await (await request.get("/api/settings")).json()).settings;
  const labels = after.probe_settings.probe_map.probe_info.map((p: { name: string }) => p.name);
  expect(labels).toContain(renamed);
  // wizard.py:230's regeneration, ported into apply_probe_map: the history
  // chart config must have followed the map.
  expect(Object.keys(after.history_page.probe_config).length).toBeGreaterThan(0);
  expect(Object.keys(after.history_page.probe_config)).toContain(renamed);
});

// Runs with the probe renamed and the map not yet restored, so the socket is
// refused for its duration (see the afterAll). Nothing here reads live data --
// the probes tab is fed over REST -- and this is the shape the file has always
// had between the rename and the restore.
test("the page fits 1280x720 without page scroll", async ({ page }) => {
  await page.goto("/settings/probes");
  await expect(page.getByRole("region", { name: "Probe devices" })).toBeVisible({ timeout: 15000 });
  // Both halves: an overflow:hidden container CLIPS an oversized grid rather
  // than scrolling, so scrollHeight alone passes vacuously (pellets.spec.ts
  // learned this the hard way with a row sitting at y=704).
  const metrics = await page.evaluate(() => ({
    scroll: document.documentElement.scrollHeight,
    inner: window.innerHeight,
    lastBottom: [...document.querySelectorAll(".pf-settings-actions button")]
      .map((el) => el.getBoundingClientRect().bottom)
      .reduce((a, b) => Math.max(a, b), 0),
  }));
  expect(metrics.scroll).toBeLessThanOrEqual(metrics.inner);
  expect(metrics.lastBottom).toBeLessThanOrEqual(metrics.inner);
});
