import { mkdirSync } from "node:fs";
import { expect, test } from "@playwright/test";

// 800x480 -- the grill's own screen.
//
// The reflow shipped with three width bands and gates on two of them:
// dashboard-fidelity.spec.ts holds 1280x720 still, dashboard-reflow.spec.ts
// drives 390x844. The band in between, `@media (max-width: 1279px)`, had no
// coverage at all -- and it is the only one an actual PiFire device renders.
//
// It was broken. Measured at 800x480 before this file existed: the three
// columns never wrapped, because the centre column's desktop `flex: 1` is a
// flex-basis of 0 and it lost every negotiation against two siblings asking
// for 260px each. probeCol took 331px, rightCol took 331px, and the column
// carrying the gauge, the cook row and all five control buttons was left 71px
// -- a 320px gauge rendered into a 69px svg, and 91px-wide buttons overhanging
// a 71px container.
//
// Same demo server as the other two projects, for the same reason: no socket,
// so this cannot be raced by the shared PiFire instance the rest of the suite
// mutates, and demoDashAt pins the content.

const ARTIFACTS = "tests/e2e/artifacts";

test.beforeEach(async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-25T12:00:00Z") });
  await page.clock.pauseAt(new Date("2026-07-25T12:00:00Z"));
  await page.goto("/");
  await expect(page.locator('[data-pf="stage"]')).toBeVisible();
});

test("the board is laid out at the panel's width, not scaled into it", async ({ page }) => {
  const viewport = page.viewportSize();
  const width = viewport?.width ?? 0;
  expect(width).toBe(800);

  const board = await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>('[data-pf="stage"]');
    if (el === null) throw new Error("no [data-pf=stage] on the page");
    const layoutWidth = el.offsetWidth;
    return {
      layoutWidth,
      scale: layoutWidth === 0 ? 1 : el.getBoundingClientRect().width / layoutWidth,
    };
  });

  // offsetWidth, not the client rect: a transform leaves the layout box alone,
  // so the rect cannot tell "800px board" from "1280px board shrunk to 0.62".
  expect(Math.abs(board.layoutWidth - width)).toBeLessThanOrEqual(1);
  expect(board.scale).toBeGreaterThan(0.99);
  expect(board.scale).toBeLessThan(1.01);

  mkdirSync(ARTIFACTS, { recursive: true });
  // Taller viewport for the capture only -- the panel scrolls, and an artifact
  // cut off at the fold is no use to a human. Both breakpoints are width-based,
  // so this is the same layout.
  await page.setViewportSize({ width, height: 1400 });
  await page
    .locator('[data-pf="stage"]')
    .screenshot({ path: `${ARTIFACTS}/dashboard-800x480.png`, animations: "disabled" });
});

test("the gauge is rendered at the size the breakpoint declares", async ({ page }) => {
  // The token and the box it lands in, compared against each other. A
  // breakpoint that sets --pf-gauge-size: 320px into a column 71px wide has
  // declared something no element can consume -- which is exactly the state
  // this file was written against, and which every "does the stylesheet
  // contain a @media block" assertion in the suite reports as fine.
  const gauge = await page.evaluate(() => {
    const dash = document.querySelector<HTMLElement>(".pf-dash");
    const svg = document.querySelector<SVGElement>('[data-pf="gauge"] svg');
    if (dash === null || svg === null) throw new Error("no gauge on the page");
    return {
      declared: Number.parseFloat(getComputedStyle(dash).getPropertyValue("--pf-gauge-size")),
      rendered: svg.getBoundingClientRect().width,
    };
  });
  expect(gauge.declared).toBeGreaterThan(0);
  expect(Math.abs(gauge.rendered - gauge.declared)).toBeLessThanOrEqual(1);
});

test("no control button is wider than the column that holds it", async ({ page }) => {
  // The squeeze this catches is silent: the buttons keep their own width and
  // simply overhang, so nothing overflows the PAGE and no landmark moves.
  const escapes = await page.evaluate(() => {
    const controls = document.querySelector<HTMLElement>('[data-pf="controls"]');
    if (controls === null) throw new Error("no control row on the page");
    const box = controls.getBoundingClientRect();
    return [...document.querySelectorAll<HTMLElement>(".pf-btn")]
      .map((el) => el.getBoundingClientRect())
      .filter((r) => r.left < box.left - 1 || r.right > box.right + 1)
      .map(
        (r) =>
          `button [${Math.round(r.left)}..${Math.round(r.right)}] escapes controls [${Math.round(box.left)}..${Math.round(box.right)}]`,
      );
  });
  expect(escapes, escapes.join("\n")).toEqual([]);
});

test("nothing is laid out wider than the panel", async ({ page }) => {
  const tooWide = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>("[data-pf]")]
      .filter((el) => el.offsetWidth > window.innerWidth + 1)
      .map(
        (el) =>
          `${el.dataset.pf}: laid out at ${el.offsetWidth}px in a ${window.innerWidth}px viewport`,
      ),
  );
  expect(tooWide, tooWide.join("\n")).toEqual([]);

  const overflow = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.innerWidth);
});

test("the whole board is reachable by scrolling", async ({ page }) => {
  // 480px is shorter than this dashboard can be, so the panel is expected to
  // scroll. What is not acceptable is content the user cannot get to: .pf-dash
  // carries `overflow: hidden` for the decorative glow, so if the board is ever
  // pinned to a height its content exceeds, the excess is deleted with no
  // scrollbar anywhere to recover it.
  const reach = await page.evaluate(() => {
    const dash = document.querySelector<HTMLElement>(".pf-dash");
    const scroller = document.querySelector<HTMLElement>(".pf-shell-main");
    const controls = document.querySelector<HTMLElement>('[data-pf="controls"]');
    if (dash === null || scroller === null || controls === null) {
      throw new Error("no board, scroller or control row");
    }
    return {
      // The glow sits at bottom: -160px and is clipped on purpose, so the board
      // legitimately overhangs itself by that much -- and by no more.
      overhang: dash.scrollHeight - dash.clientHeight,
      scrollerReachesBoard:
        scroller.scrollHeight >= Math.round(dash.getBoundingClientRect().height) - 1,
      controlsWithinBoard: controls.offsetTop + controls.offsetHeight <= dash.clientHeight + 1,
    };
  });
  expect(reach.overhang).toBeLessThanOrEqual(160);
  expect(reach.scrollerReachesBoard).toBe(true);
  expect(reach.controlsWithinBoard).toBe(true);
});

test("no button label spills out of its button", async ({ page }) => {
  // scrollWidth against clientWidth: a label wider than its box does not
  // stretch the box and does not overflow the page, it just paints over the
  // button's own rounded border. Measured at 800x480 with the desktop 25px
  // still in force: "Shutdown" wanted 104px inside a 96px button.
  const spills = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>(".pf-btn")]
      .filter((el) => el.scrollWidth > el.clientWidth)
      .map(
        (el) =>
          `"${(el.textContent ?? "").trim()}" needs ${el.scrollWidth}px inside a ${el.clientWidth}px button`,
      ),
  );
  expect(spills, spills.join("\n")).toEqual([]);
});

test("the hopper still has a level bar to read", async ({ page }) => {
  // `.pf-dash-hopper-track` is flex: 1 inside its card, so it renders at
  // whatever height the card was handed. When the right column wraps onto its
  // own line the card is sized by its content, and the bar collapsed to zero --
  // the card kept its "HOPPER", its "70%" and its "LEVEL OK" and simply lost
  // the one element that shows a level. Nothing else in this suite can see
  // that: no landmark moves, no box overflows, and the numbers are still right.
  const track = await page.evaluate(() => {
    const el = document.querySelector<HTMLElement>(".pf-dash-hopper-track");
    if (el === null) throw new Error("no hopper track on the page");
    return el.getBoundingClientRect().height;
  });
  expect(track).toBeGreaterThanOrEqual(80);
});

test("every control button is a usable touch target", async ({ page }) => {
  const heights = await page.evaluate(() =>
    [...document.querySelectorAll<HTMLElement>(".pf-btn")].map(
      (el) => el.getBoundingClientRect().height,
    ),
  );
  expect(heights.length).toBeGreaterThan(0);
  for (const h of heights) expect(h).toBeGreaterThanOrEqual(44);
});

test("an over-tall dialog stays on screen and every item stays reachable", async ({ page }) => {
  // Nothing bounded a dialog's height. The real Prime menu fits everywhere
  // (measured: 457px against this 480px panel), but only by 12px -- one more
  // item, a wrapped label or a larger font and it would have gone over, and
  // .pf-dash is overflow:hidden so the excess is clipped rather than scrolled.
  // Verified in a real browser at 30 items: without the cap the dialog
  // measured 0..487 in a 480px viewport; with it, 0..480.
  //
  // The markup is what ActionMenu renders -- the component cannot be driven
  // here because demoData pins the grill to Hold, and Prime only appears when
  // it is stopped.
  const scrimSel = ".pf-modal-scrim";
  await page.evaluate(() => {
    const host = document.querySelector(".pf-dash-controls");
    if (host === null) throw new Error("no control row to host the scrim");
    const scrim = document.createElement("div");
    scrim.className = "pf-modal-scrim";
    const items = Array.from({ length: 30 }, (_, i) => `Prime ${i}g & Startup`);
    scrim.innerHTML =
      '<div class="pf-modal"><div class="pf-modal-title">Prime</div>' +
      '<div class="pf-menu-list">' +
      items.map((l) => `<button class="pf-modal-btn pf-menu-item">${l}</button>`).join("") +
      '</div><div class="pf-modal-actions">' +
      '<button class="pf-modal-btn">Cancel</button></div></div>';
    host.appendChild(scrim);
  });

  const read = () =>
    page.evaluate(() => {
      const modal = document.querySelector<HTMLElement>(".pf-modal");
      const list = document.querySelector<HTMLElement>(".pf-menu-list");
      const items = [...document.querySelectorAll<HTMLElement>(".pf-menu-item")];
      const cancel = [...document.querySelectorAll<HTMLElement>(".pf-modal-actions .pf-modal-btn")];
      if (modal === null || list === null) throw new Error("no dialog");
      const onScreen = (el: HTMLElement) => {
        const r = el.getBoundingClientRect();
        return r.top >= 0 && r.bottom <= window.innerHeight;
      };
      const r = modal.getBoundingClientRect();
      return {
        modalTop: r.top,
        modalBottom: r.bottom,
        viewportH: window.innerHeight,
        lastItemOnScreen: onScreen(items[items.length - 1]),
        cancelOnScreen: onScreen(cancel[cancel.length - 1]),
        listScrolls: list.scrollHeight > list.clientHeight,
      };
    });

  const before = await read();
  // The dialog itself never leaves the screen, however tall its content.
  expect(before.modalTop).toBeGreaterThanOrEqual(0);
  expect(before.modalBottom).toBeLessThanOrEqual(before.viewportH);
  // Too tall to show at once, so the list -- not the whole dialog -- scrolls.
  expect(before.listScrolls).toBe(true);
  expect(before.lastItemOnScreen).toBe(false);

  await page.locator(".pf-menu-list").hover();
  await page.mouse.wheel(0, 2000);
  await page.waitForTimeout(200);

  const after = await read();
  // The far end is reachable, and Cancel never went with it: the list scrolls
  // inside a fixed frame, so the title and the buttons stay put.
  expect(after.lastItemOnScreen).toBe(true);
  expect(after.cancelOnScreen).toBe(true);
  expect(before.cancelOnScreen).toBe(true);

  await page.evaluate((sel) => document.querySelector(sel)?.remove(), scrimSel);
});
