import { useAppPrefs } from "./AppPrefs";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { Dashboard } from "./dashboard/Dashboard";
import { useDashData } from "./useDashData";

export function DashboardRoute() {
  const { dash, phase, controlAlive, targetUrl, command } = useDashData();
  const { accent, setAccent, animate, setAnimate } = useAppPrefs();
  if (phase !== "live" && phase !== "demo") {
    return (
      <div className="pf-fit">
        <ConnectionStatus phase={phase} targetUrl={targetUrl} />
      </div>
    );
  }
  return (
    <Dashboard
      dash={dash}
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
