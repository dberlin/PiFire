import type { APIRequestContext } from "@playwright/test";

import { ports } from "../../ports";

/** Origin of the PiFire backend these specs drive. Never hardcode it: several
 *  checkouts run their own backend at once, and a literal here would aim every
 *  workspace's suite at one shared grill. */
export const API = ports.pifireUrl;

/**
 * Ensures the grill is in Stop mode before running tests.
 * Checks current mode via GET /api/get/mode, and if not "Stop",
 * POSTs to /api/set/mode/stop and polls until the mode is "Stop" (up to 10s).
 */
export async function ensureStopped(request: APIRequestContext): Promise<void> {
  const baseUrl = API;
  const maxWaitMs = 10000;
  const pollIntervalMs = 200;
  const startTime = Date.now();

  // Check current mode
  const modeRes = await request.get(`${baseUrl}/api/get/mode`);
  if (!modeRes.ok()) {
    throw new Error(`Failed to get mode: HTTP ${modeRes.status}`);
  }

  const modeBody = (await modeRes.json()) as { data?: { mode?: string } };
  let currentMode = modeBody.data?.mode ?? "";

  if (currentMode === "Stop") {
    // Already stopped, nothing to do
    return;
  }

  // Issue stop command
  const stopRes = await request.post(`${baseUrl}/api/set/mode/stop`);
  if (!stopRes.ok()) {
    throw new Error(`Failed to set mode to stop: HTTP ${stopRes.status}`);
  }

  // Poll until mode is Stop or timeout
  while (Date.now() - startTime < maxWaitMs) {
    await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));

    const checkRes = await request.get(`${baseUrl}/api/get/mode`);
    if (!checkRes.ok()) {
      throw new Error(`Failed to get mode during polling: HTTP ${checkRes.status}`);
    }

    const checkBody = (await checkRes.json()) as { data?: { mode?: string } };
    currentMode = checkBody.data?.mode ?? "";

    if (currentMode === "Stop") {
      return;
    }
  }

  // If we've timed out but the mode is still not Stop, throw an error.
  // This likely means control.py is not running or not responding to mode changes.
  throw new Error(
    `Timeout waiting for grill to reach Stop mode after ${maxWaitMs}ms. ` +
      `Current mode: "${currentMode}". ` +
      `Is control.py running? Check that "uv run python control.py" is active.`,
  );
}

interface ProbeInfo {
  label: string;
  type: string;
}

interface CurrentBlob {
  P: Record<string, number>;
  F: Record<string, number>;
  [k: string]: unknown;
}

/** The labels the LIVE probe map says the controller should be publishing,
 *  grouped the way /api/current groups them. */
async function liveProbeLabels(request: APIRequestContext): Promise<{ P: string[]; F: string[] }> {
  const res = await request.get(`${API}/api/settings`);
  if (!res.ok()) throw new Error(`Failed to read settings: HTTP ${res.status}`);
  const body = (await res.json()) as {
    settings?: { probe_settings?: { probe_map?: { probe_info?: ProbeInfo[] } } };
  };
  const info = body.settings?.probe_settings?.probe_map?.probe_info ?? [];
  return {
    P: info.filter((p) => p.type === "Primary").map((p) => p.label),
    F: info.filter((p) => p.type === "Food").map((p) => p.label),
  };
}

async function pollCurrent(
  request: APIRequestContext,
  ok: (current: CurrentBlob) => boolean,
  maxWaitMs: number,
  what: string,
): Promise<CurrentBlob> {
  const startTime = Date.now();
  let last = "never read";
  for (;;) {
    const res = await request.get(`${API}/api/current`);
    if (res.ok()) {
      const current = ((await res.json()) as { current?: CurrentBlob }).current;
      if (current !== undefined) {
        last = JSON.stringify({ P: current.P, F: current.F });
        if (ok(current)) return current;
      }
    }
    if (Date.now() - startTime >= maxWaitMs) {
      throw new Error(`${what} after ${maxWaitMs}ms; /api/current last read ${last}.`);
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}

/**
 * Assert that control.py's `current` blob is keyed by the labels the LIVE probe
 * map declares -- the invariant the whole suite's socket connection rests on.
 *
 * The socket payload builder indexes `current` BY LABEL out of
 * settings.probe_settings.probe_map (blueprints/mobile/socket_io.py
 * ::_get_probe_data). When the two disagree it raises `KeyError: <label>` inside
 * the `connect` handler, python-socketio refuses the connection, and every
 * browser in the suite sits on "Connecting to PiFire…" until its timeout -- not
 * for one test, but for every later spec.
 *
 * The two only come apart because `current` is rewritten ONLY while the
 * controller is in a running mode: in Stop it keeps whatever labels the last
 * running mode left. So a spec that changes the probe map has to make sure it
 * does not RUN the grill between the change and the restore. This checks that
 * it did not, rather than repairing it -- the repair (a Monitor pass) ends a
 * cook, and a cook end whose history store spans a probe rename fails
 * create_cookfile and plants a permanent error banner on every page.
 */
export async function expectCurrentMatchesProbeMap(request: APIRequestContext): Promise<void> {
  const want = await liveProbeLabels(request);
  const sameKeys = (labels: string[], group: Record<string, number> | undefined) =>
    labels.length === Object.keys(group ?? {}).length && labels.every((l) => l in (group ?? {}));
  await pollCurrent(
    request,
    (c) => sameKeys(want.P, c.P) && sameKeys(want.F, c.F),
    5000,
    `control.py's /api/current is not keyed by the live probe map (wanted ${JSON.stringify(want)}). ` +
      "socket_io's connect handler will KeyError on this and refuse every connection",
  );
}

/**
 * Put the grill in Monitor and report what its probes read.
 *
 * A spec that needs a probe to have READ something has to run the grill: this
 * backend reads its probes only in a running mode, and every reading reads back
 * 0 in Stop. Monitor is the mode for it -- it reads probes and drives no
 * outputs, so it is safe even against real hardware.
 *
 * A backend that never produces a reading is a RESULT here, not a timeout: a
 * machine with no probe hardware runs control.py perfectly well, but its
 * configured probe modules cannot read on it (mcp9600_adafruit needs a real
 * I2C bus, thermoworks_cloud needs the network and credentials), so every
 * probe stays at 0. That is a property of the machine rather than a failure,
 * and it is the caller's business what to do about it -- so `silent` names the
 * probes that produced nothing and `current` is null, instead of a timeout
 * asking whether control.py is running.
 *
 * Leaves the grill IN Monitor either way: a caller that needs a live reading
 * needs it to still be live. Pair it with `ensureStopped` in a finally.
 */
export async function monitorProbeReadings(
  request: APIRequestContext,
  maxWaitMs = 20000,
): Promise<{ current: CurrentBlob | null; silent: string[] }> {
  const want = await liveProbeLabels(request);
  const modeRes = await request.post(`${API}/api/set/mode/monitor`);
  if (!modeRes.ok()) throw new Error(`Failed to set mode to monitor: HTTP ${modeRes.status}`);

  const silentIn = (labels: string[], group: Record<string, number> | undefined) =>
    labels.filter((label) => !((group?.[label] ?? 0) > 0));

  const startTime = Date.now();
  let last: CurrentBlob | null = null;
  for (;;) {
    const res = await request.get(`${API}/api/current`);
    if (res.ok()) {
      const current = ((await res.json()) as { current?: CurrentBlob }).current;
      if (current !== undefined) {
        last = current;
        const silent = [...silentIn(want.P, current.P), ...silentIn(want.F, current.F)];
        if (silent.length === 0) return { current, silent };
      }
    }
    if (Date.now() - startTime >= maxWaitMs) {
      return {
        current: null,
        silent: last
          ? [...silentIn(want.P, last.P), ...silentIn(want.F, last.F)]
          : [...want.P, ...want.F],
      };
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
}
