// @vitest-environment jsdom

import { render, screen } from "@testing-library/react";
import { useOutletContext } from "react-router";
import { describe, expect, it } from "vitest";
import { renderRoute } from "./test-utils";

describe("rtl harness", () => {
  it("renders and jest-dom matchers work", () => {
    render(<button>Hi</button>);
    expect(screen.getByRole("button", { name: "Hi" })).toBeInTheDocument();
  });

  it("renderRoute makes useOutletContext resolve for the rendered component", () => {
    function Probe() {
      const ctx = useOutletContext<{ settings: { foo: string }; mode: string }>();
      return (
        <div>
          <span>mode:{ctx.mode}</span>
          <span>foo:{ctx.settings.foo}</span>
        </div>
      );
    }

    renderRoute(<Probe />, { settings: { foo: "bar" }, mode: "Stop" });

    expect(screen.getByText("mode:Stop")).toBeInTheDocument();
    expect(screen.getByText("foo:bar")).toBeInTheDocument();
  });
});
