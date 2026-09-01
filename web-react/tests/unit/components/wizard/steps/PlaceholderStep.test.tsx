import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { PlaceholderStep } from "../../../../../src/components/wizard/steps/PlaceholderStep";

afterEach(cleanup);

describe("PlaceholderStep", () => {
  it("renders the section's friendly name and the 'later release' message", () => {
    render(<PlaceholderStep section="grillplatform" />);
    expect(screen.getByText("Grill Platform")).toBeInTheDocument();
    expect(
      screen.getByText("This section isn't configurable here yet — coming in a later release."),
    ).toBeInTheDocument();
  });

  it("renders a different section's label", () => {
    render(<PlaceholderStep section="probes" />);
    expect(screen.getByText("Probes")).toBeInTheDocument();
  });
});
