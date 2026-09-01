import type { ThermocoupleHealthView } from "@pifire/core/contracts/core";
import { deriveView, type ProbeCardView } from "@pifire/core/dashboard/deriveView";
import { FIXTURE_DASH } from "@pifire/core/fixture";
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProbeCard } from "../../../../src/components/dashboard/ProbeCard";

afterEach(cleanup);

// The bar track (outer div, `margin-top: 8px`) wraps a single inner div whose
// `width`/`background` are driven directly by barPct/barColor. Grabbing that
// inner div lets tests fail if the bar geometry/color regresses independently
// of the (separately styled) target text color.
function barVars(container: HTMLElement): { pct: string; color: string } {
  // The fill's width and colour are per-frame data, so they travel as custom
  // properties on the card and the stylesheet applies them. jsdom resolves
  // neither var() nor layout, so the assertion has to read them at the source.
  expect(container.querySelector(".pf-dash-bar-fill")).not.toBeNull();
  const card = container.querySelector(".pf-dash-probecard") as HTMLElement;
  expect(card).not.toBeNull();
  return {
    pct: card.style.getPropertyValue("--pf-bar-pct"),
    color: card.style.getPropertyValue("--pf-bar-color"),
  };
}

function foodHealth(
  state: ThermocoupleHealthView["report"]["state"],
  outcome: ThermocoupleHealthView["outcome"],
  current = true,
): ThermocoupleHealthView {
  const food = FIXTURE_DASH.foodProbes[0];
  return {
    device: "Food amplifier",
    port: "KTT0",
    label: food.label,
    displayName: food.title,
    role: "Food",
    report: {
      state,
      faults: state === "confirmed" ? ["open"] : [],
      evidence: state === "healthy" ? [] : ["junction-collapse"],
      temperatureValid: outcome !== "unavailable",
      detail: {},
    },
    detector: { source: state === "confirmed" ? "hardware" : "software", policy: "observe" },
    outcome,
    freshness: { current, lastReportedAgeS: current ? 0 : 75 },
  };
}

describe("ProbeCard", () => {
  it("shows a bar and the done color when within 1° of target", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 202.5, target: 203, targetReq: true }],
    });
    const { container } = render(<ProbeCard p={v.probes[0]} onOpenNotify={rs.fn()} />);
    expect(screen.getByText("→ 203°")).toBeInTheDocument();
    // The target text takes its colour from the ok token, so jsdom (which does
    // not resolve var()) has to be asked for the declaration, not the value.
    expect(screen.getByText("→ 203°").style.color).toBe("var(--ok)");
    expect(screen.getByText("203")).toBeInTheDocument(); // tempInt rounds 202.5 -> 203

    const bar = barVars(container);
    expect(Number.parseFloat(bar.pct)).toBeGreaterThan(90); // (202.5/203)*100 ~= 99.75%
    expect(bar.color).toBe("var(--ok)"); // done -> green, not the accent
  });

  it("renders an untargeted probe as AMBIENT with an empty bar", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 72, target: 0, targetReq: false }],
    });
    const { container } = render(<ProbeCard p={v.probes[0]} onOpenNotify={rs.fn()} />);
    expect(screen.getByText("AMBIENT")).toBeInTheDocument();
    expect(screen.getByText("72")).toBeInTheDocument();

    const bar = barVars(container);
    expect(bar.pct).toBe("0%");
    expect(bar.color).toBe("var(--accent)");
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

  it("presses the bell while any notification is armed", () => {
    const armed = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [
        // A LIMIT alert alone, with no target: the bell opens a modal that edits
        // all three entries, so it must light for any of them.
        { ...FIXTURE_DASH.foodProbes[0], highLimitReq: true, hasNotifications: true },
      ],
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

describe("ProbeCard thermocouple health", () => {
  it("keeps a suspected probe numeric and renders amber inline copy only", () => {
    const view = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 147 }],
      thermocoupleHealth: [foodHealth("suspected", "none")],
    });
    const { container } = render(<ProbeCard p={view.probes[0]} onOpenNotify={rs.fn()} />);

    expect(screen.getByText("147")).toBeInTheDocument();
    expect(screen.getByText("CHECK PROBE")).toBeInTheDocument();
    expect(
      screen.getByText("Possible thermocouple issue; reading still available."),
    ).toBeInTheDocument();
    expect(container.querySelector(".pf-dash-probehealth")).toHaveClass(
      "pf-dash-probehealth--warning",
    );
  });

  it("shows an em dash and truthful continue copy for confirmed unavailable food", () => {
    const view = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [
        {
          ...FIXTURE_DASH.foodProbes[0],
          temp: 147,
          status: { ...FIXTURE_DASH.foodProbes[0].status, lastTemp: 146, lastReadingAge: 30 },
        },
      ],
      thermocoupleHealth: [foodHealth("confirmed", "unavailable")],
    });
    render(<ProbeCard p={view.probes[0]} onOpenNotify={rs.fn()} />);

    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("147")).toBeNull();
    expect(screen.queryByText("146")).toBeNull();
    expect(screen.getByText("PROBE UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByText("Grill control continues.")).toBeInTheDocument();
    expect(screen.getByText("Hardware reported an open circuit.")).toBeInTheDocument();
  });

  it("shows no health pill for healthy or unmonitored probes", () => {
    const healthy = deriveView({
      ...FIXTURE_DASH,
      thermocoupleHealth: [foodHealth("healthy", "none")],
    });
    const { container, rerender } = render(
      <ProbeCard p={healthy.probes[0]} onOpenNotify={rs.fn()} />,
    );
    expect(container.querySelector(".pf-dash-probehealth")).toBeNull();

    const unmonitored = deriveView({
      ...FIXTURE_DASH,
      thermocoupleHealth: [foodHealth("unmonitored", "none")],
    });
    rerender(<ProbeCard p={unmonitored.probes[0]} onOpenNotify={rs.fn()} />);
    expect(container.querySelector(".pf-dash-probehealth")).toBeNull();
  });

  it("qualifies stale health independently from the reading", () => {
    const view = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 147 }],
      thermocoupleHealth: [foodHealth("suspected", "none", false)],
    });
    render(<ProbeCard p={view.probes[0]} onOpenNotify={rs.fn()} />);

    expect(screen.getByText("147")).toBeInTheDocument();
    expect(screen.getByText("Last reported: CHECK PROBE")).toBeInTheDocument();
  });
});

// M6: the connected/battery badges were declared in the types and read by
// nothing. Both are Bluetooth-only -- socket_io.py:858-859 copies each key only
// if the driver set it -- so a wired ADC probe must show neither.
describe("ProbeCard status badges", () => {
  // The demo fixture's probes are wired ADC ones: no `connected` key and no
  // `batteryPercentage` key, so deriveView leaves both badges null.
  const BASE: ProbeCardView = deriveView(FIXTURE_DASH).probes[0];
  const withBadges = (over: Partial<ProbeCardView>): ProbeCardView => ({ ...BASE, ...over });

  it("renders no badge element at all for a probe that reports neither", () => {
    const { container } = render(
      <ProbeCard p={withBadges({ conn: null, battery: null })} onOpenNotify={rs.fn()} />,
    );
    // Not an empty span: an empty one still takes gap space and would move the
    // card, which the 1280x720 fidelity gate would catch.
    expect(container.querySelectorAll(".pf-badge")).toHaveLength(0);
  });

  it("renders the link badge with its tone and tooltip", () => {
    const { container } = render(
      <ProbeCard
        p={withBadges({ conn: { label: "Connected", tone: "ok" } })}
        onOpenNotify={rs.fn()}
      />,
    );
    const badge = container.querySelector(".pf-badge");
    expect(badge).not.toBeNull();
    expect(badge).toHaveClass("pf-badge-ok");
    expect(badge).toHaveAttribute("title", "Connected");
  });

  it("renders a disconnected probe in the off tone", () => {
    const { container } = render(
      <ProbeCard
        p={withBadges({ conn: { label: "Disconnected", tone: "off" } })}
        onOpenNotify={rs.fn()}
      />,
    );
    expect(container.querySelector(".pf-badge")).toHaveClass("pf-badge-off");
  });

  it("renders the battery badge with its percentage as the tooltip", () => {
    const { container } = render(
      <ProbeCard
        p={withBadges({ battery: { text: "35%", tone: "warn", level: 1 } })}
        onOpenNotify={rs.fn()}
      />,
    );
    const badge = container.querySelector(".pf-badge");
    expect(badge).toHaveClass("pf-badge-warn");
    expect(badge).toHaveAttribute("title", "35%");
  });

  it("renders both badges when the probe reports both", () => {
    const { container } = render(
      <ProbeCard
        p={withBadges({
          conn: { label: "Connected", tone: "ok" },
          battery: { text: "0%", tone: "danger", level: 0 },
        })}
        onOpenNotify={rs.fn()}
      />,
    );
    expect(container.querySelectorAll(".pf-badge")).toHaveLength(2);
  });
});

// A reading the card cannot refresh must not read as live. The number stays --
// 40 s of age is still worth something to someone deciding whether to open the
// lid -- but it has to say so, because a temperature is plausible at any value.
describe("ProbeCard staleness", () => {
  const BASE: ProbeCardView = deriveView(FIXTURE_DASH).probes[0];
  const withStale = (over: Partial<ProbeCardView>): ProbeCardView => ({ ...BASE, ...over });

  it("renders no stale line while the probe is reporting", () => {
    const { container } = render(
      <ProbeCard p={withStale({ tempInt: 147, stale: null })} onOpenNotify={rs.fn()} />,
    );
    expect(screen.getByText("147")).toBeInTheDocument();
    expect(container.querySelector(".pf-dash-probestale")).toBeNull();
  });

  it("keeps the number and adds the age when the reading is stale", () => {
    const { container } = render(
      <ProbeCard
        p={withStale({ tempInt: 147, stale: "last data 47s ago" })}
        onOpenNotify={rs.fn()}
      />,
    );
    expect(screen.getByText("147")).toBeInTheDocument();
    expect(container.querySelector(".pf-dash-probestale")).toHaveTextContent("last data 47s ago");
  });

  it("shows a dash, not a zero, for a probe that has never reported", () => {
    render(<ProbeCard p={withStale({ tempInt: null, stale: null })} onOpenNotify={rs.fn()} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});
