import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { MemoryRouter, Outlet, Route, Routes } from "react-router";
import { useSettingsDraftStore } from "../../../helpers/settings/settingsDrafts";
import { renderRoute } from "../../../test-utils";
import { GeneralTab } from "./GeneralTab";

const saveMock = rs.fn().mockResolvedValue(true);

// Mock the useSaveSettings module
rs.mock("../../../helpers/settings/useSaveSettings", () => ({
  useSaveSettings: () => ({
    save: saveMock,
    saving: false,
    status: { kind: "idle" } as const,
    baseUrl: "",
  }),
}));

beforeEach(() => {
  saveMock.mockClear();
});

afterEach(cleanup);

// Harness for the render-phase resync case: keeps GeneralTab mounted while
// swapping the outlet context object out from under it, so we exercise the
// `settings !== prevSettings` branch on a live component instead of a fresh
// mount (a fresh renderRoute() call per settings object wouldn't touch that
// branch at all).
let setOutletContext: ((ctx: unknown) => void) | null = null;

function ContextHolder({ initial }: { initial: unknown }) {
  const [ctx, setCtx] = useState(initial);
  // Also stands in for SettingsShell's draft store, which is where the tab's
  // in-progress edit now lives (helpers/settings/settingsDrafts.ts).
  const store = useSettingsDraftStore((ctx as { settings?: unknown })?.settings);
  // Publishing the setter is a side effect (module-level mutable ref used
  // only by the test below to drive a re-render), so it belongs in an
  // effect, not directly in the render body.
  useEffect(() => {
    setOutletContext = setCtx;
  }, []);
  return <Outlet context={{ ...(ctx as object), ...store }} />;
}

function renderResyncHarness(initial: unknown) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<ContextHolder initial={initial} />}>
          <Route index element={<GeneralTab />} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("GeneralTab", () => {
  it("renders grill name and theme fields with loaded values", () => {
    const context = {
      settings: {
        globals: {
          grill_name: "Backyard Smoker",
          page_theme: "dark",
        },
      },
      mode: "Stop",
    };

    renderRoute(<GeneralTab />, context);

    expect(screen.getByDisplayValue("Backyard Smoker")).toBeInTheDocument();
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.value).toBe("dark");
  });

  it("saves the edited grill name with empty flags when Save is clicked", async () => {
    const context = {
      settings: {
        globals: {
          grill_name: "Old Name",
          page_theme: "light",
        },
      },
      mode: "Stop",
    };

    renderRoute(<GeneralTab />, context);

    const nameInput = screen.getByDisplayValue("Old Name");
    fireEvent.change(nameInput, { target: { value: "New Name" } });

    const saveButton = screen.getByRole("button", { name: "Save" });
    fireEvent.click(saveButton);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(saveMock).toHaveBeenCalledWith(
      {
        globals: {
          grill_name: "New Name",
          page_theme: "light",
        },
        // Written unconditionally, like PwmTab's cross-section
        // startup.pwm_duty_cycle: this fixture has no `display`, so the
        // 300 default round-trips. Harmless, and it keeps onSave branch-free.
        display: {
          sleep_timeout: 300,
        },
      },
      [],
    );
  });

  // Ruling 4, 2026-07-26 (docs/superpowers/react-migration-backlog.md).
  // Flask put this field on its Display pane (settings/index.html:1080); the
  // React app has no Display tab, and General is where it belongs. It is not
  // decoration: the display process re-reads display.sleep_timeout once a
  // second (display/qtapp.py's _timeout_fn -> PiFireBackend.TIMEOUT ->
  // asleep -> ScreenPowerController's `swaymsg output * dpms off`), and the
  // web-to-display seam is pinned in tests/web/test_api_settings_update.py.
  it("renders the screen sleep timeout with the loaded value", () => {
    const context = {
      settings: {
        globals: { grill_name: "G", page_theme: "light" },
        display: { sleep_timeout: 45 },
      },
      mode: "Stop",
    };

    renderRoute(<GeneralTab />, context);

    expect(screen.getByDisplayValue("45")).toBeInTheDocument();
  });

  it("falls back to the 300s default when display.sleep_timeout is absent", () => {
    renderRoute(<GeneralTab />, { settings: { globals: { grill_name: "G" } }, mode: "Stop" });

    expect(screen.getByDisplayValue("300")).toBeInTheDocument();
  });

  it("saves an edited sleep timeout into the delta", async () => {
    const context = {
      settings: {
        globals: { grill_name: "G", page_theme: "light" },
        display: { sleep_timeout: 300 },
      },
      mode: "Stop",
    };

    renderRoute(<GeneralTab />, context);
    fireEvent.change(screen.getByDisplayValue("300"), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    const [delta, flags] = saveMock.mock.calls[0];
    expect(delta.display.sleep_timeout).toBe(60);
    // Flask's _settings_display does a bare write_settings with no control
    // flag; the display process polls the store itself.
    expect(flags).toEqual([]);
  });

  it("keeps 0 (never sleep) rather than treating it as empty", async () => {
    const context = {
      settings: {
        globals: { grill_name: "G", page_theme: "light" },
        display: { sleep_timeout: 300 },
      },
      mode: "Stop",
    };

    renderRoute(<GeneralTab />, context);
    fireEvent.change(screen.getByDisplayValue("300"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(saveMock.mock.calls[0][0].display.sleep_timeout).toBe(0);
  });

  it("resyncs displayed values when the settings object changes on re-render", () => {
    const first = {
      settings: {
        globals: {
          grill_name: "First Grill",
          page_theme: "light",
        },
      },
      mode: "Stop",
    };

    renderResyncHarness(first);
    expect(screen.getByDisplayValue("First Grill")).toBeInTheDocument();
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("light");

    const second = {
      settings: {
        globals: {
          grill_name: "Second Grill",
          page_theme: "dark",
        },
      },
      mode: "Stop",
    };

    act(() => {
      setOutletContext?.(second);
    });

    expect(screen.getByDisplayValue("Second Grill")).toBeInTheDocument();
    expect((screen.getByRole("combobox") as HTMLSelectElement).value).toBe("dark");
  });
});
