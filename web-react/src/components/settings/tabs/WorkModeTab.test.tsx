import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, screen } from "@testing-library/react";
import { renderRoute } from "../../../test-utils";
import { WorkModeTab } from "./WorkModeTab";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
});

describe("WorkModeTab", () => {
  it("renders all three sections with loaded values", () => {
    const context = {
      settings: {
        cycle_data: {
          HoldCycleTime: 10,
          SmokeOnCycleTime: 5,
          SmokeOffCycleTime: 5,
          PMode: 1,
          u_min: 10.5,
          u_max: 95.5,
          LidOpenDetectEnabled: true,
          LidOpenThreshold: 35,
          LidOpenPauseTime: 75,
          FanPidEnabled: false,
        },
        smoke_plus: {
          enabled: true,
          min_temp: 160,
          max_temp: 280,
          on_time: 6,
          off_time: 6,
          duty_cycle: 55,
          fan_ramp: true,
        },
        keep_warm: {
          temp: 170,
          s_plus: false,
        },
      },
      mode: "Run",
    };

    renderRoute(<WorkModeTab />, context);

    // Check section titles exist
    expect(screen.getByText("Cycle Data")).toBeInTheDocument();
    expect(screen.getByText("Smoke Plus")).toBeInTheDocument();
    expect(screen.getByText("Keep Warm")).toBeInTheDocument();

    // Check Cycle Data fields
    expect(screen.getByDisplayValue("10")).toBeInTheDocument();
    expect(screen.getByDisplayValue("1")).toBeInTheDocument();
    expect(screen.getByDisplayValue("10.5")).toBeInTheDocument();
    expect(screen.getByDisplayValue("95.5")).toBeInTheDocument();
    expect(screen.getByDisplayValue("35")).toBeInTheDocument();
    expect(screen.getByDisplayValue("75")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lid Open Detect Enabled" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Fan PID Enabled" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );

    // Check Smoke Plus section
    expect(screen.getByRole("button", { name: "Enabled" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByDisplayValue("160")).toBeInTheDocument();
    expect(screen.getByDisplayValue("280")).toBeInTheDocument();
    expect(screen.getByDisplayValue("55")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Fan Ramp" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // Check Keep Warm section
    expect(screen.getByDisplayValue("170")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "S Plus" })).toHaveAttribute("aria-pressed", "false");
  });

  it("saves settings with settings_update flag when Save is clicked after editing cycle_data and smoke_plus", async () => {
    const context = {
      settings: {
        cycle_data: {
          HoldCycleTime: 10,
          SmokeOnCycleTime: 5,
          SmokeOffCycleTime: 5,
          PMode: 0,
          u_min: 0,
          u_max: 100,
          LidOpenDetectEnabled: false,
          LidOpenThreshold: 30,
          LidOpenPauseTime: 60,
          FanPidEnabled: false,
        },
        smoke_plus: {
          enabled: false,
          min_temp: 150,
          max_temp: 275,
          on_time: 5,
          off_time: 5,
          duty_cycle: 50,
          fan_ramp: false,
        },
        keep_warm: {
          temp: 150,
          s_plus: false,
        },
      },
      mode: "Stop",
    };

    renderRoute(<WorkModeTab />, context);

    // Edit a cycle_data field - find HoldCycleTime and change from 10 to 15
    const inputs = screen.getAllByDisplayValue("10");
    const holdCycleInput = inputs[0];
    fireEvent.change(holdCycleInput, { target: { value: "15" } });

    // Toggle a smoke_plus field (enabled from false to true)
    const enabledButton = screen.getByRole("button", { name: "Enabled" });
    fireEvent.click(enabledButton);

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for async save to complete
    await new Promise((resolve) => setTimeout(resolve, 50));

    // Assert spy was called with delta touching both cycle_data and smoke_plus with settings_update flag
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        cycle_data: expect.objectContaining({
          HoldCycleTime: 15,
        }),
        smoke_plus: expect.objectContaining({
          enabled: true,
        }),
      }),
      ["settings_update"],
    );
  });

  it("saves the exact full delta after editing every field across cycle_data, smoke_plus and keep_warm", async () => {
    // Every starting value below is distinct so getByDisplayValue can target
    // each field individually (defaults in WorkModeTab.tsx collide, e.g.
    // several fields default to 5 or 150).
    const context = {
      settings: {
        cycle_data: {
          HoldCycleTime: 11,
          SmokeOnCycleTime: 6,
          SmokeOffCycleTime: 7,
          PMode: 1,
          u_min: 2,
          u_max: 99,
          LidOpenDetectEnabled: false,
          LidOpenThreshold: 31,
          LidOpenPauseTime: 61,
          FanPidEnabled: false,
        },
        smoke_plus: {
          enabled: false,
          min_temp: 151,
          max_temp: 276,
          on_time: 8,
          off_time: 9,
          duty_cycle: 52,
          fan_ramp: false,
        },
        keep_warm: {
          temp: 152,
          s_plus: false,
        },
      },
      mode: "Stop",
    };

    renderRoute(<WorkModeTab />, context);

    // Every target value below is unique across the whole form (and outside
    // the range of any untouched default), so getByDisplayValue keeps
    // resolving to exactly one input regardless of edit order.
    const change = (from: string, to: string) =>
      fireEvent.change(screen.getByDisplayValue(from), { target: { value: to } });

    // cycle_data
    change("11", "21");
    change("6", "26");
    change("7", "27");
    change("1", "41");
    change("2", "42");
    change("99", "43");
    change("31", "44");
    change("61", "45");
    fireEvent.click(screen.getByRole("button", { name: "Lid Open Detect Enabled" }));
    fireEvent.click(screen.getByRole("button", { name: "Fan PID Enabled" }));

    // smoke_plus
    fireEvent.click(screen.getByRole("button", { name: "Enabled" }));
    change("151", "46");
    change("276", "47");
    change("8", "48");
    change("9", "49");
    change("52", "53");
    fireEvent.click(screen.getByRole("button", { name: "Fan Ramp" }));

    // keep_warm
    change("152", "54");
    fireEvent.click(screen.getByRole("button", { name: "S Plus" }));

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(saveMock).toHaveBeenCalledWith(
      {
        cycle_data: {
          HoldCycleTime: 21,
          SmokeOnCycleTime: 26,
          SmokeOffCycleTime: 27,
          PMode: 41,
          u_min: 42,
          u_max: 43,
          LidOpenDetectEnabled: true,
          LidOpenThreshold: 44,
          LidOpenPauseTime: 45,
          FanPidEnabled: true,
        },
        smoke_plus: {
          enabled: true,
          min_temp: 46,
          max_temp: 47,
          on_time: 48,
          off_time: 49,
          duty_cycle: 53,
          fan_ramp: true,
        },
        keep_warm: {
          temp: 54,
          s_plus: true,
        },
      },
      ["settings_update"],
    );
  });
});
