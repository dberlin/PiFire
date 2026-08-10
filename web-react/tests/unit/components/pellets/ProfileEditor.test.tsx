import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { ProfileEditor } from "../../../../src/components/pellets/ProfileEditor";
import type { PelletProfile } from "../../../../src/helpers/contracts/control.gen";

const ARCHIVE: Record<string, PelletProfile> = {
  p2: { brand: "Custom", wood: "Oak", rating: 3, comments: "second" },
  p1: { brand: "Generic", wood: "Alder", rating: 4, comments: "first" },
};

function renderEditor(over: Partial<Parameters<typeof ProfileEditor>[0]> = {}) {
  const props = {
    archive: ARCHIVE,
    brands: ["Generic", "Custom"],
    woods: ["Oak", "Alder"],
    currentId: "p1",
    busy: false,
    onAdd: rs.fn(),
    onEdit: rs.fn(),
    onDelete: rs.fn(),
    ...over,
  };
  const { rerender } = render(<ProfileEditor {...props} />);
  return {
    ...props,
    /** Replay a socket tick: the same component with a fresh set of props. */
    tick: (next: Partial<Parameters<typeof ProfileEditor>[0]>) =>
      rerender(<ProfileEditor {...props} {...next} />),
  };
}

const selected = (label: string) => (screen.getByLabelText(label) as HTMLSelectElement).value;

describe("ProfileEditor", () => {
  it("lists profiles sorted by id, each headed '<brand> <wood>'", () => {
    renderEditor();
    const heads = screen
      .getAllByRole("button", { name: /^(Generic|Custom) / })
      .map((b) => b.textContent);
    expect(heads).toEqual(["Generic Alder", "Custom Oak"]);
  });

  it("keeps the Add Profile disclosure collapsed until clicked (index.html:243)", () => {
    renderEditor();
    expect(screen.queryByRole("button", { name: "Add" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Add Profile" }));
    expect(screen.getByRole("button", { name: "Add" })).toBeTruthy();
  });

  it("defaults the add form to first sorted brand/wood, rating 5 and Flask's comment text", () => {
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Add Profile" }));

    expect((screen.getByLabelText("Brand for new profile") as HTMLSelectElement).value).toBe(
      "Custom",
    );
    expect((screen.getByLabelText("Wood for new profile") as HTMLSelectElement).value).toBe(
      "Alder",
    );
    expect((screen.getByLabelText("Rating for new profile") as HTMLSelectElement).value).toBe("5");
    expect((screen.getByLabelText("Comments for new profile") as HTMLTextAreaElement).value).toBe(
      "Enter comments here.",
    );
  });

  it("distinguishes Add from Add & Load", () => {
    // A bare { name: "Add" } would be ambiguous if name matching were loose;
    // it is a full-string match, so these are two distinct buttons.
    const props = renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Add Profile" }));

    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(props.onAdd).toHaveBeenCalledWith(
      { brand_name: "Custom", wood_type: "Alder", rating: 5, comments: "Enter comments here." },
      false,
    );

    fireEvent.click(screen.getByRole("button", { name: "Add & Load" }));
    expect(props.onAdd).toHaveBeenLastCalledWith(
      { brand_name: "Custom", wood_type: "Alder", rating: 5, comments: "Enter comments here." },
      true,
    );
  });

  it("adds what the add form shows after the brand it named leaves the vocabulary", () => {
    // The add form keeps its own draft, so a socket tick that removes a brand
    // can leave it naming a value with no matching <option>. A <select> in that
    // state falls back to DISPLAYING its first option, so the form and the
    // payload disagree and the user silently creates a profile under a brand
    // they cannot see. The server takes brand_name verbatim
    // (pellets_add_profile, common/pellets_actions.py:110-111).
    const props = renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Add Profile" }));
    expect(selected("Brand for new profile")).toBe("Custom");

    props.tick({ brands: ["Generic"], woods: ["Oak", "Alder"] });

    expect(selected("Brand for new profile")).toBe("Generic");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(props.onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ brand_name: "Generic" }),
      false,
    );
  });

  it("adopts the first brand and wood once an empty vocabulary is populated", () => {
    // Starting from nothing there is no valid default, so the draft holds "".
    // Once the user adds the first brand and wood the form shows them; posting
    // "" would write a profile whose brand and wood render as blank everywhere.
    const props = renderEditor({ brands: [], woods: [] });
    fireEvent.click(screen.getByRole("button", { name: "Add Profile" }));

    props.tick({ brands: ["Acme"], woods: ["Hickory"] });

    expect(selected("Brand for new profile")).toBe("Acme");
    expect(selected("Wood for new profile")).toBe("Hickory");
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(props.onAdd).toHaveBeenCalledWith(
      expect.objectContaining({ brand_name: "Acme", wood_type: "Hickory" }),
      false,
    );
  });

  it("keeps a deliberate add-form choice across an unrelated vocabulary change", () => {
    // The re-anchor must only fire when the chosen value actually left the
    // list; re-seeding on every tick would fight the user mid-entry, since the
    // socket republishes the pellet database roughly once a second.
    const props = renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Add Profile" }));
    fireEvent.change(screen.getByLabelText("Brand for new profile"), {
      target: { value: "Generic" },
    });

    props.tick({ brands: ["Generic", "Custom", "Acme"] });

    expect(selected("Brand for new profile")).toBe("Generic");
  });

  it("seeds an expanded profile's controls from that profile and saves them", () => {
    const props = renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Custom Oak" }));

    const comments = screen.getByLabelText("Comments for Custom Oak") as HTMLTextAreaElement;
    expect(comments.value).toBe("second");
    expect((screen.getByLabelText("Rating for Custom Oak") as HTMLSelectElement).value).toBe("3");

    fireEvent.change(comments, { target: { value: "edited" } });
    fireEvent.click(screen.getByRole("button", { name: "Save Custom Oak" }));

    expect(props.onEdit).toHaveBeenCalledWith({
      profile: "p2",
      brand_name: "Custom",
      wood_type: "Oak",
      rating: 3,
      comments: "edited",
    });
  });

  it("editing one expanded profile does not disturb another", () => {
    renderEditor();
    fireEvent.click(screen.getByRole("button", { name: "Generic Alder" }));
    fireEvent.click(screen.getByRole("button", { name: "Custom Oak" }));

    fireEvent.change(screen.getByLabelText("Comments for Custom Oak"), {
      target: { value: "changed" },
    });

    expect((screen.getByLabelText("Comments for Generic Alder") as HTMLTextAreaElement).value).toBe(
      "first",
    );
  });

  it("disables Delete on the currently loaded profile and explains why", () => {
    // Flask lets the click through and answers with an error banner
    // (routes.py:146-154); disabling is the better port, and the server still
    // refuses, so this guard is belt-and-braces rather than the only check.
    renderEditor({ currentId: "p1" });
    fireEvent.click(screen.getByRole("button", { name: "Generic Alder" }));

    const del = screen.getByRole("button", { name: "Delete Generic Alder" });
    expect(del.hasAttribute("disabled")).toBe(true);
    expect(del.getAttribute("title")).toBe("A loaded profile cannot be deleted");
  });

  it("deletes another profile only after confirmation, naming the log cascade", () => {
    const props = renderEditor({ currentId: "p1" });
    fireEvent.click(screen.getByRole("button", { name: "Custom Oak" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete Custom Oak" }));

    // routes.py:157-159 rewrites every log entry pointing at it to "deleted".
    expect(screen.getByText(/log/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(props.onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Delete Custom Oak" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(props.onDelete).toHaveBeenCalledWith("p2");
  });
});
