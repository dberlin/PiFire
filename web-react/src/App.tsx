import { createBrowserRouter, RouterProvider, Navigate } from "react-router";
import { AppPrefsProvider } from "./AppPrefs";
import { DashboardRoute } from "./DashboardRoute";
import { SettingsShell } from "./settings/SettingsShell";
import { SettingsError } from "./settings/SettingsError";
import { settingsLoader } from "./settings/settingsRoutes";
import { GeneralTab } from "./settings/tabs/GeneralTab";
import { PwmTab } from "./settings/tabs/PwmTab";
import { UnitsTab } from "./settings/tabs/UnitsTab";

const router = createBrowserRouter([
  { path: "/", element: <DashboardRoute /> },
  {
    path: "/settings",
    element: <SettingsShell />,
    loader: settingsLoader,
    errorElement: <SettingsError />,
    children: [
      { index: true, element: <Navigate to="general" replace /> },
      { path: "general", element: <GeneralTab /> },
      { path: "pwm", element: <PwmTab /> },
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
