// @vitest-environment jsdom
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { HopperGauge } from "./HopperGauge";
import { deriveView } from "./deriveView";
import { FIXTURE_DASH } from "../fixture";

afterEach(cleanup);

describe("HopperGauge", () => {
  it("is green/LEVEL OK above 35%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 68 });
    render(<HopperGauge h={v.hopper} />);
    expect(screen.getByText("68%")).toBeInTheDocument();
    expect(screen.getByText("68%")).toHaveStyle({ color: "#5ec96f" });
    expect(screen.getByText("LEVEL OK")).toBeInTheDocument();
  });

  it("is amber/RUNNING LOW below 35%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 20 });
    render(<HopperGauge h={v.hopper} />);
    expect(screen.getByText("20%")).toHaveStyle({ color: "#ffb020" });
    expect(screen.getByText("RUNNING LOW")).toBeInTheDocument();
  });

  it("is red/REFILL PELLETS below 15%", () => {
    const v = deriveView({ ...FIXTURE_DASH, hopperLevel: 8 });
    render(<HopperGauge h={v.hopper} />);
    expect(screen.getByText("8%")).toHaveStyle({ color: "#ff5a4d" });
    expect(screen.getByText("REFILL PELLETS")).toBeInTheDocument();
  });
});
