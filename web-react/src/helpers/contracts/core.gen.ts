/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

export type Message = string;
export type Result = "OK" | "ERROR";
export type Message1 = string;
export type Result1 = "OK" | "ERROR";
/**
 * @minItems 4
 * @maxItems 4
 */
export type Command = [string | null, string | null, string | null, string | null];
export type Data1 = EmptyResponseData | ControlHealthTimeoutData;
export type ResponseWas = "To_Fast";
export type Message2 = string | null;
export type Result2 = "OK" | "ERROR";
export type Allowmanualoutputs = boolean;
export type Criticalerror = boolean;
export type Currentmode = string;
export type Cycleratio = number;
export type Displaymode = string;
export type Errors = string[];
export type Fanduty = number;
export type Device = string | null;
export type Eta = number | string | null;
export type Hasnotifications = boolean;
export type Highlimitreq = boolean;
export type Highlimitshutdown = boolean;
export type Highlimittemp = number;
export type Highlimittriggered = boolean;
export type Label = string;
export type Lowlimitreignite = boolean;
export type Lowlimitreq = boolean;
export type Lowlimitshutdown = boolean;
export type Lowlimittemp = number;
export type Lowlimittriggered = boolean;
export type Maxtemp = number;
export type Settemp = number;
export type Batterycharging = boolean | null;
export type Batterypercentage = number | null;
export type Batteryvoltage = number | null;
export type Connected = boolean | null;
export type Error = boolean | string | null;
export type Lastreadingage = number | null;
export type Lasttemp = number | null;
export type Target = number;
export type Targetkeepwarm = boolean;
export type Targetreq = boolean;
export type Targetshutdown = boolean;
export type Temp = number | null;
export type Title = string;
export type Foodprobes = ProbeDataPayload[];
export type Grillname = string;
export type Hasdcfan = boolean;
export type Hasdistancesensor = boolean;
export type Hopperlevel = number;
export type Lidopendetectenabled = boolean;
export type Lidopendetected = boolean;
export type Lidopenendtime = number;
export type Manualpwm = number;
export type Modestarttime = number;
export type Modellearningrevision = string | null;
export type Nextmode = string;
export type Auger = boolean;
export type Fan = boolean;
export type Igniter = boolean;
export type Power = boolean;
export type Pmode = number;
export type Primeamount = number;
export type Primeduration = number;
export type Pwmcontrol = boolean;
export type Filename = string;
export type Mode = string;
export type Paused = boolean;
export type Recipemode = boolean;
export type Step = number;
export type Safetymaxtemp = number;
export type Shutdownduration = number;
export type Smokeplus = boolean;
export type Startduration = number;
export type Starttoholdprompt = boolean;
export type Startupcheck = boolean;
export type Startupgotomode = string;
export type Startupgototemp = number;
export type Startuptimestamp = number;
export type Status = string;
export type Tempunits = "F" | "C";
export type End = number;
export type Keepwarm = boolean;
export type Paused1 = number;
export type Shutdown = boolean;
export type Start = number;
export type Uihash = number;
export type Uuid = string;
export type Warnings = string[];
export type Warningsmaxid = number | null;
export type ThroughId = number;
export type Data2 = null;
export type Message3 = string;
export type Result3 = "OK" | "ERROR";
export type Action = "start" | "pause" | "resume" | "stop" | "reset-progress";
export type AmbientC = number;
export type AmbientSource = "measured" | "manual" | "weather" | "configured";
export type EmptyGrillConfirmed = boolean;
export type PelletsConfirmed = boolean;
export type Revision = number;
export type Data3 = MpcCalibrationCommandResponseData | CommandResponseData | null;
export type Message4 = string;
export type Result4 = "OK" | "ERROR";
export type DateLoaded = string;
export type EstUsage = number;
export type HopperLevel = number;
export type Pelletid = string;
export type Brand = string;
export type Comments = string;
export type Rating = number;
export type Wood = string;
export type Brands = string[];
export type Time = number;
export type Deleted = boolean;
export type Pelletid1 = string | null;
export type SchemaVersion = number;
export type Woods = string[];
export type Uuid1 = string;
export type Build = string | null;

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
 * via the `definition` "MpcCalibrationCommandPayload".
 */
export interface MpcCalibrationCommandPayload {
  action: Action;
  ambient_c: AmbientC;
  ambient_source: AmbientSource;
  empty_grill_confirmed: EmptyGrillConfirmed;
  pellets_confirmed: PelletsConfirmed;
  revision: Revision;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "MpcCalibrationCommandResponse".
 */
export interface MpcCalibrationCommandResponse {
  data?: Data3;
  message?: Message4;
  result: Result4;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "MpcCalibrationCommandResponseData".
 */
export interface MpcCalibrationCommandResponseData {
  mpc_calibration: MpcCalibrationCommandPayload;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "PelletCurrentPayload".
 */
export interface PelletCurrentPayload {
  date_loaded: DateLoaded;
  est_usage: EstUsage;
  hopper_level: HopperLevel;
  pelletid: Pelletid;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "PelletDatabasePayload".
 */
export interface PelletDatabasePayload {
  archive: Archive;
  brands: Brands;
  current: PelletCurrentPayload;
  lastupdated: PelletLastUpdatedPayload;
  log: Log;
  schema_version: SchemaVersion;
  woods: Woods;
}
export interface Archive {
  [k: string]: PelletProfilePayload | undefined;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "PelletProfilePayload".
 */
export interface PelletProfilePayload {
  brand: Brand;
  comments: Comments;
  rating: Rating;
  wood: Wood;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "PelletLastUpdatedPayload".
 */
export interface PelletLastUpdatedPayload {
  time: Time;
}
export interface Log {
  [k: string]: PelletLogEntryPayload | undefined;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "PelletLogEntryPayload".
 */
export interface PelletLogEntryPayload {
  deleted: Deleted;
  pelletid: Pelletid1;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "PelletSocketPayload".
 */
export interface PelletSocketPayload {
  pellets: PelletDatabasePayload;
  uuid: Uuid1;
}
/**
 * This interface was referenced by `PiFireCoreWebContracts`'s JSON-Schema
 * via the `definition` "WebUiBuildResponse".
 */
export interface WebUiBuildResponse {
  build: Build;
}
