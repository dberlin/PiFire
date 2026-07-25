import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
    const { container } = render(<ProbeCard p={v.probes[0]} onOpenNotify={rs.fn()} />);
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
    const { container } = render(<ProbeCard p={v.probes[0]} onOpenNotify={rs.fn()} />);
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
    render(<ProbeCard p={v.probes[0]} onOpenNotify={rs.fn()} />);
    expect(screen.getByText("Brisket")).toBeInTheDocument();
    expect(screen.getByText("°F")).toBeInTheDocument();
    expect(screen.getByText("145")).toBeInTheDocument();
  });
  it("opens notifications for the probe's LABEL, not its display title", async () => {
    const user = userEvent.setup();
    const onOpenNotify = rs.fn();
    const v = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], title: "Brisket", label: "Probe1" }],
    });
    render(<ProbeCard p={v.probes[0]} onOpenNotify={onOpenNotify} />);
    await user.click(screen.getByRole("button", { name: "Notifications for Brisket" }));
    // The label is the write identity (common/api_commands.py:441-449); the
    // title is free text the user can rename.
    expect(onOpenNotify).toHaveBeenCalledWith("Probe1");
  });

  it("presses the bell while a target notification is armed", () => {
    const armed = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], target: 203, targetReq: true }],
    });
    const { unmount } = render(<ProbeCard p={armed.probes[0]} onOpenNotify={rs.fn()} />);
    expect(screen.getByRole("button", { name: /^Notifications for/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    unmount();
    const idle = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], target: 0, targetReq: false }],
    });
    render(<ProbeCard p={idle.probes[0]} onOpenNotify={rs.fn()} />);
    expect(screen.getByRole("button", { name: /^Notifications for/ })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("renders the ETA only when there is one", () => {
    const withEta = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], target: 203, targetReq: true, eta: 3661 }],
    });
    const { unmount } = render(<ProbeCard p={withEta.probes[0]} onOpenNotify={rs.fn()} />);
    expect(screen.getByText("ETA 1:01:01")).toBeInTheDocument();
    unmount();
    // eta is null until the backend has enough history to extrapolate
    // (notify/notifications.py:81-99); the row disappears rather than showing
    // a placeholder.
    const noEta = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], target: 203, targetReq: true, eta: null }],
    });
    render(<ProbeCard p={noEta.probes[0]} onOpenNotify={rs.fn()} />);
    expect(screen.queryByText(/^ETA/)).not.toBeInTheDocument();
  });
});
