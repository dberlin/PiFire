import { useState } from "react";
import { useNavigate } from "react-router";
import type { AccentName, DashData } from "../types";
import type { ConnectionPhase } from "../useDashData";
import type { CommandClient } from "../command";
import { deriveView, fmtDuration, type PillView } from "./deriveView";
import { useClock, useFitScale } from "./hooks";
import { GrillGauge } from "./GrillGauge";
import { ProbeCard } from "./ProbeCard";
import { SystemStatus } from "./SystemStatus";
import { HopperGauge } from "./HopperGauge";
import { ControlButtons } from "./ControlButtons";
import { Banners } from "./Banners";

const ACCENTS: AccentName[] = ["ember", "ice", "crimson"];
const SWATCH: Record<AccentName, string> = { ember: "#ff8a2b", ice: "#3cc7d0", crimson: "#ff6a5a" };

interface DashboardProps {
  dash: DashData;
  command: CommandClient;
  phase: ConnectionPhase;
  controlAlive: boolean;
  accent: AccentName;
  setAccent: (a: AccentName) => void;
  animate: boolean;
  setAnimate: (v: boolean) => void;
}

// The full 1280x720 PiFire controller dashboard (port of PiFire Dashboard.dc.html),
// driven by the live socket_dash_data contract. Scaled to fit the browser
// viewport; on-device it renders 1:1 on the touchscreen.
export function Dashboard({ dash, command, phase, controlAlive, accent, setAccent, animate, setAnimate }: DashboardProps) {
  const view = deriveView(dash);
  const navigate = useNavigate();
  const now = useClock();
  const scale = useFitScale(1280, 720);

  // Cook-time counter: seconds since the current cook began (reset when the
  // controller leaves an active cooking mode). Adjusted synchronously during
  // render on the cooking-state transition edge (React's recommended pattern
  // for deriving state from a prop change), rather than in an effect.
  const [cookStart, setCookStart] = useState<number | null>(() => (view.cooking ? now.getTime() : null));
  const [prevCooking, setPrevCooking] = useState(view.cooking);
  if (view.cooking !== prevCooking) {
    setPrevCooking(view.cooking);
    setCookStart(view.cooking ? now.getTime() : null);
  }
  const cookTime = fmtDuration(cookStart != null ? (now.getTime() - cookStart) / 1000 : 0);
  const clock = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  return (
    <div className="pf-fit">
      <div className="pf-stage" data-animate={animate ? "true" : "false"} style={{ transform: `translate(-50%, -50%) scale(${scale})` }}>
        <div
          style={{
            position: "absolute",
            left: "50%",
            bottom: -160,
            transform: "translateX(-50%)",
            width: 820,
            height: 420,
            borderRadius: "50%",
            background: "radial-gradient(closest-side, var(--glow), transparent 70%)",
            filter: "blur(14px)",
            opacity: 0.28,
            pointerEvents: "none",
          }}
        />

        <Banners errors={dash.errors ?? []} warnings={dash.warnings ?? []} criticalError={dash.criticalError} />

        {/* Header */}
        <div
          style={{
            height: 58,
            flex: "0 0 58px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "0 22px",
            borderBottom: "1px solid rgba(255,255,255,0.06)",
            position: "relative",
            zIndex: 2,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div
              style={{
                width: 12,
                height: 12,
                borderRadius: "50%",
                background: view.liveColor,
                boxShadow: `0 0 10px ${view.liveColor}`,
                animation: "pf-blink 2.4s ease-in-out infinite",
              }}
            />
            <span style={{ font: "700 20px 'Barlow'", letterSpacing: 0.5, color: "#f4ede2" }}>
              Pi<span style={{ color: "var(--accent)" }}>Fire</span>
            </span>
            <span
              style={{
                font: "600 12px 'Barlow'",
                letterSpacing: 2,
                color: "#7d7264",
                textTransform: "uppercase",
                paddingLeft: 8,
                borderLeft: "1px solid rgba(255,255,255,0.08)",
                marginLeft: 2,
              }}
            >
              {dash.grillName}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <span
              style={{
                font: "600 13px 'Barlow'",
                letterSpacing: 1,
                color: phase === "demo" ? "#7d7264" : controlAlive ? "#8fe09a" : "#ff8b82",
              }}
            >
              {phase === "demo" ? "DEMO" : controlAlive ? "LIVE" : "CTRL OFFLINE"}
            </span>
            <span style={{ font: "600 22px 'Barlow Semi Condensed'", color: "#cfc6b8", fontVariantNumeric: "tabular-nums" }}>{clock}</span>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {ACCENTS.map((a) => (
                <button
                  key={a}
                  className={`pf-swatch ${accent === a ? "sel" : ""}`}
                  style={{ background: SWATCH[a] }}
                  onClick={() => setAccent(a)}
                  aria-label={a}
                />
              ))}
              <button className={`pf-toggle ${animate ? "on" : ""}`} onClick={() => setAnimate(!animate)}>
                ANIM
              </button>
              <button className="pf-toggle" onClick={() => navigate("/settings")} aria-label="settings">
                ⚙
              </button>
            </div>
          </div>
        </div>

        {/* Body */}
        <div style={{ flex: 1, display: "flex", gap: 16, padding: "16px 18px 18px", position: "relative", zIndex: 1, minHeight: 0 }}>
          {/* Left: food probes */}
          {view.hasProbes && (
            <div style={{ width: 298, flex: "0 0 298px", display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
              <div style={{ font: "600 13px 'Barlow'", letterSpacing: 2.5, color: "#7d7264", textTransform: "uppercase", paddingLeft: 4 }}>
                Food Probes
              </div>
              {view.probes.map((p, i) => (
                <ProbeCard key={`${p.name}-${i}`} p={p} />
              ))}
            </div>
          )}

          {/* Center: gauge + cook time + controls */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 14, minWidth: 0 }}>
            <GrillGauge
              temp={dash.primaryProbe.temp}
              setpoint={dash.primaryProbe.setTemp}
              maxTemp={view.maxTemp}
              frac={view.gaugeFrac}
              hasSetpoint={view.hasSetpoint}
              modeLabel={view.modeLabel}
              units={view.units}
              cooking={view.cooking}
              animate={animate}
            />

            <div style={{ display: "flex", gap: 14, height: 52, flex: "0 0 52px" }}>
              <div
                style={{
                  flex: 1,
                  background: "#2c231a",
                  border: "1px solid rgba(255,255,255,0.13)",
                  borderRadius: 14,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "0 20px",
                }}
              >
                <span style={{ font: "600 12px 'Barlow'", letterSpacing: 2, color: "#7d7264", textTransform: "uppercase" }}>Cook Time</span>
                <span style={{ font: "700 26px 'Barlow Semi Condensed'", fontVariantNumeric: "tabular-nums", color: "#cfc6b8" }}>{cookTime}</span>
              </div>
              {view.lidOpen && (
                <div
                  style={{
                    flex: "0 0 210px",
                    borderRadius: 14,
                    background: "rgba(255,90,77,0.14)",
                    border: "1.5px solid #ff5a4d",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    font: "700 20px 'Barlow'",
                    letterSpacing: 2,
                    color: "#ff8b82",
                    animation: "pf-blink 1s ease-in-out infinite",
                  }}
                >
                  LID OPEN
                </div>
              )}
            </div>

            <ControlButtons dash={dash} command={command} disabled={!controlAlive} />
          </div>

          {/* Right: system + pills + hopper */}
          <div style={{ width: 300, flex: "0 0 300px", display: "flex", flexDirection: "column", gap: 14, minHeight: 0 }}>
            <SystemStatus fan={view.fan} auger={view.auger} igniter={view.igniter} animate={animate} />
            <div style={{ display: "flex", gap: 14, height: 64, flex: "0 0 64px" }}>
              <Pill p={view.pillL} />
              <Pill p={view.pillR} />
            </div>
            <HopperGauge h={view.hopper} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Pill({ p }: { p: PillView }) {
  return (
    <div
      style={{
        flex: 1,
        borderRadius: 14,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 1,
        background: p.bg,
        border: `1.5px solid ${p.border}`,
      }}
    >
      <span style={{ font: "600 10px 'Barlow'", letterSpacing: 1.5, color: p.labelColor, whiteSpace: "nowrap" }}>{p.label}</span>
      <span style={{ font: "800 24px 'Barlow Semi Condensed'", color: p.valColor, whiteSpace: "nowrap", lineHeight: 1 }}>{p.value}</span>
    </div>
  );
}
