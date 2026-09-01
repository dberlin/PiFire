import { describe, expect, it } from "@rstest/core";

import { behindText } from "../../../../src/helpers/update/behindText";

describe("behindText", () => {
  it("reports unavailable when the check has not resolved", () => {
    expect(behindText(null)).toBe("Update status unavailable");
  });

  it("reports up to date at zero commits behind", () => {
    expect(behindText(0)).toBe("Up to date");
  });

  it("reports the count when behind", () => {
    expect(behindText(3)).toBe("3 commits behind");
  });
});
