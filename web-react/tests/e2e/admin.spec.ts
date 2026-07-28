import { expect, test } from "@playwright/test";
import { ensureStopped } from "./helpers";

// The admin page against the real backend, reading only.
//
// SAFETY: this spec must never complete a destructive action. Three of the
// buttons on this page power the machine off or restart it, a fourth wipes
// settings and the pellet database, and the rest clear history or delete
// files -- against the ONE shared PiFire this suite runs against
// (workers: 1, playwright.config.ts).
//
// Two independent things enforce that, because one is not enough:
//
//   1. Every admin WRITE route is aborted at the network boundary below, so
//      even a misplaced click cannot reach the server.
//   2. Every attempt is recorded, and afterEach fails the test if the list is
//      not empty. Without this, an aborted request would be silently swallowed
//      and the spec would keep passing while quietly trying to reboot.
//
// The reads are real: /api/admin/state is fetched from the live backend, so
// this also proves the endpoint and the page agree about the payload shape.

const WRITE_ROUTES = [
  "**/api/admin/system",
  "**/api/admin/factory-reset",
  "**/api/admin/maintenance",
  "**/api/admin/settings",
  "**/api/admin/backups/create",
  "**/api/admin/backups/restore",
  "**/api/admin/backups/upload",
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
  expect(attempted, "an admin write escaped this spec").toEqual([]);
});

test.describe("admin page", () => {
  test("renders the live state and the four power actions, without firing one", async ({
    page,
    request,
  }) => {
    await ensureStopped(request);
    await page.goto("/admin");

    await expect(page.getByRole("heading", { name: "Admin" })).toBeVisible();
    await expect(page.getByText("Grill mode: Stop")).toBeVisible();

    //  Stopped, so all four are offered. Asserted, never confirmed.
    for (const name of ["Reboot", "Shut Down", "Restart PiFire", "Restore Factory Defaults"]) {
      await expect(page.getByRole("button", { name })).toBeEnabled();
    }
  });

  test("a power button opens a confirmation that names the consequence", async ({
    page,
    request,
  }) => {
    await ensureStopped(request);
    await page.goto("/admin");

    await page.getByRole("button", { name: "Shut Down" }).click();
    await expect(page.getByText("Shut the system down?")).toBeVisible();
    await expect(page.getByText(/STAY off/)).toBeVisible();

    //  Cancel, not Confirm. The afterEach proves nothing left the browser.
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByText("Shut the system down?")).toBeHidden();
  });

  test("factory reset names the pellet database before anything is committed", async ({
    page,
    request,
  }) => {
    await ensureStopped(request);
    await page.goto("/admin");

    await page.getByRole("button", { name: "Restore Factory Defaults" }).click();
    //  Scoped to the dialog: "pellet database" also appears on three controls
    //  behind it, and a page-wide match would pass without the dialog saying
    //  anything at all.
    const dialogCopy = page.locator(".pf-modal-message");
    await expect(dialogCopy).toHaveText(/pellet database/i);
    await expect(dialogCopy).toHaveText(/every profile and every log entry/i);
    await page.getByRole("button", { name: "Cancel" }).click();
  });

  test("disables the power actions when the grill is not stopped", async ({ page, request }) => {
    //  A READ stub, so the gate can be exercised without lighting the grill.
    //  The write routes stay aborted underneath it.
    await ensureStopped(request);
    await page.route("**/api/admin/state", async (route) => {
      const response = await route.fetch();
      const envelope = await response.json();
      envelope.data.mode = "Hold";
      await route.fulfill({ json: envelope });
    });
    await page.goto("/admin");

    await expect(page.getByText("Grill mode: Hold")).toBeVisible();
    for (const name of ["Reboot", "Shut Down", "Restart PiFire", "Restore Factory Defaults"]) {
      await expect(page.getByRole("button", { name })).toBeDisabled();
    }
    await expect(page.getByText(/only while the grill is stopped/i)).toBeVisible();
  });

  test("the clears stay available in every mode, matching the server", async ({
    page,
    request,
  }) => {
    await ensureStopped(request);
    await page.route("**/api/admin/state", async (route) => {
      const response = await route.fetch();
      const envelope = await response.json();
      envelope.data.mode = "Hold";
      await route.fulfill({ json: envelope });
    });
    await page.goto("/admin");

    //  routes.py deliberately does not gate these; a disabled control here
    //  would offer the user less than the server accepts.
    for (const name of ["Clear History", "Clear Event Log", "Clear Pellet Log"]) {
      await expect(page.getByRole("button", { name })).toBeEnabled();
    }
  });

  test("downloads are plain links carrying a bare filename", async ({ page, request }) => {
    await ensureStopped(request);
    await page.goto("/admin");

    //  Not clicked: following them would download, and the point is the href.
    const archive = page.getByRole("link", { name: "Download All" });
    await expect(archive).toHaveAttribute("href", "/api/admin/logs/download");

    //  `exact`, or "Download All" above matches too -- Playwright's accessible
    //  name option is a substring match by default.
    const backupLinks = page.getByRole("link", { name: "Download", exact: true });
    for (const link of await backupLinks.all()) {
      const href = (await link.getAttribute("href")) ?? "";
      expect(href).toMatch(/^\/api\/admin\/backups\/download\?kind=(settings|pelletdb)&file=/);
      //  Decoded, because the encoding is what a `../` would be hiding behind.
      const file = new URL(href, "http://placeholder").searchParams.get("file") ?? "";
      expect(file, "a path escaped into a download href").not.toContain("/");
    }
  });

  test("reaches the page from the navbar", async ({ page, request }) => {
    await ensureStopped(request);
    await page.goto("/");
    await page.getByRole("link", { name: "Admin" }).click();
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.getByRole("heading", { name: "Admin" })).toBeVisible();
  });
});
