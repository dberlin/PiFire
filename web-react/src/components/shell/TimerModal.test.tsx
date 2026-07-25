import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import type { CommandClient, CommandResult } from "../../helpers/command";
import type { LiveState } from "../../helpers/types";
import { TimerModal } from "./TimerModal";

const OK: CommandResult = { ok: true, message: "" };

type Timer = LiveState["timer"];

const timer = (over: Partial<Timer> = {}): Timer => ({
  start: 0,
  paused: 0,
  end: 0,
  keepWarm: false,
  shutdown: false,
  ...over,
});

/** A command stub that records the order in which timer commands were issued. */
function stubCommand() {
  const calls: string[] = [];
  const record =
    (name: string) =>
    async (...args: unknown[]) => {
      calls.push(args.length ? `${name}:${String(args[0])}` : name);
      return OK;
    };
  const command: CommandClient = {
    setMode: rs.fn(async () => OK),
    hold: rs.fn(async () => OK),
    setSmokePlus: rs.fn(async () => OK),
    setPMode: rs.fn(async () => OK),
    prime: rs.fn(async () => OK),
    timerStart: rs.fn(record("start")),
    timerPause: rs.fn(record("pause")),
    timerStop: rs.fn(record("stop")),
    timerShutdown: rs.fn(record("shutdown")),
    timerKeepWarm: rs.fn(record("keep_warm")),
    system: rs.fn(async () => OK),
    setUnits: rs.fn(async () => OK),
    manualOutput: rs.fn(async () => OK),
    manualPwm: rs.fn(async () => OK),
  };
  return { command, calls };
}

function open(over: Partial<Timer> = {}) {
  const { command, calls } = stubCommand();
  const onClose = rs.fn();
  render(<TimerModal timer={timer(over)} command={command} onClose={onClose} />);
  return { command, calls, onClose };
}

const hours = () => screen.getByLabelText("Hours") as HTMLInputElement;
const minutes = () => screen.getByLabelText("Minutes") as HTMLInputElement;
const startButton = () => screen.getByRole("button", { name: "Start" });

describe("TimerModal", () => {
  it("renders as a dialog with both sliders at zero", () => {
    open();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(hours().value).toBe("0");
    expect(minutes().value).toBe("0");
  });

  it("bounds the sliders to the ranges base.html uses", () => {
    open();
    expect(hours().min).toBe("0");
    expect(hours().max).toBe("23");
    expect(minutes().min).toBe("0");
    expect(minutes().max).toBe("59");
  });

  it("shows the slider values as they move", () => {
    open();
    fireEvent.change(hours(), { target: { value: "2" } });
    fireEvent.change(minutes(), { target: { value: "30" } });
    expect(hours().value).toBe("2");
    expect(minutes().value).toBe("30");
    expect(screen.getByText("2")).toBeTruthy();
    expect(screen.getByText("30")).toBeTruthy();
  });

  it("sends the expiry flags BEFORE starting, because start does not set them", async () => {
    const { command, calls, onClose } = open();
    fireEvent.change(hours(), { target: { value: "1" } });
    fireEvent.change(minutes(), { target: { value: "30" } });
    fireEvent.click(screen.getByLabelText("Shutdown Grill"));

    await fireEvent.click(startButton());
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(calls).toEqual(["shutdown:true", "keep_warm:false", "start:5400"]);
    expect(command.timerStart).toHaveBeenCalledWith(90 * 60);
    expect(onClose).toHaveBeenCalled();
  });

  it("converts hours and minutes to seconds", async () => {
    const { command } = open();
    fireEvent.change(minutes(), { target: { value: "1" } });
    await fireEvent.click(startButton());
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(command.timerStart).toHaveBeenCalledWith(60);
  });

  it("sends keep_warm true when the box is checked", async () => {
    const { calls } = open();
    fireEvent.change(minutes(), { target: { value: "5" } });
    fireEvent.click(screen.getByLabelText("Start Keep Warm"));
    await fireEvent.click(startButton());
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(calls).toEqual(["shutdown:false", "keep_warm:true", "start:300"]);
  });

  it("seeds the checkboxes from the flags already set on the control process", () => {
    open({ shutdown: true, keepWarm: true });
    expect((screen.getByLabelText("Shutdown Grill") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Start Keep Warm") as HTMLInputElement).checked).toBe(true);
  });

  // A 0h0m submission must never reach the backend: /api/set/timer/start/0
  // parses as a float, so the backend would happily arm a 0-second timer, and
  // a missing/non-numeric segment silently becomes 60 seconds. Either way the
  // user gets a timer they did not ask for.
  it("refuses a 0h0m submission instead of sending it", async () => {
    const { command, calls, onClose } = open();
    await fireEvent.click(startButton());
    await Promise.resolve();

    expect(calls).toEqual([]);
    expect(command.timerStart).not.toHaveBeenCalled();
    expect(command.timerShutdown).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toMatch(/longer than zero|at least/i);
  });

  it("clears the zero-duration complaint once a real duration is chosen", async () => {
    open();
    await fireEvent.click(startButton());
    await Promise.resolve();
    expect(screen.queryByRole("alert")).toBeTruthy();

    fireEvent.change(minutes(), { target: { value: "1" } });

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("cancels without issuing any command", () => {
    const { calls, onClose } = open();
    fireEvent.change(minutes(), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(calls).toEqual([]);
    expect(onClose).toHaveBeenCalled();
  });
});
