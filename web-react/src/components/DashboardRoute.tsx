import { useEffect } from "react";
import { useNavigate } from "react-router";
import { getSettings } from "../helpers/settings/settingsApi";
import { useLiveState } from "../helpers/useLiveState";
import { useAppPrefs } from "./AppPrefs";
import { ConnectionStatus } from "./ConnectionStatus";
import { Dashboard } from "./dashboard/Dashboard";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export function DashboardRoute() {
  const { live, phase, controlAlive, targetUrl, command } = useLiveState();
  const { accent, setAccent, animate, setAnimate } = useAppPrefs();
  const navigate = useNavigate();

  // Non-blocking first_time_setup gate. "/" deliberately has NO route loader
  // (see App.tsx): React Router defers rendering until a loader resolves --
  // even a synchronous one resolves on a microtask -- so a loader here would
  // turn the dashboard's first paint into an async gap. Instead we check once
  // after mount and redirect a fresh install to the wizard. A brief dashboard
  // flash before the redirect is the accepted tradeoff; a failed check is
  // advisory and must never block the dashboard.
  useEffect(() => {
    let cancelled = false;
    getSettings(BASE_URL)
      .then((s) => {
        if (!cancelled && s.globals?.first_time_setup) navigate("/wizard");
      })
      .catch(() => {
        /* advisory only -- never block the dashboard on this check */
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  if (phase !== "live" && phase !== "demo") {
    return (
      <div className="pf-fit">
        <ConnectionStatus phase={phase} targetUrl={targetUrl} />
      </div>
    );
  }
  return (
    <Dashboard
      dash={live}
      command={command}
      phase={phase}
      controlAlive={controlAlive}
      accent={accent}
      setAccent={setAccent}
      animate={animate}
      setAnimate={setAnimate}
    />
  );
}
