import { expect, test } from "@playwright/test";

// Requires the prototype backend running (control.py + gunicorn on :5000) to
// serve /api/settings for the Notifications tab. The three /api/wled_* calls
// are ROUTE-MOCKED here: discover/push/test talk to a physical WLED device the
// CI host does not have, and the buttons act on the live form (Flask parity),
// never a Save -- so this spec mutates no shared PiFire state and needs no
// restore. WledCard reads the WLED subtree from the loaded settings; the stock
// defaults have use_profiles=true and device_address="wled.local", so the
// profile block (Push/Test) renders and the address guard does not fire.

test.describe("WLED editor", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/wled_discover*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          result: "success",
          message: "Found 1 WLED devices",
          devices: [{ ip: "10.0.0.9", led_count: 30, name: "WLED-Kitchen" }],
        }),
      }),
    );
    await page.route("**/api/wled_push_profiles", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ result: "success", message: "ok", profiles_pushed: 12 }),
      }),
    );
    await page.route("**/api/wled_test_profile", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ result: "success", message: "Activated profile 203" }),
      }),
    );
  });

  test("discover → use → push → test", async ({ page }) => {
    await page.goto("/settings/notifications");
    await expect(page.getByText("WLED", { exact: true })).toBeVisible();

    // Discover and pick a device -- the Use button fills the address field.
    await page.getByRole("button", { name: /find wled devices/i }).click();
    await page.getByRole("button", { name: /^use$/i }).click();
    await expect(page.getByLabel(/wled device address/i)).toHaveValue("10.0.0.9");

    // Ensure profile-based control is on so the Push/Test buttons are present.
    const profileToggle = page.getByRole("button", { name: /use profile-based wled control/i });
    if ((await profileToggle.getAttribute("aria-pressed")) === "false") await profileToggle.click();

    await page.getByRole("button", { name: /push profiles to wled/i }).click();
    await expect(page.getByText(/pushed 12 profiles/i)).toBeVisible();

    await page.getByRole("button", { name: /test profile/i }).click();
    await expect(page.getByText(/activated profile 203/i)).toBeVisible();
  });
});
