import { describe, expect, it, rs } from "@rstest/core";
import type { CommandClient, CommandResult } from "../command";
import { FIXTURE_DASH } from "../fixture";
import type { DashData } from "../types";
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
    timerPause: rs.fn(async () => OK),
    timerStop: rs.fn(async () => OK),
    system: rs.fn(async () => OK),
    setUnits: rs.fn(async () => OK),
  };
}

const at = (mode: string, over: Partial<DashData> = {}): DashData => ({
  ...FIXTURE_DASH,
  currentMode: mode,
  ...over,
});

describe("buttonsForMode", () => {
  it.each(["Stop", "Error", ""])(
    "%s renders Startup / Prime / Monitor as command actions",
    async (mode) => {
      const buttons = buttonsForMode(at(mode, { primeAmount: 25 }));
      expect(buttons.map((b) => b.label)).toEqual(["Startup", "Prime", "Monitor"]);
      expect(buttons.every((b) => b.action.type === "command")).toBe(true);

      const command = stubCommand();
      const prime = buttons[1];
      if (prime.action.type === "command") await prime.action.run(command);
      expect(command.prime).toHaveBeenCalledWith(25, "startup");

      const monitor = buttons[2];
      if (monitor.action.type === "command") await monitor.action.run(command);
      expect(command.setMode).toHaveBeenCalledWith("monitor");
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
    const buttons = buttonsForMode(at("Monitor"));
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
});
