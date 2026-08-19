import type { WebUiBuildResponse } from "@pifire/core/contracts/core";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { fetchBuildId } from "../../../src/helpers/useWebUiBuild";

afterEach(() => {
  rs.unstubAllGlobals();
});

describe("fetchBuildId", () => {
  it("reads the generated build response without changing null semantics", async () => {
    const response = { build: "2026.08.10" } satisfies WebUiBuildResponse;
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => new Response(JSON.stringify(response), { status: 200 })),
    );

    await expect(fetchBuildId("http://pi:5000")).resolves.toBe("2026.08.10");
  });

  it("keeps a server-emitted null as no known build", async () => {
    const response = { build: null } satisfies WebUiBuildResponse;
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => new Response(JSON.stringify(response), { status: 200 })),
    );

    await expect(fetchBuildId()).resolves.toBeNull();
  });
});
