import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppPrefsProvider, useAppPrefs } from "../../../src/components/AppPrefs";

afterEach(cleanup);

// Probe component that renders + mutates context so the provider's state
// wiring and its `document.documentElement` side effect can be observed.
function Probe() {
  const { accent, setAccent, animate, setAnimate } = useAppPrefs();
  return (
    <div>
      <span data-testid="accent">{accent}</span>
      <span data-testid="animate">{String(animate)}</span>
      <button onClick={() => setAccent("ice")}>set-ice</button>
      <button onClick={() => setAnimate(false)}>disable-animate</button>
    </div>
  );
}

describe("AppPrefsProvider", () => {
  it("defaults to the ember accent (reflected on document.documentElement) and animate=true", () => {
    render(
      <AppPrefsProvider>
        <Probe />
      </AppPrefsProvider>,
    );

    expect(screen.getByTestId("accent")).toHaveTextContent("ember");
    expect(screen.getByTestId("animate")).toHaveTextContent("true");
    expect(document.documentElement.getAttribute("data-accent")).toBe("ember");
  });

  it("setAccent updates context and sets data-accent on document.documentElement", () => {
    render(
      <AppPrefsProvider>
        <Probe />
      </AppPrefsProvider>,
    );

    fireEvent.click(screen.getByText("set-ice"));

    expect(screen.getByTestId("accent")).toHaveTextContent("ice");
    expect(document.documentElement.getAttribute("data-accent")).toBe("ice");
  });

  it("setAnimate updates the animate flag exposed via context", () => {
    render(
      <AppPrefsProvider>
        <Probe />
      </AppPrefsProvider>,
    );

    fireEvent.click(screen.getByText("disable-animate"));

    expect(screen.getByTestId("animate")).toHaveTextContent("false");
  });

  it("useAppPrefs throws when used outside the provider", () => {
    // Suppress the expected React error-boundary console.error noise.
    const spy = () => {};
    const original = console.error;
    console.error = spy;
    try {
      expect(() => render(<Probe />)).toThrow("useAppPrefs must be used within AppPrefsProvider");
    } finally {
      console.error = original;
    }
  });
});
