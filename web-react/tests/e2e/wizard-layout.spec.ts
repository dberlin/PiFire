import { expect, test } from "@playwright/test";

// Requires the prototype backend running (control.py + gunicorn on :5000).
//
// These are LAYOUT assertions. The rest of the wizard suite asserts on text and
// ARIA roles only, which is exactly why an entirely unstyled wizard shipped with
// a green suite. Everything here fails on the unstyled page.
//
// Screenshots are written as review artifacts. There is deliberately no
// toHaveScreenshot() gate: index.html loads Barlow from fonts.googleapis.com, so
// glyph rendering is network- and host-dependent and a pixel gate would be flaky
// on exactly the typography it exists to protect.

test("wizard chrome is styled and fits 1280x720 without page scroll", async ({ page }) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome", exact: true })).toBeVisible();

  // 1. No page scroll in either axis. .pf-wizard is position:fixed inset:0 and
  //    only .pf-wizard-content may scroll.
  const overflow = await page.evaluate(() => ({
    y: document.documentElement.scrollHeight - document.documentElement.clientHeight,
    x: document.documentElement.scrollWidth - document.documentElement.clientWidth,
  }));
  expect(overflow.y).toBeLessThanOrEqual(0);
  expect(overflow.x).toBeLessThanOrEqual(0);

  // 2. Chrome is pinned: header at the top, footer flush with the bottom edge.
  const header = await page.locator(".pf-wizard-header").boundingBox();
  const footer = await page.locator(".pf-wizard-footer").boundingBox();
  expect(header).not.toBeNull();
  expect(footer).not.toBeNull();
  if (header === null || footer === null) return;
  expect(header.y).toBeLessThanOrEqual(1);
  expect(header.height).toBeGreaterThan(28);
  expect(Math.abs(footer.y + footer.height - 720)).toBeLessThanOrEqual(1);

  // 3. The step indicators. This is literally the shipped defect, in numbers:
  //    six adjacent inline spans rendered as "WelcomeGrill PlatformProbes...".
  const pills = page.locator(".pf-wizard-step-indicator");
  await expect(pills).toHaveCount(6);
  const boxes = await pills.evaluateAll((els) =>
    els.map((el) => {
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width };
    }),
  );
  for (let i = 1; i < boxes.length; i++) {
    // A positive gap, not merely non-overlapping.
    expect(boxes[i].x).toBeGreaterThan(boxes[i - 1].x + boxes[i - 1].w);
    // All on one row.
    expect(Math.abs(boxes[i].y - boxes[0].y)).toBeLessThanOrEqual(1);
  }
  const activeBg = await pills.nth(0).evaluate((el) => getComputedStyle(el).backgroundColor);
  const idleBg = await pills.nth(1).evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(activeBg).not.toBe(idleBg);

  // 4. The content box has real padding. The unstyled page had none.
  const pad = await page.locator(".pf-wizard-content").evaluate((el) => {
    const cs = getComputedStyle(el);
    return { left: Number.parseFloat(cs.paddingLeft), top: Number.parseFloat(cs.paddingTop) };
  });
  expect(pad.left).toBeGreaterThanOrEqual(16);
  expect(pad.top).toBeGreaterThanOrEqual(16);

  // 5. .pf-btn actually got a wizard treatment. dashboard.css leaves it at 25px
  //    with no padding and no background; all three must have changed.
  const btn = await page
    .getByRole("button", { name: "Next", exact: true })
    .evaluate((el: HTMLElement) => {
      const cs = getComputedStyle(el);
      return {
        fontSize: Number.parseFloat(cs.fontSize),
        padTop: Number.parseFloat(cs.paddingTop),
        bg: cs.backgroundColor,
      };
    });
  expect(btn.fontSize).toBeLessThanOrEqual(20);
  expect(btn.padTop).toBeGreaterThanOrEqual(6);
  expect(btn.bg).not.toBe("rgba(0, 0, 0, 0)");

  await page.screenshot({ path: "tests/e2e/artifacts/wizard-welcome-1280x720.png" });
});

test("module cards and probe cards read as cards, and every step is captured", async ({ page }) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome", exact: true })).toBeVisible();

  const pageBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const next = page.getByRole("button", { name: "Next", exact: true });

  await next.click();
  await expect(page.getByRole("heading", { name: "Grill Platform", exact: true })).toBeVisible();

  const card = await page
    .locator(".pf-module-card")
    .first()
    .evaluate((el) => {
      const cs = getComputedStyle(el);
      return {
        padTop: Number.parseFloat(cs.paddingTop),
        padLeft: Number.parseFloat(cs.paddingLeft),
        radius: Number.parseFloat(cs.borderTopLeftRadius),
        bg: cs.backgroundColor,
      };
    });
  expect(card.padTop).toBeGreaterThanOrEqual(12);
  expect(card.padLeft).toBeGreaterThanOrEqual(12);
  expect(card.radius).toBeGreaterThanOrEqual(8);
  expect(card.bg).not.toBe(pageBg);
  expect(card.bg).not.toBe("rgba(0, 0, 0, 0)");
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-grillplatform-1280x720.png" });

  await next.click();
  await expect(page.getByRole("heading", { name: "Probes", exact: true })).toBeVisible();
  const probesBg = await page
    .locator(".pf-probes-card")
    .first()
    .evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(probesBg).not.toBe(pageBg);
  expect(probesBg).not.toBe("rgba(0, 0, 0, 0)");
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-probes-1280x720.png" });

  await next.click();
  await expect(page.getByRole("heading", { name: "Display", exact: true })).toBeVisible();
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-display-1280x720.png" });

  await next.click();
  await expect(page.getByRole("heading", { name: "Distance / Hopper", exact: true })).toBeVisible();
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-distance-1280x720.png" });

  await next.click();
  await expect(page.getByRole("heading", { name: "Finish", exact: true })).toBeVisible();
  await page.screenshot({ path: "tests/e2e/artifacts/wizard-finish-1280x720.png" });
  // Do NOT click Finish -- it fires the real installer.

  // Restore: stepping forward flushed a draft. Leave the backend as found,
  // exactly as tests/e2e/wizard.spec.ts does.
  const clear = await page.request.post("/api/wizard/draft", { data: { clear: true } });
  expect(clear.ok()).toBeTruthy();
  const afterClear = await page.request.get("/api/wizard/state");
  expect((await afterClear.json()).has_draft).toBe(false);
});

// The three role="dialog" elements cannot be reached from a test run: the 409
// dialog needs a RUNNING grill and the reboot dialog needs the real installer to
// have finished. This is a synthetic probe -- it proves the rules resolve and are
// not empty, and it proves NOTHING about how they look in situ. Those three
// surfaces are on the human checkpoint.
test("modal rules resolve to an overlay (synthetic probe, not an integration test)", async ({
  page,
}) => {
  await page.goto("/wizard");
  await expect(page.getByRole("heading", { name: "Welcome", exact: true })).toBeVisible();

  const style = await page.evaluate(() => {
    const probe = document.createElement("div");
    probe.className = "pf-wizard-modal pf-wizard-system-active-modal";
    document.querySelector(".pf-wizard-content")?.appendChild(probe);
    const cs = getComputedStyle(probe);
    const out = {
      position: cs.position,
      zIndex: cs.zIndex,
      background: cs.backgroundColor,
      boxShadow: cs.boxShadow,
    };
    probe.remove();
    return out;
  });
  expect(style.position).toBe("fixed");
  expect(Number(style.zIndex)).toBeGreaterThan(0);
  expect(style.background).not.toBe("rgba(0, 0, 0, 0)");
  // The spread-shadow scrim; see wizard.css's modal comment for why it is not a
  // ::before pseudo-element.
  expect(style.boxShadow).toContain("px");
});
