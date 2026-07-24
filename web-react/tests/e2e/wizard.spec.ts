import { expect, test } from "@playwright/test";

// Requires the prototype backend running (control.py + gunicorn on :5000).
test("display step selection + config edit round-trips as a draft, then is cleared", async ({
  page,
}) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();

  // Step from Welcome to the Display step via Next (Grill Platform -> Probes -> Display).
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Grill Platform" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Probes" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Display" })).toBeVisible();

  // Pick a non-default display module (ili9341b is the manifest default) and
  // edit its "Screen Rotation" config field.
  const moduleSelect = page.getByRole("combobox", { name: "Module" });
  await moduleSelect.selectOption("st7789_240x320e");
  const rotationSelect = page.getByLabel("Screen Rotation");
  await expect(rotationSelect).toBeVisible();
  await rotationSelect.selectOption("180");

  // Next triggers the draft flush before advancing to Distance / Hopper.
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Distance / Hopper" })).toBeVisible();

  const s = await page.request.get("/api/wizard/state");
  expect(s.ok()).toBeTruthy();
  const body = (await s.json()) as {
    has_draft?: boolean;
    selections?: { display?: string | null };
    display_config?: Record<string, Record<string, unknown>>;
  };
  expect(body.has_draft).toBe(true);
  expect(body.selections?.display).toBe("st7789_240x320e");
  // ConfigOptionField's list handler always emits a string, even for
  // numeric list_values -- the draft stores "180", not 180.
  expect(body.display_config?.st7789_240x320e?.rotation).toBe("180");

  // Do NOT click Finish here -- it would fire the real installer.

  // Restore: clear the staged draft so the backend is left as found.
  const clearRes = await page.request.post("/api/wizard/draft", { data: { clear: true } });
  expect(clearRes.ok()).toBeTruthy();
  const afterClear = await page.request.get("/api/wizard/state");
  expect((await afterClear.json()).has_draft).toBe(false);
});

test("probes step: add device + probe, staged into draft", async ({ page }) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();

  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Grill Platform" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Probes" })).toBeVisible();
  await expect(page.locator('[data-step="probes"]')).toBeVisible();

  const devicesSection = page.locator('section[aria-label="Probe devices"]');
  const portsSection = page.locator('section[aria-label="Probe ports"]');

  // The live settings seed the working probe_map with a board-default
  // Primary + several Food probes on a "proto_adc" device. Clear every
  // probe first so a freshly-added Primary below doesn't trip the
  // exactly-one-Primary guard (deleting the last Primary is only allowed
  // once no other probes remain -- delete non-Primary rows first).
  let nonPrimaryRow = portsSection.locator("tbody tr").filter({ hasNotText: "Primary" }).first();
  while (await nonPrimaryRow.count()) {
    await nonPrimaryRow.getByRole("button", { name: "Delete" }).click();
    nonPrimaryRow = portsSection.locator("tbody tr").filter({ hasNotText: "Primary" }).first();
  }
  while (await portsSection.locator("tbody tr").count()) {
    await portsSection.locator("tbody tr").first().getByRole("button", { name: "Delete" }).click();
  }
  await expect(portsSection.locator("tbody tr")).toHaveCount(0);

  // Add a device: select the ADS1115 module, accept its default name, submit.
  await devicesSection.getByLabel("Add device module").selectOption("ads1115_adafruit");
  const deviceDialog = page.getByRole("dialog", { name: "add device" });
  await expect(deviceDialog).toBeVisible();
  await deviceDialog.getByRole("button", { name: "Add", exact: true }).click();
  await expect(devicesSection.getByText("ADS1115Adafruit", { exact: true })).toBeVisible();

  // Add a probe on the new device: name, device:port, type Primary, a profile.
  await portsSection.getByRole("button", { name: "Add Probe" }).click();
  const probeDialog = page.getByRole("dialog", { name: "add probe" });
  await probeDialog.getByLabel("Probe Name").fill("Grill");
  await probeDialog.getByLabel("Device & Port").selectOption("ADS1115Adafruit:ADC0");
  await probeDialog.getByLabel("Probe Type").selectOption("Primary");
  await probeDialog.getByLabel("Probe Profile").selectOption({ index: 1 });
  await probeDialog.getByRole("button", { name: "Add", exact: true }).click();
  await expect(portsSection.getByText("Grill", { exact: true })).toBeVisible();

  // Next flushes the draft -- intercept the POST body to assert the staged
  // probe_map reached the backend.
  const draftReq = page.waitForRequest(
    (r) => r.url().includes("/api/wizard/draft") && r.method() === "POST",
  );
  await page.getByRole("button", { name: "Next" }).click();
  const body = (await (await draftReq).postData()) ?? "{}";
  const parsed = JSON.parse(body) as {
    probe_map: {
      probe_devices: { device: string; module: string }[];
      probe_info: { label: string; type: string; device: string; port: string }[];
    };
  };
  const addedDevice = parsed.probe_map.probe_devices.find((d) => d.device === "ADS1115Adafruit");
  expect(addedDevice?.module).toBe("ads1115_adafruit");
  expect(parsed.probe_map.probe_info).toHaveLength(1);
  expect(parsed.probe_map.probe_info[0].label).toBe("Grill");
  expect(parsed.probe_map.probe_info[0].type).toBe("Primary");
  expect(parsed.probe_map.probe_info[0].device).toBe("ADS1115Adafruit");
  expect(parsed.probe_map.probe_info[0].port).toBe("ADC0");

  await expect(page.getByRole("heading", { name: "Display" })).toBeVisible();

  // Do NOT click Finish here -- it would fire the real installer.

  // Restore: clear the staged draft so the backend is left as found.
  const clearRes = await page.request.post("/api/wizard/draft", { data: { clear: true } });
  expect(clearRes.ok()).toBeTruthy();
  const afterClear = await page.request.get("/api/wizard/state");
  expect((await afterClear.json()).has_draft).toBe(false);
});

test("grill platform step renders the platform module card and switches modules", async ({
  page,
}) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();

  // Step from Welcome to the Grill Platform step via Next.
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Grill Platform" })).toBeVisible();

  const moduleSelect = page.getByRole("combobox", { name: "Module" });
  await expect(moduleSelect).toBeVisible();

  // Switching the platform re-fetches its config (module-values round-trip);
  // the card stays rendered with the new selection.
  await moduleSelect.selectOption({ index: 1 });
  await expect(page.getByRole("heading", { name: "Grill Platform" })).toBeVisible();
  await expect(moduleSelect).toBeVisible();

  // Restore: clear the staged draft so the backend is left as found.
  const clearRes = await page.request.post("/api/wizard/draft", { data: { clear: true } });
  expect(clearRes.ok()).toBeTruthy();
  const afterClear = await page.request.get("/api/wizard/state");
  expect((await afterClear.json()).has_draft).toBe(false);
});

test("distance step renders the sensor module card and switches modules", async ({ page }) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();

  // Welcome -> Grill Platform -> Probes -> Display -> Distance / Hopper
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Grill Platform" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Probes" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Display" })).toBeVisible();
  await page.getByRole("button", { name: "Next" }).click();
  await expect(page.getByRole("heading", { name: "Distance / Hopper" })).toBeVisible();

  const distanceSelect = page.getByRole("combobox", { name: "Module" });
  await expect(distanceSelect).toBeVisible();

  // sen0628 is the one distance module carrying a settings dependency (a
  // usb_serial_device field); the other six render a bare card. Switching to it
  // round-trips through /module-values, then the field appears. The label is the
  // manifest's friendly_name for sen0628_device -- "Serial Device (USB)".
  await distanceSelect.selectOption("sen0628");
  await expect(page.getByLabel("Serial Device (USB)")).toBeVisible();

  // Restore: clear the staged draft so the backend is left as found.
  const distClear = await page.request.post("/api/wizard/draft", { data: { clear: true } });
  expect(distClear.ok()).toBeTruthy();
  const distAfterClear = await page.request.get("/api/wizard/state");
  expect((await distAfterClear.json()).has_draft).toBe(false);
});
