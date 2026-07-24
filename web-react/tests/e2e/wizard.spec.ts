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
