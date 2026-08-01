import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { PelletLog } from "../../../../src/components/pellets/PelletLog";
import type { PelletProfile } from "../../../../src/helpers/pellets/pelletTypes";

const ARCHIVE: Record<string, PelletProfile> = {
  p1: { id: "p1", brand: "Generic", wood: "Alder", rating: 5, comments: "c" },
};

const LOG = {
  "2026-07-25 12:00:00": "p1",
  "2026-07-23 08:00:00": "deleted",
  "2026-07-24 10:00:00": "p1",
  "2026-07-22 06:00:00": "vanished",
};

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
  return screen.getAllByRole("cell", { name: /^2026-/ }).map((c) => c.textContent);
}

describe("PelletLog", () => {
  it("sorts rows by timestamp key ascending (index.html:439 items()|sort)", () => {
    renderLog();
    expect(timestampCells()).toEqual([
      "2026-07-22 06:00:00",
      "2026-07-23 08:00:00",
      "2026-07-24 10:00:00",
      "2026-07-25 12:00:00",
    ]);
  });

  it("renders a normal row as '<brand> <wood>' with an accessible rating", () => {
    renderLog();
    expect(screen.getAllByText("Generic Alder").length).toBe(2);
    expect(screen.getAllByLabelText("Rating: 5 of 5").length).toBe(2);
  });

  it("renders the literal 'deleted' value as User Deleted Profile with no delete button", () => {
    renderLog();
    // index.html:442-445: "-" for rating and no action.
    expect(screen.getAllByText("User Deleted Profile").length).toBe(2);
    expect(
      screen.queryByRole("button", { name: "Delete log entry 2026-07-23 08:00:00" }),
    ).toBeNull();
  });

  it("gives an id missing from the archive the same deleted treatment, not a crash", () => {
    // The Jinja at index.html:447 would 500 on this.
    renderLog();
    expect(
      screen.queryByRole("button", { name: "Delete log entry 2026-07-22 06:00:00" }),
    ).toBeNull();
  });

  it("only a confirmed delete calls onDelete, with the timestamp key", () => {
    const props = renderLog();
    fireEvent.click(screen.getByRole("button", { name: "Delete log entry 2026-07-25 12:00:00" }));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(props.onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Delete log entry 2026-07-25 12:00:00" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(props.onDelete).toHaveBeenCalledWith("2026-07-25 12:00:00");
  });

  it("disables every delete button while busy", () => {
    renderLog({ busy: true });
    const buttons = screen.getAllByRole("button", { name: /^Delete log entry / });
    expect(buttons.length).toBe(2);
    for (const b of buttons) expect(b.hasAttribute("disabled")).toBe(true);
  });
});
