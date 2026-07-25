import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
    render(<HopperGauge h={v.hopper} onRefresh={rs.fn(async () => undefined)} />);
    expect(screen.getByText("68%")).toBeInTheDocument();
    expect(hopperVar(screen.getByText("68%"), "--pf-hopper-color")).toBe("#5ec96f");
    expect(screen.getByText("LEVEL OK")).toBeInTheDocument();
  });

  it("is amber/RUNNING LOW below 35%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 20 });
    render(<HopperGauge h={v.hopper} onRefresh={rs.fn(async () => undefined)} />);
    expect(hopperVar(screen.getByText("20%"), "--pf-hopper-color")).toBe("#ffb020");
    expect(screen.getByText("RUNNING LOW")).toBeInTheDocument();
  });

  it("is red/REFILL PELLETS below 15%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 8 });
    render(<HopperGauge h={v.hopper} onRefresh={rs.fn(async () => undefined)} />);
    expect(hopperVar(screen.getByText("8%"), "--pf-hopper-color")).toBe("#ff5a4d");
    expect(screen.getByText("REFILL PELLETS")).toBeInTheDocument();
  });
});

// M8: the hopper reading is only as fresh as the last distance measurement,
// and the controller does not re-measure on its own. Flask has always carried a
// "Refresh Status" button (_macro_dash_default.html:358).
describe("HopperGauge refresh", () => {
  const view = deriveView({ ...FIXTURE_DASH, hopperLevel: 55 }).hopper;

  it("renders Flask's exact label", () => {
    render(<HopperGauge h={view} onRefresh={rs.fn(async () => undefined)} />);
    expect(screen.getByRole("button", { name: "Refresh Status" })).toBeInTheDocument();
  });

  it("calls the injected handler", async () => {
    const onRefresh = rs.fn(async () => undefined);
    render(<HopperGauge h={view} onRefresh={onRefresh} />);
    fireEvent.click(screen.getByRole("button", { name: "Refresh Status" }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
  });

  it("is disabled while the request is in flight, and live again after", async () => {
    let release: () => void = () => undefined;
    const onRefresh = rs.fn(
      () =>
        new Promise<undefined>((resolve) => {
          release = () => resolve(undefined);
        }),
    );
    render(<HopperGauge h={view} onRefresh={onRefresh} />);
    const button = screen.getByRole("button", { name: "Refresh Status" });
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    release();
    await waitFor(() => expect(button).toBeEnabled());
  });

  // Flask's hopper card also links to /pellets, its pellet manager. Dropped by
  // the app-shell decision -- linking out to a Flask page drops the live socket
  // -- and asserted so it cannot come back by accident.
  it("offers no link out to the Flask pellet manager", () => {
    const { container } = render(<HopperGauge h={view} onRefresh={rs.fn(async () => undefined)} />);
    expect(container.querySelector('a[href*="pellets"]')).toBeNull();
    expect(container.querySelectorAll("a")).toHaveLength(0);
  });
});
