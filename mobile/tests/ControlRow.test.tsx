import type { DashSocketPayload } from "@pifire/core/contracts/core";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { cleanup, fireEvent, render, waitFor } from "@testing-library/react-native";
import { Alert } from "react-native";

import { ControlRow } from "../src/components/ControlRow";

// FIXTURE_DASH is a real captured socket_dash_data payload (@pifire/core/fixture).
// dashInMode overrides only currentMode, so every test here exercises the
// real payload shape rather than a hand-invented dash object.
function dashInMode(mode: DashSocketPayload["currentMode"]): DashSocketPayload {
  return { ...FIXTURE_DASH, currentMode: mode };
}

function makeCommand() {
  return {
    setMode: jest.fn().mockResolvedValue({ ok: true, message: "" }),
    hold: jest.fn().mockResolvedValue({ ok: true, message: "" }),
  };
}

// Alert is a native module -- there is nothing in the render tree to query --
// so a confirmation is driven by grabbing the buttons array off the last
// Alert.alert() call and invoking the wanted button's onPress directly. Same
// approach useLive.test.tsx uses for AppState.addEventListener.
function pressAlertButton(buttonText: string) {
  const alert = Alert.alert as jest.Mock;
  const call = alert.mock.calls[alert.mock.calls.length - 1] as
    | [string, string | undefined, { text?: string; onPress?: () => void }[] | undefined]
    | undefined;
  const button = call?.[2]?.find((b) => b.text === buttonText);
  button?.onPress?.();
}

describe("ControlRow", () => {
  beforeEach(() => {
    jest.spyOn(Alert, "alert").mockImplementation(() => undefined);
  });

  afterEach(() => {
    cleanup();
    jest.restoreAllMocks();
  });

  // Startup cannot exercise "a press sends the mode the button names"
  // directly: FIXTURE_DASH.startupCheck is true, so buttonsForMode gives
  // Startup a { type: "startup" } action (a confirm gate), not a bare
  // command -- pressing it does NOT call setMode until the check is
  // confirmed (see the second test below). "Manual" is a real,
  // always-plain-command button in the same row, used here instead to
  // exercise that claim.
  it("sends the mode a pressed button names", async () => {
    const command = makeCommand();
    const { getByText } = await render(
      <ControlRow dash={dashInMode("Stop")} command={command as never} disabled={false} />,
    );
    fireEvent.press(getByText("Manual"));
    expect(command.setMode).toHaveBeenCalledWith("manual");
  });

  it("does not dispatch Startup until the startup check is confirmed", async () => {
    const command = makeCommand();
    const { getByText } = await render(
      <ControlRow dash={dashInMode("Stop")} command={command as never} disabled={false} />,
    );
    fireEvent.press(getByText("Startup"));
    expect(command.setMode).not.toHaveBeenCalled();
    pressAlertButton("Startup");
    await waitFor(() => expect(command.setMode).toHaveBeenCalledWith("startup"));
  });

  it("confirms before dispatching Stop and Shutdown", async () => {
    const command = makeCommand();
    const { getByText } = await render(
      <ControlRow dash={dashInMode("Hold")} command={command as never} disabled={false} />,
    );
    fireEvent.press(getByText("Shutdown"));
    expect(command.setMode).not.toHaveBeenCalled();
    pressAlertButton("Confirm");
    await waitFor(() => expect(command.setMode).toHaveBeenCalledWith("shutdown"));

    fireEvent.press(getByText("Stop"));
    pressAlertButton("Confirm");
    await waitFor(() => expect(command.setMode).toHaveBeenCalledWith("stop"));
  });

  it("disables non-safety buttons while not live, but keeps Stop and Shutdown available", async () => {
    const command = makeCommand();
    const { getByText } = await render(
      <ControlRow dash={dashInMode("Hold")} command={command as never} disabled={true} />,
    );

    // Smoke is a plain command button in Hold mode -- pressing it while the
    // row is disabled (phase !== "live") must not dispatch anything.
    fireEvent.press(getByText("Smoke"));
    expect(command.setMode).not.toHaveBeenCalled();

    // Shutdown is one of the two ways out of a running cook, so it stays
    // available (and still confirms) even while the row is disabled.
    fireEvent.press(getByText("Shutdown"));
    pressAlertButton("Confirm");
    await waitFor(() => expect(command.setMode).toHaveBeenCalledWith("shutdown"));
  });

  // If a dispatch does NOT land, the user must be told, not left to assume
  // it worked -- otherwise they could walk away believing a shutdown
  // succeeded when it didn't. fire()'s `!res.ok` branch (a rejected command
  // the grill answered but refused) and its `catch` branch (a thrown fetch,
  // e.g. the network drops mid-request) are two different code paths --
  // both covered here.
  it("surfaces a rejected command via Alert", async () => {
    const command = {
      setMode: jest.fn().mockResolvedValue({ ok: false, message: "grill refused the command" }),
      hold: jest.fn(),
    };
    const { getByText } = await render(
      <ControlRow dash={dashInMode("Stop")} command={command as never} disabled={false} />,
    );
    fireEvent.press(getByText("Manual"));
    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith("Command failed", "grill refused the command"),
    );
  });

  it("surfaces a thrown dispatch (e.g. a failed fetch) via Alert", async () => {
    const command = {
      setMode: jest.fn().mockRejectedValue(new Error("network request failed")),
      hold: jest.fn(),
    };
    const { getByText } = await render(
      <ControlRow dash={dashInMode("Stop")} command={command as never} disabled={false} />,
    );
    fireEvent.press(getByText("Manual"));
    await waitFor(() =>
      expect(Alert.alert).toHaveBeenCalledWith("Command failed", "network request failed"),
    );
  });
});
