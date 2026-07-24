import { createBrowserRouter, Navigate, RouterProvider } from "react-router";
import { settingsLoader } from "../helpers/settings/settingsRoutes";
import { wizardLoader } from "../helpers/wizard/wizardRoutes";
import { AppPrefsProvider } from "./AppPrefs";
import { DashboardRoute } from "./DashboardRoute";
import { SettingsError } from "./settings/SettingsError";
import { SettingsShell } from "./settings/SettingsShell";
import { ControllerTab } from "./settings/tabs/ControllerTab";
import { GeneralTab } from "./settings/tabs/GeneralTab";
import { HistoryTab } from "./settings/tabs/HistoryTab";
import { NotificationsTab } from "./settings/tabs/NotificationsTab";
import { PelletsTab } from "./settings/tabs/PelletsTab";
import { PlatformTab } from "./settings/tabs/PlatformTab";
import { PwmTab } from "./settings/tabs/PwmTab";
import { SafetyTab } from "./settings/tabs/SafetyTab";
import { StartupTab } from "./settings/tabs/StartupTab";
import { UnitsTab } from "./settings/tabs/UnitsTab";
import { WorkModeTab } from "./settings/tabs/WorkModeTab";
import {
  WizardError,
  HydrateFallback as WizardHydrateFallback,
  WizardShell,
} from "./wizard/WizardShell";

// Rendered while the /settings route's loader runs on initial hydration —
// keeps react-router from warning "No HydrateFallback element provided".
export function HydrateFallback() {
  return <div className="pf-fit" />;
}

// Exported (not just used to build `router` below) so App.test.tsx can drive
// the same route tree through `createMemoryRouter` without a real browser
// history — structure-preserving, no behavior change.
// first_time_setup gate: Flask forces the wizard when GRILL_ID hasn't been
// set up yet. Wiring that as a redirect *from the index loader* was
// considered but rejected here: "/" (DashboardRoute) currently has no
// loader at all, and React Router's data routers always defer rendering
// until a route's loader resolves -- even a synchronous one resolves on a
// microtask (see SettingsShell.test.tsx's comment on this) -- so adding one
// would turn the dashboard's first paint into an async gap, breaking the
// existing synchronous assertions in App.test.tsx / DashboardRoute.test.tsx
// and adding an extra network round trip to every dashboard load. Per the
// task brief's escape hatch ("gate ONLY where it's safe and document the
// limitation"), the gate is intentionally NOT wired here. /wizard itself is
// always reachable directly; forcing navigation there on first-time setup is
// left as follow-up work (e.g. a non-blocking check inside DashboardRoute,
// or restructuring "/" to a loader-backed route with a HydrateFallback).
export const routes = [
  { path: "/", element: <DashboardRoute /> },
  {
    path: "/wizard",
    element: <WizardShell />,
    loader: wizardLoader,
    errorElement: <WizardError />,
    HydrateFallback: WizardHydrateFallback,
  },
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
      { path: "platform", element: <PlatformTab /> },
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
