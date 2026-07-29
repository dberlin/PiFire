import { expect, test } from "@playwright/test";

// Unlike other specs this one is fully route-mocked and needs no live backend
// -- every /api/update/* endpoint is stubbed here, including the mutation
// routes, because a real updater run would mutate the machine (git pull,
// pip/apt installs, service restarts). No test in this file may hit a live
// backend; if a route is added to UpdatePage it must be mocked here too.

test.describe("updater", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/update/state", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          result: "OK",
          data: {
            version: "v1.8.0",
            branch: "main",
            branches: ["main", "dev"],
            remote_url: "u",
            remote_version: "v1.8.1",
          },
        }),
      }),
    );
    await page.route("**/api/update/check", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ result: "OK", data: { current: "v1.8.0", behind: 3 } }),
      }),
    );
    await page.route("**/api/update/log*", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ result: "OK", data: { output: "abc123 fix" } }),
      }),
    );
    await page.route("**/api/update/upgrade", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ result: "OK", data: { started: true } }),
      }),
    );
    await page.route("**/api/update/status", (r) =>
      r.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          result: "OK",
          data: { percent: 101, status: "Done", output: "ok" },
        }),
      }),
    );
  });

  test("shows state, runs upgrade, polls to completion", async ({ page }) => {
    await page.goto("/update");
    await expect(page.getByText(/3 commits behind/i)).toBeVisible();
    await page.getByRole("button", { name: /upgrade dependencies/i }).click();
    await expect(page.getByText(/complete/i)).toBeVisible();
  });
});
