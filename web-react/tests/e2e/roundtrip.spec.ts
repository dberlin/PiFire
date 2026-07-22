import { test, expect } from "@playwright/test";

// Requires the prototype backend running: `uv run python control.py` + `uv run python app.py`.
test("startup then hold round-trips through the live socket", async ({ page }) => {
  await page.goto("/");

  // Live data renders: the grill gauge shows a numeric temperature.
  // Note: exact match — the header's `grillName` field is a deliberate
  // "BOOT_PATH_SENTINEL_GRILL" value (both in the backend's settings.json
  // and the frontend's boot-path fixture, see src/fixture.ts), which also
  // contains the substring "GRILL" and would otherwise collide in a
  // case-insensitive substring match against the gauge's "Grill" label.
  await expect(page.getByText("Grill", { exact: true })).toBeVisible();

  // If currently stopped, press Startup; otherwise we're already cooking.
  const startup = page.getByRole("button", { name: "Startup" });
  if (await startup.isVisible().catch(() => false)) {
    await startup.click();
    await expect(page.getByRole("button", { name: "Hold" })).toBeVisible({ timeout: 15_000 });
  }

  // Open the Hold setpoint modal, set a temperature, submit.
  await page.getByRole("button", { name: "Hold" }).click();
  await expect(page.getByText("Set Hold Temperature")).toBeVisible();
  await page.getByRole("button", { name: "Set Hold" }).click();

  // The mode badge reflects HOLD, echoed back over the socket.
  // Note: exact match — the badge renders `mode.toUpperCase()` ("HOLD"),
  // which otherwise case-insensitively collides with the "Hold" button
  // beneath it (disabled while already in Hold mode).
  await expect(page.getByText("HOLD", { exact: true })).toBeVisible({ timeout: 15_000 });
});
