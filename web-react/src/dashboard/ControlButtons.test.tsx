// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ControlButtons } from "./ControlButtons";
import { FIXTURE_DASH } from "../fixture";
import type { DashData } from "../types";
import type { CommandClient, CommandResult } from "../command";

const OK: CommandResult = { ok: true, message: "" };
const at = (mode: string, over: Partial<DashData> = {}): DashData => ({ ...FIXTURE_DASH, currentMode: mode, ...over });

function stubCommand(): CommandClient {
  return {
    setMode: vi.fn(async () => OK),
    hold: vi.fn(async () => OK),
    setSmokePlus: vi.fn(async () => OK),
    setPMode: vi.fn(async () => OK),
    prime: vi.fn(async () => OK),
    timerStart: vi.fn(async () => OK),
    timerPause: vi.fn(async () => OK),
    timerStop: vi.fn(async () => OK),
    system: vi.fn(async () => OK),
    setUnits: vi.fn(async () => OK),
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

  it("clicking Smoke calls command.setMode(\"smoke\")", async () => {
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
