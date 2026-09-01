import { useEffect } from "react";
import { useNavigate } from "react-router";

import { normalizeApiBase } from "../helpers/query/keys";
import { useSettings } from "../helpers/settings/useSettings";
import { useShellState } from "../helpers/shellContext";
import { useAppPrefs } from "./AppPrefs";
import { ConnectionStatus } from "./ConnectionStatus";
import { Dashboard } from "./dashboard/Dashboard";

const BASE_URL = normalizeApiBase(import.meta.env.PUBLIC_PIFIRE_URL || "");

export function DashboardRoute() {
  // Takes the shell's subscription rather than calling useLiveState() itself:
  // that hook opens a socket per call, and AppShell already holds the one this
  // page's data arrives on. See helpers/shellContext.ts.
  const { live, phase, controlAlive, targetUrl, command } = useShellState();
  const { accent, setAccent, animate, setAnimate } = useAppPrefs();
  const navigate = useNavigate();

  // Non-blocking first_time_setup gate. "/" deliberately has NO route loader
  // (see App.tsx): React Router defers rendering until a loader resolves --
  // even a synchronous one resolves on a microtask -- so a loader here would
  // turn the dashboard's first paint into an async gap. A brief dashboard
  // flash before the redirect is the accepted tradeoff.
  //
  // The read behind this is now the app's shared settings entry
  // (helpers/settings/useSettings.ts), which AppPrefsProvider has usually
  // already primed, so the gate costs no request of its own. A failed read
  // leaves `data` undefined and the gate simply does not fire -- the same
  // advisory, fail-quiet behaviour it always had.
  const { data: settings } = useSettings(BASE_URL);
  const firstTime = settings?.globals?.first_time_setup === true;
  useEffect(() => {
    if (firstTime) navigate("/wizard");
  }, [firstTime, navigate]);

  if (phase !== "live" && phase !== "demo") {
    return (
      <div className="pf-fit">
        {/* The human-readable origin, which is what this renders for the user
            to read -- deliberately not BASE_URL, which is empty in dev. */}
        <ConnectionStatus phase={phase} targetUrl={targetUrl} />
      </div>
    );
  }
  return (
    <Dashboard
      dash={live}
      command={command}
      // The dashboard's settings query and direct REST calls share this API
      // origin; CommandClient owns only command writes.
      apiBase={BASE_URL}
      phase={phase}
      controlAlive={controlAlive}
      accent={accent}
      setAccent={setAccent}
      animate={animate}
      setAnimate={setAnimate}
    />
  );
}
