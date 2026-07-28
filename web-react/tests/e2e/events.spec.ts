import { expect, test } from "@playwright/test";

// The events page against the real backend, reading only.
//
// SAFETY: this page is read-only, but the log DELETE and CLEAR doors live on
// the same API surface and a stray navigation could reach them. They are
// aborted at the network boundary and every attempt is recorded, because an
// aborted request would otherwise be silently swallowed and this spec would
// keep passing while quietly trying to delete the operator's logs.

const WRITE_ROUTES = ["**/api/admin/logs/delete", "**/api/admin/maintenance"];

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
  expect(attempted, "a log write escaped this spec").toEqual([]);
});

test.describe("events page", () => {
  test("renders the live event feed", async ({ page }) => {
    await page.goto("/events");
    await expect(page.getByRole("heading", { name: "Events" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Events" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    //  A real PiFire always has an events log; an empty frame means the
    //  endpoint and the page disagree about the payload.
    await expect(page.locator(".pf-log-frame")).toBeVisible();
    //  The assertion the jsdom unit tests cannot make: virtua only mounts rows
    //  once something has measured the viewport, so this is the only place that
    //  proves a log LINE reaches the screen.
    await expect(page.locator(".log-line").first()).toBeVisible();
  });

  test("serves the family as plain text with range support", async ({ request }) => {
    const whole = await request.get("/api/admin/logs/view?log=events");
    expect(whole.status()).toBe(200);
    expect(whole.headers()["accept-ranges"]).toBe("bytes");
    const total = (await whole.body()).length;

    const tail = await request.get("/api/admin/logs/view?log=events", {
      headers: { Range: `bytes=${Math.max(0, total - 10)}-` },
    });
    expect(tail.status()).toBe(206);
    expect(tail.headers()["content-range"]).toContain(`/${total}`);

    const past = await request.get("/api/admin/logs/view?log=events", {
      headers: { Range: `bytes=${total + 1000}-` },
    });
    expect(past.status()).toBe(416);
    //  The client's rotation detection depends on this header existing.
    expect(past.headers()["content-range"]).toBe(`bytes */${total}`);
  });

  test("refuses a path-shaped family name", async ({ request }) => {
    const escaped = await request.get("/api/admin/logs/view?log=../pifire");
    expect(escaped.status()).toBe(404);
  });

  test("browses log files and offers a family download", async ({ page }) => {
    await page.goto("/events");
    await page.getByRole("tab", { name: "Log Files" }).click();
    const picker = page.getByRole("combobox", { name: "Log file" });
    await expect(picker).toBeVisible();

    //  Not clicked: following it would download. The point is the href.
    const link = page.getByRole("link", { name: "Download", exact: true });
    const href = (await link.getAttribute("href")) ?? "";
    expect(href).toMatch(/^\/api\/admin\/logs\/view\?log=[^&/]+&download=1$/);
  });

  test("searches within the loaded log", async ({ page }) => {
    await page.goto("/events");
    //  LazyLog's search input is type="text", so it is a textbox rather than a
    //  searchbox whatever its placeholder says.
    await page.getByRole("textbox", { name: "Search Log" }).fill("PiFire");
    await expect(page.locator(".pf-log-frame")).toBeVisible();
  });

  test("reaches the page from the navbar", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Events" }).click();
    await expect(page).toHaveURL(/\/events$/);
  });
});
