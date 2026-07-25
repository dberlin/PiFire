// @vitest-environment jsdom
import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { StringListField } from "./StringListField";

describe("StringListField", () => {
  it("renders one input per value with the label", () => {
    render(<StringListField label="Locations" values={["a", "b"]} onChange={() => {}} />);
    expect(screen.getByText("Locations")).toBeInTheDocument();
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
  });
  it("editing a row emits the changed array", () => {
    const onChange = rs.fn();
    render(<StringListField label="L" values={["a", "b"]} onChange={onChange} />);
    fireEvent.change(screen.getAllByRole("textbox")[1], { target: { value: "z" } });
    expect(onChange).toHaveBeenCalledWith(["a", "z"]);
  });
  it("Add appends an empty row", () => {
    const onChange = rs.fn();
    render(<StringListField label="L" values={["a"]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /add/i }));
    expect(onChange).toHaveBeenCalledWith(["a", ""]);
  });
  it("remove drops that row once the confirm is accepted", () => {
    const onChange = rs.fn();
    render(<StringListField label="L" values={["a", "b"]} onChange={onChange} />);
    fireEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onChange).toHaveBeenCalledWith(["b"]);
  });

  // Flask confirms ONLY when the row is non-empty, and refuses to drop the last
  // row — clearing it instead, "keeps interface readier for use"
  // (settings.js:925-941). React removed any row instantly and would happily go
  // to zero rows.
  describe("conditional delete confirmation (M15)", () => {
    it("removes an EMPTY row with no dialog", () => {
      const onChange = rs.fn();
      render(<StringListField label="L" values={["", "b"]} onChange={onChange} />);
      fireEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);
      expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
      expect(onChange).toHaveBeenCalledWith(["b"]);
    });

    it("asks before removing a non-empty row and keeps it on Cancel", () => {
      const onChange = rs.fn();
      render(<StringListField label="L" values={["a", "b"]} onChange={onChange} />);
      fireEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);

      expect(screen.getByText(/Row is not empty/i)).toBeInTheDocument();
      expect(onChange).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(onChange).not.toHaveBeenCalled();
      expect(screen.getAllByRole("textbox")).toHaveLength(2);
    });

    it("clears the ONLY row to an empty string rather than dropping to zero rows", () => {
      const onChange = rs.fn();
      render(<StringListField label="L" values={["a"]} onChange={onChange} />);
      fireEvent.click(screen.getByRole("button", { name: /remove/i }));
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
      expect(onChange).toHaveBeenCalledWith([""]);
    });
  });
});
