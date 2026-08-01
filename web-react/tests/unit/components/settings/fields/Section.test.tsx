import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { Section } from "../../../../../src/components/settings/fields/Section";

describe("Section", () => {
  it("renders title and children", () => {
    render(
      <Section title="Settings">
        <p>Child content</p>
      </Section>,
    );
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Child content")).toBeInTheDocument();
  });

  it("renders title in an h2 element", () => {
    render(
      <Section title="My Section">
        <div>Content</div>
      </Section>,
    );
    const heading = screen.getByRole("heading", { level: 2 });
    expect(heading).toHaveTextContent("My Section");
  });

  it("renders multiple children", () => {
    render(
      <Section title="Multi">
        <div>First</div>
        <div>Second</div>
        <div>Third</div>
      </Section>,
    );
    expect(screen.getByText("First")).toBeInTheDocument();
    expect(screen.getByText("Second")).toBeInTheDocument();
    expect(screen.getByText("Third")).toBeInTheDocument();
  });

  it("renders children as complex elements", () => {
    render(
      <Section title="Complex">
        <form>
          <input type="text" placeholder="test" />
          <button>Submit</button>
        </form>
      </Section>,
    );
    expect(screen.getByPlaceholderText("test")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit" })).toBeInTheDocument();
  });
});
