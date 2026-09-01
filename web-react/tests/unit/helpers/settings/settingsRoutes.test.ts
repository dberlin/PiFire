import { beforeEach, describe, expect, it, rs } from "@rstest/core";

import { queryKeys } from "../../../../src/helpers/query/keys";
import { queryClient } from "../../../../src/helpers/query/queryClient";

const getSettingsMock = rs.fn();
const getModeMock = rs.fn();
const getControllerMetadataMock = rs.fn();

rs.mock("../../../../src/helpers/settings/settingsApi", () => ({
  getSettings: getSettingsMock,
  getMode: getModeMock,
  getControllerMetadata: getControllerMetadataMock,
}));

// Imported after the mock so settingsRoutes picks up the mocked module.
const { settingsLoader } = await import("../../../../src/helpers/settings/settingsRoutes");

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";
const NORMALIZED_BASE_URL = BASE_URL.replace(/\/$/, "");

// settingsLoader primes the module-singleton queryClient (it has to: it runs
// outside React, before any provider exists). That cache persists across
// tests in this file, so without clearing it here, a later test's
// fetchQuery would find a fresh, un-invalidated entry from an earlier test
// and serve it straight from cache -- the freshly configured mock would
// never even be called. (This is not just test hygiene: it is the same
// warm-cache short-circuit "propagates a getSettings rejection on a warm,
// invalidated cache too" below exercises deliberately, on purpose, on a
// single cache entry instead of across tests.)
beforeEach(() => queryClient.clear());

describe("settingsLoader", () => {
  it("resolves {settings, mode, controllerMeta} from getSettings + getMode + getControllerMetadata", async () => {
    const fixtureSettings = { globals: { grill_name: "Test Grill" } };
    const fixtureMeta = { metadata: { pid: { friendly_name: "PID Standard", config: [] } } };
    getSettingsMock.mockResolvedValueOnce(fixtureSettings);
    getModeMock.mockResolvedValueOnce("Stop");
    getControllerMetadataMock.mockResolvedValueOnce(fixtureMeta);

    await expect(settingsLoader()).resolves.toEqual({
      settings: fixtureSettings,
      mode: "Stop",
      controllerMeta: fixtureMeta,
    });
  });

  it("primes settings, mode, and controller metadata under the normalized API base", async () => {
    const fixtureSettings = { globals: { grill_name: "Base-owned Grill" } };
    const fixtureMeta = { metadata: { mpc: { friendly_name: "MPC", config: [] } } };
    getSettingsMock.mockResolvedValueOnce(fixtureSettings);
    getModeMock.mockResolvedValueOnce("Smoke");
    getControllerMetadataMock.mockResolvedValueOnce(fixtureMeta);

    await settingsLoader();

    expect(queryClient.getQueryData(queryKeys.settings(NORMALIZED_BASE_URL))).toEqual(
      fixtureSettings,
    );
    expect(queryClient.getQueryData(queryKeys.mode(NORMALIZED_BASE_URL))).toBe("Smoke");
    expect(queryClient.getQueryData(queryKeys.controllerMetadata(NORMALIZED_BASE_URL))).toEqual(
      fixtureMeta,
    );
  });

  it("propagates a getSettings rejection so the route error element renders", async () => {
    getSettingsMock.mockRejectedValueOnce(new Error("boom"));
    getModeMock.mockResolvedValueOnce("Stop");
    getControllerMetadataMock.mockResolvedValueOnce(null);

    await expect(settingsLoader()).rejects.toThrow("boom");
  });

  it("propagates a getSettings rejection on a WARM but invalidated cache too", async () => {
    // A cold cache always calls the fetcher, so the test above alone can't
    // tell an ensureQueryData-shaped bug from a correct fetchQuery: with
    // ensureQueryData, once ANY data is cached the fetcher is never called
    // again -- a later rejection would be silently swallowed and the stale
    // blob from the first load served forever, and SettingsError would never
    // render on a second failed navigation. Prime the cache with a real
    // successful load first, invalidate it (as a save does), THEN fail.
    getSettingsMock.mockResolvedValueOnce({ globals: { grill_name: "Test Grill" } });
    getModeMock.mockResolvedValueOnce("Stop");
    getControllerMetadataMock.mockResolvedValueOnce(null);
    await settingsLoader(); // warms the cache

    await queryClient.invalidateQueries({ queryKey: queryKeys.settingsRoot(NORMALIZED_BASE_URL) });
    getSettingsMock.mockRejectedValueOnce(new Error("boom"));
    getModeMock.mockResolvedValueOnce("Stop");
    getControllerMetadataMock.mockResolvedValueOnce(null);

    await expect(settingsLoader()).rejects.toThrow("boom");
  });

  it("resolves controllerMeta: null while settings/mode still resolve when metadata fetch fails", async () => {
    const fixtureSettings = { globals: { grill_name: "Test Grill" } };
    getSettingsMock.mockResolvedValueOnce(fixtureSettings);
    getModeMock.mockResolvedValueOnce("Stop");
    getControllerMetadataMock.mockResolvedValueOnce(null);

    await expect(settingsLoader()).resolves.toEqual({
      settings: fixtureSettings,
      mode: "Stop",
      controllerMeta: null,
    });
  });
});
