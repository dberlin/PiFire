import { Outlet } from "react-router";
import { useTimerVisibility } from "../../helpers/timer/timerVisibility";
import { useLiveState } from "../../helpers/useLiveState";
import { Banners } from "./Banners";
import { NavBar } from "./NavBar";
import { TimerBar } from "./TimerBar";
import "./shell.css";

// The app shell -- a layout route wrapping every ported page, ported from
// templates/base.html: the navbar, the timer strip, and the global alert strip
// that Flask renders on every page rather than only on the dashboard.
//
// It is also the app's ONE live-state subscriber. useLiveState() opens a
// socket.io connection per call, so calling it here and again in a page would
// open two sockets and double the `listen_app_data` stream. The whole bundle
// goes to the pages below on Outlet context, which they read through
// useShellState() (helpers/shellContext.ts).
//
// /wizard is deliberately NOT a child of this route (see App.tsx): a first-run
// install is a linear flow, and the navbar would invite the user to wander out
// of it half-configured.
export function AppShell() {
  const liveState = useLiveState();
  const { live, command } = liveState;

  const { visible, toggle } = useTimerVisibility(live.timer.start);
  // Same derivation the bar makes; the navbar only needs the yes/no, so it is
  // computed at render rather than lifted out of deriveTimer's richer result.
  const timerRunning = live.timer.start !== 0 && live.timer.paused === 0;

  return (
    <div className="pf-shell">
      <NavBar
        grillName={live.grillName}
        timerVisible={visible}
        timerRunning={timerRunning}
        onToggleTimer={toggle}
      />
      {visible ? <TimerBar timer={live.timer} command={command} /> : null}
      <Banners
        errors={live.errors ?? []}
        warnings={live.warnings ?? []}
        warningsMaxId={live.warningsMaxId ?? null}
        criticalError={live.criticalError}
      />
      <main className="pf-shell-main">
        <Outlet context={liveState} />
      </main>
    </div>
  );
}
