import { beforeEach, describe, expect, it, rs } from "@rstest/core";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { WorkModeTab } from "../../../../../src/components/settings/tabs/WorkModeTab";
import type { ControllerCatalog } from "../../../../../src/helpers/settings/controllerTypes.gen";
import { renderRoute } from "../../../test-utils";

const saveMock = rs.fn().mockResolvedValue(true);
const useSaveSettingsMock = rs.fn();

// Mock the useSaveSettings module
rs.mock("../../../../../src/helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => useSaveSettingsMock(),
}));

beforeEach(() => {
  saveMock.mockClear();
  useSaveSettingsMock.mockReset().mockReturnValue({
    save: saveMock,
    saving: false,
    status: { kind: "idle" } as const,
    errors: [],
    baseUrl: "",
  });
});

// Field associates its <label> to the control via htmlFor/id, so
const definitionFields = {
  attributions: [],
  author: "Test",
  contributors: [],
  image: "test.png",
  link: "",
  module_name: "test",
};

// getByLabelText(field) resolves the input directly.
function inputFor(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement;
}

describe("WorkModeTab", () => {
  it("renders all three sections with loaded values", () => {
    const context = {
      settings: {
        platform: { dc_fan: true },
        cycle_data: {
          SmokeOnCycleTime: 10,
          SmokeOffCycleTime: 5,
          PMode: 1,
          u_max: 95.5,
          LidOpenDetectEnabled: true,
          LidOpenThreshold: 35,
          LidOpenPauseTime: 75,
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
    expect(screen.getByDisplayValue("95.5")).toBeInTheDocument();
    expect(screen.getByDisplayValue("35")).toBeInTheDocument();
    expect(screen.getByDisplayValue("75")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Lid Open Detect Enabled" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.queryByRole("button", { name: "Fan PID Enabled" })).not.toBeInTheDocument();

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
        platform: { dc_fan: true },
        cycle_data: {
          SmokeOnCycleTime: 10,
          SmokeOffCycleTime: 5,
          PMode: 0,
          u_max: 100,
          LidOpenDetectEnabled: false,
          LidOpenThreshold: 30,
          LidOpenPauseTime: 60,
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

    // Edit a cycle_data field - find SmokeOnCycleTime and change from 10 to 15
    const inputs = screen.getAllByDisplayValue("10");
    const smokeOnCycleInput = inputs[0];
    fireEvent.change(smokeOnCycleInput, { target: { value: "15" } });

    // Toggle a smoke_plus field (enabled from false to true)
    const enabledButton = screen.getByRole("button", { name: "Enabled" });
    fireEvent.click(enabledButton);

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for the save call carrying the delta touching both cycle_data and
    // smoke_plus with the settings_update flag.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        expect.objectContaining({
          cycle_data: expect.objectContaining({
            SmokeOnCycleTime: 15,
          }),
          smoke_plus: expect.objectContaining({
            enabled: true,
          }),
        }),
        ["settings_update"],
      ),
    );
  });

  it("saves the exact full delta after editing every field across cycle_data, smoke_plus and keep_warm", async () => {
    // Every starting value below is distinct so getByDisplayValue can target
    // each field individually (defaults in WorkModeTab.tsx collide, e.g.
    // several fields default to 5 or 150).
    const context = {
      settings: {
        platform: { dc_fan: true },
        cycle_data: {
          SmokeOnCycleTime: 6,
          SmokeOffCycleTime: 7,
          PMode: 1,
          u_max: 99,
          LidOpenDetectEnabled: false,
          LidOpenThreshold: 31,
          LidOpenPauseTime: 61,
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
    change("6", "26");
    change("7", "27");
    change("1", "41");
    change("99", "43");
    change("31", "44");
    change("61", "45");
    fireEvent.click(screen.getByRole("button", { name: "Lid Open Detect Enabled" }));

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

    // Wait for the save call carrying the full edited delta.
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith(
        {
          cycle_data: {
            SmokeOnCycleTime: 26,
            SmokeOffCycleTime: 27,
            PMode: 41,
            u_max: 43,
            LidOpenDetectEnabled: true,
            LidOpenThreshold: 44,
            LidOpenPauseTime: 45,
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
      ),
    );
  });
  // Flask hides the Smoke-Plus fan-ramp NOTE, the sp_fan_ramp switch and the
  // sp_duty_cycle input on an AC-fan build (settings/index.html:405-423).
  it("hides the DC-fan-only Smoke Plus controls when platform.dc_fan is false", () => {
    renderRoute(<WorkModeTab />, {
      settings: { platform: { dc_fan: false } },
      mode: "Stop",
    });

    expect(screen.queryByRole("button", { name: "Fan Ramp" })).toBeNull();
    expect(screen.queryByText("Duty Cycle")).toBeNull();

    // Every other Smoke Plus control survives.
    expect(screen.getByRole("button", { name: "Enabled" })).toBeInTheDocument();
    expect(screen.getByText("Min Temp")).toBeInTheDocument();
    expect(screen.getByText("Max Temp")).toBeInTheDocument();
    expect(screen.getByText("On Time")).toBeInTheDocument();
    expect(screen.getByText("Off Time")).toBeInTheDocument();
  });

  // Hiding a control must NOT drop its key: onSave iterates Object.entries over
  // the whole smoke_plus object, so the loaded values keep round-tripping on an
  // AC build. That matches Flask, whose _settings_cycle leaves untouched keys
  // alone.
  it("still round-trips duty_cycle and fan_ramp in the delta on an AC-fan build", async () => {
    renderRoute(<WorkModeTab />, {
      settings: {
        platform: { dc_fan: false },
        smoke_plus: { enabled: true, duty_cycle: 65, fan_ramp: true },
      },
      mode: "Stop",
    });

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    // Wait for the save call, then inspect the delta it carried.
    await waitFor(() => expect(saveMock).toHaveBeenCalled());
    const [delta] = saveMock.mock.calls[0];
    expect(delta.smoke_plus.duty_cycle).toBe(65);
    expect(delta.smoke_plus.fan_ramp).toBe(true);
  });
  // Bounds ported from settings/index.html:343, 421, 430-454, 502, 511, 559.
  // None of these have a schema counterpart; Flask is the authority on what is
  // sensible, the schema on what is rejected.
  describe("input bounds (M16)", () => {
    const boundsFixture = { settings: { platform: { dc_fan: true } }, mode: "Stop" };

    it("bounds PMode 0-9 with a hint (index.html:343)", () => {
      renderRoute(<WorkModeTab />, boundsFixture);
      const pmode = inputFor("PMode");
      expect(pmode).toHaveAttribute("min", "0");
      expect(pmode).toHaveAttribute("max", "9");
      expect(screen.getByText("0–9")).toBeInTheDocument();
    });

    it("bounds Lid Open Threshold 1-80 step 1 (index.html:502)", () => {
      renderRoute(<WorkModeTab />, boundsFixture);
      const el = inputFor("Lid Open Threshold");
      expect(el).toHaveAttribute("min", "1");
      expect(el).toHaveAttribute("max", "80");
      expect(el).toHaveAttribute("step", "1");
    });

    it("bounds Lid Open Pause Time 10-1000 step 1 (index.html:511)", () => {
      renderRoute(<WorkModeTab />, boundsFixture);
      const el = inputFor("Lid Open Pause Time");
      expect(el).toHaveAttribute("min", "10");
      expect(el).toHaveAttribute("max", "1000");
      expect(el).toHaveAttribute("step", "1");
    });

    it("raises the cosmetic min-0 fields to min 1", () => {
      renderRoute(<WorkModeTab />, boundsFixture);
      for (const label of ["Smoke On Cycle Time", "Smoke Off Cycle Time"]) {
        expect(inputFor(label)).toHaveAttribute("min", "1");
      }
      // Smoke Plus + Keep Warm
      expect(inputFor("Min Temp")).toHaveAttribute("min", "1");
      expect(inputFor("Max Temp")).toHaveAttribute("min", "1");
      expect(inputFor("On Time")).toHaveAttribute("min", "1");
      expect(inputFor("Off Time")).toHaveAttribute("min", "1");
      expect(inputFor("Temp")).toHaveAttribute("min", "1");
    });

    it("leaves Smoke Plus Duty Cycle at its already-correct 20-100", () => {
      renderRoute(<WorkModeTab />, boundsFixture);
      const el = inputFor("Duty Cycle");
      expect(el).toHaveAttribute("min", "20");
      expect(el).toHaveAttribute("max", "100");
    });

    // Task 1's enforcement, reaching a real call site.
    it("clamps PMode on blur", () => {
      renderRoute(<WorkModeTab />, boundsFixture);
      const pmode = inputFor("PMode");
      fireEvent.change(pmode, { target: { value: "44" } });
      fireEvent.blur(pmode);
      expect(inputFor("PMode")).toHaveValue(9);
    });
  });

  // Flask's _macro_settings.html:136 offered the selected controller's
  // recommended value as a one-click button beside the input.
  describe("recommended u_max button", () => {
    const RECOMMEND_TITLE = "Click to Use Recommended Value.";

    // Every controller in the SHIPPED catalog recommends the same 0.9, so
    // production-shaped metadata cannot tell "reads the selected controller"
    // from "reads the first controller" from "hardcodes 0.9". These values are
    // fabricated and distinct per controller so that distinction is testable,
    // and `pid` is deliberately declared FIRST while `mpc` is the selection.
    const perControllerMeta: ControllerCatalog = {
      metadata: {
        pid: {
          ...definitionFields,
          friendly_name: "PID Standard",
          description: "",
          config: [],
          recommendations: { cycle: { cycle_ratio_max: 0.7 } },
        },
        mpc: {
          ...definitionFields,
          friendly_name: "MPC",
          description: "",
          config: [],
          recommendations: { cycle: { cycle_ratio_max: 0.55 } },
        },
      },
    };

    const contextFor = (controllerMeta: ControllerCatalog | null) => ({
      settings: {
        platform: { dc_fan: true },
        cycle_data: { u_max: 0.5 },
        controller: { selected: "mpc" },
      },
      mode: "Stop",
      controllerMeta,
    });

    it("stages the recommended u_max into the draft without saving", () => {
      renderRoute(<WorkModeTab />, contextFor(perControllerMeta));

      expect(inputFor("U Max")).toHaveValue(0.5);
      expect(screen.queryByText("Unsaved changes")).toBeNull();

      fireEvent.click(screen.getByTitle(RECOMMEND_TITLE));

      expect(inputFor("U Max")).toHaveValue(0.55);
      // Staged only: SaveBar lights up, nothing is written until the user says so.
      expect(saveMock).not.toHaveBeenCalled();
      expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    });

    it("offers the SELECTED controller's recommendation, not the first one's", () => {
      renderRoute(<WorkModeTab />, contextFor(perControllerMeta));

      // 0.55 is mpc's (the selection); 0.7 is pid's (the first key).
      expect(screen.getByTitle(RECOMMEND_TITLE)).toHaveTextContent("0.55");
      expect(screen.getByTitle(RECOMMEND_TITLE)).not.toHaveTextContent("0.7");
    });

    // `title` is only an accessible-name FALLBACK: with text content present it
    // is a tooltip, so without an aria-label the button announces as
    // "leftwards arrow 0.55". Querying byRole/name computes the real accname,
    // which getByTitle does not.
    it("announces the action and the value, not just the arrow", () => {
      renderRoute(<WorkModeTab />, contextFor(perControllerMeta));

      expect(
        screen.getByRole("button", { name: "Use recommended value 0.55" }),
      ).toBeInTheDocument();
    });

    it("renders no button when controller metadata is unavailable", () => {
      // getControllerMetadata fails OPEN with null by design, so the tab must
      // still render -- without inventing a recommendation.
      renderRoute(<WorkModeTab />, contextFor(null));

      expect(screen.queryByTitle(RECOMMEND_TITLE)).toBeNull();
      expect(inputFor("U Max")).toHaveValue(0.5);
    });

    it("renders no button when the selected controller recommends nothing", () => {
      // Nothing validates controllers.json against a schema, so a controller
      // with no recommendations block -- or one with no cycle_ratio_max in it
      // -- is representable and must simply offer no button.
      renderRoute(
        <WorkModeTab />,
        contextFor({
          metadata: {
            pid: {
              ...definitionFields,
              friendly_name: "PID Standard",
              description: "",
              config: [],
              recommendations: { cycle: { cycle_ratio_max: 0.7 } },
            },
            mpc: { ...definitionFields, friendly_name: "MPC", description: "", config: [] },
          },
        }),
      );

      expect(screen.queryByTitle(RECOMMEND_TITLE)).toBeNull();
    });

    // Same reasoning one level down: a third-party controller can ship the key
    // with a null value. `!== undefined` would admit it, render an empty arrow
    // and stage null into a float setting.
    it("renders no button when the recommended value is null", () => {
      renderRoute(
        <WorkModeTab />,
        contextFor({
          metadata: {
            mpc: {
              friendly_name: "MPC",
              description: "",
              config: [],
              recommendations: { cycle: { cycle_ratio_max: null } },
            },
          },
        } as unknown as ControllerCatalog),
      );

      expect(screen.queryByTitle(RECOMMEND_TITLE)).toBeNull();
      expect(inputFor("U Max")).toHaveValue(0.5);
    });
  });

  describe("per-field save errors", () => {
    const fixture = { settings: { platform: { dc_fan: true } }, mode: "Stop" };

    it("places a rejected field's reason beside that field, not in the save bar", () => {
      useSaveSettingsMock.mockReturnValue({
        save: saveMock,
        saving: false,
        status: { kind: "error", message: "Some settings were refused" },
        errors: [{ path: "cycle_data.PMode", message: "Input should be less than or equal to 9" }],
        baseUrl: "",
      });

      renderRoute(<WorkModeTab />, fixture);

      // Inline, next to the control that owns the path.
      const alerts = screen.getAllByRole("alert").map((n) => n.textContent);
      expect(alerts).toContain("Input should be less than or equal to 9");
      // The save bar prefixes unplaced errors with their path. This one is
      // placed, so that prefixed form must NOT appear.
      expect(alerts).not.toContain("cycle_data.PMode: Input should be less than or equal to 9");
    });

    it("falls back to the save bar for a path this tab does not render", () => {
      // A cross-section rule can refuse a path that lives on another tab.
      useSaveSettingsMock.mockReturnValue({
        save: saveMock,
        saving: false,
        status: { kind: "error", message: "Some settings were refused" },
        errors: [{ path: "safety.maxtemp", message: "Input should be greater than 0" }],
        baseUrl: "",
      });

      renderRoute(<WorkModeTab />, fixture);

      expect(screen.getAllByRole("alert").map((n) => n.textContent)).toContain(
        "safety.maxtemp: Input should be greater than 0",
      );
    });
  });
});
