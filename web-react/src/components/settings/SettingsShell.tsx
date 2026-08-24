import type { ControllerCatalog } from "@pifire/core/settings/controllerTypes";
import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { useEffect, useRef } from "react";
import { NavLink, Outlet, useLoaderData, useNavigate, useOutletContext } from "react-router";
import { readAccent } from "../../helpers/settings/accent";
import { hasDcFan } from "../../helpers/settings/platform";
import { useSettingsDraftStore } from "../../helpers/settings/settingsDrafts";
import { SETTINGS_TABS } from "../../helpers/settings/settingsTabs";
import type { ShellContext } from "../../helpers/shellContext";
import { useAppPrefs } from "../AppPrefs";

export function SettingsShell() {
  const { settings, mode, controllerMeta } = useLoaderData() as {
    settings: SettingsSchema;
    mode: string;
    controllerMeta: ControllerCatalog | null;
  };
  const shell = useOutletContext<ShellContext | null>();
  const navigate = useNavigate();
  // A deep link into /settings never passes the dashboard, so this is the other
  // place the stored accent has to be picked up from.
  //
  // Seeded ONCE per mount, not on every `settings` identity change. GeneralTab
  // applies a picked theme live before it is saved (GeneralTab.tsx:68-73), and
  // saving any OTHER tab calls revalidate() -- which hands this shell a fresh
  // `settings` object. Re-seeding on that would snap the preview back to the
  // stored accent while the General draft still holds the picked one.
  //
  // Guarded with a ref rather than AppPrefs' render-phase `seeded` state: that
  // idiom is only legal on a component's OWN state, and `setAccent` belongs to
  // the provider above (AppPrefs.tsx:26-29).
  const { setAccent } = useAppPrefs();
  const seeded = useRef(false);
  useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    setAccent(readAccent(settings));
  }, [settings, setAccent]);
  // The shell outlives the tabs, so it is where an in-progress edit waits while
  // the user is on another pill (helpers/settings/settingsDrafts.ts).
  const draftStore = useSettingsDraftStore(settings);
  // Flask hides the PWM pill on an AC-fan build (settings/index.html:63-65).
  // Only the PILL goes; the /settings/pwm route stays registered so a
  // bookmarked URL still resolves, and PwmTab explains why it is inert.
  const tabs = SETTINGS_TABS.filter(
    ({ hideWithoutDcFan }) => !hideWithoutDcFan || hasDcFan(settings),
  );
  return (
    <div className="pf-settings">
      <aside className="pf-settings-nav">
        <button className="pf-settings-back" onClick={() => navigate("/")}>
          ← Dashboard
        </button>
        <div className="pf-settings-title">Settings</div>
        {tabs.map((tab) => (
          <NavLink
            key={tab.id}
            to={tab.id}
            className={({ isActive }) => `pf-settings-link ${isActive ? "active" : ""}`}
          >
            {tab.label}
            {/* The only thing still on screen once the user navigates away
                from the tab holding the edit -- without it, "preserved across
                the switch" would be indistinguishable from "discarded". */}
            {tab.editable && draftStore.drafts[tab.id]?.saved === false && (
              <span
                className="pf-settings-unsaved"
                role="img"
                aria-label="Unsaved changes"
                title="Unsaved changes"
              >
                •
              </span>
            )}
          </NavLink>
        ))}
      </aside>
      <main className="pf-settings-content">
        <Outlet
          context={{
            settings,
            mode,
            controllerMeta,
            thermocoupleHealth: shell?.live.thermocoupleHealth ?? [],
            ...draftStore,
          }}
        />
      </main>
    </div>
  );
}
