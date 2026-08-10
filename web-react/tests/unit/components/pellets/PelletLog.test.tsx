import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { PelletLog } from "../../../../src/components/pellets/PelletLog";
import type { PelletLogEntry, PelletProfile } from "../../../../src/helpers/contracts/control.gen";

const ARCHIVE: Record<string, PelletProfile> = {
  p1: { brand: "Generic", wood: "Alder", rating: 5, comments: "c" },
};

// Epoch milliseconds, as decimal strings. "999999999999" sorts after
// "1784851200000" as text and before it as a number, which is why the
// component sorts numerically.
const LOG: Record<string, PelletLogEntry> = {
  "1785024000000": { pelletid: "p1", deleted: false },
  "999999999999": { pelletid: "p1", deleted: false },
  "1784851200000": { pelletid: null, deleted: true },
  "1784937600000": { pelletid: "vanished", deleted: false },
};

const WHEN = (key: string) => new Date(Number(key)).toLocaleString();

function renderLog(over: Partial<Parameters<typeof PelletLog>[0]> = {}) {
  const props = {
    log: LOG,
    archive: ARCHIVE,
    busy: false,
    onDelete: rs.fn(),
    ...over,
  };
  render(<PelletLog {...props} />);
  return props;
}

function timestampCells() {
  return screen.getAllByRole("row").map((r) => r.firstElementChild?.textContent ?? "");
}

describe("PelletLog", () => {
  it("sorts rows oldest first, numerically rather than as text", () => {
    renderLog();
    expect(timestampCells()).toEqual([
      WHEN("999999999999"),
      WHEN("1784851200000"),
      WHEN("1784937600000"),
      WHEN("1785024000000"),
    ]);
  });

  it("renders a normal row as '<brand> <wood>' with an accessible rating", () => {
    renderLog();
    expect(screen.getAllByText("Generic Alder").length).toBe(2);
    expect(screen.getAllByLabelText("Rating: 5 of 5").length).toBe(2);
  });

  it("renders a tombstone as User Deleted Profile with no delete button", () => {
    renderLog();
    expect(screen.getAllByText("User Deleted Profile").length).toBe(2);
    expect(
      screen.queryByRole("button", { name: `Delete log entry ${WHEN("1784851200000")}` }),
    ).toBeNull();
  });

  it("gives an id missing from the archive the same deleted treatment, not a crash", () => {
    renderLog();
    expect(
      screen.queryByRole("button", { name: `Delete log entry ${WHEN("1784937600000")}` }),
    ).toBeNull();
  });

  it("only a confirmed delete calls onDelete, with the millisecond key", () => {
    const props = renderLog();
    const label = `Delete log entry ${WHEN("1785024000000")}`;
    fireEvent.click(screen.getByRole("button", { name: label }));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(props.onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: label }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(props.onDelete).toHaveBeenCalledWith("1785024000000");
  });

  it("disables every delete button while busy", () => {
    renderLog({ busy: true });
    const buttons = screen.getAllByRole("button", { name: /^Delete log entry / });
    expect(buttons.length).toBe(2);
    for (const b of buttons) expect(b.hasAttribute("disabled")).toBe(true);
  });
});
