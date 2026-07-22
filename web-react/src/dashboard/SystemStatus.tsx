import type { ReactNode } from "react";
import { augerRunStroke, type OutputView } from "./deriveView";

// Right-column "System" card: fan / auger / igniter, each with a hand-drawn SVG
// that animates only while that output is on (and animation is enabled).

export function SystemStatus({
  fan,
  auger,
  igniter,
  animate,
}: {
  fan: OutputView;
  auger: OutputView;
  igniter: OutputView;
  animate: boolean;
}) {
  return (
    <div
      style={{
        background: "#2c231a",
        border: "1px solid rgba(255,255,255,0.13)",
        borderRadius: 18,
        padding: 16,
        display: "flex",
        flexDirection: "column",
        gap: 10,
      }}
    >
      <div style={{ font: "600 13px 'Barlow'", letterSpacing: 2.5, color: "#7d7264", textTransform: "uppercase" }}>System</div>
      <StatusRow name="FAN" o={fan} icon={<FanIcon color={fan.color} spin={fan.on && animate} />} />
      <StatusRow name="AUGER" o={auger} icon={<AugerIcon color={auger.color} on={auger.on} feed={auger.on && animate} />} />
      <StatusRow name="IGNITER" o={igniter} icon={<IgniterIcon on={igniter.on} color={igniter.color} anim={igniter.on && animate} />} />
    </div>
  );
}

function StatusRow({ name, o, icon }: { name: string; o: OutputView; icon: ReactNode }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 14,
        padding: "10px 14px",
        background: "#1c1712",
        borderRadius: 13,
        border: `1px solid ${o.edge}`,
      }}
    >
      <div style={{ width: 64, display: "flex", justifyContent: "center", alignItems: "center" }}>{icon}</div>
      <div style={{ flex: 1 }}>
        <div style={{ font: "600 17px 'Barlow'", letterSpacing: 1.5, color: "#cfc6b8" }}>{name}</div>
        <div style={{ font: "600 13px 'Barlow'", letterSpacing: 2, color: o.color }}>{o.status}</div>
      </div>
      <div style={{ width: 9, height: 9, borderRadius: "50%", background: o.dot, boxShadow: `0 0 8px ${o.dot}` }} />
    </div>
  );
}

function FanIcon({ color, spin }: { color: string; spin: boolean }) {
  return (
    <svg width={46} height={46} viewBox="0 0 100 100">
      <g style={{ transformOrigin: "50px 50px", animation: spin ? "pf-spin 0.85s linear infinite" : "none" }}>
        <path d="M50 50 Q 28 30 34 10 Q 50 4 50 50 Z" fill={color} />
        <path d="M50 50 Q 78 42 92 58 Q 88 76 50 50 Z" fill={color} />
        <path d="M50 50 Q 44 78 24 84 Q 10 72 50 50 Z" fill={color} />
      </g>
      <circle cx={50} cy={50} r={8} fill="#14100c" stroke={color} strokeWidth={3} />
    </svg>
  );
}

function AugerIcon({ color, on, feed }: { color: string; on: boolean; feed: boolean }) {
  const stroke = on ? augerRunStroke : "#3a342c";
  return (
    <svg width={66} height={40} viewBox="0 0 120 60">
      <defs>
        <clipPath id="pfAugerClip">
          <rect x={10} y={18} width={100} height={26} rx={13} />
        </clipPath>
      </defs>
      <g clipPath="url(#pfAugerClip)">
        <rect x={10} y={18} width={100} height={26} fill="#0e0b08" />
        <g style={{ animation: feed ? "pf-augerFeed 0.55s linear infinite" : "none" }}>
          <path
            d="M-40 46 L-14 14 M-24 46 L2 14 M-8 46 L18 14 M8 46 L34 14 M24 46 L50 14 M40 46 L66 14 M56 46 L82 14 M72 46 L98 14 M88 46 L114 14 M104 46 L130 14 M120 46 L146 14 M136 46 L162 14"
            stroke={color}
            strokeWidth={6}
            strokeLinecap="round"
          />
        </g>
      </g>
      <rect x={10} y={18} width={100} height={26} rx={13} fill="none" stroke={stroke} strokeWidth={2.5} />
      <g style={{ animation: feed ? "pf-pellet 0.7s linear infinite" : "none" }}>
        <circle cx={30} cy={10} r={3.4} fill={color} />
      </g>
      <g style={{ animation: feed ? "pf-pellet 0.7s linear 0.35s infinite" : "none" }}>
        <circle cx={40} cy={10} r={3.4} fill={color} />
      </g>
    </svg>
  );
}

function IgniterIcon({ on, color, anim }: { on: boolean; color: string; anim: boolean }) {
  const glow = on ? "#ff7a1a" : "transparent";
  return (
    <svg width={60} height={40} viewBox="0 0 100 60">
      <g style={{ animation: anim ? "pf-heat 1.4s ease-out infinite" : "none", transformOrigin: "50px 30px" }}>
        <path d="M30 40 Q 34 26 38 40" fill="none" stroke="#ff9f43" strokeWidth={3} strokeLinecap="round" />
        <path d="M50 42 Q 54 26 58 42" fill="none" stroke="#ff7a1a" strokeWidth={3} strokeLinecap="round" />
        <path d="M68 40 Q 72 28 76 40" fill="none" stroke="#ffb066" strokeWidth={3} strokeLinecap="round" />
      </g>
      <path
        d="M8 40 C 16 14 30 14 34 40 C 38 56 50 56 54 40 C 58 14 72 14 76 40 C 80 56 92 56 92 40"
        fill="none"
        stroke={color}
        strokeWidth={6}
        strokeLinecap="round"
        style={{ filter: `drop-shadow(0 0 6px ${glow})`, animation: anim ? "pf-flicker 1.6s ease-in-out infinite" : "none" }}
      />
    </svg>
  );
}
