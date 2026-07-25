import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, screen } from "@testing-library/react";
import { renderRoute } from "../../../test-utils";
import { StartupTab } from "./StartupTab";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    status: { kind: "idle" } as const,
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
});

// NumberField wraps its input in a <label> whose text also carries the suffix,
// so getByLabelText("Prime on Startup") does not match. Reach the input through
// the label span instead.
function inputFor(label: string): HTMLInputElement {
  const input = screen.getByText(label).closest("label")?.querySelector("input");
  if (!input) throw new Error(`no input for field "${label}"`);
  return input;
}

describe("StartupTab", () => {
  it("renders all sections with loaded values", () => {
    const context = {
      settings: {
        platform: { dc_fan: true },
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
            // Distinct from the other scalar values asserted below (90, 60,
            // 150, 50, 75, 250) so the RangeProfileTable's boundary inputs
            // don't collide with the singular getByDisplayValue queries.
            temp_range_list: [65, 82, 97],
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
    expect(screen.getByRole("button", { name: "Auto Power Off" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

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
    expect(screen.getByRole("button", { name: "Start to Hold Prompt" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("clamps prime_on_startup to 0 when set to out-of-range value 999", async () => {
    const context = {
      settings: {
        platform: { dc_fan: true },
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

    // prime_on_startup is 0, i.e. DISABLED, so its number field is hidden
    // behind the "Always Prime on Startup" switch (Flask index.html:843-856).
    // Turn it on to reach the field, then push it out of range.
    fireEvent.click(screen.getByRole("button", { name: "Always Prime on Startup" }));
    const primeInput = inputFor("Prime on Startup");
    fireEvent.change(primeInput, { target: { value: "999" } });

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for async save to complete
    await new Promise((resolve) => setTimeout(resolve, 50));

    // Assert that the saved delta has prime_on_startup clamped to 0
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        startup: expect.objectContaining({
          prime_on_startup: 0,
        }),
      }),
      ["settings_update"],
    );
  });

  it("changes after_startup_mode Select and saves with settings_update flag", async () => {
    const context = {
      settings: {
        platform: { dc_fan: true },
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
    await new Promise((resolve) => setTimeout(resolve, 50));

    // Assert that the saved delta includes the mode change and has settings_update flag
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        startup: expect.objectContaining({
          start_to_mode: expect.objectContaining({
            after_startup_mode: "Hold",
          }),
        }),
      }),
      ["settings_update"],
    );
  });

  it("clamps pwm_duty_cycle to max_duty_cycle when value exceeds bound", async () => {
    const context = {
      settings: {
        platform: { dc_fan: true },
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
    await new Promise((resolve) => setTimeout(resolve, 50));

    // Assert that the saved delta has pwm_duty_cycle clamped to max 100
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        startup: expect.objectContaining({
          pwm_duty_cycle: 100,
        }),
      }),
      ["settings_update"],
    );
  });

  const smartstartFixture = () => ({
    settings: {
      platform: { dc_fan: true },
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
          enabled: true,
          exit_temp: 150,
          temp_range_list: [60, 80, 90],
          profiles: [
            { startuptime: 360, augerontime: 15, p_mode: 0 },
            { startuptime: 360, augerontime: 15, p_mode: 1 },
            { startuptime: 240, augerontime: 15, p_mode: 3 },
            { startuptime: 240, augerontime: 15, p_mode: 5 },
          ],
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
  });

  it("renders 4 SmartStart profile rows with derived range labels", () => {
    renderRoute(<StartupTab />, smartstartFixture());

    expect(screen.getByText("< 60°")).toBeInTheDocument();
    expect(screen.getByText("60 – 79°")).toBeInTheDocument();
    expect(screen.getByText("80 – 89°")).toBeInTheDocument();
    expect(screen.getByText("≥ 90°")).toBeInTheDocument();

    expect(screen.getByLabelText("Startup time row 1")).toHaveValue(360);
    expect(screen.getByLabelText("Auger on row 1")).toHaveValue(15);
    expect(screen.getByLabelText("P-Mode row 1")).toHaveValue(0);
    expect(screen.getByLabelText("P-Mode row 2")).toHaveValue(1);
    expect(screen.getByLabelText("P-Mode row 3")).toHaveValue(3);
    expect(screen.getByLabelText("P-Mode row 4")).toHaveValue(5);
  });

  it("edits a SmartStart profile cell and saves the full profiles array with temp_range_list unchanged", async () => {
    renderRoute(<StartupTab />, smartstartFixture());

    const input = screen.getByLabelText("Startup time row 2");
    fireEvent.change(input, { target: { value: "300" } });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        startup: expect.objectContaining({
          smartstart: expect.objectContaining({
            temp_range_list: [60, 80, 90],
            profiles: [
              { startuptime: 360, augerontime: 15, p_mode: 0 },
              { startuptime: 300, augerontime: 15, p_mode: 1 },
              { startuptime: 240, augerontime: 15, p_mode: 3 },
              { startuptime: 240, augerontime: 15, p_mode: 5 },
            ],
          }),
        }),
      }),
      ["settings_update"],
    );
  });

  it("adds a SmartStart range and saves with both arrays grown by one", async () => {
    renderRoute(<StartupTab />, smartstartFixture());

    fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(saveMock).toHaveBeenCalledTimes(1);
    const [delta, flags] = saveMock.mock.calls[0];
    expect(delta.startup.smartstart.temp_range_list).toEqual([60, 80, 90, 100]);
    expect(delta.startup.smartstart.profiles).toHaveLength(5);
    expect(delta.startup.smartstart.profiles[4]).toEqual({
      startuptime: 240,
      augerontime: 15,
      p_mode: 5,
    });
    expect(flags).toEqual(["settings_update"]);
  });
  // Flask hides the DC-fan duty-cycle NOTE and its input on an AC-fan build
  // (settings/index.html:857-868).
  it("hides PWM Duty Cycle when platform.dc_fan is false", () => {
    renderRoute(<StartupTab />, {
      settings: { platform: { dc_fan: false } },
      mode: "Stop",
    });

    expect(screen.queryByText("PWM Duty Cycle")).toBeNull();
    // The rest of the Startup section is untouched. (The prime AMOUNT field is
    // behind its own 0-means-disabled switch, so assert on the switch.)
    expect(screen.getByText("Duration")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Always Prime on Startup" })).toBeInTheDocument();
  });

  // The clamp at onSave stays unconditional: the value is still in the delta on
  // an AC build, so it still has to satisfy _check_startup_pwm_duty_cycle.
  it("still clamps and writes pwm_duty_cycle on an AC-fan build", async () => {
    renderRoute(<StartupTab />, {
      settings: {
        platform: { dc_fan: false },
        startup: { pwm_duty_cycle: 500 },
        pwm: { min_duty_cycle: 20, max_duty_cycle: 80 },
      },
      mode: "Stop",
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    const [delta] = saveMock.mock.calls[0];
    expect(delta.startup.pwm_duty_cycle).toBe(80);
  });
  // Flask's Startup pane is three conditionals plus one dynamic bound:
  // index.html:812-856 with settings.js:943-980. React rendered all of them as
  // always-visible fields, which made "0 = disabled" undiscoverable.
  describe("conditional structure (I15)", () => {
    const fixture = (startup: object = {}, extra: object = {}) => ({
      settings: {
        platform: { dc_fan: true },
        startup: {
          duration: 60,
          startup_exit_temp: 150,
          prime_on_startup: 0,
          pwm_duty_cycle: 50,
          smartstart: { enabled: false, exit_temp: 160 },
          start_to_mode: {
            after_startup_mode: "Smoke",
            primary_setpoint: 225,
            start_to_hold_prompt: false,
          },
          ...startup,
        },
        pwm: { min_duty_cycle: 20, max_duty_cycle: 100 },
        ...extra,
      },
      mode: "Stop",
    });

    it("hides the Hold block unless after_startup_mode is Hold, and reveals it on switch", () => {
      renderRoute(<StartupTab />, fixture());

      expect(screen.queryByText("Primary Setpoint")).toBeNull();
      expect(screen.queryByRole("button", { name: "Start to Hold Prompt" })).toBeNull();

      fireEvent.change(screen.getByRole("combobox"), { target: { value: "Hold" } });
      expect(screen.getByText("Primary Setpoint")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Start to Hold Prompt" })).toBeInTheDocument();

      fireEvent.change(screen.getByRole("combobox"), { target: { value: "Smoke" } });
      expect(screen.queryByText("Primary Setpoint")).toBeNull();
    });

    it("hiding the Hold block does not clear primary_setpoint", () => {
      renderRoute(
        <StartupTab />,
        fixture({
          start_to_mode: {
            after_startup_mode: "Hold",
            primary_setpoint: 275,
            start_to_hold_prompt: false,
          },
        }),
      );

      expect(inputFor("Primary Setpoint")).toHaveValue(275);
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "Smoke" } });
      fireEvent.change(screen.getByRole("combobox"), { target: { value: "Hold" } });
      expect(inputFor("Primary Setpoint")).toHaveValue(275);
    });

    it("still writes primary_setpoint into the delta while Hold is not selected", async () => {
      renderRoute(<StartupTab />, fixture());

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      const [delta] = saveMock.mock.calls[0];
      expect(delta.startup.start_to_mode.primary_setpoint).toBe(225);
    });

    it("renders the exit-temp switch OFF with no field when startup_exit_temp is 0, seeding 140 on enable", () => {
      renderRoute(<StartupTab />, fixture({ startup_exit_temp: 0 }));

      const toggle = screen.getByRole("button", { name: "Exit Startup @ Temperature" });
      expect(toggle).toHaveAttribute("aria-pressed", "false");
      expect(screen.queryByText("Startup Exit Temp")).toBeNull();

      fireEvent.click(toggle);
      expect(inputFor("Startup Exit Temp")).toHaveValue(140);
    });

    it("renders the exit-temp switch ON with its value, and writes 0 when switched off", async () => {
      renderRoute(<StartupTab />, fixture({ startup_exit_temp: 200 }));

      const toggle = screen.getByRole("button", { name: "Exit Startup @ Temperature" });
      expect(toggle).toHaveAttribute("aria-pressed", "true");
      expect(inputFor("Startup Exit Temp")).toHaveValue(200);

      fireEvent.click(toggle);
      expect(screen.queryByText("Startup Exit Temp")).toBeNull();

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));
      const [delta] = saveMock.mock.calls[0];
      expect(delta.startup.startup_exit_temp).toBe(0);
    });

    // Flask's settings.js:952-956 captures the LOADED value first and only
    // substitutes 140 when it was 0.
    it("restores the loaded exit temp (not 140) when toggled off and back on", () => {
      renderRoute(<StartupTab />, fixture({ startup_exit_temp: 200 }));

      const toggle = screen.getByRole("button", { name: "Exit Startup @ Temperature" });
      fireEvent.click(toggle);
      fireEvent.click(toggle);
      expect(inputFor("Startup Exit Temp")).toHaveValue(200);
    });

    it("renders the prime switch OFF with no field when prime_on_startup is 0, seeding 10 on enable", () => {
      renderRoute(<StartupTab />, fixture({ prime_on_startup: 0 }));

      const toggle = screen.getByRole("button", { name: "Always Prime on Startup" });
      expect(toggle).toHaveAttribute("aria-pressed", "false");
      expect(screen.queryByText("Prime on Startup")).toBeNull();

      fireEvent.click(toggle);
      expect(inputFor("Prime on Startup")).toHaveValue(10);
    });

    it("renders the prime switch ON with its value, and writes 0 when switched off", async () => {
      renderRoute(<StartupTab />, fixture({ prime_on_startup: 40 }));

      const toggle = screen.getByRole("button", { name: "Always Prime on Startup" });
      expect(toggle).toHaveAttribute("aria-pressed", "true");
      expect(inputFor("Prime on Startup")).toHaveValue(40);

      fireEvent.click(toggle);
      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));
      const [delta] = saveMock.mock.calls[0];
      expect(delta.startup.prime_on_startup).toBe(0);
    });

    it("restores the loaded prime amount (not 10) when toggled off and back on", () => {
      renderRoute(<StartupTab />, fixture({ prime_on_startup: 40 }));

      const toggle = screen.getByRole("button", { name: "Always Prime on Startup" });
      fireEvent.click(toggle);
      fireEvent.click(toggle);
      expect(inputFor("Prime on Startup")).toHaveValue(40);
    });

    // index.html:819 — min="{{ safety.maxstartuptemp }}" max="{{ safety.maxtemp }}".
    it("bounds the setpoint dynamically by safety.maxstartuptemp / safety.maxtemp", () => {
      renderRoute(
        <StartupTab />,
        fixture(
          {
            start_to_mode: {
              after_startup_mode: "Hold",
              primary_setpoint: 250,
              start_to_hold_prompt: false,
            },
          },
          { safety: { maxstartuptemp: 100, maxtemp: 550 } },
        ),
      );

      const setpoint = inputFor("Primary Setpoint");
      expect(setpoint).toHaveAttribute("min", "100");
      expect(setpoint).toHaveAttribute("max", "550");

      // Task 1's blur clamp, seen through a real call site: typing is not
      // clamped, finishing the edit is.
      fireEvent.change(setpoint, { target: { value: "700" } });
      expect(inputFor("Primary Setpoint")).toHaveValue(700);
      fireEvent.blur(setpoint);
      expect(inputFor("Primary Setpoint")).toHaveValue(550);
    });

    it("caps Prime on Startup at the schema-backed max of 200", () => {
      renderRoute(<StartupTab />, fixture({ prime_on_startup: 40 }));
      expect(inputFor("Prime on Startup")).toHaveAttribute("max", "200");
    });

    // Hiding a control must never drop its key, or the next save silently
    // reverts a hidden field.
    it("writes all thirteen delta paths regardless of which controls are hidden", async () => {
      renderRoute(<StartupTab />, fixture({ startup_exit_temp: 0, prime_on_startup: 0 }));

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      const [delta] = saveMock.mock.calls[0];
      expect(Object.keys(delta.shutdown).sort()).toEqual(["auto_power_off", "shutdown_duration"]);
      expect(Object.keys(delta.startup).sort()).toEqual([
        "duration",
        "prime_on_startup",
        "pwm_duty_cycle",
        "smartstart",
        "start_to_mode",
        "startup_exit_temp",
      ]);
      expect(Object.keys(delta.startup.smartstart).sort()).toEqual([
        "enabled",
        "exit_temp",
        "profiles",
        "temp_range_list",
      ]);
      expect(Object.keys(delta.startup.start_to_mode).sort()).toEqual([
        "after_startup_mode",
        "primary_setpoint",
        "start_to_hold_prompt",
      ]);
    });
  });
});
