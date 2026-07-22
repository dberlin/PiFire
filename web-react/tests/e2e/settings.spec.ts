import { test, expect } from "@playwright/test";

// Requires the prototype backend running (control.py + gunicorn on :5000).
test("grill name saves and round-trips to the dashboard header", async ({ page }) => {
  await page.goto("/settings/general");
  const name = "E2E Grill " + Date.now().toString().slice(-4);
  await page.getByLabel("Grill Name").fill(name);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
  await page.reload();
  await expect(page.getByLabel("Grill Name")).toHaveValue(name);
  await page.goto("/");
  await expect(page.getByText(name)).toBeVisible({ timeout: 15000 });
});

test("PWM update-time saves via the settings_update path", async ({ page }) => {
  await page.goto("/settings/pwm");
  await page.getByLabel("Update Time").fill("9");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
  await page.reload();
  await expect(page.getByLabel("Update Time")).toHaveValue("9");
});

test("units change is gated by a confirm and applies", async ({ page }) => {
  await page.goto("/settings/units");
  await page.getByLabel("Temperature Units").selectOption("C");
  await expect(page.getByText(/stop the grill/i)).toBeVisible();
  await page.getByRole("button", { name: "Confirm" }).click();
  await page.reload();
  await expect(page.getByLabel("Temperature Units")).toHaveValue("C");
  // reset to F for rerun idempotency
  await page.getByLabel("Temperature Units").selectOption("F");
  await page.getByRole("button", { name: "Confirm" }).click();
});

test("safety max grill temp saves via a bare write and round-trips", async ({ page }) => {
  await page.goto("/settings/safety");
  const maxTemp = (500 + (Date.now() % 50)).toString(); // unique-ish value per run
  await page.getByLabel("Max Grill Temp").fill(maxTemp);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
  await page.reload();
  await expect(page.getByLabel("Max Grill Temp")).toHaveValue(maxTemp);
});

test("history ext_data toggle disabled state matches the live grill mode", async ({ page }) => {
  const modeRes = await page.request.get("/api/get/mode");
  expect(modeRes.ok()).toBeTruthy();
  const modeBody = (await modeRes.json()) as { data?: { mode?: string } };
  const liveMode = modeBody.data?.mode ?? "";

  await page.goto("/settings/history");
  const extDataToggle = page.getByRole("button", { name: "Extended Data Logging" });
  await expect(extDataToggle).toBeVisible();

  if (liveMode === "Stop") {
    await expect(extDataToggle).toBeEnabled();
    await expect(page.getByText(/stop the grill to change/i)).not.toBeVisible();
  } else {
    await expect(extDataToggle).toBeDisabled();
    await expect(page.getByText(/stop the grill to change/i)).toBeVisible();
  }
});
