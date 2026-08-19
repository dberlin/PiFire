import { deriveView, type HopperView } from "@pifire/core/dashboard/deriveView";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { HopperGauge } from "../../../../src/components/dashboard/HopperGauge";

afterEach(cleanup);

/** The card carries a router <Link>, so it needs a router around it. */
function renderHopper(h: HopperView) {
  return render(
    <MemoryRouter>
      <HopperGauge h={h} />
    </MemoryRouter>,
  );
}

/** Read a custom property off the hopper card wrapping a rendered value. */
function hopperVar(el: HTMLElement, name: string): string {
  const card = el.closest(".pf-dash-hopper");
  expect(card).not.toBeNull();
  return (card as HTMLElement).style.getPropertyValue(name);
}

describe("HopperGauge", () => {
  it("is green/LEVEL OK above 35%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 68 });
    renderHopper(v.hopper);
    expect(screen.getByText("68%")).toBeInTheDocument();
    expect(hopperVar(screen.getByText("68%"), "--pf-hopper-color")).toBe("var(--ok)");
    expect(screen.getByText("LEVEL OK")).toBeInTheDocument();
  });

  it("is amber/RUNNING LOW below 35%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 20 });
    renderHopper(v.hopper);
    expect(hopperVar(screen.getByText("20%"), "--pf-hopper-color")).toBe("var(--warn)");
    expect(screen.getByText("RUNNING LOW")).toBeInTheDocument();
  });

  it("is red/REFILL PELLETS below 15%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 8 });
    renderHopper(v.hopper);
    expect(hopperVar(screen.getByText("8%"), "--pf-hopper-color")).toBe("var(--danger)");
    expect(screen.getByText("REFILL PELLETS")).toBeInTheDocument();
  });
});

// The hopper card carries no BUTTONS: it is a readout plus one navigation
// shortcut.
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
    renderHopper(view);
    expect(screen.queryByRole("button", { name: "Refresh Status" })).not.toBeInTheDocument();
  });

  it("offers no buttons at all", () => {
    renderHopper(view);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
  });

  // These two assertions used to be their exact inverse ("offers no link out to
  // the Flask pellet manager"), from the dashboard slice's decision that
  // linking out would drop the live socket. That reasoning expired: /pellets is
  // a React route as of 2026-07-25, reached through the router rather than a
  // page load, and the human ruled on 2026-07-26 that the link exists because
  // it exists in Bootstrap (_macro_dash_default.html:360). It is also the only
  // entry point to the pellet manager -- there is no navbar item for it.
  it("links to the pellet manager", () => {
    renderHopper(view);
    const link = screen.getByRole("link", { name: "Manager" });
    expect(link).toHaveAttribute("href", "/pellets");
  });

  // The reason the old assertion existed at all. A bare <a href> would reload
  // the document and tear down the socket; a router <Link> does not, and the
  // difference is invisible in the rendered href, so it is pinned here.
  it("navigates in-app rather than reloading the document", () => {
    renderHopper(view);
    const link = screen.getByRole("link", { name: "Manager" });
    // react-router's Link intercepts the click; a plain anchor would not.
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    link.dispatchEvent(click);
    expect(click.defaultPrevented).toBe(true);
  });

  // The readout itself must survive the button's removal -- the footer row is
  // shared with the label, and a previous reflow silently dropped it.
  it("still renders the level and its caption", () => {
    renderHopper(view);
    expect(screen.getByText("55%")).toBeInTheDocument();
    expect(screen.getByText("LEVEL OK")).toBeInTheDocument();
  });
});
