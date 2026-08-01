import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { SafetyTab } from "../../../../../src/components/settings/tabs/SafetyTab";
import { renderRoute } from "../../../test-utils";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../../../src/helpers/settings/useSaveSettings", () => ({
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

afterEach(cleanup);

// NumberField wraps its input in a <label> whose text also carries the suffix,
// so getByLabelText(field) does not match. Reach the input via the label span.
function inputFor(label: string): HTMLInputElement {
  const input = screen.getByText(label).closest("label")?.querySelector("input");
  if (!input) throw new Error(`no input for field "${label}"`);
  return input;
}

describe("SafetyTab", () => {
  it("renders safety fields with loaded values", () => {
    const context = {
      settings: {
        safety: {
          minstartuptemp: 80,
          maxstartuptemp: 110,
          maxtemp: 550,
          reigniteretries: 2,
          startup_check: true,
          allow_manual_changes: false,
          manual_override_time: 45,
        },
      },
      mode: "Stop",
    };

    renderRoute(<SafetyTab />, context);

    // Check that fields display the loaded values
    expect(screen.getByDisplayValue("80")).toBeInTheDocument();
    expect(screen.getByDisplayValue("110")).toBeInTheDocument();
    expect(screen.getByDisplayValue("550")).toBeInTheDocument();
    expect(screen.getByDisplayValue("2")).toBeInTheDocument();
    expect(screen.getByDisplayValue("45")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Startup Check" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Allow Manual Output Changes" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("saves settings with empty flags when Save is clicked", async () => {
    const context = {
      settings: {
        safety: {
          minstartuptemp: 75,
          maxstartuptemp: 100,
          maxtemp: 550,
          reigniteretries: 1,
          startup_check: false,
          allow_manual_changes: false,
          manual_override_time: 30,
        },
      },
      mode: "Stop",
    };

    renderRoute(<SafetyTab />, context);

    // Change Max Grill Temp from 550 to 600
    const maxTempInput = screen.getByDisplayValue("550");
    fireEvent.change(maxTempInput, { target: { value: "600" } });

    // Click Save
    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    // Wait for async save to complete and assert spy was called with correct delta and empty flags
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        safety: expect.objectContaining({
          maxtemp: 600,
        }),
      }),
      [],
    );
  });

  it("saves the exact full delta after editing every numeric field and both toggles", async () => {
    const context = {
      settings: {
        safety: {
          minstartuptemp: 80,
          maxstartuptemp: 110,
          maxtemp: 550,
          reigniteretries: 2,
          startup_check: false,
          allow_manual_changes: false,
          manual_override_time: 45,
        },
      },
      mode: "Stop",
    };

    renderRoute(<SafetyTab />, context);

    fireEvent.change(screen.getByDisplayValue("80"), { target: { value: "85" } });
    fireEvent.change(screen.getByDisplayValue("110"), { target: { value: "115" } });
    fireEvent.change(screen.getByDisplayValue("550"), { target: { value: "560" } });
    fireEvent.change(screen.getByDisplayValue("2"), { target: { value: "3" } });
    fireEvent.change(screen.getByDisplayValue("45"), { target: { value: "50" } });
    fireEvent.click(screen.getByRole("button", { name: "Startup Check" }));
    fireEvent.click(screen.getByRole("button", { name: "Allow Manual Output Changes" }));

    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(saveMock).toHaveBeenCalledWith(
      {
        safety: {
          minstartuptemp: 85,
          maxstartuptemp: 115,
          maxtemp: 560,
          reigniteretries: 3,
          startup_check: true,
          allow_manual_changes: true,
          manual_override_time: 50,
        },
      },
      [],
    );
  });
  describe("input bounds", () => {
    const fixture = { settings: {}, mode: "Stop" };

    // React currently accepts NEGATIVE grill temperatures here — these three
    // fields carry no min at all (index.html:1262, 1269, 1276 all say min="1").
    it("gives the three temperature fields a min of 1", () => {
      renderRoute(<SafetyTab />, fixture);
      for (const label of ["Min Startup Temp", "Max Startup Temp", "Max Grill Temp"]) {
        expect(inputFor(label)).toHaveAttribute("min", "1");
      }
    });

    it("bounds reignite retries 0-10 (index.html:1286)", () => {
      renderRoute(<SafetyTab />, fixture);
      const el = inputFor("Reignite Retries");
      expect(el).toHaveAttribute("min", "0");
      expect(el).toHaveAttribute("max", "10");
    });

    it("clamps a negative max grill temp on blur", () => {
      renderRoute(<SafetyTab />, fixture);
      const el = inputFor("Max Grill Temp");
      fireEvent.change(el, { target: { value: "-40" } });
      fireEvent.blur(el);
      expect(inputFor("Max Grill Temp")).toHaveValue(1);
    });
  });
});
