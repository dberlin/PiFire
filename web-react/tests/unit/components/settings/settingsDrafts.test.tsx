import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { AppPrefsProvider } from "../../../../src/components/AppPrefs";
import { SettingsShell } from "../../../../src/components/settings/SettingsShell";
import { PwmTab } from "../../../../src/components/settings/tabs/PwmTab";
import { SafetyTab } from "../../../../src/components/settings/tabs/SafetyTab";
import type {
  SettingsDrafts,
  useSettingsDraft,
} from "../../../../src/helpers/settings/settingsDrafts";
import type { EditableSettingsTabId } from "../../../../src/helpers/settings/settingsTabs";
import { testQueryClient } from "../../test-utils";

// Ruling 3, 2026-07-26 (docs/superpowers/backlogs/react-migration-backlog.md): a
// settings edit must survive a tab switch. Flask persisted every field the
// moment it changed; React's per-tab SaveBar is the deliberate replacement, so
// the edit has to be HELD until that Save -- across navigation included --
// rather than saved on the keystroke. Losing it silently is the one outcome
// the ruling rules out.
//
// This is the only test that drives tabs through the REAL SettingsShell rather
// than the `renderRoute` harness: the draft store lives on the shell, which is
// exactly what a per-tab test cannot see.

const SETTINGS = {
  globals: { units: "F" },
  platform: { dc_fan: true },
  pwm: {
    pwm_control: true,
    update_time: 10,
    min_duty_cycle: 20,
    max_duty_cycle: 100,
    frequency: 100,
    temp_range_list: [3, 7, 10, 15],
    profiles: [
      { duty_cycle: 20 },
      { duty_cycle: 35 },
      { duty_cycle: 50 },
      { duty_cycle: 75 },
      { duty_cycle: 100 },
    ],
  },
  safety: {
    minstartuptemp: 75,
    maxstartuptemp: 100,
    maxtemp: 500,
    reigniteretries: 1,
    startupcheck: true,
    allow_manual_changes: false,
    manual_override_time: 30,
  },
};

type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;

const draftStoreKeysMatchEditableTabs: Equal<keyof SettingsDrafts, EditableSettingsTabId> = true;
const draftHookKeyMatchesEditableTabs: Equal<
  Parameters<typeof useSettingsDraft>[0],
  EditableSettingsTabId
> = true;

function renderSettings() {
  let loaderCalls = 0;
  const router = createMemoryRouter(
    [
      {
        path: "/settings",
        element: <SettingsShell />,
        // A FRESH settings object per call, like the real loader: if react-router
        // re-ran this on every tab switch, an identity-based reset would wipe the
        // draft, and these tests would say so.
        loader: () => {
          loaderCalls += 1;
          return { settings: structuredClone(SETTINGS), mode: "Stop", controllerMeta: null };
        },
        children: [
          { path: "pwm", element: <PwmTab /> },
          { path: "safety", element: <SafetyTab /> },
        ],
      },
    ],
    { initialEntries: ["/settings/pwm"] },
  );
  render(
    <QueryClientProvider client={testQueryClient()}>
      <AppPrefsProvider>
        <RouterProvider router={router} />
      </AppPrefsProvider>
    </QueryClientProvider>,
  );
  return { router, loaderCalls: () => loaderCalls };
}

// The dirty marker changes the pill's accessible name, so match on the prefix.
const tab = (label: string) => screen.getByRole("link", { name: new RegExp(`^${label}`) });

// Field associates its <label> to the control via htmlFor/id, so
// getByLabelText resolves the input directly (same helper as PwmTab.test.tsx).
const inputFor = (label: string) => screen.getByLabelText(label) as HTMLInputElement;

beforeEach(() => {
  // Only the last test saves; the rest never reach the network.
  rs.stubGlobal(
    "fetch",
    rs.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ result: "success", message: "" }),
    }),
  );
});

afterEach(() => {
  rs.unstubAllGlobals();
  cleanup();
});

describe("settings drafts survive a tab switch", () => {
  it("keys the draft store and hook by exactly the editable tab identities", () => {
    expect(draftStoreKeysMatchEditableTabs).toBe(true);
    expect(draftHookKeyMatchesEditableTabs).toBe(true);
  });

  it("keeps an unsaved table edit when the user visits another tab and comes back", async () => {
    renderSettings();

    fireEvent.change(await screen.findByLabelText("Duty cycle row 3"), { target: { value: "60" } });
    expect(screen.getByLabelText("Duty cycle row 3")).toHaveValue(60);

    fireEvent.click(tab("Safety"));
    expect(await screen.findByText("Min Startup Temp")).toBeInTheDocument();
    fireEvent.click(tab("PWM Fan"));

    expect(await screen.findByLabelText("Duty cycle row 3")).toHaveValue(60);
  });

  it("keeps an unsaved scalar edit too, and does not touch the untouched fields", async () => {
    renderSettings();

    await screen.findByLabelText("Duty cycle row 3");
    fireEvent.change(inputFor("Frequency"), { target: { value: "25000" } });

    fireEvent.click(tab("Safety"));
    await screen.findByText("Min Startup Temp");
    fireEvent.click(tab("PWM Fan"));

    expect(await screen.findByDisplayValue("25000")).toBeInTheDocument();
    expect(screen.getByLabelText("Duty cycle row 3")).toHaveValue(50);
  });

  it("tells the user the edit is unsaved, on the tab pill and in the save bar", async () => {
    renderSettings();

    expect(screen.queryByText("Unsaved changes")).toBeNull();
    fireEvent.change(await screen.findByLabelText("Duty cycle row 3"), { target: { value: "60" } });

    // In the save bar, where the user is looking when they edit...
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();
    // ...and on the pill, which is the only thing still on screen once they
    // navigate away from the tab holding the edit.
    fireEvent.click(tab("Safety"));
    await screen.findByText("Min Startup Temp");
    expect(screen.getByRole("link", { name: /PWM Fan.*Unsaved changes/ })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /^Safety/ })).toHaveAccessibleName("Safety");
  });

  it("drops a draft the loader has caught up with, and keeps one it has not", async () => {
    const { router } = renderSettings();

    fireEvent.change(await screen.findByLabelText("Duty cycle row 3"), { target: { value: "60" } });
    // Revalidation with the edit still in flight (someone else's save, a
    // sibling loader, a poll): the user's typing must not vanish under them.
    await router.revalidate();
    expect(await screen.findByLabelText("Duty cycle row 3")).toHaveValue(60);
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument();

    // Once THIS tab's own save has landed, the draft is spent: the next
    // settings the loader delivers are authoritative.
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByText("Saved ✓")).toBeInTheDocument());
    await router.revalidate();

    // waitFor, not findBy: the input exists throughout, so findBy resolves on
    // the first tick and leaves the VALUE assertion unguarded -- and
    // `revalidate()` resolves when the loader settles, which is before React
    // has committed the render carrying its result. Retiring the draft is
    // exactly that render, so asserting straight after the await sampled the
    // pre-revalidation value about a quarter of the time.
    await waitFor(() => expect(screen.getByLabelText("Duty cycle row 3")).toHaveValue(50));
    expect(screen.queryByText("Unsaved changes")).toBeNull();
  });
});
