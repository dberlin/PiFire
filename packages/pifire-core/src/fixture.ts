import type { DashSocketPayload } from "./contracts/core.gen";

// Real `socket_dash_data` payload captured 2026-07-21 from the running
// prototype backend (control.py + gunicorn on localhost:5000), via a
// one-shot python-socketio client emitting `listen_app_data` and recording
// the first `socket_dash_data` event. The prototype grill platform was idle
// (currentMode/displayMode "Stop", status "inactive") at capture time, so
// temps/outputs read as zero/false — this is a genuine backend snapshot, not
// a hand-authored "nice" cook state.
export const FIXTURE_DASH = {
  uuid: "91a66346-7e6a-11f1-b29c-84470959a251",
  errors: [],
  warnings: [],
  warningsMaxId: null,
  status: "inactive",
  criticalError: false,
  grillName: "BOOT_PATH_SENTINEL_GRILL",
  currentMode: "Stop",
  nextMode: "Stop",
  displayMode: "Stop",
  smokePlus: false,
  pwmControl: false,
  pMode: 2,
  hopperLevel: 100,
  startupTimestamp: 0,
  modeStartTime: 0,
  lidOpenDetectEnabled: false,
  lidOpenDetected: false,
  lidOpenEndTime: 0,
  startDuration: 0,
  shutdownDuration: 0,
  primeDuration: 0,
  primeAmount: 0,
  tempUnits: "F",
  hasDcFan: false,
  hasDistanceSensor: false,
  startupCheck: true,
  startToHoldPrompt: false,
  startupGotoTemp: 165,
  startupGotoMode: "Smoke",
  allowManualOutputs: false,
  safetyMaxTemp: 550, // common/defaults.py settings["safety"]["maxtemp"]
  cycleRatio: 0,
  fanDuty: 0,
  manualPwm: 100,
  // Not part of the original capture -- uiHash was added to the socket frame
  // after this fixture was recorded. Any fixed value is fine here: what
  // matters to consumers is that it changes between frames, not its value.
  uiHash: 0,
  timer: {
    start: 0,
    paused: 0,
    end: 0,
    keepWarm: false,
    shutdown: false,
  },
  outputs: {
    fan: false,
    auger: false,
    igniter: false,
    power: false,
  },
  recipeStatus: {
    recipeMode: false,
    filename: "",
    mode: "Stop",
    paused: false,
    step: 0,
  },
  foodProbes: [
    {
      title: "Probe-1",
      label: "Probe1",
      eta: null,
      temp: 0,
      setTemp: 0,
      maxTemp: 300,
      target: 0,
      lowLimitTemp: 0,
      highLimitTemp: 0,
      targetReq: false,
      hasNotifications: false,
      lowLimitReq: false,
      highLimitReq: false,
      highLimitShutdown: false,
      highLimitTriggered: false,
      lowLimitShutdown: false,
      lowLimitReignite: false,
      lowLimitTriggered: false,
      targetShutdown: false,
      targetKeepWarm: false,
      status: {
        error: null,
      },
      device: "proto_adc",
    },
    {
      title: "Probe-2",
      label: "Probe2",
      eta: null,
      temp: 0,
      setTemp: 0,
      maxTemp: 300,
      target: 0,
      lowLimitTemp: 0,
      highLimitTemp: 0,
      targetReq: false,
      hasNotifications: false,
      lowLimitReq: false,
      highLimitReq: false,
      highLimitShutdown: false,
      highLimitTriggered: false,
      lowLimitShutdown: false,
      lowLimitReignite: false,
      lowLimitTriggered: false,
      targetShutdown: false,
      targetKeepWarm: false,
      status: {
        error: null,
      },
      device: "proto_adc",
    },
    {
      title: "Probe-3",
      label: "Probe3",
      eta: null,
      temp: 0,
      setTemp: 0,
      maxTemp: 300,
      target: 0,
      lowLimitTemp: 0,
      highLimitTemp: 0,
      targetReq: false,
      hasNotifications: false,
      lowLimitReq: false,
      highLimitReq: false,
      highLimitShutdown: false,
      highLimitTriggered: false,
      lowLimitShutdown: false,
      lowLimitReignite: false,
      lowLimitTriggered: false,
      targetShutdown: false,
      targetKeepWarm: false,
      status: {
        error: null,
      },
      device: "proto_adc",
    },
  ],
  primaryProbe: {
    title: "Grill",
    label: "Grill",
    eta: null,
    temp: 0,
    setTemp: 0,
    maxTemp: 600,
    target: 0,
    lowLimitTemp: 0,
    highLimitTemp: 0,
    targetReq: false,
    hasNotifications: false,
    lowLimitReq: false,
    highLimitReq: false,
    highLimitShutdown: false,
    highLimitTriggered: false,
    lowLimitShutdown: false,
    lowLimitReignite: false,
    lowLimitTriggered: false,
    targetShutdown: false,
    targetKeepWarm: false,
    status: {
      error: null,
    },
    device: "proto_adc",
  },
  modelLearningRevision: null,
} satisfies DashSocketPayload;

// The same payload shape in a RUNNING state, for the cases FIXTURE_DASH
// cannot show. FIXTURE_DASH was captured from an idle grill, so every temp is
// 0, every output false, and the mode badge reads "Stop" -- which means it
// exercises none of the live-cook rendering, and the widest mode label the UI
// ever draws ("Monitor", 7 characters against "Stop"'s 4) never appears. The
// mobile gauge's mode badge is sized to fit inside the arc, so the short label
// is exactly the one that cannot prove the fit.
//
// Derived from FIXTURE_DASH rather than re-captured, so the two can never
// disagree about the payload's shape: only the fields a running Monitor cook
// would actually change are overridden.
export const FIXTURE_DASH_MONITOR = {
  ...FIXTURE_DASH,
  status: "active",
  currentMode: "Monitor",
  nextMode: "Monitor",
  displayMode: "Monitor",
  // Monitor watches and reports; it does not drive to a setpoint, so the
  // primary probe carries a temperature but no target.
  outputs: { fan: true, auger: false, igniter: false, power: true },
  primaryProbe: { ...FIXTURE_DASH.primaryProbe, temp: 237 },
  // One probe with an ARMED target (so the target line and progress bar
  // render) and two ambient ones, which is the mix the probe row has to lay
  // out in practice.
  foodProbes: [
    { ...FIXTURE_DASH.foodProbes[0], title: "Brisket", temp: 148, target: 203, targetReq: true },
    { ...FIXTURE_DASH.foodProbes[1], title: "Pork Butt", temp: 132 },
    { ...FIXTURE_DASH.foodProbes[2], title: "Ambient", temp: 79 },
  ],
} satisfies DashSocketPayload;
