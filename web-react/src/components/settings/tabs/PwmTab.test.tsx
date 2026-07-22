import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { renderRoute } from "../../../test-utils";
import { PwmTab } from "./PwmTab";

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

afterEach(cleanup);

describe("PwmTab", () => {
  it("renders pwm fields with loaded values", () => {
    const context = {
      settings: {
        pwm: {
          pwm_control: true,
          update_time: 5,
          min_duty_cycle: 10,
          max_duty_cycle: 90,
          frequency: 50,
        },
      },
      mode: "Stop",
    };

    renderRoute(<PwmTab />, context);

    expect(screen.getByRole("button", { name: "PWM Control" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByDisplayValue("5")).toBeInTheDocument();
    expect(screen.getByDisplayValue("10")).toBeInTheDocument();
    expect(screen.getByDisplayValue("90")).toBeInTheDocument();
    expect(screen.getByDisplayValue("50")).toBeInTheDocument();
  });

  it("falls back to defaults when settings.pwm is absent", () => {
    const context = {
      settings: {},
      mode: "Stop",
    };

    renderRoute(<PwmTab />, context);

    expect(screen.getByRole("button", { name: "PWM Control" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByDisplayValue("10")).toBeInTheDocument(); // update_time default
    expect(screen.getByDisplayValue("20")).toBeInTheDocument(); // min_duty_cycle default
    // max_duty_cycle and frequency both default to 100
    expect(screen.getAllByDisplayValue("100")).toHaveLength(2);
  });

  it("edits a number field and a toggle, then saves with the settings_update flag", async () => {
    const context = {
      settings: {
        pwm: {
          pwm_control: false,
          update_time: 10,
          min_duty_cycle: 20,
          max_duty_cycle: 100,
          frequency: 100,
        },
      },
      mode: "Stop",
    };

    renderRoute(<PwmTab />, context);

    // Toggle PWM Control on
    fireEvent.click(screen.getByRole("button", { name: "PWM Control" }));

    // Edit Update Time
    const updateTimeInput = screen.getByDisplayValue("10");
    fireEvent.change(updateTimeInput, { target: { value: "15" } });

    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      {
        pwm: {
          pwm_control: true,
          update_time: 15,
          min_duty_cycle: 20,
          max_duty_cycle: 100,
          frequency: 100,
        },
      },
      ["settings_update"],
    );
  });
});
