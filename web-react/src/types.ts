// Mirrors the payload emitted by PiFire's existing Flask-SocketIO
// `socket_dash_data` event — see blueprints/mobile/socket_io.py `_get_dash_data`
// (dict at lines ~210-263) and `_get_probe_structure` (~813-836).
// Only the fields the POC consumes are typed strictly; the rest are optional.

export interface ProbeData {
  title: string;
  label: string;
  temp: number;
  setTemp: number; // primary only: current setpoint (PSP)
  maxTemp: number;
  target: number;
  targetReq: boolean;
  hasNotifications: boolean;
  status: Record<string, unknown>;
  [k: string]: unknown;
}

export interface DashData {
  uuid: string;
  grillName: string;
  currentMode: string;
  displayMode: string;
  nextMode: string;
  smokePlus: boolean;
  pMode: number;
  hopperLevel: number;
  tempUnits: "F" | "C";
  lidOpenDetected: boolean;
  outputs: { fan: number; auger: number; igniter: number };
  timer: { start: number; paused: number; end: number; keepWarm: boolean; shutdown: boolean };
  recipeStatus: { recipeMode: boolean; filename: string; mode: string; paused: boolean; step: number };
  primaryProbe: ProbeData;
  foodProbes: ProbeData[];
  errors: string[];
  warnings: string[];
  [k: string]: unknown;
}

export type AccentName = "ember" | "ice" | "crimson";

// The command contract: post_app_data(action, type, json_data) -> write_control.
// See socket_io.py `post_app_data` (line ~135) and qtbackend.py:229-324 for the
// QML equivalents the full port will map 1:1.
export type SendCommand = (action: string, type: string | null, jsonData?: unknown) => void;
