import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { DiscoveryPanel } from "./DiscoveryPanel";

afterEach(cleanup);

describe("DiscoveryPanel", () => {
  test("renders groups and picks an item", () => {
    const onPick = rs.fn();
    render(
      <DiscoveryPanel
        result={{
          groups: [{ title: "By Bus", items: [{ value: "1", label: "i2c-1" }] }],
          error: null,
        }}
        onPick={onPick}
      />,
    );
    expect(screen.getByText("By Bus")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "i2c-1" }));
    expect(onPick).toHaveBeenCalledWith("1");
  });

  test("shows error instead of table", () => {
    render(<DiscoveryPanel result={{ groups: [], error: "No devices found." }} onPick={rs.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("No devices found.");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  test("skips groups with empty items", () => {
    render(
      <DiscoveryPanel
        result={{
          groups: [
            { title: "By Bus", items: [{ value: "1", label: "i2c-1" }] },
            { title: "By Serial", items: [] },
          ],
          error: null,
        }}
        onPick={rs.fn()}
      />,
    );
    expect(screen.getByText("By Bus")).toBeInTheDocument();
    expect(screen.queryByText("By Serial")).not.toBeInTheDocument();
  });

  test("shows a fallback message when all groups are empty and there is no error", () => {
    render(
      <DiscoveryPanel
        result={{ groups: [{ title: "By Serial", items: [] }], error: null }}
        onPick={rs.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("No devices found.");
  });
});
