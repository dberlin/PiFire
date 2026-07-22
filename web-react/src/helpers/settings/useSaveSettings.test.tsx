import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { createMemoryRouter, RouterProvider, useLoaderData } from "react-router";

const mockApplySettings = rs.fn();

rs.mock("./settingsApi", () => ({
  applySettings: mockApplySettings,
}));

// Imported after the mock is registered so useSaveSettings resolves its
// `./settingsApi` dependency to the mocked module (mirrors settingsRoutes.test.ts).
const { useSaveSettings } = await import("./useSaveSettings");

afterEach(() => {
  cleanup();
  mockApplySettings.mockReset();
});

// A probe route component: subscribing to `useLoaderData` makes this route
// react to `revalidator.revalidate()` the same way a real settings tab
// (reading its data via the loader / outlet context) would.
function Probe() {
  useLoaderData();
  const { save, saving } = useSaveSettings();
  const [result, setResult] = useState<string>("");
  return (
    <div>
      <span data-testid="saving">{String(saving)}</span>
      <span data-testid="result">{result}</span>
      <button
        type="button"
        onClick={async () => {
          const ok = await save({ globals: { grill_name: "X" } }, ["settings_update"]);
          setResult(String(ok));
        }}
      >
        Save
      </button>
    </div>
  );
}

function renderWithLoader(loader: () => unknown) {
  const router = createMemoryRouter([{ path: "/", element: <Probe />, loader }], {
    initialEntries: ["/"],
  });
  return render(<RouterProvider router={router} />);
}

describe("useSaveSettings", () => {
  it("POSTs the exact {settings: delta, flags} body via applySettings", async () => {
    mockApplySettings.mockResolvedValueOnce({ ok: true, message: "" });
    const loader = rs.fn(() => ({}));
    renderWithLoader(loader);
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(mockApplySettings).toHaveBeenCalled());
    expect(mockApplySettings).toHaveBeenCalledWith("", { globals: { grill_name: "X" } }, [
      "settings_update",
    ]);
  });

  it("resolves ok:true on a successful save and triggers a revalidation", async () => {
    mockApplySettings.mockResolvedValueOnce({ ok: true, message: "" });
    const loader = rs.fn(() => ({}));
    renderWithLoader(loader);
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByTestId("result").textContent).toBe("true"));
    // useRevalidator() re-runs the route loader on success.
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("saving").textContent).toBe("false");
  });

  it("resolves ok:false on a failed save and does not revalidate", async () => {
    mockApplySettings.mockResolvedValueOnce({ ok: false, message: "bad" });
    const loader = rs.fn(() => ({}));
    renderWithLoader(loader);
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByTestId("result").textContent).toBe("false"));
    // Give any stray revalidation a tick to land, then confirm it never did.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(loader).toHaveBeenCalledTimes(1);
  });
});
