// Mirrors blueprints/mobile/socket_io.py _get_dash_data / _get_probe_structure.
// Kept in sync with the real socket_dash_data payload; index signatures allow
// forward-compat with backend fields not modeled here.

export interface ProbeStatus {
  batteryCharging?: boolean;
  /** null is a real value here: the driver reports the key but has no reading
   *  (probes/bt_ibbq.py, probes/bt_meater.py). Absent means the probe has no
   *  battery at all. */
  batteryPercentage?: number | null;
  batteryVoltage?: number;
  connected?: boolean;
  error?: boolean | null; // real capture emits `null` when no error is present
  /** The last reading this probe actually produced, and how many seconds ago
   *  it was taken. Both are published on every frame, so while the probe is
   *  reporting `lastTemp` agrees with `temp` and the age is 0; they matter
   *  when `temp` is null, which is the only time a card has a stale value to
   *  show. Absent for a probe that has never reported since the last flush. */
  lastTemp?: number;
  lastReadingAge?: number;
  [k: string]: unknown;
}

export interface ProbeData {
  title: string;
  label: string;
  eta: number | string | null; // real capture emits `null` when no ETA applies
  /** null when the device had no reading to give -- a network-polled probe
   *  whose cache went stale returns None rather than inventing a number, and
   *  it reaches the wire unchanged (common/datastore_accessors.py). Rendering
   *  it as a number would turn absence into a plausible temperature. */
  temp: number | null;
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

export interface LiveState {
  uuid: string;
  errors: string[];
  warnings: string[];
  /** High-water mark of the warnings above; null when there are none. Posted
   *  back to dismiss exactly the warnings this payload carried. */
  warningsMaxId: number | null;
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
  /** settings.safety.maxtemp, in tempUnits. The grill shuts down above it in
   *  any mode, so it is the ceiling for every setpoint the dashboard offers.
   *  Distinct from ProbeData.maxTemp, which is only the gauge's top of scale. */
  safetyMaxTemp: number;
  /** The auger's share of its cycle, 0..1 (control:status cycle_ratio). */
  cycleRatio: number;
  /** The fan's commanded duty, as a percentage (control:status fan_duty). */
  fanDuty: number;
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
  /** REST report invalidation only. The dashboard never renders this value. */
  modelLearningRevision?: string | null;
  /** Hash of the probe map (common/app.py::create_ui_hash). It moves when a
   *  probe is reconfigured anywhere, including from another client. Note it
   *  also moves on a server restart, because Python salts hash() for strings
   *  -- a spurious settings refetch, which is cheap and the reason this drives
   *  an invalidation rather than Flask's full page reload. */
  uiHash: number;
  [k: string]: unknown;
}

export type AccentName = "ember" | "ice" | "crimson";
