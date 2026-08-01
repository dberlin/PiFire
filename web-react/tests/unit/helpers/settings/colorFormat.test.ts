import { describe, expect, it } from "@rstest/core";
import { hexToRgbString, rgbStringToHex } from "../../../../src/helpers/settings/colorFormat";

describe("colorFormat", () => {
  it("round-trips every stored COLOR_LIST value", () => {
    const COLOR_LIST_VALUES = [
      "rgb(0, 64, 255, 1)",
      "rgb(0, 128, 255, 1)",
      "rgb(0, 200, 64, 1)",
      "rgb(0, 232, 126, 1)",
      "rgb(132, 0, 0, 1)",
      "rgb(200, 0, 0, 1)",
      "rgb(126, 0, 126, 1)",
      "rgb(126, 64, 125, 1)",
      "rgb(255, 210, 0, 1)",
      "rgb(255, 255, 0, 1)",
      "rgb(255, 126, 0, 1)",
      "rgb(255, 126, 64, 1)",
    ];
    for (const v of COLOR_LIST_VALUES) {
      expect(hexToRgbString(rgbStringToHex(v))).toBe(v);
    }
  });

  it("malformed rgb falls back to #000000", () => {
    expect(rgbStringToHex("nope")).toBe("#000000");
  });

  it("malformed hex falls back to rgb(0, 0, 0, 1)", () => {
    expect(hexToRgbString("zz")).toBe("rgb(0, 0, 0, 1)");
  });

  it("uppercase hex accepted", () => {
    expect(hexToRgbString("#FF8A2B")).toBe("rgb(255, 138, 43, 1)");
  });
});
