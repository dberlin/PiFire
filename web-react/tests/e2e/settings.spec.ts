import { expect, test } from "@playwright/test";

// Requires the prototype backend running (control.py + gunicorn on :5000).
test("grill name saves and round-trips to the dashboard header", async ({ page }) => {
  const beforeRes = await page.request.get("/api/settings");
  expect(beforeRes.ok()).toBeTruthy();
  const beforeBody = (await beforeRes.json()) as {
    settings?: { globals?: { grill_name?: string } };
  };
  const originalName = beforeBody.settings?.globals?.grill_name ?? "";

  try {
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
  } finally {
    // Leave the backend as found. The grill name is not just a string on this
    // page: the navbar renders it, and `.pf-nav-actions` is positioned by a
    // `margin-left: auto` that moves with its width, so a name left behind here
    // shifts a measured layout in pellets-fidelity.spec.ts. That spec pins the
    // name for its own run either way -- this is the other half, so a
    // randomised name does not outlive the test that needed it.
    await page.request.post("/api/settings_update", {
      data: { settings: { globals: { grill_name: originalName } } },
    });
  }
});

test("PWM update-time saves via the settings_update path", async ({ page }) => {
  // The PWM tab renders an "unavailable" notice with no Save button when the
  // platform is an AC-fan build, so there is nothing here to exercise.
  const res = await page.request.get("/api/settings");
  const body = (await res.json()) as { settings?: { platform?: { dc_fan?: boolean } } };
  test.skip(!body.settings?.platform?.dc_fan, "PWM tab is hidden on an AC-fan build");

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

// NOTE (guards sweep, Task 3): this spec's original vector -- raising
// min_duty_cycle above the lowest profile duty cycle so PwmSettings._check_profiles
// rejects the merged tree -- is NO LONGER REACHABLE through the PWM tab. PwmTab.onSave
// now re-clamps every profile duty_cycle AND startup.pwm_duty_cycle into the new range
// (ported from blueprints/settings/routes.py:485-495), so that save now succeeds by
// design. The spec is kept, not deleted, and re-pointed at what the guard actually does;
// the server-rejection channel itself is still covered by the API-level spec above
// ("invalid settings_update delta is rejected atomically...") and, at the UI level, by
// the PwmTab unit test "surfaces a rejected save inline and withholds the success marker".
test("narrowing the duty-cycle range clamps the dependent profiles instead of being rejected", async ({
  page,
}) => {
  const beforeRes = await page.request.get("/api/settings");
  expect(beforeRes.ok()).toBeTruthy();
  const beforeBody = (await beforeRes.json()) as {
    settings?: {
      platform?: { dc_fan?: boolean };
      pwm?: {
        min_duty_cycle?: number;
        max_duty_cycle?: number;
        profiles?: { duty_cycle: number }[];
      };
    };
  };
  test.skip(!beforeBody.settings?.platform?.dc_fan, "PWM tab is hidden on an AC-fan build");

  const pwm = beforeBody.settings?.pwm;
  const originalMin = pwm?.min_duty_cycle;
  expect(originalMin).not.toBeUndefined();
  const dutyCycles = (pwm?.profiles ?? []).map((p) => p.duty_cycle);
  expect(dutyCycles.length).toBeGreaterThan(0);
  const lowestDuty = Math.min(...dutyCycles);
  // One above the lowest profile duty cycle: before the guard this tripped
  // PwmSettings._check_profiles and the save was refused.
  const narrowedMin = lowestDuty + 1;
  expect(narrowedMin).toBeLessThan(pwm?.max_duty_cycle ?? 100);

  try {
    await page.goto("/settings/pwm");
    const minField = page.getByLabel("Min Duty Cycle");
    await expect(minField).toBeVisible();
    await minField.fill(String(narrowedMin));
    await page.getByRole("button", { name: "Save" }).click();

    await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });
    await expect(page.getByRole("alert")).toHaveCount(0);

    const afterRes = await page.request.get("/api/settings");
    expect(afterRes.ok()).toBeTruthy();
    const afterBody = (await afterRes.json()) as {
      settings?: {
        pwm?: { min_duty_cycle?: number; profiles?: { duty_cycle: number }[] };
        startup?: { pwm_duty_cycle?: number };
      };
    };
    expect(afterBody.settings?.pwm?.min_duty_cycle).toBe(narrowedMin);
    // Every profile, and the cross-section startup value, are inside the range.
    for (const p of afterBody.settings?.pwm?.profiles ?? []) {
      expect(p.duty_cycle).toBeGreaterThanOrEqual(narrowedMin);
    }
    expect(afterBody.settings?.startup?.pwm_duty_cycle).toBeGreaterThanOrEqual(narrowedMin);
  } finally {
    // This spec WRITES, so put the range back however the assertions went.
    await page.request.post("/api/settings_update", {
      data: { settings: { pwm: { min_duty_cycle: originalMin, profiles: pwm?.profiles } } },
    });
  }
});

// Guards sweep, Task 2 (I5). Flask gates the PWM nav pill on
// settings['platform']['dc_fan'] (settings/index.html:63-65); React showed it
// unconditionally. The /settings/pwm ROUTE stays registered either way so a
// bookmarked URL still resolves.
test("the PWM Fan nav item tracks platform.dc_fan and the route still resolves", async ({
  page,
}) => {
  // Read the flag -- do NOT assume. The e2e suite shares one live store, which
  // is why it runs with workers: 1.
  const res = await page.request.get("/api/settings");
  expect(res.ok()).toBeTruthy();
  const body = (await res.json()) as { settings?: { platform?: { dc_fan?: boolean } } };
  const dcFan = !!body.settings?.platform?.dc_fan;

  await page.goto("/settings/general");
  const pwmLink = page.getByRole("link", { name: "PWM Fan" });
  await expect(page.getByRole("link", { name: "General" })).toBeVisible({ timeout: 10000 });
  await expect(pwmLink).toHaveCount(dcFan ? 1 : 0);

  // Either way the route resolves rather than 404ing.
  await page.goto("/settings/pwm");
  // The heading specifically: a bare text match also hits the "PWM fan control
  // is unavailable..." hint rendered in the gated branch.
  await expect(page.getByRole("heading", { name: "PWM Fan" })).toBeVisible({ timeout: 10000 });
  if (!dcFan) {
    await expect(page.getByText(/AC fan/i)).toBeVisible();
    await expect(page.getByRole("button", { name: "Save" })).toHaveCount(0);
  }
});

// Guards sweep, Task 3 (I6). This is the CLIENT-side half; it deliberately does
// not collide with the server-rejection spec above, which now proves the clamp.
test("a min >= max duty cycle is refused client-side without issuing any request", async ({
  page,
}) => {
  const beforeRes = await page.request.get("/api/settings");
  expect(beforeRes.ok()).toBeTruthy();
  const beforeBody = (await beforeRes.json()) as {
    settings?: { platform?: { dc_fan?: boolean }; pwm?: { max_duty_cycle?: number } };
  };
  test.skip(!beforeBody.settings?.platform?.dc_fan, "PWM tab is hidden on an AC-fan build");
  const beforeSnapshot = JSON.stringify(beforeBody.settings);

  // Observe the wire directly -- do not infer "no save" from the absence of a
  // success marker.
  let posted = false;
  await page.route("**/api/settings_update", (route) => {
    posted = true;
    return route.continue();
  });

  await page.goto("/settings/pwm");
  const maxField = page.getByLabel("Max Duty Cycle");
  await expect(maxField).toBeVisible();
  const originalMax = await maxField.inputValue();

  // Push min above max. Both fields carry min=1/max=100, so this is a legal
  // pair of values that is nonetheless an illegal RANGE -- exactly Flask's
  // validateDutyCycle() case (index.html:747-758).
  await page
    .getByLabel("Min Duty Cycle")
    .fill(String(Number(originalMax) + 5 > 100 ? 100 : Number(originalMax) + 5));
  await maxField.fill(String(Math.max(1, Number(originalMax) - 5)));
  await page.getByRole("button", { name: "Save" }).click();

  const alert = page.getByRole("alert");
  await expect(alert).toBeVisible({ timeout: 10000 });
  await expect(alert).toContainText("Max Duty Cycle");
  await expect(page.getByText("Saved ✓")).toHaveCount(0);
  expect(posted).toBe(false);

  // Read-back is byte-identical: the guard fires before any network call, so
  // there is nothing to restore -- but assert that rather than assume it.
  const afterRes = await page.request.get("/api/settings");
  expect(afterRes.ok()).toBeTruthy();
  const afterBody = (await afterRes.json()) as { settings?: unknown };
  expect(JSON.stringify(afterBody.settings)).toBe(beforeSnapshot);
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
  // exact, because getByLabel matches accessible names by SUBSTRING: the field
  // is a SecretField, and its reveal button is named "Show IFTTT API Key" so a
  // screen-reader user can tell which of the six secrets it uncovers. Without
  // exact:true that button is a second match and the locator is ambiguous.
  const apiKeyField = page.getByLabel("IFTTT API Key", { exact: true });
  await expect(toggle).toBeVisible();
  // Masked, so read the value off the input rather than the rendered text.
  await expect(apiKeyField).toHaveAttribute("type", "password");
  await expect(apiKeyField).toHaveValue(originalApiKey);

  const nextApiKey = `e2e-key-${Date.now().toString().slice(-6)}`;
  const nextEnabled = !originalEnabled;
  await toggle.click();
  await apiKeyField.fill(nextApiKey);
  // The one browser-level check on the reveal: jsdom will report whatever type
  // attribute React wrote, so it cannot show that a real input actually swaps
  // between masked and readable.
  await page.getByRole("button", { name: "Show IFTTT API Key" }).click();
  await expect(apiKeyField).toHaveAttribute("type", "text");
  await expect(apiKeyField).toHaveValue(nextApiKey);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved ✓")).toBeVisible({ timeout: 10000 });

  await page.reload();
  await expect(page.getByLabel("IFTTT API Key", { exact: true })).toHaveValue(nextApiKey);
  await expect(page.getByRole("button", { name: "IFTTT Enabled" })).toHaveAttribute(
    "aria-pressed",
    String(nextEnabled),
  );

  // Restore the original enabled/APIKey so the backend is left as found.
  const restoreToggle = page.getByRole("button", { name: "IFTTT Enabled" });
  const restoreApiKeyField = page.getByLabel("IFTTT API Key", { exact: true });
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
