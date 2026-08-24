// biome-ignore-all lint/suspicious/noEmptyInterface: Generated from closed empty JSON objects.
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

type Message = string;
type Result = "OK" | "ERROR";
type Message1 = string;
type Result1 = "OK" | "ERROR";
/**
 * @minItems 4
 * @maxItems 4
 */
type Command = [string | null, string | null, string | null, string | null];
type Data1 = EmptyResponseData | ControlHealthTimeoutData;
type ResponseWas = "To_Fast";
type Message2 = string | null;
type Result2 = "OK" | "ERROR";
type Allowmanualoutputs = boolean;
type Criticalerror = boolean;
type Currentmode = string;
type Cycleratio = number;
type Displaymode = string;
type Errors = string[];
type Fanduty = number;
type Device = string | null;
type Eta = number | string | null;
type Hasnotifications = boolean;
type Highlimitreq = boolean;
type Highlimitshutdown = boolean;
type Highlimittemp = number;
type Highlimittriggered = boolean;
type Label = string;
type Lowlimitreignite = boolean;
type Lowlimitreq = boolean;
type Lowlimitshutdown = boolean;
type Lowlimittemp = number;
type Lowlimittriggered = boolean;
type Maxtemp = number;
type Settemp = number;
type Batterycharging = boolean | null;
type Batterypercentage = number | null;
type Batteryvoltage = number | null;
type Connected = boolean | null;
type Error = boolean | string | null;
type Lastreadingage = number | null;
type Lasttemp = number | null;
type Target = number;
type Targetkeepwarm = boolean;
type Targetreq = boolean;
type Targetshutdown = boolean;
type Temp = number | null;
type Title = string;
type Foodprobes = ProbeDataPayload[];
type Grillname = string;
type Hasdcfan = boolean;
type Hasdistancesensor = boolean;
type Hopperlevel = number;
type Lidopendetectenabled = boolean;
type Lidopendetected = boolean;
type Lidopenendtime = number;
type Manualpwm = number;
type Modestarttime = number;
type Modellearningrevision = string | null;
type Nextmode = string;
type Auger = boolean;
type Fan = boolean;
type Igniter = boolean;
type Power = boolean;
type Pmode = number;
type Primeamount = number;
type Primeduration = number;
type Pwmcontrol = boolean;
type Filename = string;
type Mode = string;
type Paused = boolean;
type Recipemode = boolean;
type Step = number;
type Safetymaxtemp = number;
type Shutdownduration = number;
type Smokeplus = boolean;
type Startduration = number;
type Starttoholdprompt = boolean;
type Startupcheck = boolean;
type Startupgotomode = string;
type Startupgototemp = number;
type Startuptimestamp = number;
type Status = string;
type Tempunits = "F" | "C";
type Policy = "off" | "observe" | "enforce";
type Source = "hardware" | "software" | "mixed";
type Device1 = string;
type Displayname = string;
type Current = boolean;
type Lastreportedages = number;
type Label1 = string;
type Outcome = "none" | "notify_only" | "unavailable" | "stopped";
type Port = string;
type Evidence = (
  | "hardware"
  | "junction-collapse"
  | "stuck-response"
  | "excitation-response"
  | "implausible-step"
)[];
type Faults = ("open" | "short" | "malfunction")[];
type State = "unmonitored" | "healthy" | "suspected" | "confirmed";
type Temperaturevalid = boolean;
type Role = "Primary" | "Food" | "Aux";
type Thermocouplehealth = ThermocoupleHealthView[];
type End = number;
type Keepwarm = boolean;
type Paused1 = number;
type Shutdown = boolean;
type Start = number;
type Uihash = number;
type Uuid = string;
type Warnings = string[];
type Warningsmaxid = number | null;
type ThroughId = number;
type Data2 = null;
type Message3 = string;
type Result3 = "OK" | "ERROR";
type Build = string | null;

export interface PiFireCoreWebContracts {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "ApiEnvelope".
 */
export interface ApiEnvelope {
  data?: unknown;
  message?: Message;
  result: Result;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "CommandResponse".
 */
export interface CommandResponse {
  data?: CommandResponseData | null;
  message?: Message1;
  result: Result1;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "CommandResponseData".
 */
export interface CommandResponseData {}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "ControlHealthResponse".
 */
export interface ControlHealthResponse {
  command: Command;
  data: Data1;
  message: Message2;
  result: Result2;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "EmptyResponseData".
 */
export interface EmptyResponseData {}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "ControlHealthTimeoutData".
 */
export interface ControlHealthTimeoutData {
  Response_Was: ResponseWas;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "DashSocketPayload".
 */
export interface DashSocketPayload {
  allowManualOutputs: Allowmanualoutputs;
  criticalError: Criticalerror;
  currentMode: Currentmode;
  cycleRatio: Cycleratio;
  displayMode: Displaymode;
  errors: Errors;
  fanDuty: Fanduty;
  foodProbes: Foodprobes;
  grillName: Grillname;
  hasDcFan: Hasdcfan;
  hasDistanceSensor: Hasdistancesensor;
  hopperLevel: Hopperlevel;
  lidOpenDetectEnabled: Lidopendetectenabled;
  lidOpenDetected: Lidopendetected;
  lidOpenEndTime: Lidopenendtime;
  manualPwm: Manualpwm;
  modeStartTime: Modestarttime;
  modelLearningRevision: Modellearningrevision;
  nextMode: Nextmode;
  outputs: OutputPayload;
  pMode: Pmode;
  primaryProbe: ProbeDataPayload;
  primeAmount: Primeamount;
  primeDuration: Primeduration;
  pwmControl: Pwmcontrol;
  recipeStatus: RecipeStatusPayload;
  safetyMaxTemp: Safetymaxtemp;
  shutdownDuration: Shutdownduration;
  smokePlus: Smokeplus;
  startDuration: Startduration;
  startToHoldPrompt: Starttoholdprompt;
  startupCheck: Startupcheck;
  startupGotoMode: Startupgotomode;
  startupGotoTemp: Startupgototemp;
  startupTimestamp: Startuptimestamp;
  status: Status;
  tempUnits: Tempunits;
  thermocoupleHealth?: Thermocouplehealth;
  timer: TimerPayload;
  uiHash: Uihash;
  uuid: Uuid;
  warnings: Warnings;
  warningsMaxId: Warningsmaxid;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "ProbeDataPayload".
 */
export interface ProbeDataPayload {
  device?: Device;
  eta: Eta;
  hasNotifications: Hasnotifications;
  highLimitReq: Highlimitreq;
  highLimitShutdown: Highlimitshutdown;
  highLimitTemp: Highlimittemp;
  highLimitTriggered: Highlimittriggered;
  label: Label;
  lowLimitReignite: Lowlimitreignite;
  lowLimitReq: Lowlimitreq;
  lowLimitShutdown: Lowlimitshutdown;
  lowLimitTemp: Lowlimittemp;
  lowLimitTriggered: Lowlimittriggered;
  maxTemp: Maxtemp;
  setTemp: Settemp;
  status: ProbeStatusPayload;
  target: Target;
  targetKeepWarm: Targetkeepwarm;
  targetReq: Targetreq;
  targetShutdown: Targetshutdown;
  temp: Temp;
  title: Title;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "ProbeStatusPayload".
 */
export interface ProbeStatusPayload {
  batteryCharging?: Batterycharging;
  batteryPercentage?: Batterypercentage;
  batteryVoltage?: Batteryvoltage;
  connected?: Connected;
  error?: Error;
  lastReadingAge?: Lastreadingage;
  lastTemp?: Lasttemp;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "OutputPayload".
 */
export interface OutputPayload {
  auger: Auger;
  fan: Fan;
  igniter: Igniter;
  power: Power;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "RecipeStatusPayload".
 */
export interface RecipeStatusPayload {
  filename: Filename;
  mode: Mode;
  paused: Paused;
  recipeMode: Recipemode;
  step: Step;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "ThermocoupleHealthView".
 */
export interface ThermocoupleHealthView {
  detector: ThermocoupleHealthDetectorView;
  device: Device1;
  displayName: Displayname;
  freshness: ThermocoupleHealthFreshnessView;
  label: Label1;
  outcome: Outcome;
  port: Port;
  report: ThermocoupleHealthReportView;
  role: Role;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "ThermocoupleHealthDetectorView".
 */
export interface ThermocoupleHealthDetectorView {
  policy: Policy;
  source: Source;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "ThermocoupleHealthFreshnessView".
 */
export interface ThermocoupleHealthFreshnessView {
  current: Current;
  lastReportedAgeS: Lastreportedages;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "ThermocoupleHealthReportView".
 */
export interface ThermocoupleHealthReportView {
  detail: Detail;
  evidence: Evidence;
  faults: Faults;
  state: State;
  temperatureValid: Temperaturevalid;
}
interface Detail {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "TimerPayload".
 */
export interface TimerPayload {
  end: End;
  keepWarm: Keepwarm;
  paused: Paused1;
  shutdown: Shutdown;
  start: Start;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "DismissWarningsRequest".
 */
export interface DismissWarningsRequest {
  through_id: ThroughId;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "DismissWarningsResponse".
 */
export interface DismissWarningsResponse {
  data?: Data2;
  message?: Message3;
  result: Result3;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "WebUiBuildResponse".
 */
export interface WebUiBuildResponse {
  build: Build;
}
