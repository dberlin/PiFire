import { useEffect, useRef } from "react";
import { NavLink, Outlet, useLoaderData, useNavigate } from "react-router";
import { readAccent } from "../../helpers/settings/accent";
import { hasDcFan } from "../../helpers/settings/platform";
import type { ControllerCatalog } from "../../helpers/settings/controllerTypes.gen";
import type { SettingsSchema } from "../../helpers/settings/settingsTypes.gen";
import { useSettingsDraftStore } from "../../helpers/settings/settingsDrafts";
import { useAppPrefs } from "../AppPrefs";

const SETTINGS_TABS = [
  { path: "general", label: "General" },
  { path: "work-mode", label: "Work Mode" },
  { path: "controller", label: "Controller" },
  { path: "pwm", label: "PWM Fan" },
  { path: "startup", label: "Startup / Shutdown" },
  { path: "safety", label: "Safety" },
  { path: "pellets", label: "Pellet Levels" },
  { path: "history", label: "History" },
  { path: "notifications", label: "Notifications" },
  { path: "units", label: "Units" },
  { path: "platform", label: "Platform" },
  // Last deliberately: it is the most destructive tab in the group, and `tabs`
  // below is a .filter() over this array, so order here is display order.
  { path: "probes", label: "Probes" },
];

export function SettingsShell() {
  const { settings, mode, controllerMeta } = useLoaderData() as {
    settings: SettingsSchema;
    mode: string;
    controllerMeta: ControllerCatalog | null;
  };
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
  // Only the PILL goes; the /settings/pwm route stays registered in App.tsx so
  // a bookmarked URL still resolves, and PwmTab explains why it is inert.
  const tabs = SETTINGS_TABS.filter((t) => t.path !== "pwm" || hasDcFan(settings));
  return (
    <div className="pf-settings">
      <aside className="pf-settings-nav">
        <button className="pf-settings-back" onClick={() => navigate("/")}>
          ← Dashboard
        </button>
        <div className="pf-settings-title">Settings</div>
        {tabs.map((t) => (
          <NavLink
            key={t.path}
            to={t.path}
            className={({ isActive }) => `pf-settings-link ${isActive ? "active" : ""}`}
          >
            {t.label}
            {/* The only thing still on screen once the user navigates away
                from the tab holding the edit -- without it, "preserved across
                the switch" would be indistinguishable from "discarded". */}
            {draftStore.drafts[t.path]?.saved === false && (
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
        <Outlet context={{ settings, mode, controllerMeta, ...draftStore }} />
      </main>
    </div>
  );
}
