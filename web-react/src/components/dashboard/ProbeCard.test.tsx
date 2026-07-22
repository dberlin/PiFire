import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { deriveView } from "../../helpers/dashboard/deriveView";
import { FIXTURE_DASH } from "../../helpers/fixture";
import { ProbeCard } from "./ProbeCard";

afterEach(cleanup);

// The bar track (outer div, `margin-top: 8px`) wraps a single inner div whose
// `width`/`background` are driven directly by barPct/barColor. Grabbing that
// inner div lets tests fail if the bar geometry/color regresses independently
// of the (separately styled) target text color.
function getBar(container: HTMLElement): HTMLElement {
  const track = container.querySelector('div[style*="margin-top"]');
  expect(track).not.toBeNull();
  const bar = track!.firstElementChild;
  expect(bar).not.toBeNull();
  return bar as HTMLElement;
}

describe("ProbeCard", () => {
  it("shows a bar and the done color when within 1° of target", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 202.5, target: 203, targetReq: true }],
    });
    const { container } = render(<ProbeCard p={v.probes[0]} />);
    expect(screen.getByText("→ 203°")).toBeInTheDocument();
    expect(screen.getByText("→ 203°")).toHaveStyle({ color: "#5ec96f" });
    expect(screen.getByText("203")).toBeInTheDocument(); // tempInt rounds 202.5 -> 203

    const bar = getBar(container);
    expect(parseFloat(bar.style.width)).toBeGreaterThan(90); // (202.5/203)*100 ~= 99.75%
    expect(bar).toHaveStyle({ background: "#5ec96f" }); // done -> green, not the accent
  });

  it("renders an untargeted probe as AMBIENT with an empty bar", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 72, target: 0, targetReq: false }],
    });
    const { container } = render(<ProbeCard p={v.probes[0]} />);
    expect(screen.getByText("AMBIENT")).toBeInTheDocument();
    expect(screen.getByText("72")).toBeInTheDocument();

    const bar = getBar(container);
    expect(bar.style.width).toBe("0%");
    expect(bar).toHaveStyle({ background: "var(--accent)" });
  });

  it("shows the probe name and temperature unit", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [
        { ...FIXTURE_DASH.foodProbes[0], title: "Brisket", temp: 145, target: 0, targetReq: false },
      ],
    });
    render(<ProbeCard p={v.probes[0]} />);
    expect(screen.getByText("Brisket")).toBeInTheDocument();
    expect(screen.getByText("°F")).toBeInTheDocument();
    expect(screen.getByText("145")).toBeInTheDocument();
  });
});
