import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { SystemStatus } from "../../../../src/components/dashboard/SystemStatus";
import { deriveView } from "../../../../src/helpers/dashboard/deriveView";
import { FIXTURE_DASH } from "../../../../src/helpers/fixture";

afterEach(cleanup);

/** Read a custom property off the .pf-dash-statusrow wrapping a status label. */
function rowVar(label: HTMLElement, name: string): string {
  const row = label.closest(".pf-dash-statusrow");
  expect(row).not.toBeNull();
  return (row as HTMLElement).style.getPropertyValue(name);
}

describe("SystemStatus", () => {
  it("shows RUNNING/FEEDING/HOT when outputs are on", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      outputs: { fan: true, auger: true, igniter: true, power: true },
    });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByText("FEEDING")).toBeInTheDocument();
    expect(screen.getByText("HOT")).toBeInTheDocument();
  });

  it("shows IDLE/IDLE/IDLE when outputs are off", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      outputs: { fan: false, auger: false, igniter: false, power: false },
    });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    expect(screen.getAllByText("IDLE")).toHaveLength(3);
  });

  it("uses the accent color for a running fan and the fixed ember color for a hot igniter", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      outputs: { fan: true, auger: false, igniter: true, power: false },
    });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    // The row's colours reach the stylesheet as custom properties now; the
    // assertion is the same value in its new place.
    expect(rowVar(screen.getByText("RUNNING"), "--pf-out-color")).toBe("var(--accent)");
    expect(rowVar(screen.getByText("HOT"), "--pf-out-color")).toBe("var(--igniter)");
  });

  it("uses the idle color when an output is off", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      outputs: { fan: false, auger: false, igniter: false, power: false },
    });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    expect(rowVar(screen.getAllByText("IDLE")[0], "--pf-out-color")).toBe("var(--icon-idle)");
  });
});
