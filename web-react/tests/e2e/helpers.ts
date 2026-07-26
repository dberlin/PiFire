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
