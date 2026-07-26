import { NavLink, Outlet, useLoaderData, useNavigate } from "react-router";
import { hasDcFan } from "../../helpers/settings/platform";
import type { ControllerMetadata, Settings } from "../../helpers/settings/settingsApi";

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
    settings: Settings;
    mode: string;
    controllerMeta: ControllerMetadata | null;
  };
  const navigate = useNavigate();
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
          </NavLink>
        ))}
      </aside>
      <main className="pf-settings-content">
        <Outlet context={{ settings, mode, controllerMeta }} />
      </main>
    </div>
  );
}
