// @vitest-environment jsdom
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { ProbeCard } from "./ProbeCard";
import { deriveView } from "./deriveView";
import { FIXTURE_DASH } from "../fixture";

afterEach(cleanup);

describe("ProbeCard", () => {
  it("shows a bar and the done color when within 1° of target", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 202.5, target: 203, targetReq: true }],
    });
    render(<ProbeCard p={v.probes[0]} />);
    expect(screen.getByText("→ 203°")).toBeInTheDocument();
    expect(screen.getByText("→ 203°")).toHaveStyle({ color: "#5ec96f" });
    expect(screen.getByText("203")).toBeInTheDocument(); // tempInt rounds 202.5 -> 203
  });

  it("renders an untargeted probe as AMBIENT with an empty bar", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], temp: 72, target: 0, targetReq: false }],
    });
    render(<ProbeCard p={v.probes[0]} />);
    expect(screen.getByText("AMBIENT")).toBeInTheDocument();
    expect(screen.getByText("72")).toBeInTheDocument();
  });

  it("shows the probe name and temperature unit", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      foodProbes: [{ ...FIXTURE_DASH.foodProbes[0], title: "Brisket", temp: 145, target: 0, targetReq: false }],
    });
    render(<ProbeCard p={v.probes[0]} />);
    expect(screen.getByText("Brisket")).toBeInTheDocument();
    expect(screen.getByText("°F")).toBeInTheDocument();
    expect(screen.getByText("145")).toBeInTheDocument();
  });
});
