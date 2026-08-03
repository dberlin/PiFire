import { type CSSProperties, useState } from "react";
import { useNavigate } from "react-router";
import type { CommandClient } from "../../helpers/command";
import { useControlHealth } from "../../helpers/dashboard/controlHealth";
import { cookElapsed, fmtElapsed } from "../../helpers/dashboard/cookTime";
import { lidCountdown, modeCountdown, recipeLabel } from "../../helpers/dashboard/countdowns";
import { deriveView, type PillView, reading } from "../../helpers/dashboard/deriveView";
import { useClock, useFitScale } from "../../helpers/dashboard/hooks";
import { type NotifyEdit, readNotifyEdit, saveNotifyEdit } from "../../helpers/notify/notifyState";
import { saveAccent } from "../../helpers/settings/accent";
import type { AccentName, LiveState, ProbeData } from "../../helpers/types";
import type { ConnectionPhase } from "../../helpers/useLiveState";
import { ActionMenu, type MenuItem } from "./ActionMenu";
import { ControlButtons } from "./ControlButtons";
import { GrillGauge } from "./GrillGauge";
import { HopperGauge } from "./HopperGauge";
import { NotifyBell } from "./NotifyBell";
import { ProbeCard } from "./ProbeCard";
import { ProbeNotifyModal } from "./ProbeNotifyModal";
import { SystemStatus } from "./SystemStatus";

const ACCENTS: AccentName[] = ["ember", "ice", "crimson"];
// The picker paints all three at once, so it cannot use --accent (which tracks
// the CURRENT selection); theme.css keeps the three Theme.accentColor branches
// as constants for exactly this.
const SWATCH: Record<AccentName, string> = {
  ember: "var(--accent-ember)",
  ice: "var(--accent-ice)",
  crimson: "var(--accent-crimson)",
};

// Flask's P-Mode dropup: ten items, 0 labelled "0 - Off"
// (_macro_dash_default.html:95-104).
const PMODE_ITEMS: MenuItem[] = Array.from({ length: 10 }, (_, n) => ({
  label: n === 0 ? "0 - Off" : String(n),
  value: String(n),
}));

interface DashboardProps {
  dash: LiveState;
  command: CommandClient;
  /** Base URL for the REST writes that do not go through CommandClient --
   *  currently just the notify round trip, which needs a GET as well as a POST.
   *  This is the API base (empty in dev, so requests stay same-origin and the
   *  dev proxy forwards them), NOT the human-readable target shown by
   *  ConnectionStatus -- those differ, and fetching the display string sends
   *  the browser cross-origin. */
  apiBase: string;
  phase: ConnectionPhase;
  controlAlive: boolean;
  accent: AccentName;
  setAccent: (a: AccentName) => void;
  animate: boolean;
  setAnimate: (v: boolean) => void;
}

// The PiFire controller dashboard (port of PiFire Dashboard.dc.html), driven by
// the live socket_dash_data contract.
//
// Two layouts, split at 1280px. Below it the dashboard REFLOWS: columns stack,
// the control row wraps, type is re-sized per element. That is the whole point
// of C8 -- the old build scaled the board uniformly at every size, so the
// 800x480 on-device panel rendered 66px probe temperatures at about 20px and a
// phone at about 14px.
//
// At 1280px and up it keeps the original fixed board scaled to fit, because
// the reference resolution has to look unchanged and an unscaled 720px board
// does not fit under the shell's navbar in a 720px window.
export function Dashboard({
  dash,
  command,
  apiBase,
  phase,
  controlAlive,
  accent,
  setAccent,
  animate,
  setAnimate,
}: DashboardProps) {
  const view = deriveView(dash);
  const navigate = useNavigate();
  const now = useClock();
  const health = useControlHealth(controlAlive, apiBase);
  // Desktop only. Below 1280px this is inert and the breakpoints in
  // dashboard.css do the work; at 1280px and up the board is fixed and scaled,
  // which is what keeps a literal 1280x720 window from clipping the control
  // row off the bottom. `fitRef` goes on .pf-dash-root -- inside the app shell
  // that box is the area left under the navbar, not the whole viewport.
  const { scale, fitted, ref: fitRef } = useFitScale(1280, 720);

  // Elapsed cook time comes from the CONTROLLER's startup_timestamp
  // (blueprints/mobile/socket_io.py:234, epoch seconds), not from when this
  // browser happened to mount. Reloading four hours into a brisket used to
  // report 00:00, and two devices watching the same cook disagreed with each
  // other. Reignite deliberately does not rewrite the timestamp
  // (controller/runtime/modes/reignite.py:17-18), so a reignited cook keeps
  // counting from the original ignition -- which is what Flask has always done.
  const cookTime = fmtElapsed(cookElapsed(dash.startupTimestamp, now.getTime() / 1000));
  const clock = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  // Status readouts Flask carries and this port had dropped. All three render
  // INSIDE existing boxes -- no new rows -- so the 1280x720 geometry is
  // unchanged when they are absent, which is every frame in the demo fixture.
  const nowSeconds = now.getTime() / 1000;
  const modeLeft = modeCountdown(dash, nowSeconds);
  const lidLeft = lidCountdown(dash, nowSeconds);
  // A running recipe replaces the gauge's mode badge outright, matching Flask's
  // "Recipe | <step mode>" status header (dash_default.js:297-300).
  const modeLabel = recipeLabel(dash) ?? view.modeLabel;

  // Per-probe notifications: the target and both limit alerts. Only the LABEL of
  // the probe being edited is held here; the probe itself is re-resolved from
  // `dash` on every render, so the modal keeps tracking the socket payload while
  // it is open and nothing is mirrored locally. That matters because the backend
  // edits these entries by itself -- it clears req/target/eta the moment a target
  // is reached and flips a limit's `triggered` as the temperature crosses
  // (notify/notifications.py:109-117) -- so a local copy would fight it.
  const [pModeOpen, setPModeOpen] = useState(false);

  const [notifyLabel, setNotifyLabel] = useState<string | null>(null);
  const [notifySaving, setNotifySaving] = useState(false);
  const [notifyError, setNotifyError] = useState<string | null>(null);
  const notifyProbe: ProbeData | null =
    notifyLabel === null
      ? null
      : dash.primaryProbe.label === notifyLabel
        ? dash.primaryProbe
        : (dash.foodProbes?.find((f) => f.label === notifyLabel) ?? null);

  const openNotify = (label: string) => {
    setNotifyError(null);
    setNotifyLabel(label);
  };
  const submitNotify = async (edit: NotifyEdit) => {
    if (notifyLabel === null || notifyProbe === null) return;
    setNotifySaving(true);
    setNotifyError(null);
    try {
      // The probe's reading goes with the write: it is what pre-arms each
      // limit's `triggered` latch, so an alert saved while the temperature is
      // already out of range stays quiet until it leaves and comes back. When
      // the probe has no current reading this is the same carried-over value
      // its card is showing, which is what the operator set the limit against.
      await saveNotifyEdit(
        apiBase,
        notifyLabel,
        edit,
        reading(notifyProbe.temp, notifyProbe.status).shown ?? 0,
      );
      setNotifyLabel(null);
    } catch (e) {
      // Stay open on failure. This write is not echoed back until the control
      // loop drains the queue (~110 ms), so closing on an error would be
      // indistinguishable from a save that worked.
      setNotifyError(e instanceof Error ? e.message : "could not save notification");
    } finally {
      setNotifySaving(false);
    }
  };

  return (
    <div className="pf-dash-root" ref={fitRef}>
      <div
        className="pf-dash"
        data-pf="stage"
        data-animate={animate ? "true" : "false"}
        // Always paired with the CSS above: the board is pinned at top/left 50%
        // there, so the translate is what centres it and the scale is what fits
        // it. Absent entirely on the reflow branch.
        style={fitted ? { transform: `translate(-50%, -50%) scale(${scale})` } : undefined}
      >
        <div className="pf-dash-glow" />

        {/* The error/warning banners used to float here, over the stage. They
            are now shell chrome (components/shell/Banners.tsx) shown above
            every page, matching Flask, which renders them from base.html. */}

        {/* Header */}
        <div data-pf="header" className="pf-dash-header">
          <div data-pf="brand" className="pf-dash-brand">
            <div
              className="pf-dash-dot"
              style={{ "--pf-live-color": view.liveColor } as CSSProperties}
            />
            <span className="pf-dash-wordmark">
              Pi<span className="text-accent">Fire</span>
            </span>
            <span className="pf-dash-grillname">{dash.grillName}</span>
          </div>
          <div className="pf-dash-headerright">
            <span
              data-pf="status"
              className="pf-dash-status"
              style={
                {
                  "--pf-status-color":
                    phase === "demo"
                      ? "var(--label)"
                      : health.alive
                        ? "var(--ok)"
                        : "var(--danger)",
                } as CSSProperties
              }
            >
              {phase === "demo" ? "DEMO" : health.alive ? "LIVE" : "CTRL OFFLINE"}
            </span>
            {/* The offline signal is a blob nothing can clear (see
                helpers/dashboard/controlHealth.ts), so offer to ask the control
                process directly rather than leaving the user staring at a
                verdict from up to 30 seconds ago. Not rendered in demo mode:
                there is no backend to ask. */}
            {!health.alive && phase !== "demo" && (
              <button className="pf-toggle" onClick={health.recheck} disabled={health.rechecking}>
                Recheck
              </button>
            )}
            <span data-pf="clock" className="pf-dash-clock">
              {clock}
            </span>
            <div className="pf-dash-tools">
              {ACCENTS.map((a) => (
                <button
                  key={a}
                  className={`pf-swatch ${accent === a ? "sel" : ""}`}
                  style={{ background: SWATCH[a] }}
                  // Applied locally first so the swatch responds at once, then
                  // persisted for the next load and for the attached screen.
                  onClick={() => {
                    setAccent(a);
                    void saveAccent(apiBase, a);
                  }}
                  aria-label={a}
                />
              ))}
              <button
                className={`pf-toggle ${animate ? "on" : ""}`}
                onClick={() => setAnimate(!animate)}
              >
                ANIM
              </button>
              <button
                className="pf-toggle"
                onClick={() => navigate("/settings")}
                aria-label="settings"
              >
                ⚙
              </button>
            </div>
          </div>
        </div>

        {/* Body */}
        <div data-pf="body" className="pf-dash-body">
          {/* Left: food probes */}
          {view.hasProbes && (
            <div data-pf="probeCol" className="pf-dash-probecol">
              <div data-pf="probeColTitle" className="pf-dash-caption pf-dash-coltitle">
                Food Probes
              </div>
              {view.probes.map((p, i) => (
                <ProbeCard key={`${p.name}-${i}`} p={p} onOpenNotify={openNotify} />
              ))}
            </div>
          )}

          {/* Center: gauge + cook time + controls */}
          <div data-pf="centerCol" className="pf-dash-centercol">
            <GrillGauge
              temp={view.tempInt}
              stale={view.stale}
              setpoint={dash.primaryProbe.setTemp}
              maxTemp={view.maxTemp}
              frac={view.gaugeFrac}
              hasSetpoint={view.hasSetpoint}
              modeLabel={modeLabel}
              units={view.units}
              cooking={view.cooking}
              animate={animate}
            />

            <div data-pf="cookRow" className="pf-dash-cookrow">
              <div data-pf="cookCard" className="pf-dash-card pf-dash-cookcard">
                <div className="pf-dash-cooklabels">
                  <span className="pf-dash-cookcaption">Cook Time</span>
                  {/* Flask's literal string, `s` suffix included
                      (_macro_dash_default.html:373). No MM:SS -- only the
                      integer is injected there. Rendered as a second line in
                      this card's existing label column so the 52px row keeps
                      its height. */}
                  {modeLeft !== null && (
                    <span className="pf-dash-modeleft">Time Left in Mode: {modeLeft}s</span>
                  )}
                </div>
                <span className="pf-dash-cookval">{cookTime}</span>
              </div>
              {/* The primary probe gets a bell too: the Flask dashboard renders
                  the notify modal for probe_status['P'] as well as ['F']
                  (dash_default.html:36,53), so a target on the grill probe is
                  not a food-probe-only feature. The gauge column has no card
                  header to hang it on, so it sits beside the Cook Time card. */}
              <div className="pf-dash-card pf-dash-bellbox">
                <NotifyBell
                  probeName={dash.primaryProbe.title}
                  // Any of the three entries, not just the target -- the modal
                  // behind this bell edits all three (deriveView.ts:133-137).
                  on={dash.primaryProbe.hasNotifications}
                  onClick={() => openNotify(dash.primaryProbe.label)}
                />
              </div>
              {view.lidOpen && (
                <div className="pf-dash-lid">
                  <span className="pf-dash-lid-title">LID OPEN</span>
                  {/* Flask: "Lid Open Detected: PID Paused Ns"
                      (dash_default.js:397). Two lines inside the SAME 210x52
                      box -- the box is not widened. */}
                  {lidLeft !== null && (
                    <span className="pf-dash-lid-sub">PID Paused {lidLeft}s</span>
                  )}
                </div>
              )}
            </div>

            <ControlButtons
              dash={dash}
              command={command}
              disabled={!health.alive}
              apiBase={apiBase}
            />
          </div>

          {/* Right: system + pills + hopper */}
          <div data-pf="rightCol" className="pf-dash-rightcol">
            <SystemStatus
              fan={view.fan}
              auger={view.auger}
              igniter={view.igniter}
              animate={animate}
            />
            <div data-pf="pills" className="pf-dash-pills">
              <Pill
                p={view.pillL}
                onClick={view.pModeEditable ? () => setPModeOpen(true) : undefined}
              />
              <Pill p={view.pillR} />
            </div>
            {/* Flask hides the whole hopper card when settings.modules.dist is
                "none" (_macro_dash_default.html:416-420), which is exactly what
                hasDistanceSensor is on the wire (socket_io.py:270). That field
                had zero consumers, so React showed a pellet gauge -- reading a
                hard-coded level -- on grills with no sensor at all. */}
            {dash.hasDistanceSensor && <HopperGauge h={view.hopper} />}
          </div>
        </div>

        {/* Inside the stage: .pf-modal-scrim is position:absolute, so it covers
            the 1280x720 board rather than the whole viewport. */}
        {/* One POST, not Flask's two chained ones: /api/set/pmode/{n} writes
            cycle_data.PMode AND sets control["settings_update"] in one merge,
            and range-checks 0 <= n < 10 server-side
            (common/api_commands.py:377-383). */}
        <ActionMenu
          open={pModeOpen}
          title="P-Mode"
          items={PMODE_ITEMS}
          onCancel={() => setPModeOpen(false)}
          onPick={(value) => {
            setPModeOpen(false);
            void command.setPMode(Number(value));
          }}
        />
        {notifyProbe !== null && (
          <ProbeNotifyModal
            open
            probeName={notifyProbe.title}
            isPrimary={notifyProbe.label === dash.primaryProbe.label}
            units={view.units}
            initial={readNotifyEdit(notifyProbe)}
            saving={notifySaving}
            error={notifyError}
            onSubmit={submitNotify}
            onCancel={() => setNotifyLabel(null)}
          />
        )}
      </div>
    </div>
  );
}

function Pill({ p, onClick }: { p: PillView; onClick?: () => void }) {
  // The <button> and <div> variants share one class, so they measure
  // identically -- which is exactly what the 1280x720 fidelity gate checks. The
  // button reset (font/padding/margin/appearance) lives in .pf-dash-pill.
  const vars = {
    "--pf-pill-bg": p.bg,
    "--pf-pill-border": p.border,
    "--pf-pill-label-color": p.labelColor,
    "--pf-pill-val-color": p.valColor,
  } as CSSProperties;
  if (onClick !== undefined) {
    return (
      <button type="button" className="pf-dash-pill" style={vars} onClick={onClick}>
        <PillBody p={p} />
      </button>
    );
  }
  return (
    <div className="pf-dash-pill" style={vars}>
      <PillBody p={p} />
    </div>
  );
}

function PillBody({ p }: { p: PillView }) {
  return (
    <>
      <span className="pf-dash-pill-label">{p.label}</span>
      <span className="pf-dash-pill-val">{p.value}</span>
    </>
  );
}
