import { expect, test } from "@playwright/test";

// Requires the prototype backend running (control.py + gunicorn on :5000).
test("grill name saves and round-trips to the dashboard header", async ({ page }) => {
  await page.goto("/settings/general");
  const name = `E2E Grill ${Date.now().toString().slice(-4)}`;
  await page.getByLabel("Grill Name").fill(name);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
  await page.reload();
  await expect(page.getByLabel("Grill Name")).toHaveValue(name);
  await page.goto("/");
  // Scoped to <main>: the shell's navbar renders the grill name too, so an
  // unscoped match resolves to two elements and trips strict mode. The
  // dashboard header is the one this test is about.
  await expect(page.getByRole("main").getByText(name)).toBeVisible({ timeout: 15000 });
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

test("chart color saves and round-trips on the history tab", async ({ page }) => {
  await page.goto("/settings/history");
  // First color input in the Chart Colors section (the first probe's Line Color).
  const colorInput = page.locator('input[type="color"]').first();
  await expect(colorInput).toBeVisible();
  const original = await colorInput.inputValue();
  const next = original.toLowerCase() === "#336699" ? "#996633" : "#336699";

  await colorInput.fill(next);
  await expect(colorInput).toHaveValue(next);

  // The Chart Colors Save button sits at the end of that section, after the
  // per-probe cards; it's the only "Save" button on this tab.
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });

  await page.reload();
  await expect(page.locator('input[type="color"]').first()).toHaveValue(next);

  // Restore the original value so the backend is left as found.
  await page.locator('input[type="color"]').first().fill(original);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
});

test("smartstart table startup-time saves and round-trips on the startup tab", async ({ page }) => {
  await page.goto("/settings/startup");
  const firstRowStartupTime = page.getByLabel("Startup time row 1");
  await expect(firstRowStartupTime).toBeVisible();
  const original = await firstRowStartupTime.inputValue();

  await firstRowStartupTime.fill("361");
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });

  await page.reload();
  await expect(page.getByLabel("Startup time row 1")).toHaveValue("361");

  // Restore the original value so the backend is left as found.
  await page.getByLabel("Startup time row 1").fill(original);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
});

test("invalid settings_update delta is rejected atomically with a dotted-path error", async ({
  page,
}) => {
  const beforeRes = await page.request.get("/api/settings");
  expect(beforeRes.ok()).toBeTruthy();
  const beforeBody = (await beforeRes.json()) as { settings?: { safety?: { maxtemp?: number } } };
  const originalMaxtemp = beforeBody.settings?.safety?.maxtemp;
  expect(originalMaxtemp).not.toBeUndefined();

  const updateRes = await page.request.post("/api/settings_update", {
    data: { settings: { safety: { maxtemp: "nope" } } },
  });
  expect(updateRes.ok()).toBeTruthy();
  const updateBody = (await updateRes.json()) as { result?: string; message?: string };
  expect(updateBody.result).toBe("error");
  expect(updateBody.message).toContain("safety.maxtemp");

  // Nothing was written -- no restore needed, just confirm the read-back is unchanged.
  const afterRes = await page.request.get("/api/settings");
  expect(afterRes.ok()).toBeTruthy();
  const afterBody = (await afterRes.json()) as { settings?: { safety?: { maxtemp?: number } } };
  expect(afterBody.settings?.safety?.maxtemp).toBe(originalMaxtemp);
});

test("a save the backend rejects is shown inline on the tab and writes nothing", async ({
  page,
}) => {
  // Read the live pwm section: the e2e suite shares one store, so the values
  // here are whatever an earlier test left behind, not the schema defaults.
  const beforeRes = await page.request.get("/api/settings");
  expect(beforeRes.ok()).toBeTruthy();
  const beforeBody = (await beforeRes.json()) as {
    settings?: {
      pwm?: {
        min_duty_cycle?: number;
        max_duty_cycle?: number;
        profiles?: { duty_cycle: number }[];
      };
    };
  };
  const pwm = beforeBody.settings?.pwm;
  const originalMin = pwm?.min_duty_cycle;
  expect(originalMin).not.toBeUndefined();
  const dutyCycles = (pwm?.profiles ?? []).map((p) => p.duty_cycle);
  expect(dutyCycles.length).toBeGreaterThan(0);
  const lowestDuty = Math.min(...dutyCycles);
  // One above the lowest profile duty cycle, so PwmSettings._check_profiles
  // must reject the merged tree. The tab neither clamps nor guards this.
  const rejectedMin = lowestDuty + 1;
  expect(rejectedMin).toBeLessThanOrEqual(pwm?.max_duty_cycle ?? 100);

  await page.goto("/settings/pwm");
  const minField = page.getByLabel("Min Duty Cycle");
  await expect(minField).toBeVisible();
  await minField.fill(String(rejectedMin));
  await page.getByRole("button", { name: "Save" }).click();

  // The whole point of this plan: the rejection is HTTP 200, so without the
  // inline message the user would see nothing at all.
  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible({ timeout: 10000 });
  await expect(alert).toContainText("duty_cycle");
  await expect(page.getByText("Saved ✓")).not.toBeVisible();

  // The refused value stays on screen so the user can correct it...
  await expect(page.getByLabel("Min Duty Cycle")).toHaveValue(String(rejectedMin));
  // ...but nothing reached the store: write_settings() is atomic. Nothing to
  // restore -- the failure path is the one under test.
  const afterRes = await page.request.get("/api/settings");
  expect(afterRes.ok()).toBeTruthy();
  const afterBody = (await afterRes.json()) as {
    settings?: { pwm?: { min_duty_cycle?: number } };
  };
  expect(afterBody.settings?.pwm?.min_duty_cycle).toBe(originalMin);
});

test("controller tab shows the live controller and PB round-trips, cross-checked via /api/settings", async ({
  page,
}) => {
  const settingsRes = await page.request.get("/api/settings");
  expect(settingsRes.ok()).toBeTruthy();
  const settingsBody = (await settingsRes.json()) as {
    settings?: {
      controller?: { selected?: string; config?: Record<string, Record<string, unknown>> };
    };
  };
  const selectedKey = settingsBody.settings?.controller?.selected ?? "";
  expect(selectedKey).not.toBe("");

  const metaRes = await page.request.get("/api/controller_metadata");
  expect(metaRes.ok()).toBeTruthy();
  const metaBody = (await metaRes.json()) as {
    metadata: Record<string, { friendly_name: string }>;
  };
  const expectedLabel = metaBody.metadata[selectedKey]?.friendly_name;
  expect(expectedLabel).toBeTruthy();

  await page.goto("/settings/controller");
  const select = page.getByLabel("Controller");
  await expect(select).toBeVisible();
  await expect(select).toHaveValue(selectedKey);
  const selectedOptionText = await select.locator("option:checked").textContent();
  expect(selectedOptionText).toBe(expectedLabel);

  const pbField = page.getByLabel("Proportional Band(PB)");
  await expect(pbField).toBeVisible();
  const originalPb = await pbField.inputValue();
  const nextPb = Number(originalPb) === 61 ? 62 : 61;

  await pbField.fill(String(nextPb));
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });

  await page.reload();
  await expect(page.getByLabel("Proportional Band(PB)")).toHaveValue(String(nextPb));

  const crossCheckRes = await page.request.get("/api/settings");
  expect(crossCheckRes.ok()).toBeTruthy();
  const crossCheckBody = (await crossCheckRes.json()) as {
    settings?: { controller?: { config?: Record<string, Record<string, unknown>> } };
  };
  const pbValue = crossCheckBody.settings?.controller?.config?.[selectedKey]?.PB;
  expect(Number(pbValue)).toBe(nextPb);

  // Restore the original value so the backend is left as found.
  await page.getByLabel("Proportional Band(PB)").fill(originalPb);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
});

test("IFTTT enabled + APIKey save and round-trip on the notifications tab", async ({ page }) => {
  const beforeRes = await page.request.get("/api/settings");
  expect(beforeRes.ok()).toBeTruthy();
  const beforeBody = (await beforeRes.json()) as {
    settings?: { notify_services?: { ifttt?: { enabled?: boolean; APIKey?: string } } };
  };
  const originalEnabled = beforeBody.settings?.notify_services?.ifttt?.enabled ?? false;
  const originalApiKey = beforeBody.settings?.notify_services?.ifttt?.APIKey ?? "";

  await page.goto("/settings/notifications");
  const toggle = page.getByRole("button", { name: "IFTTT Enabled" });
  const apiKeyField = page.getByLabel("IFTTT API Key");
  await expect(toggle).toBeVisible();
  await expect(apiKeyField).toHaveValue(originalApiKey);

  const nextApiKey = `e2e-key-${Date.now().toString().slice(-6)}`;
  const nextEnabled = !originalEnabled;
  await toggle.click();
  await apiKeyField.fill(nextApiKey);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });

  await page.reload();
  await expect(page.getByLabel("IFTTT API Key")).toHaveValue(nextApiKey);
  await expect(page.getByRole("button", { name: "IFTTT Enabled" })).toHaveAttribute(
    "aria-pressed",
    String(nextEnabled),
  );

  // Restore the original enabled/APIKey so the backend is left as found.
  const restoreToggle = page.getByRole("button", { name: "IFTTT Enabled" });
  const restoreApiKeyField = page.getByLabel("IFTTT API Key");
  const currentPressed = await restoreToggle.getAttribute("aria-pressed");
  if (currentPressed !== String(originalEnabled)) {
    await restoreToggle.click();
  }
  await restoreApiKeyField.fill(originalApiKey);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });

  const afterRes = await page.request.get("/api/settings");
  expect(afterRes.ok()).toBeTruthy();
  const afterBody = (await afterRes.json()) as {
    settings?: { notify_services?: { ifttt?: { enabled?: boolean; APIKey?: string } } };
  };
  expect(afterBody.settings?.notify_services?.ifttt?.enabled).toBe(originalEnabled);
  expect(afterBody.settings?.notify_services?.ifttt?.APIKey).toBe(originalApiKey);
});
