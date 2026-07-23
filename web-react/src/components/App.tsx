import { createBrowserRouter, Navigate, RouterProvider } from "react-router";
import { settingsLoader } from "../helpers/settings/settingsRoutes";
import { AppPrefsProvider } from "./AppPrefs";
import { DashboardRoute } from "./DashboardRoute";
import { SettingsError } from "./settings/SettingsError";
import { SettingsShell } from "./settings/SettingsShell";
import { ControllerTab } from "./settings/tabs/ControllerTab";
import { GeneralTab } from "./settings/tabs/GeneralTab";
import { HistoryTab } from "./settings/tabs/HistoryTab";
import { NotificationsTab } from "./settings/tabs/NotificationsTab";
import { PelletsTab } from "./settings/tabs/PelletsTab";
import { PwmTab } from "./settings/tabs/PwmTab";
import { SafetyTab } from "./settings/tabs/SafetyTab";
import { StartupTab } from "./settings/tabs/StartupTab";
import { UnitsTab } from "./settings/tabs/UnitsTab";
import { WorkModeTab } from "./settings/tabs/WorkModeTab";

// Rendered while the /settings route's loader runs on initial hydration —
// keeps react-router from warning "No HydrateFallback element provided".
export function HydrateFallback() {
  return <div className="pf-fit" />;
}

// Exported (not just used to build `router` below) so App.test.tsx can drive
// the same route tree through `createMemoryRouter` without a real browser
// history — structure-preserving, no behavior change.
export const routes = [
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
      { path: "controller", element: <ControllerTab /> },
      { path: "pwm", element: <PwmTab /> },
      { path: "startup", element: <StartupTab /> },
      { path: "safety", element: <SafetyTab /> },
      { path: "pellets", element: <PelletsTab /> },
      { path: "history", element: <HistoryTab /> },
      { path: "notifications", element: <NotificationsTab /> },
      { path: "units", element: <UnitsTab /> },
    ],
  },
];

const router = createBrowserRouter(routes);

export default function App() {
  return (
    <AppPrefsProvider>
      <RouterProvider router={router} />
    </AppPrefsProvider>
  );
}
