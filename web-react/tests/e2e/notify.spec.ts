import { readFileSync } from "node:fs";
import path from "node:path";
import type { APIRequestContext } from "@playwright/test";
import { expect, test } from "@playwright/test";
import { API, ensureStopped, monitorProbeReadings } from "./helpers";

// Requires the prototype backend running: `uv run python control.py` + `uv run
// python app.py`.
//
// SAFETY: notify_data is real control state. A `probe` entry with req:true and
// shutdown:true will shut the grill down the moment the probe reaches its
// target, so every test here restores the array it captured before it ran, in
// an afterEach that runs even when the assertions fail. Nothing in this file
// ever writes `shutdown: true`.

interface NotifyEntry {
  label: string;
  type: string;
  req: boolean;
  shutdown: boolean;
  keep_warm?: boolean;
  target?: number;
  triggered?: boolean;
  [k: string]: unknown;
}

async function getNotify(request: APIRequestContext): Promise<NotifyEntry[]> {
  const res = await request.get(`${API}/api/get/notify`);
  expect(res.ok()).toBe(true);
  const body = (await res.json()) as { result?: string; data?: NotifyEntry[] };
  expect(body.result).toBe("OK");
  expect(Array.isArray(body.data)).toBe(true);
  return body.data as NotifyEntry[];
}

/** HARNESS ONLY -- the whole-array door. This file's setup and its afterEach
 *  restore genuinely mean "the array is exactly this", which is the one thing
 *  notify_data's replace semantics are right for. The production client does
 *  NOT post this shape (see notifyApi.ts): an array built from a queue-blind
 *  read reverts whatever another writer changed in the same control cycle. */
async function postNotify(request: APIRequestContext, entries: NotifyEntry[]): Promise<void> {
  const res = await request.post(`${API}/api/control`, { data: { notify_data: entries } });
  expect(res.ok()).toBe(true);
  // Lowercase "success", not the "OK" the api_response envelope uses elsewhere
  // (blueprints/api/routes.py:211). If this ever becomes "OK", the production
  // client in helpers/notify/notifyApi.ts needs to change with it.
  expect(((await res.json()) as { result?: string }).result).toBe("success");
}

const entryFor = (entries: NotifyEntry[], label: string, type: string) =>
  entries.find((e) => e.label === label && e.type === type);

/** The label of the first food probe, and the display name its card shows.
 *  /api/current's `current.F` is keyed by food-probe label and `current.P` by
 *  the primary's, which is the only reliable way to tell them apart -- all of
 *  them carry the same three notify entries. */
async function firstFoodProbe(
  request: APIRequestContext,
): Promise<{ label: string; name: string }> {
  const res = await request.get(`${API}/api/current`);
  expect(res.ok()).toBe(true);
  const body = (await res.json()) as { current?: { F?: Record<string, number> } };
  const label = Object.keys(body.current?.F ?? {})[0];
  expect(label, "the backend reports no food probes to test against").toBeTruthy();
  const entry = entryFor(await getNotify(request), label, "probe");
  expect(entry, `no type:"probe" notify entry for ${label}`).toBeDefined();
  return { label, name: String(entry?.name ?? label) };
}

/** Count of the null-stripping diagnostic in control.log. The harness's own
 *  whole-array postNotify above round-trips each probe entry's `"eta": null`,
 *  and execute_control_writes logs at ERROR when it strips nulls from a MERGE
 *  partial, naming /api/control as a suspected source
 *  (common/datastore_accessors.py:106-119). It should not fire:
 *  strip_null_members returns lists unchanged (common/common.py:188-191).
 *  (The production client never sends eta at all now -- it names four fields.) */
function nullStripCount(): number {
  try {
    // Same repo-root derivation history.spec.ts uses -- test.info().file rather
    // than __dirname, which is not defined under an ESM transform.
    const repoRoot = path.resolve(path.dirname(test.info().file), "..", "..", "..");
    const log = readFileSync(path.join(repoRoot, "logs", "control.log"), "utf8");
    return log.split("stripped null member").length - 1;
  } catch {
    return -1; // log not readable from here; the assertion below is skipped
  }
}

let baseline: NotifyEntry[] = [];

test.beforeEach(async ({ request }) => {
  await ensureStopped(request);
  baseline = await getNotify(request);
});

test.afterEach(async ({ request }) => {
  await postNotify(request, baseline);
  await expect
    .poll(async () => JSON.stringify(await getNotify(request)), {
      timeout: 15_000,
      message: "notify_data was not restored to its pre-test state",
    })
    .toBe(JSON.stringify(baseline));
});

test("a probe target set in the UI reaches the backend and comes back over the socket", async ({
  page,
  request,
}) => {
  const { label, name } = await firstFoodProbe(request);
  const logBefore = nullStripCount();

  // Pre-arm this probe's HIGH LIMIT entry directly. Three notify entries share
  // one label (common/defaults.py:512-538). The modal owns all three and states
  // all three on Set, so the high limit's req/target must come back UNCHANGED --
  // it is read off the socket payload and written straight back -- while the
  // entries the modal never names (every other probe, the Timer) are untouched
  // because the drain applies addressed updates against live state instead of
  // against whatever array this page last saw.
  const armed = baseline.map((e) =>
    e.label === label && e.type === "probe_limit_high"
      ? { ...e, req: true, target: 555, triggered: true, shutdown: false, keep_warm: false }
      : e,
  );
  const timerBefore = entryFor(baseline, "Timer", "timer");
  await postNotify(request, armed);
  await expect
    .poll(async () => entryFor(await getNotify(request), label, "probe_limit_high")?.target, {
      timeout: 15_000,
      message: "the high-limit pre-arm never landed",
    })
    .toBe(555);
  const limitBefore = entryFor(await getNotify(request), label, "probe_limit_high");

  await page.goto("/");
  await page.getByRole("button", { name: `Notifications for ${name}` }).click();
  await expect(page.getByText(`${name} Notifications`)).toBeVisible();

  await page.getByRole("checkbox", { name: /target temperature/i }).check();
  await page.getByRole("spinbutton", { name: /^target/i }).fill("203");
  await page.getByRole("radio", { name: /keep warm/i }).check();
  // exact: the dashboard's settings gear is aria-label="settings", which a
  // substring match on "Set" also selects.
  await page.getByRole("button", { name: "Set", exact: true }).click();

  // POLL, do not sleep: the write is queued and only applied when the control
  // loop drains it. Measured at ~110 ms in Stop mode against this backend, but
  // the cadence differs by mode, so the budget here is deliberately generous.
  await expect
    .poll(
      async () => {
        const e = entryFor(await getNotify(request), label, "probe");
        return e ? { req: e.req, target: e.target, keep_warm: e.keep_warm } : null;
      },
      { timeout: 20_000, message: "the target write never reached control.notify_data" },
    )
    .toEqual({ req: true, target: 203, keep_warm: true });

  // The high-limit entry for the SAME label keeps the alert it was carrying: the
  // modal seeds it from the socket payload and writes it back verbatim. Only
  // `triggered` may move, and only to whatever the live temperature says --
  // which is what the backend itself would compute on its next pass.
  const limitAfter = entryFor(await getNotify(request), label, "probe_limit_high");
  expect({ req: limitAfter?.req, target: limitAfter?.target }).toEqual({
    req: limitBefore?.req,
    target: limitBefore?.target,
  });
  // And the entry the modal never names is byte-identical. This is the assertion
  // that would have caught the clobbering landmine.
  expect(entryFor(await getNotify(request), "Timer", "timer")).toEqual(timerBefore);

  // And the real cross-process seam: the card re-renders the new target from the
  // socket echo, not from anything the modal kept locally.
  await expect(page.getByText("→ 203°")).toBeVisible({ timeout: 20_000 });

  // Re-sending "eta": null must not trip the null-strip diagnostic.
  if (logBefore >= 0) {
    expect(
      nullStripCount(),
      "POST /api/control logged 'stripped null member(s)' -- the payload is sending a null " +
        "the merge patch would delete; fix the payload rather than the log",
    ).toBe(logBefore);
  }
});

test("turning the notification off clears the target and still leaves the limits alone", async ({
  page,
  request,
}) => {
  const { label, name } = await firstFoodProbe(request);

  const armed = baseline.map((e) => {
    if (e.label !== label) return e;
    if (e.type === "probe") return { ...e, req: true, target: 203, keep_warm: true };
    if (e.type === "probe_limit_high") return { ...e, req: true, target: 555, triggered: true };
    return e;
  });
  await postNotify(request, armed);
  await expect
    .poll(async () => entryFor(await getNotify(request), label, "probe")?.req, {
      timeout: 15_000,
      message: "the target pre-arm never landed",
    })
    .toBe(true);
  const limitBefore = entryFor(await getNotify(request), label, "probe_limit_high");

  await page.goto("/");
  await page.getByRole("button", { name: `Notifications for ${name}` }).click();
  await page.getByRole("checkbox", { name: /target temperature/i }).uncheck();
  // exact: the dashboard's settings gear is aria-label="settings", which a
  // substring match on "Set" also selects.
  await page.getByRole("button", { name: "Set", exact: true }).click();

  await expect
    .poll(
      async () => {
        const e = entryFor(await getNotify(request), label, "probe");
        return e ? { req: e.req, target: e.target } : null;
      },
      { timeout: 20_000, message: "the disable write never reached control.notify_data" },
    )
    .toEqual({ req: false, target: 0 });

  // Flask's Cancel button wipes the limit entries too (dash_default.js:807-813).
  // Turning the TARGET off here is one master switch of three: the high limit is
  // written back exactly as the modal read it, still armed at 555.
  const limitAfter = entryFor(await getNotify(request), label, "probe_limit_high");
  expect({ req: limitAfter?.req, target: limitAfter?.target }).toEqual({
    req: limitBefore?.req,
    target: limitBefore?.target,
  });
});

// The pre-arm, end to end. A limit saved with triggered:false while the probe
// has ALREADY passed it sounds its alarm on the very next control pass
// (notify/notifications.py:112). Arming a high limit of 1 degree is the cheapest
// way to make the client's pre-arm the only thing standing between the save and
// an immediate alarm -- `triggered` is computed as `currentTemp >= target` at
// save time (helpers/notify/notifyState.ts::limitEditFields).
//
// The grill therefore has to be RUNNING for this test to mean anything: the
// prototype backend reads its probes only in a running mode, and every reading
// reads back 0 in Stop -- against which no positive target has been "already
// passed" at all, and the save guard (ProbeNotifyModal.tsx) refuses a target of
// 0 outright. Monitor is the mode that reads probes and drives no outputs, so
// this arranges the very situation the pre-arm exists for: a probe genuinely
// sitting far above the limit being armed.
//
// SAFETY: no action is chosen, so this writes shutdown:false. A `triggered`
// high limit with "Shutdown PiFire" would stop the grill. Monitor mode itself
// runs no auger and no igniter.
test("a high limit the probe has already passed is saved pre-armed", async ({ page, request }) => {
  // Two mode changes and a queued write on top of the usual UI round trip; the
  // default 30s budget is spent before the assertions start.
  test.setTimeout(90_000);
  const { label, name } = await firstFoodProbe(request);

  try {
    const { current, silent } = await monitorProbeReadings(request);
    // The pre-arm can only be the thing under test on a backend whose probes
    // actually read. Without probe hardware the configured modules cannot load
    // and every probe sits at 0, which is a fact about this machine -- say so
    // and name the probes, rather than letting it look like a product failure.
    test.skip(
      current === null,
      `Backend has no live probe readings: after a Monitor pass ${silent.join(", ")} still read 0. ` +
        "The configured probe modules cannot read on a machine with no probe hardware.",
    );
    if (current === null) return;

    expect(
      current.F[label],
      "the probe must already be above the 1° limit for the pre-arm to be the thing under test",
    ).toBeGreaterThan(1);

    await page.goto("/");
    await page.getByRole("button", { name: `Notifications for ${name}` }).click();
    await page.getByRole("checkbox", { name: /high limit/i }).check();
    await page.getByRole("spinbutton", { name: /^high limit/i }).fill("1");
    // exact: the dashboard's settings gear is aria-label="settings", which a
    // substring match on "Set" also selects.
    await page.getByRole("button", { name: "Set", exact: true }).click();

    await expect
      .poll(
        async () => {
          const e = entryFor(await getNotify(request), label, "probe_limit_high");
          return e ? { req: e.req, target: e.target, triggered: e.triggered } : null;
        },
        { timeout: 20_000, message: "the high-limit write never reached control.notify_data" },
      )
      .toEqual({ req: true, target: 1, triggered: true });
  } finally {
    // Before the afterEach restores notify_data, and however the assertions
    // went: this is the only test in the file that starts the controller.
    await ensureStopped(request);
  }
});

test("the primary probe has a bell, with the limit actions but no target action", async ({
  page,
  request,
}) => {
  const res = await request.get(`${API}/api/current`);
  const body = (await res.json()) as { current?: { P?: Record<string, number> } };
  const primaryLabel = Object.keys(body.current?.P ?? {})[0];
  const primary = entryFor(await getNotify(request), primaryLabel, "probe");
  const name = String(primary?.name ?? primaryLabel);

  await page.goto("/");
  await page.getByRole("button", { name: `Notifications for ${name}` }).click();
  await expect(page.getByText(`${name} Notifications`)).toBeVisible();
  // _macro_dash_default.html:188-198 hides the TARGET action controls for the
  // Primary probe, and :238-244/:284-308 show the LIMIT ones for it alone --
  // acting on the fire is a primary-probe decision. :174-186 gives it the wider
  // range (600 F / 300 C). Read the unit off the control rather than assuming F
  // -- settings.spec.ts can leave this shared instance in Celsius.
  await expect(page.getByRole("group", { name: /when it is reached/i })).toHaveCount(0);
  await expect(page.getByRole("group", { name: /above the high limit/i })).toBeVisible();
  await expect(page.getByRole("group", { name: /below the low limit/i })).toBeVisible();
  const slider = page.getByRole("slider", { name: /^target temperature/i });
  const inCelsius = ((await slider.getAttribute("aria-label")) ?? "").includes("°C");
  await expect(slider).toHaveAttribute("max", inCelsius ? "300" : "600");
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText(`${name} Notifications`)).toHaveCount(0);
});
