import { describe, expect, it, rs } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CommandClient, CommandResult } from "../../helpers/command";
import { FIXTURE_DASH } from "../../helpers/fixture";
import type { DashData } from "../../helpers/types";
import { ControlButtons } from "./ControlButtons";

const OK: CommandResult = { ok: true, message: "" };
const at = (mode: string, over: Partial<DashData> = {}): DashData => ({
  ...FIXTURE_DASH,
  currentMode: mode,
  ...over,
});

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

describe("ControlButtons", () => {
  it("Stopped mode renders Startup / Prime / Monitor", () => {
    render(<ControlButtons dash={at("Stop")} command={stubCommand()} disabled={false} />);
    expect(screen.getByRole("button", { name: "Startup" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prime" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Monitor" })).toBeInTheDocument();
  });

  it("Monitor mode renders Startup / Stop", () => {
    render(<ControlButtons dash={at("Monitor")} command={stubCommand()} disabled={false} />);
    expect(screen.getByRole("button", { name: "Startup" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prime" })).not.toBeInTheDocument();
  });

  it("Cooking mode renders Smoke / Hold / Smoke+ / Shutdown / Stop", () => {
    render(<ControlButtons dash={at("Hold")} command={stubCommand()} disabled={false} />);
    expect(screen.getByRole("button", { name: "Smoke" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hold" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Smoke+" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Shutdown" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
  });

  it('clicking Smoke calls command.setMode("smoke")', async () => {
    const user = userEvent.setup();
    const command = stubCommand();
    render(<ControlButtons dash={at("Hold")} command={command} disabled={false} />);
    await user.click(screen.getByRole("button", { name: "Smoke" }));
    expect(command.setMode).toHaveBeenCalledWith("smoke");
  });

  it("clicking Hold opens the setpoint modal", async () => {
    const user = userEvent.setup();
    render(<ControlButtons dash={at("Hold")} command={stubCommand()} disabled={false} />);
    await user.click(screen.getByRole("button", { name: "Hold" }));
    expect(screen.getByText("Set Hold Temperature")).toBeInTheDocument();
  });

  it("clicking Stop opens the confirm modal", async () => {
    const user = userEvent.setup();
    render(<ControlButtons dash={at("Hold")} command={stubCommand()} disabled={false} />);
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(screen.getByText("Stop the cook?")).toBeInTheDocument();
  });
});
