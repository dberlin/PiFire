// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { StartupTab } from "./StartupTab";
import { renderRoute } from "../../test-utils";

const saveMock = vi.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
vi.mock("../useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
});

describe("StartupTab", () => {
  it("renders all sections with loaded values", () => {
    const context = {
      settings: {
        shutdown: {
          shutdown_duration: 90,
          auto_power_off: true,
        },
        startup: {
          duration: 60,
          startup_exit_temp: 150,
          prime_on_startup: 50,
          pwm_duty_cycle: 75,
          smartstart: {
            enabled: true,
            exit_temp: 160,
          },
          start_to_mode: {
            after_startup_mode: "Hold",
            primary_setpoint: 250,
            start_to_hold_prompt: false,
          },
        },
        pwm: {
          min_duty_cycle: 20,
          max_duty_cycle: 100,
        },
      },
      mode: "Stop",
    };

    renderRoute(<StartupTab />, context);

    // Check section titles exist
    expect(screen.getByText("Shutdown")).toBeInTheDocument();
    expect(screen.getByText("Startup")).toBeInTheDocument();
    expect(screen.getByText("SmartStart")).toBeInTheDocument();
    expect(screen.getByText("Start to Mode")).toBeInTheDocument();

    // Check Shutdown fields
    expect(screen.getByDisplayValue("90")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Auto Power Off" })).toHaveAttribute("aria-pressed", "true");

    // Check Startup fields
    expect(screen.getByDisplayValue("60")).toBeInTheDocument();
    expect(screen.getByDisplayValue("150")).toBeInTheDocument();
    expect(screen.getByDisplayValue("50")).toBeInTheDocument();
    expect(screen.getByDisplayValue("75")).toBeInTheDocument();

    // Check SmartStart fields
    expect(screen.getByRole("button", { name: "Enabled" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByDisplayValue("160")).toBeInTheDocument();

    // Check Start to Mode fields
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("Hold");
    expect(screen.getByDisplayValue("250")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start to Hold Prompt" })).toHaveAttribute("aria-pressed", "false");
  });

  it("clamps prime_on_startup to 0 when set to out-of-range value 999", async () => {
    const context = {
      settings: {
        shutdown: {
          shutdown_duration: 60,
          auto_power_off: false,
        },
        startup: {
          duration: 60,
          startup_exit_temp: 150,
          prime_on_startup: 0,
          pwm_duty_cycle: 50,
          smartstart: {
            enabled: false,
            exit_temp: 150,
          },
          start_to_mode: {
            after_startup_mode: "Smoke",
            primary_setpoint: 225,
            start_to_hold_prompt: false,
          },
        },
        pwm: {
          min_duty_cycle: 20,
          max_duty_cycle: 100,
        },
      },
      mode: "Stop",
    };

    renderRoute(<StartupTab />, context);

    // Find and change prime_on_startup to 999
    const primeInputs = screen.getAllByDisplayValue("0");
    // The first 0 should be prime_on_startup in the Startup section
    const primeInput = primeInputs[0];
    fireEvent.change(primeInput, { target: { value: "999" } });

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for async save to complete
    await new Promise(resolve => setTimeout(resolve, 50));

    // Assert that the saved delta has prime_on_startup clamped to 0
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        startup: expect.objectContaining({
          prime_on_startup: 0,
        }),
      }),
      ["settings_update"]
    );
  });

  it("changes after_startup_mode Select and saves with settings_update flag", async () => {
    const context = {
      settings: {
        shutdown: {
          shutdown_duration: 60,
          auto_power_off: false,
        },
        startup: {
          duration: 60,
          startup_exit_temp: 150,
          prime_on_startup: 0,
          pwm_duty_cycle: 50,
          smartstart: {
            enabled: false,
            exit_temp: 150,
          },
          start_to_mode: {
            after_startup_mode: "Smoke",
            primary_setpoint: 225,
            start_to_hold_prompt: false,
          },
        },
        pwm: {
          min_duty_cycle: 20,
          max_duty_cycle: 100,
        },
      },
      mode: "Stop",
    };

    renderRoute(<StartupTab />, context);

    // Change after_startup_mode from Smoke to Hold
    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "Hold" } });

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for async save to complete
    await new Promise(resolve => setTimeout(resolve, 50));

    // Assert that the saved delta includes the mode change and has settings_update flag
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        startup: expect.objectContaining({
          start_to_mode: expect.objectContaining({
            after_startup_mode: "Hold",
          }),
        }),
      }),
      ["settings_update"]
    );
  });

  it("clamps pwm_duty_cycle to max_duty_cycle when value exceeds bound", async () => {
    const context = {
      settings: {
        shutdown: {
          shutdown_duration: 60,
          auto_power_off: false,
        },
        startup: {
          duration: 60,
          startup_exit_temp: 150,
          prime_on_startup: 0,
          pwm_duty_cycle: 50,
          smartstart: {
            enabled: false,
            exit_temp: 150,
          },
          start_to_mode: {
            after_startup_mode: "Smoke",
            primary_setpoint: 225,
            start_to_hold_prompt: false,
          },
        },
        pwm: {
          min_duty_cycle: 20,
          max_duty_cycle: 100,
        },
      },
      mode: "Stop",
    };

    renderRoute(<StartupTab />, context);

    // Find and change pwm_duty_cycle to 150 (exceeds max of 100)
    const inputs = screen.getAllByDisplayValue("50");
    const pwmInput = inputs[0]; // pwm_duty_cycle is 50 in Startup section
    fireEvent.change(pwmInput, { target: { value: "150" } });

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for async save to complete
    await new Promise(resolve => setTimeout(resolve, 50));

    // Assert that the saved delta has pwm_duty_cycle clamped to max 100
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        startup: expect.objectContaining({
          pwm_duty_cycle: 100,
        }),
      }),
      ["settings_update"]
    );
  });
});
