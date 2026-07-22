import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";
import { deriveView } from "../../helpers/dashboard/deriveView";
import { FIXTURE_DASH } from "../../helpers/fixture";
import { SystemStatus } from "./SystemStatus";

afterEach(cleanup);

describe("SystemStatus", () => {
  it("shows RUNNING/FEEDING/HOT when outputs are on", () => {
    const v = deriveView({ ...FIXTURE_DASH, outputs: { fan: true, auger: true, igniter: true } });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    expect(screen.getByText("RUNNING")).toBeInTheDocument();
    expect(screen.getByText("FEEDING")).toBeInTheDocument();
    expect(screen.getByText("HOT")).toBeInTheDocument();
  });

  it("shows IDLE/IDLE/IDLE when outputs are off", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      outputs: { fan: false, auger: false, igniter: false },
    });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    expect(screen.getAllByText("IDLE")).toHaveLength(3);
  });

  it("uses the accent color for a running fan and the fixed ember color for a hot igniter", () => {
    const v = deriveView({ ...FIXTURE_DASH, outputs: { fan: true, auger: false, igniter: true } });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    expect(screen.getByText("RUNNING")).toHaveStyle({ color: "var(--accent)" });
    expect(screen.getByText("HOT")).toHaveStyle({ color: "#ff7a1a" });
  });

  it("uses the idle color when an output is off", () => {
    const v = deriveView({
      ...FIXTURE_DASH,
      outputs: { fan: false, auger: false, igniter: false },
    });
    render(<SystemStatus fan={v.fan} auger={v.auger} igniter={v.igniter} animate={false} />);
    expect(screen.getAllByText("IDLE")[0]).toHaveStyle({ color: "#57514a" });
  });
});
