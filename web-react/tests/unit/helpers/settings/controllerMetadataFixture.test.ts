import { describe, expect, it } from "@rstest/core";
import type { ControllerMetadata } from "../../../../src/helpers/settings/settingsApi";
import fixture from "../../../e2e/fixtures/controller-metadata.json";

/**
 * Pins the TypeScript view of /api/controller_metadata against the REAL
 * payload rather than a hand-written literal.
 *
 * The loop this closes: `blueprints/api/routes.py` serves
 * `controller/controllers.json` verbatim; the Python-side
 * `tests/unit/controller/test_controller_catalog.py::test_controller_metadata_fixture_matches_production_catalog`
 * pins this fixture byte-for-byte to that catalog; the assignment below pins
 * the fixture to `ControllerMetadata`. A fixture written by hand only proves
 * it agrees with the type it was written from, which is no proof at all.
 *
 * The assignment IS the load-bearing half of this test -- it fails at
 * typecheck, not at runtime, the moment the catalog grows a shape the type
 * cannot represent (it has already caught `option_min`/`option_max` being
 * declared required against a catalog whose bool and list options omit them).
 */
const catalog: ControllerMetadata = fixture;

describe("controller metadata fixture", () => {
  it("types as ControllerMetadata and recommends a numeric cycle_ratio_max for every controller", () => {
    const names = Object.keys(catalog.metadata);
    expect(names.length).toBeGreaterThan(0);
    for (const name of names) {
      expect(typeof catalog.metadata[name].recommendations?.cycle?.cycle_ratio_max).toBe("number");
    }
  });
});
