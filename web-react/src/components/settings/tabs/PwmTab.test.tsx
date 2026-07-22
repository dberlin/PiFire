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
    // 10 also appears as the default temp_range_list's 3rd boundary; the
    // scalar field renders earlier in the DOM than the table.
    expect(screen.getAllByDisplayValue("10")[0]).toBeInTheDocument();
    expect(screen.getByDisplayValue("90")).toBeInTheDocument();
    // 50 also appears as the default profiles' 3rd duty_cycle; the scalar
    // field renders earlier in the DOM than the table.
    expect(screen.getAllByDisplayValue("50")[0]).toBeInTheDocument();
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
    // update_time default (10) also collides with the default
    // temp_range_list's 3rd boundary (10); the scalar field precedes the
    // table in the DOM.
    expect(screen.getAllByDisplayValue("10")[0]).toBeInTheDocument();
    // min_duty_cycle default (20) also collides with the default profiles'
    // 1st duty_cycle (20).
    expect(screen.getAllByDisplayValue("20")[0]).toBeInTheDocument();
    // max_duty_cycle and frequency both default to 100, and the default
    // profiles' 5th duty_cycle is also 100.
    expect(screen.getAllByDisplayValue("100")).toHaveLength(3);

    // The table itself: default temp_range_list [3, 7, 10, 15] and default
    // duty_cycle profiles [20, 35, 50, 75, 100].
    expect(screen.getByLabelText("Duty cycle row 1")).toHaveValue(20);
    expect(screen.getByLabelText("Duty cycle row 5")).toHaveValue(100);
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

    // Edit Update Time (10 also collides with the default temp_range_list's
    // 3rd boundary; the scalar field precedes the table in the DOM).
    const updateTimeInput = screen.getAllByDisplayValue("10")[0];
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
          temp_range_list: [3, 7, 10, 15],
          profiles: [
            { duty_cycle: 20 },
            { duty_cycle: 35 },
            { duty_cycle: 50 },
            { duty_cycle: 75 },
            { duty_cycle: 100 },
          ],
        },
      },
      ["settings_update"],
    );
  });

  const dutyCycleFixture = () => ({
    settings: {
      pwm: {
        pwm_control: true,
        update_time: 10,
        min_duty_cycle: 20,
        max_duty_cycle: 100,
        frequency: 25000,
        temp_range_list: [3, 7, 10, 15],
        profiles: [
          { duty_cycle: 20 },
          { duty_cycle: 35 },
          { duty_cycle: 50 },
          { duty_cycle: 75 },
          { duty_cycle: 100 },
        ],
      },
    },
    mode: "Stop",
  });

  it("renders 5 duty-cycle profile rows with derived ΔT range labels", () => {
    renderRoute(<PwmTab />, dutyCycleFixture());

    expect(screen.getByText("ΔT range (°F)")).toBeInTheDocument();
    expect(screen.getByText("< 3°")).toBeInTheDocument();
    expect(screen.getByText("3 – 6°")).toBeInTheDocument();
    expect(screen.getByText("7 – 9°")).toBeInTheDocument();
    expect(screen.getByText("10 – 14°")).toBeInTheDocument();
    expect(screen.getByText("≥ 15°")).toBeInTheDocument();

    expect(screen.getByLabelText("Duty cycle row 1")).toHaveValue(20);
    expect(screen.getByLabelText("Duty cycle row 2")).toHaveValue(35);
    expect(screen.getByLabelText("Duty cycle row 3")).toHaveValue(50);
    expect(screen.getByLabelText("Duty cycle row 4")).toHaveValue(75);
    expect(screen.getByLabelText("Duty cycle row 5")).toHaveValue(100);
  });

  it("edits a duty_cycle cell and saves the full profiles array with temp_range_list unchanged", async () => {
    renderRoute(<PwmTab />, dutyCycleFixture());

    const input = screen.getByLabelText("Duty cycle row 3");
    fireEvent.change(input, { target: { value: "60" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        pwm: expect.objectContaining({
          temp_range_list: [3, 7, 10, 15],
          profiles: [
            { duty_cycle: 20 },
            { duty_cycle: 35 },
            { duty_cycle: 60 },
            { duty_cycle: 75 },
            { duty_cycle: 100 },
          ],
        }),
      }),
      ["settings_update"],
    );
  });

  it("clamps a duty_cycle edit to the tab's current (un-saved) max_duty_cycle value", () => {
    renderRoute(<PwmTab />, dutyCycleFixture());

    // Lower Max Duty Cycle from 100 to 80 without saving. 100 also matches
    // the table's 5th duty_cycle row; the scalar field precedes the table
    // in the DOM.
    const maxDutyInput = screen.getAllByDisplayValue("100")[0];
    fireEvent.change(maxDutyInput, { target: { value: "80" } });

    // Now push a duty_cycle cell above the new (local, un-saved) max.
    const cell = screen.getByLabelText("Duty cycle row 1");
    fireEvent.change(cell, { target: { value: "999" } });

    expect(screen.getByLabelText("Duty cycle row 1")).toHaveValue(80);
  });
});
