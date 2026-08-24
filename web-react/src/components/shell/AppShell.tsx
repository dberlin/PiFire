import {
  projectProbeHealthList,
  summarizeProbeHealth,
} from "@pifire/core/dashboard/probeHealth";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";
import { Outlet } from "react-router";
import { normalizeApiBase, queryKeys } from "../../helpers/query/keys";
import { useTimerVisibility } from "../../helpers/timer/timerVisibility";
import { useLiveState } from "../../helpers/useLiveState";
import { Banners } from "./Banners";
import { NavBar } from "./NavBar";
import { TimerBar } from "./TimerBar";
import "./shell.css";

const BASE_URL = normalizeApiBase(import.meta.env.PUBLIC_PIFIRE_URL || "");

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

  const probeHealthSummary = useMemo(
    () =>
      summarizeProbeHealth(
        projectProbeHealthList(live.thermocoupleHealth).filter(
          (health) => health.state === "confirmed",
        ),
      ),
    [live.thermocoupleHealth],
  );
  const { visible, toggle } = useTimerVisibility(live.timer.start);
  // Same derivation the bar makes; the navbar only needs the yes/no, so it is
  // computed at render rather than lifted out of deriveTimer's richer result.
  const timerRunning = live.timer.start !== 0 && live.timer.paused === 0;

  // uiHash moves when set_probe_map() runs anywhere -- another client, the
  // wizard, discovery -- and that rebuilds settings the React app already
  // cached: probe_config, recipe.probe_map, dashboard hidden_cards and
  // control.notify_data. The cards themselves stay live off the socket, so
  // this only needs to drop the stale settings cache, not reload the page.
  const queryClient = useQueryClient();
  const seenUiHash = useRef<number | null>(null);
  useEffect(() => {
    const next = live.uiHash;
    if (next === undefined) return;
    if (seenUiHash.current === null) {
      seenUiHash.current = next; // first frame seeds; it is not a change
      return;
    }
    if (seenUiHash.current === next) return;
    seenUiHash.current = next;
    queryClient.invalidateQueries({ queryKey: queryKeys.settings(BASE_URL) });
  }, [live.uiHash, queryClient]);

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
        probeHealthSummary={probeHealthSummary}
        healthLastReported={liveState.phase === "unreachable"}
      />
      <main className="pf-shell-main">
        <Outlet context={liveState} />
      </main>
    </div>
  );
}
