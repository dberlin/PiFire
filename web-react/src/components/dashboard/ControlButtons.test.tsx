import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { CommandClient, CommandResult } from "../../helpers/command";
import { FIXTURE_DASH } from "../../helpers/fixture";
import type { LiveState } from "../../helpers/types";
import { ControlButtons } from "./ControlButtons";

const OK: CommandResult = { ok: true, message: "" };
const at = (mode: string, over: Partial<LiveState> = {}): LiveState => ({
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

describe("ControlButtons", () => {
  it("Stopped mode renders Startup / Prime / Monitor", () => {
    render(
      <ControlButtons apiBase="" dash={at("Stop")} command={stubCommand()} disabled={false} />,
    );
    expect(screen.getByRole("button", { name: "Startup" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prime" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Monitor" })).toBeInTheDocument();
  });

  it("Monitor mode renders Startup / Stop", () => {
    render(
      <ControlButtons apiBase="" dash={at("Monitor")} command={stubCommand()} disabled={false} />,
    );
    expect(screen.getByRole("button", { name: "Startup" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prime" })).not.toBeInTheDocument();
  });

  it("Cooking mode renders Smoke / Hold / Smoke+ / Shutdown / Stop", () => {
    render(
      <ControlButtons apiBase="" dash={at("Hold")} command={stubCommand()} disabled={false} />,
    );
    expect(screen.getByRole("button", { name: "Smoke" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Hold" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Smoke+" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Shutdown" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
  });

  it('clicking Smoke calls command.setMode("smoke")', async () => {
    const user = userEvent.setup();
    const command = stubCommand();
    render(<ControlButtons apiBase="" dash={at("Hold")} command={command} disabled={false} />);
    await user.click(screen.getByRole("button", { name: "Smoke" }));
    expect(command.setMode).toHaveBeenCalledWith("smoke");
  });

  it("clicking Hold opens the setpoint modal", async () => {
    const user = userEvent.setup();
    render(
      <ControlButtons apiBase="" dash={at("Hold")} command={stubCommand()} disabled={false} />,
    );
    await user.click(screen.getByRole("button", { name: "Hold" }));
    expect(screen.getByText("Set Hold Temperature")).toBeInTheDocument();
  });

  it("clicking Stop opens the confirm modal", async () => {
    const user = userEvent.setup();
    render(
      <ControlButtons apiBase="" dash={at("Hold")} command={stubCommand()} disabled={false} />,
    );
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(screen.getByText("Stop the cook?")).toBeInTheDocument();
  });

  it("cancelling the Stop confirm modal closes it without running the command", async () => {
    const user = userEvent.setup();
    const command = stubCommand();
    render(<ControlButtons apiBase="" dash={at("Hold")} command={command} disabled={false} />);
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Stop the cook?")).not.toBeInTheDocument();
    expect(command.setMode).not.toHaveBeenCalled();
  });

  it('confirming the Stop confirm modal closes it and runs setMode("stop")', async () => {
    const user = userEvent.setup();
    const command = stubCommand();
    render(<ControlButtons apiBase="" dash={at("Hold")} command={command} disabled={false} />);
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(screen.queryByText("Stop the cook?")).not.toBeInTheDocument();
    expect(command.setMode).toHaveBeenCalledWith("stop");
  });

  it("cancelling the Hold setpoint modal closes it without calling hold", async () => {
    const user = userEvent.setup();
    const command = stubCommand();
    render(<ControlButtons apiBase="" dash={at("Hold")} command={command} disabled={false} />);
    await user.click(screen.getByRole("button", { name: "Hold" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByText("Set Hold Temperature")).not.toBeInTheDocument();
    expect(command.hold).not.toHaveBeenCalled();
  });

  it("submitting the Hold setpoint modal closes it and calls hold with the chosen temp", async () => {
    const user = userEvent.setup();
    const command = stubCommand();
    render(<ControlButtons apiBase="" dash={at("Hold")} command={command} disabled={false} />);
    await user.click(screen.getByRole("button", { name: "Hold" }));
    await user.click(screen.getByRole("button", { name: "Set Hold" }));
    expect(screen.queryByText("Set Hold Temperature")).not.toBeInTheDocument();
    expect(command.hold).toHaveBeenCalledWith(expect.any(Number));
  });

  it("fires a manual output toggle from the Manual button row", async () => {
    const command = stubCommand();
    render(
      <ControlButtons
        apiBase=""
        dash={at("Manual", { hasDcFan: false })}
        command={command}
        disabled={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Auger" }));
    await waitFor(() => expect(command.manualOutput).toHaveBeenCalledWith("auger"));
  });

  it("opens the PWM entry and submits a duty cycle", async () => {
    const command = stubCommand();
    render(
      <ControlButtons
        apiBase=""
        dash={at("Manual", { hasDcFan: true, manualPwm: 40 })}
        command={command}
        disabled={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Fan %" }));
    fireEvent.change(screen.getByRole("slider", { name: /fan duty/i }), {
      target: { value: "75" },
    });
    fireEvent.click(screen.getByRole("button", { name: /set/i }));
    await waitFor(() => expect(command.manualPwm).toHaveBeenCalledWith(75));
  });
});

// D1: `disabled` is set from controlAlive, which is derived from the errors
// blob -- a blob that never clears without a control.py restart and that a
// queue race can write on a perfectly healthy system. Withholding the exits in
// exactly that state is React's own invention; Flask leaves every button live
// and shows a dismissible banner (templates/base.html:117-125).
describe("ControlButtons never withholds the exits", () => {
  it("keeps Stop and Shutdown live while dimming the rest", () => {
    render(<ControlButtons apiBase="" dash={at("Hold")} command={stubCommand()} disabled={true} />);
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Shutdown" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Smoke" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Hold" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Smoke+" })).toBeDisabled();
  });

  it("keeps Stop live in Manual mode too", () => {
    render(
      <ControlButtons
        apiBase=""
        dash={at("Manual", { hasDcFan: false })}
        command={stubCommand()}
        disabled
      />,
    );
    expect(screen.getByRole("button", { name: "Stop" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Power" })).toBeDisabled();
  });

  it("still lets a disabled-but-safety button run its command", async () => {
    const command = stubCommand();
    render(<ControlButtons apiBase="" dash={at("Hold")} command={command} disabled={true} />);
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    fireEvent.click(await screen.findByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(command.setMode).toHaveBeenCalledWith("stop"));
  });
});

// I3: Flask's #startupModal has ONE body with two Jinja-selected variants, and
// the hold-temperature variant WINS when both are configured
// (_macro_control_panel.html:214,224 test the same condition twice). There is
// no path that renders both.
describe("ControlButtons startup confirmation", () => {
  afterEach(() => {
    rs.unstubAllGlobals();
  });

  const holdPrompt = (over: Partial<LiveState> = {}) =>
    at("Stop", {
      startToHoldPrompt: true,
      startupGotoMode: "Hold",
      startupGotoTemp: 225,
      ...over,
    });

  // The modal's submit carries Flask's label, "Startup" -- the same name as the
  // control button that opened it. Query it by its class rather than by role +
  // name, which matches both.
  const modalSubmit = (): Element => {
    const el = document.querySelector(".pf-modal-btn.accent");
    if (el === null) throw new Error("no modal submit button on screen");
    return el;
  };
  const openStartup = () => fireEvent.click(screen.getByRole("button", { name: "Startup" }));

  it("ignites immediately when neither gate is configured", async () => {
    const command = stubCommand();
    render(
      <ControlButtons
        apiBase=""
        dash={at("Stop", { startupCheck: false, startToHoldPrompt: false })}
        command={command}
        disabled={false}
      />,
    );
    openStartup();
    await waitFor(() => expect(command.setMode).toHaveBeenCalledWith("startup"));
  });

  it("asks Confirm Startup Grill? when only safety.startup_check is set", async () => {
    const command = stubCommand();
    render(
      <ControlButtons
        apiBase=""
        dash={at("Stop", { startupCheck: true, startToHoldPrompt: false })}
        command={command}
        disabled={false}
      />,
    );
    openStartup();
    expect(screen.getByText("Startup Check")).toBeInTheDocument();
    expect(screen.getByText("Confirm Startup Grill?")).toBeInTheDocument();
    expect(command.setMode).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /confirm/i }));
    await waitFor(() => expect(command.setMode).toHaveBeenCalledWith("startup"));
  });

  it("cancelling the safety check ignites nothing", () => {
    const command = stubCommand();
    render(
      <ControlButtons
        apiBase=""
        dash={at("Stop", { startupCheck: true })}
        command={command}
        disabled={false}
      />,
    );
    openStartup();
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(screen.queryByText("Startup Check")).not.toBeInTheDocument();
    expect(command.setMode).not.toHaveBeenCalled();
  });

  it("offers the hold-temperature prompt, seeded and bounded as Flask bounds it", () => {
    render(
      <ControlButtons apiBase="" dash={holdPrompt()} command={stubCommand()} disabled={false} />,
    );
    openStartup();
    expect(screen.getByText("Change Hold Temp?")).toBeInTheDocument();
    expect(modalSubmit()).toHaveTextContent("Startup");
    // Seeded from settings.startup.start_to_mode.primary_setpoint, which is
    // startupGotoTemp on the wire.
    expect(screen.getByText("225")).toBeInTheDocument();
    const slider = screen.getByRole("slider");
    // 125-600 step 5 for °F (_macro_control_panel.html:236) -- WIDER than the
    // Hold setpoint's 150-500, which is why the bounds are props.
    expect(slider).toHaveAttribute("min", "125");
    expect(slider).toHaveAttribute("max", "600");
    expect(slider).toHaveAttribute("step", "5");
  });

  it("uses Flask's Celsius bounds when the grill reports Celsius", () => {
    render(
      <ControlButtons
        apiBase=""
        dash={holdPrompt({ tempUnits: "C", startupGotoTemp: 110 })}
        command={stubCommand()}
        disabled={false}
      />,
    );
    openStartup();
    const slider = screen.getByRole("slider");
    expect(slider).toHaveAttribute("min", "50");
    expect(slider).toHaveAttribute("max", "260");
  });

  it("shows the hold variant, not the confirmation, when BOTH are configured", () => {
    render(
      <ControlButtons
        apiBase=""
        dash={holdPrompt({ startupCheck: true })}
        command={stubCommand()}
        disabled={false}
      />,
    );
    openStartup();
    expect(screen.getByText("Change Hold Temp?")).toBeInTheDocument();
    expect(screen.queryByText("Startup Check")).not.toBeInTheDocument();
    expect(screen.queryByText("Confirm Startup Grill?")).not.toBeInTheDocument();
  });

  it("writes the hold setting and AWAITS it before igniting", async () => {
    const order: string[] = [];
    const fetchMock: ReturnType<typeof rs.fn> = rs.fn(async () => {
      order.push("settings");
      return { ok: true, status: 200, json: async () => ({ result: "success" }) };
    });
    rs.stubGlobal("fetch", fetchMock);
    const command = stubCommand();
    command.setMode = rs.fn(async () => {
      order.push("mode");
      return OK;
    });

    render(<ControlButtons apiBase="" dash={holdPrompt()} command={command} disabled={false} />);
    openStartup();
    fireEvent.click(modalSubmit());

    await waitFor(() => expect(command.setMode).toHaveBeenCalledWith("startup"));
    // Flask fires both as unchained $.ajax calls (control_panel.js:421-427),
    // which is a real race: the grill can start before the target is recorded.
    expect(order).toEqual(["settings", "mode"]);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/settings_update");
    expect(JSON.parse(String(init.body))).toEqual({
      settings: {
        startup: {
          start_to_mode: {
            after_startup_mode: "Hold",
            start_to_hold_prompt: true,
            primary_setpoint: 225,
          },
        },
      },
      flags: ["settings_update"],
    });
  });

  it("does NOT ignite when the hold setting fails to save", async () => {
    rs.stubGlobal(
      "fetch",
      rs.fn(async () => ({
        ok: true,
        status: 200,
        json: async () => ({ result: "error", message: "Settings update failed." }),
      })),
    );
    const command = stubCommand();
    render(<ControlButtons apiBase="" dash={holdPrompt()} command={command} disabled={false} />);
    openStartup();
    fireEvent.click(modalSubmit());

    // Lighting a grill that just failed to record its hold target is the wrong
    // failure mode: it would run at whatever the previous cook left behind.
    await waitFor(() => expect(screen.getByText("Settings update failed.")).toBeInTheDocument());
    expect(command.setMode).not.toHaveBeenCalled();
    expect(screen.getByText("Change Hold Temp?")).toBeInTheDocument();
  });
});
