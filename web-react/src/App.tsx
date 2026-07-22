import { useState } from "react";
import { useDashData } from "./useDashData";
import { ConnectionStatus } from "./components/ConnectionStatus";
import { Dashboard } from "./dashboard/Dashboard";
import type { AccentName } from "./types";

export default function App() {
  const { dash, phase, controlAlive, targetUrl, command } = useDashData();
  const [accent, setAccent] = useState<AccentName>("ember");
  const [animate, setAnimate] = useState(true);

  // Drive the CSS token set (mirrors QML backend.accentTheme).
  document.documentElement.setAttribute("data-accent", accent);

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
