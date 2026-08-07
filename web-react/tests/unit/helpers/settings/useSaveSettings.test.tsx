import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { createMemoryRouter, RouterProvider, useLoaderData } from "react-router";
import { queryKeys } from "../../../../src/helpers/query/keys";
import { queryClient } from "../../../../src/helpers/query/queryClient";

const mockApplySettings = rs.fn();
// getSettings/getMode/getControllerMetadata are needed too: the rewritten
// invalidation test below drives the REAL settingsLoader (not just
// useSaveSettings) to check the observable outcome of a save, so
// settingsRoutes's dependencies on this module have to resolve to something.
const getSettingsMock = rs.fn();
const getModeMock = rs.fn();
const getControllerMetadataMock = rs.fn();

rs.mock("../../../../src/helpers/settings/settingsApi", () => ({
  applySettings: mockApplySettings,
  getSettings: getSettingsMock,
  getMode: getModeMock,
  getControllerMetadata: getControllerMetadataMock,
}));

// Imported after the mock is registered so useSaveSettings resolves its
// `./settingsApi` dependency to the mocked module (mirrors settingsRoutes.test.ts).
const { useSaveSettings, normalizeSaveError } = await import(
  "../../../../src/helpers/settings/useSaveSettings"
);
const { settingsLoader } = await import("../../../../src/helpers/settings/settingsRoutes");

beforeEach(() => {
  // useSaveSettings invalidates the module-singleton queryClient on a
  // successful save, so cache state (and isInvalidated flags) would
  // otherwise leak from one test into the next.
  queryClient.clear();
});

afterEach(() => {
  cleanup();
  mockApplySettings.mockReset();
  getSettingsMock.mockReset();
  getModeMock.mockReset();
  getControllerMetadataMock.mockReset();
});

// A probe route component: subscribing to `useLoaderData` makes this route
// react to `revalidator.revalidate()` the same way a real settings tab
// (reading its data via the loader / outlet context) would.
function Probe() {
  useLoaderData();
  const { save, saving, status, errors } = useSaveSettings();
  const [result, setResult] = useState<string>("");
  return (
    <div>
      <span data-testid="saving">{String(saving)}</span>
      <span data-testid="result">{result}</span>
      <span data-testid="status-kind">{status.kind}</span>
      <span data-testid="status-message">{status.kind === "error" ? status.message : ""}</span>
      <span data-testid="errors">{JSON.stringify(errors)}</span>
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

function renderWithLoader(loader: () => unknown = () => ({})) {
  const router = createMemoryRouter([{ path: "/", element: <Probe />, loader }], {
    initialEntries: ["/"],
  });
  // The singleton, not a throwaway test client: useSaveSettings resolves its
  // QueryClient via useQueryClient() now, but "makes settingsLoader return the
  // post-save settings" drives the REAL settingsLoader directly (outside
  // React), which always reads the singleton -- so the hook and the loader
  // have to be looking at the exact same cache for that test to mean anything.
  return render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
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

  it("makes settingsLoader return the post-save settings, not the pre-save cache", async () => {
    // Deliberately asserts the OBSERVABLE outcome (what settingsLoader
    // actually returns on the next navigation) rather than an internal cache
    // flag like isInvalidated -- a flag can be set correctly and still be
    // ignored by whatever reads the cache next. ensureQueryData is exactly
    // that: it returns cached data whenever any exists, never consulting
    // isInvalidated, so this test fails against it even though isInvalidated
    // would report true. It only passes against fetchQuery, which treats an
    // invalidated entry as stale and actually refetches.
    queryClient.setQueryData(queryKeys.settings, { globals: { grill_name: "before" } });
    getSettingsMock.mockResolvedValue({ globals: { grill_name: "after" } });
    getModeMock.mockResolvedValue("Stop");
    getControllerMetadataMock.mockResolvedValue(null);
    mockApplySettings.mockResolvedValue({ ok: true, message: "", errors: [] });

    // `renderWithLoader` and `Probe` are this file's existing harness -- Probe
    // calls useSaveSettings() and exposes a button that invokes save().
    renderWithLoader();
    await userEvent.click(await screen.findByRole("button", { name: /save/i }));
    await waitFor(() => expect(screen.getByTestId("result").textContent).toBe("true"));

    await expect(settingsLoader()).resolves.toMatchObject({
      settings: { globals: { grill_name: "after" } },
    });
  });
});

describe("useSaveSettings status", () => {
  it("starts idle", async () => {
    renderWithLoader(() => ({}));
    expect(await screen.findByTestId("status-kind")).toHaveTextContent("idle");
  });

  it("reports saved on a successful save", async () => {
    mockApplySettings.mockResolvedValueOnce({ ok: true, message: "" });
    const loader = rs.fn(() => ({}));
    renderWithLoader(loader);
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByTestId("status-kind").textContent).toBe("saved"));
    await waitFor(() => expect(loader).toHaveBeenCalledTimes(2));
  });

  it("reports error with the 'Settings update failed: ' prefix stripped", async () => {
    mockApplySettings.mockResolvedValueOnce({
      ok: false,
      message: "Settings update failed: safety.maxtemp: Input should be a valid integer",
    });
    renderWithLoader(() => ({}));

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByTestId("status-kind").textContent).toBe("error"));
    expect(screen.getByTestId("status-message").textContent).toBe(
      "safety.maxtemp: Input should be a valid integer",
    );
  });

  it("leaves a message without the prefix untouched", async () => {
    mockApplySettings.mockResolvedValueOnce({ ok: false, message: "HTTP 503" });
    renderWithLoader(() => ({}));

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByTestId("status-kind").textContent).toBe("error"));
    expect(screen.getByTestId("status-message").textContent).toBe("HTTP 503");
  });

  it("falls back to a non-empty message when the server sends none", async () => {
    mockApplySettings.mockResolvedValueOnce({ ok: false, message: "" });
    renderWithLoader(() => ({}));

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.getByTestId("status-kind").textContent).toBe("error"));
    expect(screen.getByTestId("status-message").textContent).toBe("Save failed.");
  });

  it("returns to idle for the duration of the next in-flight save", async () => {
    mockApplySettings.mockResolvedValueOnce({ ok: false, message: "boom" });
    let release: (v: { ok: boolean; message: string }) => void = () => {};
    mockApplySettings.mockReturnValueOnce(
      new Promise<{ ok: boolean; message: string }>((resolve) => {
        release = resolve;
      }),
    );
    renderWithLoader(() => ({}));

    const button = await screen.findByRole("button", { name: "Save" });
    fireEvent.click(button);
    await waitFor(() => expect(screen.getByTestId("status-kind").textContent).toBe("error"));

    fireEvent.click(button);
    await waitFor(() => expect(screen.getByTestId("saving").textContent).toBe("true"));
    expect(screen.getByTestId("status-kind").textContent).toBe("idle");

    release({ ok: true, message: "" });
    await waitFor(() => expect(screen.getByTestId("status-kind").textContent).toBe("saved"));
  });
});

describe("useSaveSettings errors", () => {
  it("exposes the per-field errors a failed save carries", async () => {
    mockApplySettings.mockResolvedValueOnce({
      ok: false,
      message: "bad",
      errors: [{ path: "startup.duration", message: "bad" }],
    });
    renderWithLoader(() => ({}));

    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(screen.getByTestId("errors").textContent).toBe(
        JSON.stringify([{ path: "startup.duration", message: "bad" }]),
      ),
    );
  });

  it("clears errors at the start of the next save attempt", async () => {
    mockApplySettings.mockResolvedValueOnce({
      ok: false,
      message: "bad",
      errors: [{ path: "startup.duration", message: "bad" }],
    });
    let release: (v: { ok: boolean; message: string; errors: unknown[] }) => void = () => {};
    mockApplySettings.mockReturnValueOnce(
      new Promise<{ ok: boolean; message: string; errors: unknown[] }>((resolve) => {
        release = resolve;
      }),
    );
    renderWithLoader(() => ({}));

    const button = await screen.findByRole("button", { name: "Save" });
    fireEvent.click(button);
    await waitFor(() =>
      expect(screen.getByTestId("errors").textContent).toBe(
        JSON.stringify([{ path: "startup.duration", message: "bad" }]),
      ),
    );

    fireEvent.click(button);
    await waitFor(() => expect(screen.getByTestId("saving").textContent).toBe("true"));
    expect(screen.getByTestId("errors").textContent).toBe("[]");

    release({ ok: true, message: "", errors: [] });
    await waitFor(() => expect(screen.getByTestId("status-kind").textContent).toBe("saved"));
  });
});

describe("normalizeSaveError", () => {
  it("strips the server's noise prefix", () => {
    expect(normalizeSaveError("Settings update failed: pwm: Value error")).toBe("pwm: Value error");
  });

  it("passes other messages through and trims", () => {
    expect(normalizeSaveError("  HTTP 503  ")).toBe("HTTP 503");
  });

  it("substitutes a fallback for blank messages", () => {
    expect(normalizeSaveError("   ")).toBe("Save failed.");
    expect(normalizeSaveError("Settings update failed: ")).toBe("Save failed.");
  });
});
