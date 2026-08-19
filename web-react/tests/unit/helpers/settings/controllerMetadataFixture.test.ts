import type { ControllerCatalog } from "@pifire/core/settings/controllerTypes";
import { describe, expect, it } from "@rstest/core";
import fixture from "../../../e2e/fixtures/controller-metadata.json";

/**
 * Pins the TypeScript consumer shape of /api/controller_metadata against the
 * real payload. Python validates the fixture byte-for-byte against
 * ControllerCatalog; JSON imports widen discriminator strings, so the runtime
 * fixture crosses that TypeScript boundary with an explicit assertion here.
 */
const catalog = fixture as unknown as ControllerCatalog;

describe("controller metadata fixture", () => {
  it("uses ControllerCatalog and recommends a numeric cycle_ratio_max for every controller", () => {
    const names = Object.keys(catalog.metadata);
    expect(names.length).toBeGreaterThan(0);
    for (const name of names) {
      expect(typeof catalog.metadata[name]?.recommendations?.cycle?.cycle_ratio_max).toBe("number");
    }
  });
});
