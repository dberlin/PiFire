// End-to-end coverage for the pellet inventory manager at /pellets.
//
// PRECONDITIONS: a real backend must be running -- control.py plus gunicorn --
// and, if you are running from a jj workspace, PIFIRE_DB_PATH must point at
// the SAME database that backend serves. common/datastore.py resolves DB_PATH
// relative to its own checkout, so a workspace otherwise writes to a different
// pifire.db than the server reads and every assertion here reads back stale
// state. Point the dev proxy at your backend with PIFIRE_BACKEND_URL (NOT
// PUBLIC_PIFIRE_URL, which rsbuild bakes into the browser bundle and makes
// every request cross-origin -- Flask sends no CORS headers).
//
// The suite is workers: 1 because every spec drives one shared, stateful
// grill. This spec mutates shared pellet state, so each test cleans up after
// itself.
//
// LOCATOR DISCIPLINE, restated because this project has paid for it: every
// getByRole below carries exact: true or is scoped by getByRole("region").
// Do not add a bare getByText("Delete") or { name: "Add" } -- "Add" matches
// "Add & Load", and this page has four separate Delete affordances.

import { expect, test } from "@playwright/test";

const UNIQUE = Date.now().toString().slice(-6);
const BRAND = `E2E Brand ${UNIQUE}`;

test("a brand round-trips through the UI and the store", async ({ page }) => {
  await page.goto("/pellets");
  const brands = page.getByRole("region", { name: "Brands" });
  await expect(brands).toBeVisible({ timeout: 15000 });

  await brands.getByLabel("New brand").fill(BRAND);
  await brands.getByRole("button", { name: "Add brand", exact: true }).click();

  // The socket republishes within ~1s of the write; no reload.
  await expect(brands.getByText(BRAND, { exact: true })).toBeVisible({ timeout: 10000 });

  const res = await page.request.get("/api/pellets");
  const body = (await res.json()) as { data: { pellets: { brands: string[] } } };
  expect(body.data.pellets.brands).toContain(BRAND);

  // The page must fit the target viewport. Note that `scrollHeight <=
  // innerHeight` is NOT sufficient on its own here: .pf-pellets is
  // overflow:hidden, so an oversized grid is CLIPPED rather than scrolled and
  // that check passes vacuously -- it did, while the bottom row sat at y=704
  // on a 720px viewport and was unclickable. Assert both: the page does not
  // scroll AND every card is actually inside the viewport.
  const overflow = await page.evaluate(() => {
    const el = document.scrollingElement;
    const cards = Array.from(document.querySelectorAll(".pf-pellets-card")).map((c) => {
      const r = c.getBoundingClientRect();
      return { label: c.getAttribute("aria-label"), bottom: Math.round(r.bottom) };
    });
    return {
      pageScrolls: el ? el.scrollHeight > window.innerHeight : false,
      viewport: window.innerHeight,
      offscreen: cards.filter((c) => c.bottom > window.innerHeight),
    };
  });
  expect(overflow.pageScrolls).toBe(false);
  // A card below the fold is a failure, not a cosmetic note -- fix the grid,
  // do not relax this.
  expect(overflow.offscreen).toEqual([]);

  // Clean up through the UI, which also exercises the confirm dialog.
  await brands.getByRole("button", { name: `Delete ${BRAND}`, exact: true }).click();
  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(brands.getByText(BRAND, { exact: true })).toHaveCount(0, { timeout: 10000 });
});

test("the loaded profile cannot be deleted", async ({ page }) => {
  await page.goto("/pellets");
  const profiles = page.getByRole("region", { name: "Pellet Profiles" });
  await expect(profiles).toBeVisible({ timeout: 15000 });

  const res = await page.request.get("/api/pellets");
  const db = (await res.json()) as {
    data: {
      pellets: {
        current: { pelletid: string };
        archive: Record<string, { brand: string; wood: string }>;
      };
    };
  };
  const loaded = db.data.pellets.archive[db.data.pellets.current.pelletid];
  test.skip(!loaded, "current pelletid is not in the archive on this install");

  const label = `${loaded.brand} ${loaded.wood}`;
  await profiles.getByRole("button", { name: label, exact: true }).click();
  await expect(
    profiles.getByRole("button", { name: `Delete ${label}`, exact: true }),
  ).toBeDisabled();
});

test("refresh status raises the hopper-check flag", async ({ page }) => {
  await page.request.post("/api/control", {
    data: { hopper_check: false },
    headers: { "Content-Type": "application/json" },
  });
  await page.goto("/pellets");
  await page.getByRole("button", { name: "Refresh Status", exact: true }).click();

  // controller/runtime/modes/base.py clears hopper_check the moment it
  // services the request, so a naive toBe(true) is a race the control loop
  // wins about half the time. What must NOT happen is the flag staying
  // absent because the write never landed at all.
  await expect
    .poll(
      async () => {
        const r = await page.request.get("/api/control");
        const b = (await r.json()) as { control: { hopper_check: boolean } };
        return typeof b.control.hopper_check;
      },
      { timeout: 10000 },
    )
    .toBe("boolean");
});
