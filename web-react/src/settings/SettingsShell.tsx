import { NavLink, Outlet, useLoaderData, useNavigate } from "react-router";
import type { Settings } from "./settingsApi";

const SETTINGS_TABS = [
  { path: "general", label: "General" },
  { path: "work-mode", label: "Work Mode" },
  { path: "pwm", label: "PWM Fan" },
  { path: "startup", label: "Startup / Shutdown" },
  { path: "safety", label: "Safety" },
  { path: "pellets", label: "Pellet Levels" },
  { path: "history", label: "History" },
  { path: "units", label: "Units" },
];

export function SettingsShell() {
  const { settings, mode } = useLoaderData() as { settings: Settings; mode: string };
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
        <Outlet context={{ settings, mode }} />
      </main>
    </div>
  );
}
