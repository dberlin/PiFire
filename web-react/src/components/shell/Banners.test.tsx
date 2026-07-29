import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import * as warningsApi from "../../helpers/shell/warningsApi";
import { Banners } from "./Banners";

rs.mock("../../helpers/shell/warningsApi", () => ({ dismissWarnings: rs.fn() }));

afterEach(() => {
  rs.resetAllMocks();
  cleanup();
});

describe("Banners", () => {
  it("renders one banner per error and warning", () => {
    render(
      <Banners
        errors={["control down"]}
        warnings={["lid open", "low hopper"]}
        warningsMaxId={1}
        criticalError={false}
      />,
    );
    expect(screen.getByText("control down")).toBeInTheDocument();
    expect(screen.getByText("lid open")).toBeInTheDocument();
    expect(screen.getByText("low hopper")).toBeInTheDocument();
  });

  it("styles error banners as plain error by default, not critical", () => {
    render(
      <Banners
        errors={["control down"]}
        warnings={[]}
        warningsMaxId={null}
        criticalError={false}
      />,
    );
    expect(screen.getByText("control down")).toHaveClass("pf-banner--error");
    expect(screen.getByText("control down")).not.toHaveClass("pf-banner--critical");
  });

  it("styles the error banner critical when criticalError is set", () => {
    render(
      <Banners
        errors={["high limit shutdown"]}
        warnings={[]}
        warningsMaxId={null}
        criticalError={true}
      />,
    );
    expect(screen.getByText("high limit shutdown")).toHaveClass("pf-banner--critical");
  });

  it("renders nothing when there are no errors or warnings", () => {
    const { container } = render(
      <Banners errors={[]} warnings={[]} warningsMaxId={null} criticalError={false} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows no dismiss control when there are no warnings", () => {
    render(<Banners errors={["boom"]} warnings={[]} warningsMaxId={null} criticalError={false} />);
    expect(screen.queryByRole("button", { name: /dismiss warnings/i })).toBeNull();
  });

  it("posts the high-water mark and hides the warnings on dismiss", async () => {
    (warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).mockResolvedValue(true);
    render(
      <Banners errors={[]} warnings={["hopper low"]} warningsMaxId={5} criticalError={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss warnings/i }));
    await waitFor(() => expect(screen.queryByText("hopper low")).toBeNull());
    expect(warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).toHaveBeenCalledWith(5);
  });

  it("keeps the warnings up when the dismiss is refused", async () => {
    (warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).mockResolvedValue(false);
    render(
      <Banners errors={[]} warnings={["hopper low"]} warningsMaxId={5} criticalError={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss warnings/i }));
    await waitFor(() => expect(screen.getByText("hopper low")).toBeTruthy());
  });

  it("shows a newer warning that arrives after a dismiss", async () => {
    (warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).mockResolvedValue(true);
    const { rerender } = render(
      <Banners errors={[]} warnings={["hopper low"]} warningsMaxId={5} criticalError={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss warnings/i }));
    await waitFor(() => expect(screen.queryByText("hopper low")).toBeNull());
    // A higher mark means the backend raised something new -- it must not be
    // swallowed by the earlier dismiss.
    rerender(
      <Banners errors={[]} warnings={["auger jam"]} warningsMaxId={6} criticalError={false} />,
    );
    expect(screen.getByText("auger jam")).toBeTruthy();
  });

  it("still renders errors after warnings are dismissed", async () => {
    (warningsApi.dismissWarnings as ReturnType<typeof rs.fn>).mockResolvedValue(true);
    render(
      <Banners
        errors={["boom"]}
        warnings={["hopper low"]}
        warningsMaxId={5}
        criticalError={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /dismiss warnings/i }));
    await waitFor(() => expect(screen.queryByText("hopper low")).toBeNull());
    expect(screen.getByText("boom")).toBeTruthy();
  });
});
