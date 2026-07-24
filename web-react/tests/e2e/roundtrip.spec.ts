import { expect, test } from "@playwright/test";
import { ensureStopped } from "./helpers";

// Requires the prototype backend running: `uv run python control.py` + `uv run python app.py`.
test("startup then hold round-trips through the live socket", async ({ page, request }) => {
  // Ensure grill is stopped before test to avoid Hold-mode race condition.
  await ensureStopped(request);

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

// Manual output control. SAFETY: this drives the grill's output relays, so it
// is only safe against the prototype backend -- `grillplat/prototype.py` keeps
// its outputs in an in-memory dict and touches no GPIO (its only gpiozero
// import is GPIOThread, a thread helper for fan ramping). Do NOT run this
// against a real-hardware platform module.
test("manual mode exposes the output relays and toggles one end to end", async ({
  page,
  request,
}) => {
  const api = "http://localhost:5000";
  await ensureStopped(request);
  await page.goto("/");

  // Enter Manual from the idle button row.
  await page.getByRole("button", { name: "Manual" }).click();

  // The button row becomes the output control panel (legacy's control-panel
  // buttons, hidden outside Manual mode).
  for (const label of ["Power", "Igniter", "Auger", "Fan"]) {
    await expect(page.getByRole("button", { name: label })).toBeVisible({ timeout: 15_000 });
  }

  // Toggle the auger ON and confirm it round-trips all the way to the control
  // process's output state -- asserting through the API rather than button
  // styling keeps this about behaviour, not CSS.
  await page.getByRole("button", { name: "Auger" }).click();
  await expect
    .poll(
      async () => {
        const res = await request.get(`${api}/api/get/status`);
        const body = (await res.json()) as { data?: { outpins?: Record<string, boolean> } };
        return body.data?.outpins?.auger ?? null;
      },
      { timeout: 15_000, message: "auger relay never reported ON" },
    )
    .toBe(true);

  // Toggle it back OFF, then leave the grill as we found it.
  await page.getByRole("button", { name: "Auger" }).click();
  await expect
    .poll(
      async () => {
        const res = await request.get(`${api}/api/get/status`);
        const body = (await res.json()) as { data?: { outpins?: Record<string, boolean> } };
        return body.data?.outpins?.auger ?? null;
      },
      { timeout: 15_000, message: "auger relay never reported OFF" },
    )
    .toBe(false);

  await ensureStopped(request);
});
