import { describe, expect, it } from "@rstest/core";
import { moduleImageUrl } from "./wizardAssets";

describe("moduleImageUrl", () => {
  it("prefixes a bare manifest filename with PiFire's static wizard path", () => {
    expect(moduleImageUrl("", "pcb_4.x.x.png")).toBe("/static/img/wizard/pcb_4.x.x.png");
  });

  it("keeps the configured PiFire origin when one is set", () => {
    expect(moduleImageUrl("http://pifire.local:5000", "ads1115.png")).toBe(
      "http://pifire.local:5000/static/img/wizard/ads1115.png",
    );
  });

  it("returns undefined for a module with no image so no <img> is rendered", () => {
    expect(moduleImageUrl("", undefined)).toBeUndefined();
    expect(moduleImageUrl("", "")).toBeUndefined();
  });
});
