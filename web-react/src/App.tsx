import { createBrowserRouter, RouterProvider, Navigate } from "react-router";
import { AppPrefsProvider } from "./AppPrefs";
import { DashboardRoute } from "./DashboardRoute";
import { SettingsShell } from "./settings/SettingsShell";
import { SettingsError } from "./settings/SettingsError";
import { settingsLoader } from "./settings/settingsRoutes";
import { GeneralTab } from "./settings/tabs/GeneralTab";
import { WorkModeTab } from "./settings/tabs/WorkModeTab";
import { PwmTab } from "./settings/tabs/PwmTab";
import { StartupTab } from "./settings/tabs/StartupTab";
import { SafetyTab } from "./settings/tabs/SafetyTab";
import { PelletsTab } from "./settings/tabs/PelletsTab";
import { HistoryTab } from "./settings/tabs/HistoryTab";
import { UnitsTab } from "./settings/tabs/UnitsTab";

// Rendered while the /settings route's loader runs on initial hydration —
// keeps react-router from warning "No HydrateFallback element provided".
export function HydrateFallback() {
  return <div className="pf-fit" />;
}

const router = createBrowserRouter([
  { path: "/", element: <DashboardRoute /> },
  {
    path: "/settings",
    element: <SettingsShell />,
    loader: settingsLoader,
    errorElement: <SettingsError />,
    HydrateFallback,
    children: [
      { index: true, element: <Navigate to="general" replace /> },
      { path: "general", element: <GeneralTab /> },
      { path: "work-mode", element: <WorkModeTab /> },
      { path: "pwm", element: <PwmTab /> },
      { path: "startup", element: <StartupTab /> },
      { path: "safety", element: <SafetyTab /> },
      { path: "pellets", element: <PelletsTab /> },
      { path: "history", element: <HistoryTab /> },
      { path: "units", element: <UnitsTab /> },
    ],
  },
]);

export default function App() {
  return (
    <AppPrefsProvider>
      <RouterProvider router={router} />
    </AppPrefsProvider>
  );
}
