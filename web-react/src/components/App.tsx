import { createBrowserRouter, Navigate, RouterProvider } from "react-router";
import { probeModulesLoader } from "../helpers/probes/probeMapRoutes";
import { settingsLoader } from "../helpers/settings/settingsRoutes";
import { wizardLoader } from "../helpers/wizard/wizardRoutes";
import { AppPrefsProvider } from "./AppPrefs";
import { AdminPage } from "./admin/AdminPage";
import { CookFilePage } from "./cookfiles/CookFilePage";
import { DashboardRoute } from "./DashboardRoute";
import { HistoryPage } from "./history/HistoryPage";
import { EventsPage } from "./logs/EventsPage";
import { MetricsPage } from "./metrics/MetricsPage";
import { PelletsPage } from "./pellets/PelletsPage";
import { RecipeList } from "./recipes/RecipeList";
import { RecipePage } from "./recipes/RecipePage";
import { SettingsError } from "./settings/SettingsError";
import { SettingsShell } from "./settings/SettingsShell";
import { ControllerTab } from "./settings/tabs/ControllerTab";
import { GeneralTab } from "./settings/tabs/GeneralTab";
import { HistoryTab } from "./settings/tabs/HistoryTab";
import { NotificationsTab } from "./settings/tabs/NotificationsTab";
import { PelletsTab } from "./settings/tabs/PelletsTab";
import { PlatformTab } from "./settings/tabs/PlatformTab";
import { ProbesTab } from "./settings/tabs/ProbesTab";
import { PwmTab } from "./settings/tabs/PwmTab";
import { SafetyTab } from "./settings/tabs/SafetyTab";
import { StartupTab } from "./settings/tabs/StartupTab";
import { UnitsTab } from "./settings/tabs/UnitsTab";
import { WorkModeTab } from "./settings/tabs/WorkModeTab";
import { AppShell } from "./shell/AppShell";
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
// and adding an extra network round trip to every dashboard load. The gate is
// instead a non-blocking post-mount check inside DashboardRoute: it fetches
// settings once after mount and navigates to /wizard only on a fresh install,
// so "/" keeps its synchronous first paint and a failed check stays advisory.
export const routes = [
  // Pathless layout route: AppShell renders the navbar, the timer strip and the
  // global alert strip around whichever page below it matched, and owns the
  // single useLiveState() subscription that those pages read off Outlet
  // context. Nesting a page here is what makes it reachable by clicking.
  {
    element: <AppShell />,
    children: [
      { path: "/", element: <DashboardRoute /> },
      // The cook-history chart page. NOT to be confused with the
      // /settings/history child route below, which is the History *settings* tab.
      { path: "/history", element: <HistoryPage /> },
      // The detail view for ONE saved cook file. The list that links here is a
      // section of /history, mirroring Flask, where the cook-file list lives on
      // the history page and "Open" posts through to cookfile/index.html.
      { path: "/cookfiles/:filename", element: <CookFilePage /> },
      // Per-mode metrics for the cooks in the history store. Reached from
      // /history, matching Flask, where templates/base.html's navbar has no
      // Metrics entry and history/index.html:47 is the only link in. No
      // loader: the page reads on mount so a failed read is retryable in place.
      { path: "/metrics", element: <MetricsPage /> },
      // The recipe browser and its detail view, mirroring the cook-file pair
      // above -- a recipe is authored rather than produced by cooking, which
      // is the one deliberate difference in RecipeList (a "New Recipe" create
      // button with no cook-file equivalent).
      { path: "/recipes", element: <RecipeList /> },
      { path: "/recipes/:filename", element: <RecipePage /> },
      // The pellet INVENTORY manager (brands, woods, profiles, current load,
      // log). NOT to be confused with /settings/pellets below, which is the
      // pellet-LEVEL settings tab (thresholds, auger rate, prime ignition).
      { path: "/pellets", element: <PelletsPage /> },
      // System maintenance: reboot/shutdown/restart, the clears, backups and
      // logs. No loader -- GET /api/admin/state probes the platform and writes
      // the readings back into control, so it belongs behind a mount effect the
      // page can retry, not in front of the first paint.
      { path: "/admin", element: <AdminPage /> },
      // The event feed and the log-file browser. No loader: the page reads its
      // own families and log text, and a loader would hold the shell on a file
      // read that is measured in megabytes on a grill that has been running.
      { path: "/events", element: <EventsPage /> },
      {
        path: "/settings",
        element: <SettingsShell />,
        loader: settingsLoader,
        errorElement: <SettingsError />,
        // Still honoured under the layout route: react-router renders the
        // fallback of the DEEPEST match that declares one, so the shell keeps
        // painting while the settings loader runs.
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
          // The only settings child with its own loader: the probes MODULE
          // MANIFEST (18 entries with per-field config metadata and vendor
          // photos) is nothing settingsLoader fetches, and inlining it there
          // would make every other tab pay for it. Running as a sibling loader
          // also means useSaveSettings' revalidate() refreshes both.
          { path: "probes", element: <ProbesTab />, loader: probeModulesLoader },
        ],
      },
    ],
  },
  // Deliberately OUTSIDE the shell. A first-run install is a linear flow, and
  // a navbar would offer a half-configured user a way to wander out of it. It
  // follows that the wizard opens no live socket either, which it has no use
  // for: nothing is running yet.
  {
    path: "/wizard",
    element: <WizardShell />,
    loader: wizardLoader,
    errorElement: <WizardError />,
    HydrateFallback: WizardHydrateFallback,
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
