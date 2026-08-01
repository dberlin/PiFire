import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { VocabTable } from "../../../../src/components/pellets/VocabTable";

function renderTable(over: Partial<Parameters<typeof VocabTable>[0]> = {}) {
  const props = {
    title: "Brands" as const,
    itemNoun: "brand" as const,
    values: ["Hickory", "Acme", "Zed"],
    busy: false,
    onAdd: rs.fn(),
    onDelete: rs.fn(),
    ...over,
  };
  render(<VocabTable {...props} />);
  return props;
}

function rowLabels() {
  return screen
    .getAllByRole("button", { name: /^Delete / })
    .map((b) => b.getAttribute("aria-label"));
}

describe("VocabTable", () => {
  it("renders values sorted regardless of input order (index.html:152, :196)", () => {
    renderTable({ values: ["Zed", "Hickory", "Acme"] });
    expect(rowLabels()).toEqual(["Delete Acme", "Delete Hickory", "Delete Zed"]);
  });

  it("names each delete button for its own row, never a bare 'Delete'", () => {
    renderTable();
    // A generic "Delete" would match every row AND every other card on the page.
    expect(screen.getByRole("button", { name: "Delete Hickory" })).toBeTruthy();
  });

  it("only a confirmed delete calls onDelete", () => {
    const props = renderTable();
    fireEvent.click(screen.getByRole("button", { name: "Delete Hickory" }));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(props.onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Delete Hickory" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(props.onDelete).toHaveBeenCalledWith("Hickory");
  });

  it("adds the trimmed value and clears the field", () => {
    const props = renderTable();
    const input = screen.getByLabelText("New brand") as HTMLInputElement;

    fireEvent.change(input, { target: { value: "  Maple Co  " } });
    fireEvent.click(screen.getByRole("button", { name: "Add brand" }));

    expect(props.onAdd).toHaveBeenCalledWith("Maple Co");
    expect(input.value).toBe("");
  });

  it("does not add an empty or whitespace-only value", () => {
    const props = renderTable();
    const input = screen.getByLabelText("New brand");

    fireEvent.click(screen.getByRole("button", { name: "Add brand" }));
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Add brand" }));

    expect(props.onAdd).not.toHaveBeenCalled();
  });

  it("rejects a duplicate with Flask's wording instead of calling onAdd", () => {
    // routes.py:62-63 -- a duplicate is an error, not a silent no-op. The
    // socketio handler no-ops instead, so the client pre-checks; see the
    // plan's design decision 3.
    const props = renderTable();
    fireEvent.change(screen.getByLabelText("New brand"), { target: { value: "Hickory" } });
    fireEvent.click(screen.getByRole("button", { name: "Add brand" }));

    expect(props.onAdd).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe("Hickory already in pellet brands list.");
  });

  it("uses the wood wording for the woods instance (routes.py:86-87)", () => {
    const props = renderTable({
      title: "Wood Types",
      itemNoun: "wood type",
      values: ["Oak"],
    });
    fireEvent.change(screen.getByLabelText("New wood type"), { target: { value: "Oak" } });
    fireEvent.click(screen.getByRole("button", { name: "Add wood type" }));

    expect(props.onAdd).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe("Oak already in pellet wood list.");
  });

  it("disables the add button and every delete button while busy", () => {
    renderTable({ busy: true });
    expect(screen.getByRole("button", { name: "Add brand" }).hasAttribute("disabled")).toBe(true);
    for (const b of screen.getAllByRole("button", { name: /^Delete / })) {
      expect(b.hasAttribute("disabled")).toBe(true);
    }
  });
});
