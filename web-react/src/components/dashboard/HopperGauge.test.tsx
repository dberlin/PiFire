import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { deriveView } from "../../helpers/dashboard/deriveView";
import { FIXTURE_DASH } from "../../helpers/fixture";
import { HopperGauge } from "./HopperGauge";

afterEach(cleanup);

/** Read a custom property off the hopper card wrapping a rendered value. */
function hopperVar(el: HTMLElement, name: string): string {
  const card = el.closest(".pf-dash-hopper");
  expect(card).not.toBeNull();
  return (card as HTMLElement).style.getPropertyValue(name);
}

describe("HopperGauge", () => {
  it("is green/LEVEL OK above 35%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 68 });
    render(<HopperGauge h={v.hopper} />);
    expect(screen.getByText("68%")).toBeInTheDocument();
    expect(hopperVar(screen.getByText("68%"), "--pf-hopper-color")).toBe("var(--ok)");
    expect(screen.getByText("LEVEL OK")).toBeInTheDocument();
  });

  it("is amber/RUNNING LOW below 35%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 20 });
    render(<HopperGauge h={v.hopper} />);
    expect(hopperVar(screen.getByText("20%"), "--pf-hopper-color")).toBe("var(--warn)");
    expect(screen.getByText("RUNNING LOW")).toBeInTheDocument();
  });

  it("is red/REFILL PELLETS below 15%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 8 });
    render(<HopperGauge h={v.hopper} />);
    expect(hopperVar(screen.getByText("8%"), "--pf-hopper-color")).toBe("var(--danger)");
    expect(screen.getByText("REFILL PELLETS")).toBeInTheDocument();
  });
});

// The hopper card carries no controls at all: it is a readout.
//
// It used to have a "Refresh Status" button, copied from Flask
// (_macro_dash_default.html:359), because nothing re-measured the hopper on its
// own. That is no longer true -- the control loop refreshes the level every
// ~10s (distance/intervals.py) and the socket pushes hopperLevel with every
// frame -- so the button was asking for something already on its way. Removing
// it is a deliberate divergence from Flask, not an oversight, and these
// assertions exist so it cannot drift back.
describe("HopperGauge has no controls", () => {
  const view = deriveView({ ...FIXTURE_DASH, hopperLevel: 55 }).hopper;

  it("offers no Refresh Status button", () => {
    render(<HopperGauge h={view} />);
    expect(screen.queryByRole("button", { name: "Refresh Status" })).not.toBeInTheDocument();
  });

  it("offers no buttons at all", () => {
    render(<HopperGauge h={view} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  // Flask's hopper card also links to /pellets, its pellet manager. Dropped by
  // the app-shell decision -- linking out to a Flask page drops the live socket
  // -- and asserted so it cannot come back by accident.
  it("offers no link out to the Flask pellet manager", () => {
    const { container } = render(<HopperGauge h={view} />);
    expect(container.querySelector('a[href*="pellets"]')).toBeNull();
    expect(container.querySelectorAll("a")).toHaveLength(0);
  });

  // The readout itself must survive the button's removal -- the footer row is
  // shared with the label, and a previous reflow silently dropped it.
  it("still renders the level and its caption", () => {
    render(<HopperGauge h={view} />);
    expect(screen.getByText("55%")).toBeInTheDocument();
    expect(screen.getByText("LEVEL OK")).toBeInTheDocument();
  });
});
