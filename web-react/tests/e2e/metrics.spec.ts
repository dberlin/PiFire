import { expect, test } from "@playwright/test";
import { stubMetrics } from "./apiFixtures";

// The metrics page against the real backend, reading only.
//
// SAFETY: /api/metrics has no write half — blueprints/api_metrics registers no
// POST at all. The guard below is not about that endpoint; it is about the
// destructive doors that live one path segment away on the same server. They
// are aborted at the network boundary and every attempt is RECORDED, because an
// aborted request is otherwise silently swallowed and this spec would keep
// passing while quietly trying to power the grill off.

const WRITE_ROUTES = [
  "**/api/admin/system",
  "**/api/admin/factory-reset",
  "**/api/admin/maintenance",
  "**/api/admin/logs/delete",
];

let attempted: string[] = [];

test.beforeEach(async ({ page }) => {
  attempted = [];
  for (const pattern of WRITE_ROUTES) {
    await page.route(pattern, async (route) => {
      attempted.push(route.request().url());
      await route.abort();
    });
  }
});

test.afterEach(() => {
  expect(attempted, "a destructive call escaped this spec").toEqual([]);
});

test.describe("metrics page", () => {
  test("renders the page for whatever the grill has recorded", async ({ page }) => {
    await page.goto("/metrics");
    await expect(page.getByRole("heading", { name: "Metrics", level: 1 })).toBeVisible();
    await expect(page.getByText(/^Auger rate: /)).toBeVisible();

    //  A real machine may legitimately have no metrics at all, so this asserts
    //  the page reached ONE of its two settled states -- never that it is
    //  still loading, which is what a broken read would leave on screen.
    const cards = page.locator(".pf-metrics-card");
    const empty = page.getByRole("heading", { name: "No Data" });
    await expect
      .poll(async () => (await cards.count()) > 0 || (await empty.count()) > 0)
      .toBe(true);
    await expect(page.getByText("Loading metrics…")).toHaveCount(0);
  });

  test("renders a card and opens the raw record on demand", async ({ page }) => {
    //  The one test here that STUBS the listing. A developer machine has often
    //  never lit the grill, so the live table is empty and the card path would
    //  go untested in a real browser -- and seeding one would mean an e2e
    //  writing to the operator's metrics table, which is not a trade worth
    //  making for a DOM assertion. Everything under test below is client-side.
    await stubMetrics(page);
    await page.goto("/metrics");

    const card = page.locator(".pf-metrics-card").first();
    await expect(card).toBeVisible();
    await expect(card.getByRole("heading", { name: "Smoke Mode" })).toBeVisible();
    //  Ten rows for Smoke, plus the header row.
    await expect(card.locator("tbody tr")).toHaveCount(10);
    await expect(page.getByRole("link", { name: "Download CSV Data" })).toHaveAttribute(
      "href",
      /\/api\/metrics\/export$/,
    );

    const details = card.locator("details");
    await expect(details).not.toHaveAttribute("open", /.*/);
    await card.getByText("Raw Data").click();
    await expect(details).toHaveAttribute("open", /.*/);
    //  A field no table row names, reachable only through the disclosure.
    await expect(details).toContainText('"pellet_level_end": 85');
  });

  test("serves the listing envelope", async ({ request }) => {
    const resp = await request.get("/api/metrics");
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.result).toBe("OK");
    expect(Array.isArray(body.data.metrics)).toBe(true);
    expect(typeof body.data.units).toBe("string");
    expect(typeof body.data.augerrate).toBe("number");
  });

  test("serves the export as a CSV attachment", async ({ request }) => {
    //  Fetched through `request`, not by clicking the link: following it would
    //  write a file into the runner's download directory on every run.
    const resp = await request.get("/api/metrics/export");
    expect(resp.status()).toBe(200);
    expect(resp.headers()["content-disposition"]).toContain("attachment");
    expect(resp.headers()["content-disposition"]).toContain("PiFire-Metrics-Export.csv");
  });

  test("reaches the page from the history page", async ({ page }) => {
    await page.goto("/history");
    await page.getByRole("link", { name: "Metrics" }).click();
    await expect(page).toHaveURL(/\/metrics$/);
  });
});
