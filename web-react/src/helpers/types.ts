// Mirrors blueprints/mobile/socket_io.py _get_dash_data / _get_probe_structure.
// Kept in sync with the real socket_dash_data payload; index signatures allow
// forward-compat with backend fields not modeled here.

export interface ProbeStatus {
  batteryCharging?: boolean;
  batteryPercentage?: number;
  batteryVoltage?: number;
  connected?: boolean;
  error?: boolean | null; // real capture emits `null` when no error is present
  [k: string]: unknown;
}

export interface ProbeData {
  title: string;
  label: string;
  eta: number | string | null; // real capture emits `null` when no ETA applies
  temp: number;
  setTemp: number;
  maxTemp: number;
  target: number;
  lowLimitTemp: number;
  highLimitTemp: number;
  targetReq: boolean;
  hasNotifications: boolean;
  lowLimitReq: boolean;
  highLimitReq: boolean;
  highLimitShutdown: boolean;
  highLimitTriggered: boolean;
  lowLimitShutdown: boolean;
  lowLimitReignite: boolean;
  lowLimitTriggered: boolean;
  targetShutdown: boolean;
  targetKeepWarm: boolean;
  device?: string;
  status: ProbeStatus;
  [k: string]: unknown;
}

export interface DashData {
  uuid: string;
  errors: string[];
  warnings: string[];
  criticalError: boolean;
  grillName: string;
  currentMode: string;
  nextMode: string;
  displayMode: string;
  smokePlus: boolean;
  pwmControl: boolean;
  pMode: number;
  hopperLevel: number;
  startupTimestamp: number;
  modeStartTime: number;
  lidOpenDetectEnabled: boolean;
  lidOpenDetected: boolean;
  lidOpenEndTime: number;
  startDuration: number;
  shutdownDuration: number;
  primeDuration: number;
  primeAmount: number;
  tempUnits: "F" | "C";
  hasDcFan: boolean;
  hasDistanceSensor: boolean;
  startupCheck: boolean;
  startToHoldPrompt: boolean;
  startupGotoTemp: number;
  startupGotoMode: string;
  allowManualOutputs: boolean;
  manualPwm: number;
  timer: { start: number; paused: number; end: number; keepWarm: boolean; shutdown: boolean };
  outputs: { fan: boolean; auger: boolean; igniter: boolean; power: boolean };
  recipeStatus: {
    recipeMode: boolean;
    filename: string;
    mode: string;
    paused: boolean;
    step: number;
  };
  primaryProbe: ProbeData;
  foodProbes: ProbeData[];
  [k: string]: unknown;
}

export type AccentName = "ember" | "ice" | "crimson";
