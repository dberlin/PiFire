import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { queryKeys } from "../../../src/helpers/query/keys";
import * as actualSettingsApi from "../../../src/helpers/settings/settingsApi" with {
  rstest: "importActual",
};
import { renderWithQuery, testQueryClient } from "../test-utils";

// The API module is mocked, not `fetch` -- the idiom MetricsPage.test.tsx
// established. Stubbed through a lazy wrapper so the hoisted mock factory
// never captures an uninitialised binding.
const getSettingsMock = rs.fn();
rs.mock("../../../src/helpers/settings/settingsApi", () => ({
  ...actualSettingsApi,
  getSettings: (...a: unknown[]) => getSettingsMock(...a),
}));

const { AppPrefsProvider, useAppPrefs } = await import("../../../src/components/AppPrefs");

afterEach(cleanup);

beforeEach(() => {
  // Resolves to an empty settings object by default: readAccent() falls back
  // to "ember" for it, so tests that don't care about seeding still get the
  // provider's normal default rather than a permanently-pending query.
  getSettingsMock.mockReset();
  getSettingsMock.mockResolvedValue({});
});

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
      <button type="button" onClick={() => setAccent("ice")}>
        pick ice
      </button>
    </div>
  );
}

describe("AppPrefsProvider", () => {
  it("defaults to the ember accent (reflected on document.documentElement) and animate=true", () => {
    renderWithQuery(
      <AppPrefsProvider>
        <Probe />
      </AppPrefsProvider>,
    );

    expect(screen.getByTestId("accent")).toHaveTextContent("ember");
    expect(screen.getByTestId("animate")).toHaveTextContent("true");
    expect(document.documentElement.getAttribute("data-accent")).toBe("ember");
  });

  it("setAccent updates context and sets data-accent on document.documentElement", () => {
    renderWithQuery(
      <AppPrefsProvider>
        <Probe />
      </AppPrefsProvider>,
    );

    fireEvent.click(screen.getByText("set-ice"));

    expect(screen.getByTestId("accent")).toHaveTextContent("ice");
    expect(document.documentElement.getAttribute("data-accent")).toBe("ice");
  });

  it("setAnimate updates the animate flag exposed via context", () => {
    renderWithQuery(
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

  it("adopts the stored accent when settings arrive", async () => {
    getSettingsMock.mockResolvedValue({
      modules: { display: "ili9341" },
      display: { config: { ili9341: { accent_theme: "Crimson" } } },
    });
    renderWithQuery(
      <AppPrefsProvider>
        <Probe />
      </AppPrefsProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("accent")).toHaveTextContent("crimson"));
  });

  it("does not overwrite a user's choice when settings refetch", async () => {
    // A save invalidates the settings key, so a refetch WILL happen while the
    // user is sitting on their own selection. Seeding is a first-answer-only
    // event for exactly this reason.
    getSettingsMock.mockResolvedValue({
      modules: { display: "ili9341" },
      display: { config: { ili9341: { accent_theme: "Crimson" } } },
    });
    const client = testQueryClient();
    render(
      <QueryClientProvider client={client}>
        <AppPrefsProvider>
          <Probe />
        </AppPrefsProvider>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("accent")).toHaveTextContent("crimson"));

    await userEvent.click(screen.getByRole("button", { name: "pick ice" }));
    expect(screen.getByTestId("accent")).toHaveTextContent("ice");

    await act(() => client.invalidateQueries({ queryKey: queryKeys.settings }));
    expect(screen.getByTestId("accent")).toHaveTextContent("ice");
  });
});
