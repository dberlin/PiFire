import { NavLink, Outlet, useLoaderData, useNavigate } from "react-router";
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
];

export function SettingsShell() {
  const { settings, mode, controllerMeta } = useLoaderData() as {
    settings: Settings;
    mode: string;
    controllerMeta: ControllerMetadata | null;
  };
  const navigate = useNavigate();
  return (
    <div className="pf-settings">
      <aside className="pf-settings-nav">
        <button className="pf-settings-back" onClick={() => navigate("/")}>
          ← Dashboard
        </button>
        <div className="pf-settings-title">Settings</div>
        {SETTINGS_TABS.map((t) => (
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
