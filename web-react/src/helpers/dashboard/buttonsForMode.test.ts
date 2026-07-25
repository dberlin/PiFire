import { describe, expect, it, rs } from "@rstest/core";
import type { CommandClient, CommandResult } from "../command";
import { FIXTURE_DASH } from "../fixture";
import type { LiveState } from "../types";
import { buttonsForMode } from "./buttonsForMode";

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
      expect(buttons.every((b) => b.action.type === "command")).toBe(true);

      const command = stubCommand();
      const prime = buttons[1];
      if (prime.action.type === "command") await prime.action.run(command);
      expect(command.prime).toHaveBeenCalledWith(25, "startup");

      const monitor = buttons[2];
      if (monitor.action.type === "command") await monitor.action.run(command);
      expect(command.setMode).toHaveBeenCalledWith("monitor");

      const manual = buttons[3];
      if (manual.action.type === "command") await manual.action.run(command);
      expect(command.setMode).toHaveBeenCalledWith("manual");
    },
  );

  it("Stop mode's Prime falls back to 10g when primeAmount is falsy", async () => {
    const buttons = buttonsForMode(at("Stop", { primeAmount: 0 }));
    const command = stubCommand();
    const prime = buttons.find((b) => b.label === "Prime");
    expect(prime).toBeDefined();
    if (prime?.action.type === "command") await prime.action.run(command);
    expect(command.prime).toHaveBeenCalledWith(10, "startup");
  });

  it("Monitor mode renders Startup (command) / Stop (confirm, danger)", async () => {
    const buttons = buttonsForMode(at("Monitor", { startupCheck: false }));
    expect(buttons.map((b) => b.label)).toEqual(["Startup", "Stop"]);
    expect(buttons[0].action.type).toBe("command");
    expect(buttons[1].action.type).toBe("confirm");
    expect(buttons[1].variant).toBe("danger");

    const command = stubCommand();
    const startup = buttons[0];
    if (startup.action.type === "command") await startup.action.run(command);
    expect(command.setMode).toHaveBeenCalledWith("startup");

    const stop = buttons[1];
    if (stop.action.type === "confirm") {
      expect(stop.action.title).toBe("Stop the cook?");
      await stop.action.run(command);
    }
    expect(command.setMode).toHaveBeenCalledWith("stop");
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
