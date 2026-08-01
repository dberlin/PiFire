import { describe, expect, it, rs } from "@rstest/core";

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

  it("propagates a getSettings rejection so the route error element renders", async () => {
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
