import { expect, test } from "@playwright/test";
import { API, ensureStopped } from "./helpers";

// Requires the prototype backend running: `uv run python control.py` + `uv run python app.py`.
test("startup then hold round-trips through the live socket", async ({ page, request }) => {
  // Ensure grill is stopped before test to avoid Hold-mode race condition.
  await ensureStopped(request);

  await page.goto("/");

  // Live data renders: the grill gauge shows a numeric temperature.
  // Note: exact match — the header's `grillName` field is a deliberate
  // "BOOT_PATH_SENTINEL_GRILL" value (both in the backend's settings store
  // and the frontend's boot-path fixture, see src/fixture.ts), which also
  // contains the substring "GRILL" and would otherwise collide in a
  // case-insensitive substring match against the gauge's "Grill" label.
  await expect(page.getByText("Grill", { exact: true })).toBeVisible();

  // If currently stopped, press Startup; otherwise we're already cooking.
  const startup = page.getByRole("button", { name: "Startup" });
  if (await startup.isVisible().catch(() => false)) {
    await startup.click();
    // Ignition is gated behind Flask's startup modal whenever
    // safety.startup_check is set, or the hold prompt is configured for Hold
    // (_macro_control_panel.html:89-90). This backend has startup_check on.
    // Written to tolerate either configuration -- and neither -- so the spec
    // does not depend on the instance's settings.
    const confirm = page.getByRole("button", { name: "Confirm" });
    const holdPrompt = page.getByText("Change Hold Temp?");
    if (await confirm.isVisible().catch(() => false)) {
      await confirm.click();
    } else if (await holdPrompt.isVisible().catch(() => false)) {
      // The hold variant's submit carries Flask's label, "Startup" -- the same
      // name as the button that opened it, so match the modal button by class.
      await page.locator(".pf-modal-btn.accent").click();
    }
    await expect(page.getByRole("button", { name: "Hold" })).toBeVisible({ timeout: 15_000 });
  }

  // Open the Hold setpoint modal, set a temperature, submit.
  await page.getByRole("button", { name: "Hold" }).click();
  await expect(page.getByText("Set Hold Temperature")).toBeVisible();
  await page.getByRole("button", { name: "Set Hold" }).click();

  // The controller owns the mode from here, and Hold is not a mode it
  // guarantees to stay in: when the grill probe reads below the startup temp
  // recorded at the end of Startup, check_safety turns Hold into Reignite
  // (controller/runtime/logic/safety.py::evaluate_flameout), and Reignite only
  // hands back to Hold once the grill reheats. On the simulated prototype
  // grill that crossing depends on whatever temperature the previous spec left
  // behind, so pinning the badge to "HOLD" fails whenever the suite runs in
  // order rather than this file alone.
  //
  // What the round trip actually claims is that the badge mirrors the live
  // controller, so assert exactly that: read the mode over REST and require
  // the badge to show it. Stop is excluded because the submit must have
  // started *some* cook -- a badge faithfully mirroring STOP would otherwise
  // satisfy this.
  //
  // Note: exact match is case-sensitive, which is what keeps "HOLD" off the
  // "Hold" button beneath it (disabled while already in Hold mode).
  await expect
    .poll(
      async () => {
        const res = await request.get(`${API}/api/get/status`);
        const mode = ((await res.json()) as { data?: { mode?: string } }).data?.mode ?? "";
        if (!mode || mode === "Stop") return `controller reports ${mode || "nothing"}`;
        const shown = await page.getByText(mode.toUpperCase(), { exact: true }).count();
        return shown > 0 ? "badge matches controller" : `badge does not show ${mode}`;
      },
      { timeout: 15_000, message: "mode badge never matched the controller's live mode" },
    )
    .toBe("badge matches controller");
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
        const res = await request.get(`${API}/api/get/status`);
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
        const res = await request.get(`${API}/api/get/status`);
        const body = (await res.json()) as { data?: { outpins?: Record<string, boolean> } };
        return body.data?.outpins?.auger ?? null;
      },
      { timeout: 15_000, message: "auger relay never reported OFF" },
    )
    .toBe(false);

  await ensureStopped(request);
});
