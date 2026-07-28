import { expect, test } from "@playwright/test";
import { ports } from "../../ports";

// Control reads and the safety close go STRAIGHT to Flask, not through the
// dev-server proxy, and 127.0.0.1 rather than `localhost` keeps the
// browser-context request off IPv6 where nothing is bound. Neither is the real
// unlock, though: the backend MUST run with a threaded worker
// (`gunicorn -k gthread --threads 25`, the repo's dev default). A plain sync
// `-w 1` worker is held by the app's Socket.IO connection the moment a page
// loads, and every later HTTP request -- proxy or direct -- queues behind it
// until the test times out.
const API = ports.pifireUrl.replace("localhost", "127.0.0.1");

// The tuner page against the real backend.
//
// This machine runs control.py, so opening a session ACTUALLY moves the grill
// into Monitor. Monitor lights nothing -- it reads probes -- but it is a real
// mode change, and the whole point of the session/reading split is that one is
// never left open. Two guards enforce that:
//
//   - the destructive admin routes are aborted and RECORDED (a stray click
//     could reach them), asserted empty after each test;
//   - afterEach force-closes any session and then polls the live control state,
//     failing the test if the grill is not back in Stop. This is the slice's
//     own hazard and no other spec covers it: a test that opens a session and
//     dies mid-way would otherwise leave the operator's grill in Monitor.

const WRITE_ROUTES = [
  "**/api/admin/system",
  "**/api/admin/factory-reset",
  "**/api/admin/maintenance",
  "**/api/admin/logs/delete",
];

let attempted: string[] = [];

async function controlMode(request: {
  get: (url: string) => Promise<{ json: () => Promise<unknown> }>;
}): Promise<{ mode: string; tuning: boolean }> {
  const body = (await (await request.get(`${API}/api/control`)).json()) as {
    control?: { mode?: string; tuning_mode?: boolean };
    data?: { control?: { mode?: string; tuning_mode?: boolean } };
  };
  const control = body.control ?? body.data?.control ?? {};
  return { mode: control.mode ?? "unknown", tuning: Boolean(control.tuning_mode) };
}

test.beforeEach(async ({ page }) => {
  attempted = [];
  for (const pattern of WRITE_ROUTES) {
    await page.route(pattern, async (route) => {
      attempted.push(route.request().url());
      await route.abort();
    });
  }
});

test.afterEach(async ({ request }) => {
  //  Force-close whatever this test may have opened, then confirm the grill
  //  settled back into Stop. This is the slice's own hazard and no other spec
  //  covers it: a test that opened a session and died mid-way would otherwise
  //  leave the operator's live grill in Monitor with tuning_mode set.
  await request.post(`${API}/api/tuner/session`, { data: { open: false } });
  await expect.poll(async () => (await controlMode(request)).mode, { timeout: 5000 }).toBe("Stop");
  const state = await controlMode(request);
  expect(state.tuning, "a test left tuning_mode set on the live grill").toBe(false);
  expect(attempted, "a destructive admin call escaped this spec").toEqual([]);
});

test.describe("tuner page", () => {
  test("renders the manual flow", async ({ page }) => {
    await page.goto("/tuner");
    await expect(page.getByRole("heading", { name: "Tuner", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "High", level: 3 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Medium", level: 3 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Low", level: 3 })).toBeVisible();
    //  Nothing has started, so Finish is offered but disabled.
    await expect(page.getByRole("button", { name: "Finish" })).toBeDisabled();
  });

  test("does not touch the grill until Start is pressed", async ({ page, request }) => {
    await page.goto("/tuner");
    await expect(page.getByRole("heading", { name: "Tuner", level: 1 })).toBeVisible();
    //  Mounting the page and reading the instructions must leave the grill in
    //  Stop -- the mode change is consent given by pressing Start, not by
    //  navigating here.
    expect((await controlMode(request)).mode).toBe("Stop");
  });

  test("Start opens a session and the readout appears", async ({ page, request }) => {
    await page.goto("/tuner");
    await page.getByRole("button", { name: "Start tuning" }).click();
    await expect(page.getByRole("button", { name: "Stop tuning" })).toBeVisible();

    //  The session really opened: the live grill is now in Monitor.
    await expect.poll(async () => (await controlMode(request)).mode).toBe("Monitor");
    //  A resistance readout reaches the screen (a number followed by the ohm
    //  sign), which only happens once the poll has landed a reading.
    await expect(page.locator(".pf-tuner-reading").first()).toContainText("Ω");

    //  Close in-band -- Stop tuning goes through the page, which the single
    //  gunicorn worker serves without the afterEach contention. The grill is
    //  back in Stop before the test ends.
    await page.getByRole("button", { name: "Stop tuning" }).click();
    await expect
      .poll(async () => (await controlMode(request)).mode, { timeout: 5000 })
      .toBe("Stop");
  });

  test("leaving the page closes the session", async ({ page, request }) => {
    await page.goto("/tuner");
    await page.getByRole("button", { name: "Start tuning" }).click();
    await expect.poll(async () => (await controlMode(request)).mode).toBe("Monitor");

    //  Navigate away the way a user does -- a client-side navbar click, not a
    //  full page load. This unmounts TunerPage without tearing down the page
    //  context, so the closeSession() fetch the hook fires on unmount actually
    //  lands. (A hard reload would cancel that fetch; the afterEach force-close
    //  is the net for that genuinely un-closeable case, e.g. a closed tab.)
    await page.getByRole("link", { name: "Dashboard" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect
      .poll(async () => (await controlMode(request)).mode, { timeout: 5000 })
      .toBe("Stop");
  });

  test("serves an inert Tr reading in the envelope", async ({ request }) => {
    const resp = await request.get(`${API}/api/tuner/tr?probe=Grill`);
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.result).toBe("OK");
    expect(body.data.probe).toBe("Grill");
    //  number or null -- never a coerced 0 for an absent probe.
    expect(["number", "object"]).toContain(typeof body.data.trohms);
    expect(typeof body.data.tuning).toBe("boolean");
  });

  test("refuses a degenerate triple with a 422", async ({ request }) => {
    const resp = await request.post(`${API}/api/tuner/coefficients`, {
      data: {
        points: [
          { segment: "High", temp: 400, trohms: 5000 },
          { segment: "Medium", temp: 250, trohms: 5000 },
          { segment: "Low", temp: 100, trohms: 40000 },
        ],
      },
    });
    expect(resp.status()).toBe(422);
    expect((await resp.json()).message).toBe("uncomputable");
  });

  test("reaches the page from Settings > Probes", async ({ page }) => {
    await page.goto("/settings/probes");
    await page.getByRole("link", { name: "Tune a probe" }).click();
    await expect(page).toHaveURL(/\/tuner$/);
  });

  test("auto mode opens a session and shows the accumulation readout", async ({
    page,
    request,
  }) => {
    await page.goto("/tuner");
    await page.getByRole("button", { name: "Auto" }).click();
    await expect(page.getByRole("combobox", { name: /reference/i })).toBeVisible();

    await page.getByRole("button", { name: "Start tuning" }).click();
    await expect(page.getByRole("button", { name: "Stop tuning" })).toBeVisible();
    //  The session really opened: the live grill is now in Monitor.
    await expect.poll(async () => (await controlMode(request)).mode).toBe("Monitor");
    //  The progress line appears once the first poll lands. Do NOT wait for
    //  ready: a 50 F spread will not happen on a monitored grill during a test.
    await expect(page.getByText(/Collecting samples|Ready/)).toBeVisible();

    await page.getByRole("button", { name: "Stop tuning" }).click();
    await expect
      .poll(async () => (await controlMode(request)).mode, { timeout: 5000 })
      .toBe("Stop");
  });

  test("serves the auto-status envelope", async ({ request }) => {
    const resp = await request.post(`${API}/api/tuner/auto-status`, {
      data: { probe: "Grill", reference: "Grill" },
    });
    expect(resp.status()).toBe(200);
    const body = await resp.json();
    expect(body.result).toBe("OK");
    expect(typeof body.data.samples).toBe("number");
    expect(typeof body.data.ready).toBe("boolean");
    //  number or null -- never a coerced 0 for an absent probe.
    expect(["number", "object"]).toContain(typeof body.data.current_tr);
  });

  test("leaving the page in auto mode closes the session", async ({ page, request }) => {
    await page.goto("/tuner");
    await page.getByRole("button", { name: "Auto" }).click();
    await page.getByRole("button", { name: "Start tuning" }).click();
    await expect.poll(async () => (await controlMode(request)).mode).toBe("Monitor");

    //  Client-side nav, so the unmount's closeSession fetch lands (a hard
    //  reload would cancel it; the afterEach force-close is the net for that).
    await page.getByRole("link", { name: "Dashboard" }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect
      .poll(async () => (await controlMode(request)).mode, { timeout: 5000 })
      .toBe("Stop");
  });
});
