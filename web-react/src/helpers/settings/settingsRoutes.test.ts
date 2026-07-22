import { describe, expect, it, rs } from "@rstest/core";

const getSettingsMock = rs.fn();
const getModeMock = rs.fn();

rs.mock("./settingsApi", () => ({
  getSettings: getSettingsMock,
  getMode: getModeMock,
}));

// Imported after the mock so settingsRoutes picks up the mocked module.
const { settingsLoader } = await import("./settingsRoutes");

describe("settingsLoader", () => {
  it("resolves {settings, mode} from getSettings + getMode", async () => {
    const fixtureSettings = { globals: { grill_name: "Test Grill" } };
    getSettingsMock.mockResolvedValueOnce(fixtureSettings);
    getModeMock.mockResolvedValueOnce("Stop");

    await expect(settingsLoader()).resolves.toEqual({
      settings: fixtureSettings,
      mode: "Stop",
    });
  });

  it("propagates a getSettings rejection so the route error element renders", async () => {
    getSettingsMock.mockRejectedValueOnce(new Error("boom"));
    getModeMock.mockResolvedValueOnce("Stop");

    await expect(settingsLoader()).rejects.toThrow("boom");
  });
});
