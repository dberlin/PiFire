import { describe, expect, it, rs } from "@rstest/core";
import type { CommandClient, CommandResult } from "../../../../src/helpers/command";
import { buttonsForMode } from "../../../../src/helpers/dashboard/buttonsForMode";
import { FIXTURE_DASH } from "../../../../src/helpers/fixture";
import type { LiveState } from "../../../../src/helpers/types";

const OK: CommandResult = { ok: true, message: "" };

function stubCommand(): CommandClient {
  return {
    setMode: rs.fn(async () => OK),
    hold: rs.fn(async () => OK),
    setSmokePlus: rs.fn(async () => OK),
    setPMode: rs.fn(async () => OK),
    prime: rs.fn(async () => OK),
    timerStart: rs.fn(async () => OK),
    timerStartWithOptions: rs.fn(async () => OK),
    timerPause: rs.fn(async () => OK),
    timerStop: rs.fn(async () => OK),
    timerShutdown: rs.fn(async () => OK),
    timerKeepWarm: rs.fn(async () => OK),
    system: rs.fn(async () => OK),
    setUnits: rs.fn(async () => OK),
    manualOutput: rs.fn(async () => OK),
    manualPwm: rs.fn(async () => OK),
    recipeNextStep: rs.fn(async () => OK),
    recipeUnpause: rs.fn(async () => OK),
  };
}

const at = (mode: string, over: Partial<LiveState> = {}): LiveState => ({
  ...FIXTURE_DASH,
  currentMode: mode,
  ...over,
});

describe("buttonsForMode", () => {
  it.each(["Stop", "Error", ""])(
    "%s renders Startup / Prime / Monitor / Manual as command actions",
    async (mode) => {
      // startupCheck off explicitly: the fixture ships it ON, and with either
      // gate configured Startup is a "startup" intent rather than a bare
      // command (see the startup-confirmation block below).
      const buttons = buttonsForMode(at(mode, { primeAmount: 25, startupCheck: false }));
      expect(buttons.map((b) => b.label)).toEqual(["Startup", "Prime", "Monitor", "Manual"]);
      // Startup / Monitor / Manual are bare commands; Prime opens a menu.
      expect(buttons.map((b) => b.action.type)).toEqual(["command", "menu", "command", "command"]);

      const command = stubCommand();
      const monitor = buttons[2];
      if (monitor.action.type === "command") await monitor.action.run(command);
      expect(command.setMode).toHaveBeenCalledWith("monitor");

      const manual = buttons[3];
      if (manual.action.type === "command") await manual.action.run(command);
      expect(command.setMode).toHaveBeenCalledWith("manual");
    },
  );

  // I2: Flask offers six prime choices -- three amounts x two follow-on modes
  // (_macro_control_panel.html:80-85). React had one button that always primed
  // dash.primeAmount and always went on to Startup, so "prime and stop", the
  // variant for loading a fresh bag, was unreachable.
  describe("Stop mode's Prime menu", () => {
    const primeAction = () => {
      const prime = buttonsForMode(at("Stop")).find((b) => b.label === "Prime");
      if (prime?.action.type !== "menu") throw new Error("Prime is not a menu action");
      return prime.action;
    };

    it("offers exactly Flask's six items, in Flask's order", () => {
      expect(primeAction().items.map((i) => i.label)).toEqual([
        "Prime 10g",
        "Prime 25g",
        "Prime 50g",
        "Prime 10g & Startup",
        "Prime 25g & Startup",
        "Prime 50g & Startup",
      ]);
    });

    it("primes and stops for the first three", async () => {
      const action = primeAction();
      const command = stubCommand();
      await action.run(command, action.items[1].value);
      expect(command.prime).toHaveBeenCalledWith(25, "stop");
    });

    it("primes and starts up for the last three", async () => {
      const action = primeAction();
      const command = stubCommand();
      await action.run(command, action.items[5].value);
      expect(command.prime).toHaveBeenCalledWith(50, "startup");
    });

    it("ignores dash.primeAmount entirely -- the user picks", () => {
      const prime = buttonsForMode(at("Stop", { primeAmount: 37 })).find(
        (b) => b.label === "Prime",
      );
      if (prime?.action.type !== "menu") throw new Error("Prime is not a menu action");
      expect(prime.action.items.map((i) => i.value)).not.toContain("37:stop");
    });
  });

  // Monitor keeps the whole idle row instead of collapsing to Startup/Stop, so
  // the row reads as a mode selector rather than changing shape underneath the
  // press -- matching the attached display, whose _button_row_for_mode falls
  // through to one list for Stop, Prime AND Monitor and marks the active mode.
  it("Monitor mode renders the idle row plus Stop, not a collapsed Startup/Stop", () => {
    const buttons = buttonsForMode(at("Monitor", { startupCheck: false }));
    expect(buttons.map((b) => b.label)).toEqual(["Startup", "Prime", "Monitor", "Manual", "Stop"]);
  });

  it("marks Monitor as the active mode while monitoring, and not while stopped", () => {
    const monitoring = buttonsForMode(at("Monitor", { startupCheck: false }));
    expect(monitoring.find((b) => b.label === "Monitor")?.variant).toBe("accent");

    const stopped = buttonsForMode(at("Stop", { startupCheck: false }));
    expect(stopped.find((b) => b.label === "Monitor")?.variant).toBeUndefined();
  });

  // Accent is the row's one "you are in this mode" mark. Startup used to carry
  // it unconditionally, so pressing Monitor marked two buttons at once and the
  // grill looked like it was starting up. It is primary instead -- the way in,
  // not a mode -- and primary is drawn as a fill, never as a lit border.
  it.each(["Stop", "Monitor"])("marks Startup primary in %s, never accent", (mode) => {
    const buttons = buttonsForMode(at(mode, { startupCheck: false }));
    expect(buttons.find((b) => b.label === "Startup")?.variant).toBe("primary");
    expect(buttons.filter((b) => b.variant === "accent").map((b) => b.label)).toEqual(
      mode === "Monitor" ? ["Monitor"] : [],
    );
  });

  it("pressing Monitor while monitoring leaves Monitor rather than re-entering it", async () => {
    const monitor = buttonsForMode(at("Monitor", { startupCheck: false })).find(
      (b) => b.label === "Monitor",
    );
    const command = stubCommand();
    if (monitor?.action.type !== "command") throw new Error("Monitor is not a command action");
    await monitor.action.run(command);
    expect(command.setMode).toHaveBeenCalledWith("stop");
  });

  it("pressing Monitor while stopped enters Monitor", async () => {
    const monitor = buttonsForMode(at("Stop", { startupCheck: false })).find(
      (b) => b.label === "Monitor",
    );
    const command = stubCommand();
    if (monitor?.action.type !== "command") throw new Error("Monitor is not a command action");
    await monitor.action.run(command);
    expect(command.setMode).toHaveBeenCalledWith("monitor");
  });

  it("Stop is unconfirmed in Monitor -- nothing was ever lit to lose", async () => {
    const buttons = buttonsForMode(at("Monitor", { startupCheck: false }));
    const stop = buttons.find((b) => b.label === "Stop");
    expect(stop?.variant).toBe("danger");
    expect(stop?.action.type).toBe("command");

    const command = stubCommand();
    if (stop?.action.type !== "command") throw new Error("Stop is not a command action");
    await stop.action.run(command);
    expect(command.setMode).toHaveBeenCalledWith("stop");
  });

  it("Stop stays behind a confirmation during an active cook", () => {
    const stop = buttonsForMode(at("Smoke")).find((b) => b.label === "Stop");
    expect(stop?.action.type).toBe("confirm");
    if (stop?.action.type === "confirm") expect(stop.action.title).toBe("Stop the cook?");
  });

  it("Startup still works from Monitor", async () => {
    const startup = buttonsForMode(at("Monitor", { startupCheck: false })).find(
      (b) => b.label === "Startup",
    );
    const command = stubCommand();
    if (startup?.action.type !== "command") throw new Error("Startup is not a command action");
    await startup.action.run(command);
    expect(command.setMode).toHaveBeenCalledWith("startup");
  });

  it.each(["Startup", "Smoke", "Hold", "Prime", "Reignite", "Shutdown", "SomeUnknownMode"])(
    "%s (active/unknown mode) renders Smoke / Hold / Smoke+ / Shutdown / Stop",
    (mode) => {
      const buttons = buttonsForMode(at(mode));
      expect(buttons.map((b) => b.label)).toEqual(["Smoke", "Hold", "Smoke+", "Shutdown", "Stop"]);
      expect(buttons[0].action.type).toBe("command");
      expect(buttons[1].action.type).toBe("setpoint");
      expect(buttons[1].variant).toBe("accent");
      expect(buttons[2].action.type).toBe("command");
      expect(buttons[3].action.type).toBe("confirm");
      expect(buttons[4].action.type).toBe("confirm");
      expect(buttons[4].variant).toBe("danger");
    },
  );

  it("Smoke+ variant reflects dash.smokePlus and toggles the opposite value", async () => {
    const on = buttonsForMode(at("Hold", { smokePlus: true })).find((b) => b.label === "Smoke+");
    expect(on?.variant).toBe("accent");
    const command = stubCommand();
    if (on?.action.type === "command") await on.action.run(command);
    expect(command.setSmokePlus).toHaveBeenCalledWith(false);

    const off = buttonsForMode(at("Hold", { smokePlus: false })).find((b) => b.label === "Smoke+");
    expect(off?.variant).toBeUndefined();
    const command2 = stubCommand();
    if (off?.action.type === "command") await off.action.run(command2);
    expect(command2.setSmokePlus).toHaveBeenCalledWith(true);
  });

  it("Smoke and Shutdown actions in an active mode dispatch the expected commands", async () => {
    const buttons = buttonsForMode(at("Hold"));
    const command = stubCommand();

    const smoke = buttons.find((b) => b.label === "Smoke");
    if (smoke?.action.type === "command") await smoke.action.run(command);
    expect(command.setMode).toHaveBeenCalledWith("smoke");

    const shutdown = buttons.find((b) => b.label === "Shutdown");
    if (shutdown?.action.type === "confirm") {
      expect(shutdown.action.title).toBe("Shut down the grill?");
      await shutdown.action.run(command);
    }
    expect(command.setMode).toHaveBeenCalledWith("shutdown");
  });

  it("offers a Manual entry button when idle", () => {
    const buttons = buttonsForMode({ ...FIXTURE_DASH, currentMode: "Stop" });
    expect(buttons.map((b) => b.label)).toContain("Manual");
  });

  it("in Manual mode shows the four output toggles and Stop", () => {
    const buttons = buttonsForMode({ ...FIXTURE_DASH, currentMode: "Manual", hasDcFan: false });
    expect(buttons.map((b) => b.label)).toEqual(["Power", "Igniter", "Auger", "Fan", "Stop"]);
  });

  it("marks an output button accent while that output is live", () => {
    const buttons = buttonsForMode({
      ...FIXTURE_DASH,
      currentMode: "Manual",
      hasDcFan: false,
      outputs: { fan: false, auger: true, igniter: false, power: false },
    });
    const byLabel = Object.fromEntries(buttons.map((b) => [b.label, b]));
    expect(byLabel.Auger.variant).toBe("accent");
    expect(byLabel.Fan.variant).toBeUndefined();
  });

  it("adds a Fan % button only when the platform has a DC fan", () => {
    const withFan = buttonsForMode({ ...FIXTURE_DASH, currentMode: "Manual", hasDcFan: true });
    expect(withFan.map((b) => b.label)).toContain("Fan %");
    const withoutFan = buttonsForMode({ ...FIXTURE_DASH, currentMode: "Manual", hasDcFan: false });
    expect(withoutFan.map((b) => b.label)).not.toContain("Fan %");
  });

  it("does not show manual outputs outside Manual mode even if manual changes are allowed", () => {
    // Legacy hides these unless mode == Manual, despite _cmd_set_manual also
    // permitting toggles when safety.allow_manual_changes is true. Match that.
    const buttons = buttonsForMode({
      ...FIXTURE_DASH,
      currentMode: "Smoke",
      allowManualOutputs: true,
    });
    expect(buttons.map((b) => b.label)).not.toContain("Auger");
  });
});

// I3: Flask gates ignition behind a modal (_macro_control_panel.html:89-90).
// React shipped Startup as a bare command, so the safety check and the hold
// prompt both disappeared. buttonsForMode returns the INTENT; which of the two
// variants to render is ControlButtons' decision.
describe("buttonsForMode startup confirmation", () => {
  const startupAction = (dash: LiveState) =>
    buttonsForMode(dash).find((b) => b.label === "Startup")?.action;

  it("is a plain command when neither the check nor the hold prompt is configured", () => {
    expect(
      startupAction(at("Stop", { startupCheck: false, startToHoldPrompt: false })),
    ).toMatchObject({ type: "command" });
  });

  it("asks for confirmation when safety.startup_check is set", () => {
    expect(startupAction(at("Stop", { startupCheck: true, startToHoldPrompt: false }))).toEqual({
      type: "startup",
    });
  });

  it("asks for confirmation when the hold prompt is configured for Hold", () => {
    expect(
      startupAction(
        at("Stop", { startupCheck: false, startToHoldPrompt: true, startupGotoMode: "Hold" }),
      ),
    ).toEqual({ type: "startup" });
  });

  it("asks for confirmation when both are set", () => {
    expect(
      startupAction(
        at("Stop", { startupCheck: true, startToHoldPrompt: true, startupGotoMode: "Hold" }),
      ),
    ).toEqual({ type: "startup" });
  });

  it("stays a plain command when the hold prompt is set but the target mode is not Hold", () => {
    // Flask's condition is `start_to_hold_prompt AND after_startup_mode ==
    // 'Hold'` (_macro_control_panel.html:89) -- the prompt alone is not enough.
    expect(
      startupAction(
        at("Stop", { startupCheck: false, startToHoldPrompt: true, startupGotoMode: "Smoke" }),
      ),
    ).toMatchObject({ type: "command" });
  });

  it("applies the same gate to the Startup button in Monitor mode", () => {
    expect(startupAction(at("Monitor", { startupCheck: true }))).toEqual({ type: "startup" });
  });
});

// I4a: buttonsForMode used to fall through to the active-cook ladder for any
// unrecognised mode, so a running recipe offered Smoke / Hold / Smoke+ --
// exactly the buttons that break out of the recipe, and exactly the ones Flask
// hides (control_panel.js:181-182).
describe("buttonsForMode during a recipe", () => {
  const inRecipe = (over: Partial<LiveState["recipeStatus"]> = {}, mode = "Recipe") =>
    at(mode, {
      recipeStatus: { ...FIXTURE_DASH.recipeStatus, recipeMode: true, ...over },
    });

  it("offers only the recipe controls", () => {
    const labels = buttonsForMode(inRecipe()).map((b) => b.label);
    expect(labels).toEqual(["Next Step", "Shutdown", "Stop"]);
  });

  it("offers none of the controls that would break out of the recipe", () => {
    const labels = buttonsForMode(inRecipe()).map((b) => b.label);
    expect(labels).not.toContain("Smoke");
    expect(labels).not.toContain("Hold");
    expect(labels).not.toContain("Smoke+");
  });

  it("keys off recipeStatus.recipeMode, not the mode string", () => {
    // The controller publishes the boolean; currentMode can read as the running
    // SUB-mode (controller/runtime/modes/base.py:478).
    const labels = buttonsForMode(inRecipe({}, "Smoke")).map((b) => b.label);
    expect(labels).toEqual(["Next Step", "Shutdown", "Stop"]);
  });

  it("is unaffected when recipeMode is false even in mode Recipe", () => {
    const labels = buttonsForMode(
      at("Recipe", { recipeStatus: { ...FIXTURE_DASH.recipeStatus, recipeMode: false } }),
    ).map((b) => b.label);
    expect(labels).toEqual(["Smoke", "Hold", "Smoke+", "Shutdown", "Stop"]);
  });

  it("Next Step advances the recipe when it is not paused", async () => {
    const command = stubCommand();
    const next = buttonsForMode(inRecipe())[0];
    if (next.action.type === "command") await next.action.run(command);
    expect(command.recipeNextStep).toHaveBeenCalled();
    expect(command.recipeUnpause).not.toHaveBeenCalled();
  });

  it("glows Next Step while the recipe is paused, and unpauses instead of advancing", async () => {
    const paused = buttonsForMode(inRecipe({ paused: true }))[0];
    expect(paused.variant).toBe("accent");
    expect(buttonsForMode(inRecipe({ paused: false }))[0].variant).toBeUndefined();

    // Paused sends recipeUnpause (clears the pause flag), NOT recipeNextStep --
    // {updated:true} is ignored by a paused step, so advancing would do nothing.
    const command = stubCommand();
    if (paused.action.type === "command") await paused.action.run(command);
    expect(command.recipeUnpause).toHaveBeenCalled();
    expect(command.recipeNextStep).not.toHaveBeenCalled();
  });

  it("keeps Shutdown behind its confirmation, and Stop behind its own", () => {
    const buttons = buttonsForMode(inRecipe());
    expect(buttons[1].action.type).toBe("confirm");
    expect(buttons[2].action.type).toBe("confirm");
    expect(buttons[2].variant).toBe("danger");
  });

  // Flask's #recipe_group also carries a "Step N" link to /recipes, a Flask
  // page. The app-shell decision forbids linking out -- it drops the live
  // socket -- so it is deliberately absent. This assertion is what stops it
  // coming back.
  it("offers no link out to the Flask recipes page", () => {
    const labels = buttonsForMode(inRecipe({ step: 3 })).map((b) => b.label);
    expect(labels.some((l) => /step\s*\d/i.test(l))).toBe(false);
  });
});
