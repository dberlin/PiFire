import { describe, expect, it } from "@rstest/core";

import { deriveRunView } from "../../../../src/helpers/recipes/runStatus";

const STOPPED = { recipeMode: false, filename: "", mode: "Stop", paused: false, step: 0 };

describe("deriveRunView", () => {
  it("is inactive and runnable when the grill is stopped and no recipe is loaded", () => {
    const view = deriveRunView(STOPPED, "brisket.pfrecipe");
    expect(view).toEqual({
      active: false,
      otherFilename: null,
      paused: false,
      step: 0,
      canRun: true,
    });
  });

  it("is active when recipeMode is on and the filename matches this page", () => {
    const status = {
      ...STOPPED,
      recipeMode: true,
      mode: "Recipe",
      filename: "brisket.pfrecipe",
      step: 3,
    };
    const view = deriveRunView(status, "brisket.pfrecipe");
    expect(view.active).toBe(true);
    expect(view.otherFilename).toBeNull();
    expect(view.step).toBe(3);
  });

  // G9: Flask's control loop ignores the requested filename and just drives
  // whatever recipe is actually loaded. A client-side view has to say which
  // recipe that is rather than highlighting a step of the one being viewed.
  it("reports the OTHER filename when a different recipe is running, and is not active", () => {
    const status = {
      ...STOPPED,
      recipeMode: true,
      mode: "Recipe",
      filename: "ribs.pfrecipe",
      step: 1,
    };
    const view = deriveRunView(status, "brisket.pfrecipe");
    expect(view.active).toBe(false);
    expect(view.otherFilename).toBe("ribs.pfrecipe");
  });

  it("carries paused through unchanged", () => {
    const status = { ...STOPPED, recipeMode: true, mode: "Recipe", filename: "f", paused: true };
    expect(deriveRunView(status, "f").paused).toBe(true);
  });

  it("disallows Run unless recipeStatus.mode is exactly Stop", () => {
    expect(deriveRunView({ ...STOPPED, mode: "Hold" }, "f").canRun).toBe(false);
    expect(deriveRunView({ ...STOPPED, mode: "Startup" }, "f").canRun).toBe(false);
    expect(deriveRunView({ ...STOPPED, mode: "Stop" }, "f").canRun).toBe(true);
  });
});
