import { afterEach, describe, expect, rs, test } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { DiscoveryPanel } from "../../../../src/components/wizard/DiscoveryPanel";

afterEach(cleanup);

const RESULT = {
  groups: [{ title: "By Bus", items: [{ value: "1", label: "i2c-1" }] }],
  error: null,
};

describe("DiscoveryPanel", () => {
  test("renders groups and picks an item", () => {
    const onPick = rs.fn();
    render(
      <DiscoveryPanel result={RESULT} onPick={onPick} onRefresh={rs.fn()} onClose={rs.fn()} />,
    );
    expect(screen.getByText("By Bus")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "i2c-1" }));
    expect(onPick).toHaveBeenCalledWith("1");
  });

  test("shows error instead of table", () => {
    // groups is non-empty here on purpose: the grid COULD render this button
    // if the error branch fell through, so the absence check below actually
    // proves the early return, rather than just restating an empty fixture.
    render(
      <DiscoveryPanel
        result={{ ...RESULT, error: "No devices found." }}
        onPick={rs.fn()}
        onRefresh={rs.fn()}
        onClose={rs.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("No devices found.");
    expect(screen.queryByRole("button", { name: "i2c-1" })).not.toBeInTheDocument();
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
        onRefresh={rs.fn()}
        onClose={rs.fn()}
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
        onRefresh={rs.fn()}
        onClose={rs.fn()}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("No devices found.");
  });

  test("re-runs the scan from Refresh and dismisses from Close", async () => {
    const user = userEvent.setup();
    const onRefresh = rs.fn();
    const onClose = rs.fn();
    render(
      <DiscoveryPanel result={RESULT} onPick={() => {}} onRefresh={onRefresh} onClose={onClose} />,
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test("offers Refresh even when the scan found nothing", async () => {
    // The empty case is exactly when a re-scan is what the user wants.
    const user = userEvent.setup();
    const onRefresh = rs.fn();
    render(
      <DiscoveryPanel
        result={{ groups: [], error: null }}
        onPick={() => {}}
        onRefresh={onRefresh}
        onClose={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  test("offers Refresh even when the scan errored", async () => {
    // The error case is exactly when a re-scan is what the user wants.
    const user = userEvent.setup();
    const onRefresh = rs.fn();
    render(
      <DiscoveryPanel
        result={{ groups: [], error: "No devices found." }}
        onPick={() => {}}
        onRefresh={onRefresh}
        onClose={() => {}}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });
});
