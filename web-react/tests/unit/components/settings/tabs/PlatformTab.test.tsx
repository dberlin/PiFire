import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, screen } from "@testing-library/react";
import { PlatformTab } from "../../../../../src/components/settings/tabs/PlatformTab";
import { renderRoute } from "../../../test-utils";

afterEach(cleanup);

describe("PlatformTab", () => {
  it("renders the platform summary values read-only", () => {
    renderRoute(<PlatformTab />, {
      settings: {
        platform: {
          current: "pcb_4.x.x",
          system_type: "raspberry_pi_all",
          dc_fan: true,
          triggerlevel: "HIGH",
          standalone: true,
          real_hw: true,
          outputs: { auger: 14, fan: 15, igniter: 18, power: 4, dc_fan: 26, pwm: 13 },
        },
      } as SettingsSchema,
      mode: "Stop",
    });

    expect(screen.getByText("pcb_4.x.x")).toBeInTheDocument();
    expect(screen.getByText("raspberry_pi_all")).toBeInTheDocument();
    expect(screen.getByText("DC Fan (PWM)")).toBeInTheDocument();
    expect(screen.getByText("HIGH")).toBeInTheDocument();
    // read-only: no inputs, selects or save button anywhere
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save/i })).not.toBeInTheDocument();
  });

  it("links to the setup wizard", () => {
    renderRoute(<PlatformTab />, { settings: { platform: {} } as SettingsSchema, mode: "Stop" });
    expect(screen.getByRole("link", { name: /configure in setup wizard/i })).toHaveAttribute(
      "href",
      "/wizard",
    );
  });

  it("renders placeholders when platform settings are absent", () => {
    renderRoute(<PlatformTab />, { settings: {} as SettingsSchema, mode: "Stop" });
    expect(screen.getByText("Grill Platform")).toBeInTheDocument();
    // AC Fan is the falsy-dc_fan rendering; must not throw on a missing section
    expect(screen.getByText("AC Fan")).toBeInTheDocument();
  });
});
