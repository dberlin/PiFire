import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { PwmTab } from "../../../../../src/components/settings/tabs/PwmTab";
import type { SaveStatus } from "../../../../../src/helpers/settings/useSaveSettings";
import { renderRoute } from "../../../test-utils";

const saveMock = rs.fn().mockResolvedValue(true);
// Read at hook-call time (i.e. during render), so a test can choose the status
// the tab is handed. The applySettings -> SaveStatus mapping itself is pinned
// in useSaveSettings.test.tsx; what matters here is that the tab forwards it.
let mockStatus: SaveStatus = { kind: "idle" };

// Mock the useSaveSettings module
rs.mock("../../../../../src/helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    status: mockStatus,
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
  mockStatus = { kind: "idle" };
});

afterEach(cleanup);

describe("PwmTab", () => {
  it("renders pwm fields with loaded values", () => {
    const context = {
      settings: {
        platform: { dc_fan: true },
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
      settings: { platform: { dc_fan: true } },
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
    // max_duty_cycle defaults to 100, also the default profiles' 5th
    // duty_cycle.
    expect(screen.getAllByDisplayValue("100")).toHaveLength(2);
    // frequency defaults to 25000, not 100.
    expect(screen.getByDisplayValue("25000")).toBeInTheDocument();

    // The table itself: default temp_range_list [3, 7, 10, 15] and default
    // duty_cycle profiles [20, 35, 50, 75, 100].
    expect(screen.getByLabelText("Duty cycle row 1")).toHaveValue(20);
    expect(screen.getByLabelText("Duty cycle row 5")).toHaveValue(100);
  });

  it("edits a number field and a toggle, then saves with the settings_update flag", async () => {
    const context = {
      settings: {
        platform: { dc_fan: true },
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
        // The delta now also carries the cross-section clamp of
        // startup.pwm_duty_cycle (routes.py:495). This fixture has no
        // `startup`, so the 100 default is used, already inside [20, 100].
        startup: { pwm_duty_cycle: 100 },
      },
      ["settings_update"],
    );
  });

  const dutyCycleFixture = () => ({
    settings: {
      platform: { dc_fan: true },
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

  // PwmTab is the witness for the rejection path because it is the tab with a
  // live, reachable backend rejection: raising min_duty_cycle above an existing
  // profile duty cycle trips PwmSettings._check_profiles, and the tab neither
  // clamps nor guards. Before SaveBar the user got silence.
  it("surfaces a rejected save inline and withholds the success marker", () => {
    mockStatus = {
      kind: "error",
      message: "pwm: Value error, profiles[0].duty_cycle must be within [min, max]",
    };

    renderRoute(<PwmTab />, dutyCycleFixture());

    expect(screen.getByRole("alert")).toHaveTextContent(
      "pwm: Value error, profiles[0].duty_cycle must be within [min, max]",
    );
    expect(screen.queryByText("Saved ✓")).toBeNull();
    // The refused values stay on screen so the user can fix them and retry.
    expect(screen.getByLabelText("Duty cycle row 1")).toHaveValue(20);
    expect(screen.getByRole("button", { name: "Save" })).not.toBeDisabled();
  });

  // Flask hides the entire PWM pane on an AC-fan build
  // (settings/index.html:581-768). The React route stays registered so a
  // bookmarked /settings/pwm still resolves — the tab explains why it is inert
  // rather than 404ing.
  it("renders an explanatory notice and no controls when platform.dc_fan is false", () => {
    renderRoute(<PwmTab />, {
      settings: { platform: { dc_fan: false }, pwm: { min_duty_cycle: 20, max_duty_cycle: 100 } },
      mode: "Stop",
    });

    expect(screen.getByText(/DC fan/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByRole("button", { name: "PWM Control" })).toBeNull();
    expect(screen.queryByLabelText("Duty cycle row 1")).toBeNull();
    expect(screen.queryByDisplayValue("20")).toBeNull();
  });

  it("renders the full control set when platform.dc_fan is true", () => {
    renderRoute(<PwmTab />, {
      settings: dutyCycleFixture().settings,
      mode: "Stop",
    });

    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "PWM Control" })).toBeInTheDocument();
    expect(screen.getByLabelText("Duty cycle row 1")).toBeInTheDocument();
  });
  // Ports index.html:747-758 (validateDutyCycle blocks submit) plus
  // routes.py:485-495 (the two dependent re-clamps). A min >= max save is
  // ALWAYS a rejection in practice: PwmSettings._check_profiles requires every
  // profile to satisfy min <= duty <= max, and profiles is never empty.
  describe("min/max guard and dependent clamps", () => {
    const boundsFixture = (min: number, max: number, extra: object = {}) => ({
      settings: {
        platform: { dc_fan: true },
        pwm: {
          pwm_control: true,
          update_time: 10,
          min_duty_cycle: min,
          max_duty_cycle: max,
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
        ...extra,
      },
      mode: "Stop",
    });

    it("refuses to save when min_duty_cycle > max_duty_cycle and names the constraint", async () => {
      renderRoute(<PwmTab />, boundsFixture(90, 50));

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      expect(saveMock).not.toHaveBeenCalled();
      expect(screen.getByRole("alert")).toHaveTextContent(/Max Duty Cycle/i);
    });

    // Flask's own check is `>=` (index.html:752): with equal bounds every
    // profile would have to equal exactly that value.
    it("refuses to save when min_duty_cycle === max_duty_cycle", async () => {
      renderRoute(<PwmTab />, boundsFixture(50, 50));

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      expect(saveMock).not.toHaveBeenCalled();
      expect(screen.getByRole("alert")).toBeInTheDocument();
    });

    it("saves once when the bounds are valid", async () => {
      renderRoute(<PwmTab />, boundsFixture(20, 100));

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      expect(saveMock).toHaveBeenCalledTimes(1);
      expect(screen.queryByRole("alert")).toBeNull();
    });

    it("re-clamps every profile duty_cycle into the narrowed range (routes.py:489-490)", async () => {
      renderRoute(<PwmTab />, boundsFixture(40, 60));

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      // Assert on the DELTA, not the rendered table: the table only clamps a
      // cell when that cell is edited, so narrowing min/max alone leaves the
      // on-screen rows untouched.
      const [delta] = saveMock.mock.calls[0];
      expect(delta.pwm.profiles).toEqual([
        { duty_cycle: 40 },
        { duty_cycle: 40 },
        { duty_cycle: 50 },
        { duty_cycle: 60 },
        { duty_cycle: 60 },
      ]);
    });

    // THE cross-section assertion. startup.pwm_duty_cycle lives on a DIFFERENT
    // tab; without this the save is rejected by
    // SettingsSchema._check_startup_pwm_duty_cycle (routes.py:495).
    it("clamps startup.pwm_duty_cycle into the new range and writes it into the delta", async () => {
      renderRoute(<PwmTab />, boundsFixture(40, 60, { startup: { pwm_duty_cycle: 100 } }));

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      const [delta, flags] = saveMock.mock.calls[0];
      expect(delta.startup.pwm_duty_cycle).toBe(60);
      // The flag list is a control-loop contract, not a style choice.
      expect(flags).toEqual(["settings_update"]);
    });

    it("still writes startup.pwm_duty_cycle when it is already inside the range", async () => {
      renderRoute(<PwmTab />, boundsFixture(40, 60, { startup: { pwm_duty_cycle: 55 } }));

      fireEvent.click(screen.getByRole("button", { name: "Save" }));
      await new Promise((resolve) => setTimeout(resolve, 50));

      const [delta] = saveMock.mock.calls[0];
      expect(delta.startup.pwm_duty_cycle).toBe(55);
    });

    // NumberField wraps its input in a <label> whose text also carries the
    // suffix, so getByLabelText("Min Duty Cycle") does not match. Reach the
    // input through the label span instead.
    const inputFor = (label: string) => {
      const input = screen.getByText(label).closest("label")?.querySelector("input");
      if (!input) throw new Error(`no input for field "${label}"`);
      return input;
    };

    it("bounds the two duty-cycle inputs at min 1 / max 100 (index.html:735,743)", () => {
      renderRoute(<PwmTab />, boundsFixture(20, 100));

      const minInput = inputFor("Min Duty Cycle");
      const maxInput = inputFor("Max Duty Cycle");
      expect(minInput).toHaveAttribute("min", "1");
      expect(minInput).toHaveAttribute("max", "100");
      expect(maxInput).toHaveAttribute("min", "1");
      expect(maxInput).toHaveAttribute("max", "100");
    });
  });
});
