// @vitest-environment jsdom
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { Banners } from "./Banners";

describe("Banners", () => {
  it("renders one banner per error and warning", () => {
    render(
      <Banners
        errors={["control down"]}
        warnings={["lid open", "low hopper"]}
        criticalError={false}
      />,
    );
    expect(screen.getByText("control down")).toBeInTheDocument();
    expect(screen.getByText("lid open")).toBeInTheDocument();
    expect(screen.getByText("low hopper")).toBeInTheDocument();
  });

  it("styles error banners as plain error by default, not critical", () => {
    render(<Banners errors={["control down"]} warnings={[]} criticalError={false} />);
    expect(screen.getByText("control down")).toHaveClass("pf-banner--error");
    expect(screen.getByText("control down")).not.toHaveClass("pf-banner--critical");
  });

  it("styles the error banner critical when criticalError is set", () => {
    render(<Banners errors={["high limit shutdown"]} warnings={[]} criticalError={true} />);
    expect(screen.getByText("high limit shutdown")).toHaveClass("pf-banner--critical");
  });

  it("renders nothing when there are no errors or warnings", () => {
    const { container } = render(<Banners errors={[]} warnings={[]} criticalError={false} />);
    expect(container).toBeEmptyDOMElement();
  });
});
