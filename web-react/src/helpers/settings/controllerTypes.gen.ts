/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

export type Enabled = boolean;
export type Locations = string[];
export type Hidden = boolean;
export type OptionDefault = boolean;
export type OptionDescription = string;
export type OptionFriendlyName = string;
export type OptionMax = number | null;
export type OptionMin = number | null;
export type OptionName = string;
export type OptionStep = number | null;
export type OptionType = "bool";
export type Attributions = string[];
export type Author = string;
export type Hidden1 = boolean;
export type OptionDefault1 = number;
export type OptionDescription1 = string;
export type OptionFriendlyName1 = string;
export type OptionMax1 = number | null;
export type OptionMin1 = number | null;
export type OptionName1 = string;
export type OptionStep1 = number | null;
export type OptionType1 = "float";
export type Hidden2 = boolean;
export type OptionDefault2 = number;
export type OptionDescription2 = string;
export type OptionFriendlyName2 = string;
export type OptionMax2 = number | null;
export type OptionMin2 = number | null;
export type OptionName2 = string;
export type OptionStep2 = number | null;
export type OptionType2 = "int";
export type Hidden3 = boolean;
export type ListLabels = string[] | null;
export type ListValues = (number | boolean | string)[];
export type OptionDefault3 = number | boolean | string;
export type OptionDescription3 = string;
export type OptionFriendlyName3 = string;
export type OptionMax3 = number | null;
export type OptionMin3 = number | null;
export type OptionName3 = string;
export type OptionStep3 = number | null;
export type OptionType3 = "list";
export type Hidden4 = boolean;
export type OptionDefault4 = string;
export type OptionDescription4 = string;
export type OptionFriendlyName4 = string;
export type OptionMax4 = number | null;
export type OptionMin4 = number | null;
export type OptionName4 = string;
export type OptionStep4 = number | null;
export type OptionType4 = "string";
export type Config = (
  FloatControllerOption | IntControllerOption | BoolControllerOption | ListControllerOption | StringControllerOption
)[];
export type Contributors = string[];
export type Extra = string | null;
export type Modules = string[];
export type Description = string;
export type FriendlyName = string;
export type Image = string;
export type Link = string;
export type ModuleName = string;
export type CycleRatioMax = number;
export type CC = number;
export type KQ = number;
export type QW = number;
export type RDq = number;
export type TAmb = number;
export type ControlPeriod = number;
export type EnableFanInput = boolean;
export type EnableIdentification = boolean;
export type EnableOnlineAdaptation = boolean;
export type EstQDist = number;
export type EstQTemp = number;
export type EstRMeas = number;
export type Estimator = "ekf" | "kf";
export type FanMaxPct = number;
export type FanMinPct = number;
export type HAmb = number;
export type NHorizon = number;
export type Sigma = number;
export type Theta = number;
export type Pb = number;
export type Td = number;
export type Ti = number;
export type Center = number;
export type Pb1 = number;
export type Td1 = number;
export type Ti1 = number;
export type CenterFactor = number;
export type StableWindow = number;
export type Selected = string;
export type Lidopendetectenabled = boolean;
export type Lidopenpausetime = number;
export type Lidopenthreshold = number;
export type Pmode = number;
export type Smokeoffcycletime = number;
export type Smokeoncycletime = number;
export type UMax = number;
export type Current = string;
export type Selected1 = string;
export type SleepTimeout = number;
export type Augerrate = number;
export type BootToMonitor = boolean;
export type DebugMode = boolean;
export type DispRotation = number;
export type ExtData = boolean;
export type FirstTimeSetup = boolean;
export type GrillName = string;
export type PrimeIgnition = boolean;
export type PythonExec = string;
export type Units = "F" | "C";
export type UpdatedMessage = boolean;
export type Uv = boolean;
export type Venv = boolean;
export type Autorefresh = "on" | "off";
export type Clearhistoryonstart = boolean;
export type Datapoints = number;
export type FidelityDegrees = number;
export type Minutes = number;
export type BgColor = string;
export type BgColorSetpoint = string | null;
export type BgColorTarget = string;
export type DashSetpoint = boolean;
export type Enabled1 = boolean;
export type Fill = boolean;
export type LineColor = string;
export type LineColorSetpoint = string | null;
export type LineColorTarget = string;
export type Name = string;
export type Type = string;
export type Apikey = string;
export type Enabled2 = boolean;
export type Bucket = string;
export type Enabled3 = boolean;
export type Org = string;
export type Token = string;
export type Url = string;
export type SPlus = boolean;
export type Temp = number;
export type Time = number;
export type Mode = string;
export type Message = string;
export type Result = "OK" | "ERROR";
export type Display = string;
export type Dist = string;
export type Grillplat = string;
export type Broker = string;
export type Enabled4 = boolean;
export type HomeassistantAutodiscoveryTopic = string;
export type Id = string;
export type Password = string;
export type Port = string;
export type UpdateSec = string;
export type Username = string;
export type AppId = string;
export type Enabled5 = boolean;
export type Uuid = string;
export type Apikey1 = string;
export type Publicurl = string;
export type Enabled6 = boolean;
export type Apikey2 = string;
export type Publicurl1 = string;
export type Userkeys = string;
export type Enabled7 = boolean;
export type DeviceAddress = string;
export type Enabled8 = boolean;
export type GrillError = number;
export type PelletLevelLow = number;
export type RecipeNext = number;
export type TempAchieved = number;
export type TimerExpired = number;
export type Hold = number;
export type Prime = number;
export type Reignite = number;
export type Shutdown = number;
export type Smoke = number;
export type Startup = number;
export type Stop = number;
export type NotifyDuration = number;
export type Booting = number;
export type Cooking = number;
export type Cooldown = number;
export type ErrorFault = number;
export type Idle = number;
export type LowPellets = number;
export type NightMode = number;
export type OvershootAlarm = number;
export type Preheat = number;
export type ProbeAlarm = number;
export type TargetReached = number;
export type TimerDone = number;
export type CookingColor = string;
export type IdleBrightness = number;
export type LedCount = number;
export type NightMode1 = boolean;
export type UseProfiles = boolean;
export type UseSuggestedPresets = boolean;
export type Empty = number;
export type Full = number;
export type WarningEnabled = boolean;
export type WarningLevel = number;
export type WarningTime = number;
export type Buttonslevel = string;
export type Current1 = string;
export type DcFan = boolean;
export type Dc = number;
export type Led = number;
export type Rst = number;
export type Address = string | number | null;
export type Device = string;
export type Echo = number;
export type I2CBus = _BasicBus | _KernelBusNumber | _KernelAdapterName | _KernelSerialMatch | _FT232HBus | _MCP2221Bus;
export type Kind = "basic";
export type BusNum = number;
export type Kind1 = "kernel";
export type Adapter = string;
export type Kind2 = "kernel";
export type Kind3 = "kernel";
export type Serial = string;
export type Kind4 = "ft232h";
export type Url1 = string;
export type Kind5 = "mcp2221";
export type Serial1 = string;
export type Trig = number;
export type DownDt = number;
export type EnterSw = number;
export type UpClk = number;
export type Address1 = string;
export type Chip = string;
export type I2CBus1 = _BasicBus | _KernelBusNumber | _KernelAdapterName | _KernelSerialMatch | _FT232HBus | _MCP2221Bus;
export type Url2 = string;
export type Selector = number | null;
export type Shutdown1 = number | null;
export type Serial2 = string;
export type Baudrate = number;
export type Device1 = string;
export type Auger = number | string | null;
export type DcFan1 = number | string | null;
export type Fan = number | string | null;
export type Igniter = number | string | null;
export type Power = number | string | null;
export type Pwm = number | string | null;
export type RealHw = boolean;
export type Standalone = boolean;
export type Wire = number | null;
export type Ce0 = number;
export type Ce1 = number;
export type SystemType = string;
export type Triggerlevel = string;
export type ProbeDevices = {
  [k: string]: unknown | undefined;
}[];
export type ProbeInfo = {
  [k: string]: unknown | undefined;
}[];
export type DutyCycle = number;
export type Frequency = number;
export type MaxDutyCycle = number;
export type MinDutyCycle = number;
export type Profiles = PwmProfile[];
export type PwmControl = boolean;
export type TempRangeList = number[];
export type UpdateTime = number;
export type Food = string[];
export type Primary = string;
export type AllowManualChanges = boolean;
export type ManualOverrideTime = number;
export type Maxstartuptemp = number;
export type Maxtemp = number;
export type Minstartuptemp = number;
export type Reigniteretries = number;
export type StartupCheck = boolean;
export type Message1 = string;
export type Path = string;
export type Uuid1 = string;
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SettingsFlag".
 */
export type SettingsFlag = "settings_update" | "controller_update" | "distance_update" | "probe_profile_update";
export type SchemaVersion = number;
export type AutoPowerOff = boolean;
export type ShutdownDuration = number;
export type DutyCycle1 = number;
export type Enabled9 = boolean;
export type FanRamp = boolean;
export type MaxTemp = number;
export type MinTemp = number;
export type OffTime = number;
export type OnTime = number;
export type Duration = number;
export type PrimeOnStartup = number;
export type PwmDutyCycle = number;
export type Enabled10 = boolean;
export type ExitTemp = number;
export type Augerontime = number;
export type PMode = number;
export type Startuptime = number;
export type Profiles1 = SmartStartProfile[];
export type TempRangeList1 = number[];
export type AfterStartupMode = "Smoke" | "Hold";
export type PrimarySetpoint = number;
export type StartToHoldPrompt = boolean;
export type StartupExitTemp = number;
export type Build = number;
export type Cookfile = string;
export type Recipe2 = string;
export type Server = string;
export type Flags = SettingsFlag[];
export type Data =
  | SettingsSchema
  | {
      [k: string]: unknown | undefined;
    };
export type Errors = SaveFieldError[];
export type Message2 = string;
export type Result1 = "success" | "error";

export interface PiFireControllerWebContracts {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "AppriseService".
 */
export interface AppriseService {
  enabled?: Enabled;
  locations?: Locations;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "BoolControllerOption".
 */
export interface BoolControllerOption {
  hidden?: Hidden;
  option_default: OptionDefault;
  option_description: OptionDescription;
  option_friendly_name: OptionFriendlyName;
  option_max?: OptionMax;
  option_min?: OptionMin;
  option_name: OptionName;
  option_step?: OptionStep;
  option_type: OptionType;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ControllerCatalog".
 */
export interface ControllerCatalog {
  metadata: ControllerMetadata;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ControllerMetadata".
 */
export interface ControllerMetadata {
  [k: string]: ControllerDefinition | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ControllerDefinition".
 */
export interface ControllerDefinition {
  attributions: Attributions;
  author: Author;
  config: Config;
  contributors: Contributors;
  dependencies?: ControllerDependencies | null;
  description: Description;
  friendly_name: FriendlyName;
  image: Image;
  link: Link;
  module_name: ModuleName;
  recommendations?: ControllerRecommendations | null;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "FloatControllerOption".
 */
export interface FloatControllerOption {
  hidden?: Hidden1;
  option_default: OptionDefault1;
  option_description: OptionDescription1;
  option_friendly_name: OptionFriendlyName1;
  option_max?: OptionMax1;
  option_min?: OptionMin1;
  option_name: OptionName1;
  option_step?: OptionStep1;
  option_type: OptionType1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "IntControllerOption".
 */
export interface IntControllerOption {
  hidden?: Hidden2;
  option_default: OptionDefault2;
  option_description: OptionDescription2;
  option_friendly_name: OptionFriendlyName2;
  option_max?: OptionMax2;
  option_min?: OptionMin2;
  option_name: OptionName2;
  option_step?: OptionStep2;
  option_type: OptionType2;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ListControllerOption".
 */
export interface ListControllerOption {
  hidden?: Hidden3;
  list_labels?: ListLabels;
  list_values: ListValues;
  option_default: OptionDefault3;
  option_description: OptionDescription3;
  option_friendly_name: OptionFriendlyName3;
  option_max?: OptionMax3;
  option_min?: OptionMin3;
  option_name: OptionName3;
  option_step?: OptionStep3;
  option_type: OptionType3;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "StringControllerOption".
 */
export interface StringControllerOption {
  hidden?: Hidden4;
  option_default: OptionDefault4;
  option_description: OptionDescription4;
  option_friendly_name: OptionFriendlyName4;
  option_max?: OptionMax4;
  option_min?: OptionMin4;
  option_name: OptionName4;
  option_step?: OptionStep4;
  option_type: OptionType4;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ControllerDependencies".
 */
export interface ControllerDependencies {
  extra?: Extra;
  modules?: Modules;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ControllerRecommendations".
 */
export interface ControllerRecommendations {
  cycle: ControllerCycleRecommendation;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ControllerCycleRecommendation".
 */
export interface ControllerCycleRecommendation {
  cycle_ratio_max: CycleRatioMax;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ControllerConfigs".
 */
export interface ControllerConfigs {
  mpc?: MpcConfig;
  pid?: PidConfig;
  pid_sp?: PidSpConfig;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "MpcConfig".
 */
export interface MpcConfig {
  C_c?: CC;
  K_Q?: KQ;
  Q_w?: QW;
  R_dQ?: RDq;
  T_amb?: TAmb;
  control_period?: ControlPeriod;
  enable_fan_input?: EnableFanInput;
  enable_identification?: EnableIdentification;
  enable_online_adaptation?: EnableOnlineAdaptation;
  est_q_dist?: EstQDist;
  est_q_temp?: EstQTemp;
  est_r_meas?: EstRMeas;
  estimator?: Estimator;
  fan_max_pct?: FanMaxPct;
  fan_min_pct?: FanMinPct;
  h_amb?: HAmb;
  n_horizon?: NHorizon;
  sigma?: Sigma;
  theta?: Theta;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "PidConfig".
 */
export interface PidConfig {
  PB?: Pb;
  Td?: Td;
  Ti?: Ti;
  center?: Center;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "PidSpConfig".
 */
export interface PidSpConfig {
  PB?: Pb1;
  Td?: Td1;
  Ti?: Ti1;
  center_factor?: CenterFactor;
  stable_window?: StableWindow;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ControllerSettings".
 */
export interface ControllerSettings {
  config?: Config1;
  selected?: Selected;
}
export interface Config1 {
  [k: string]:
    | {
        [k: string]: (number | boolean | string) | undefined;
      }
    | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "CycleData".
 */
export interface CycleData {
  LidOpenDetectEnabled?: Lidopendetectenabled;
  LidOpenPauseTime?: Lidopenpausetime;
  LidOpenThreshold?: Lidopenthreshold;
  PMode?: Pmode;
  SmokeOffCycleTime?: Smokeoffcycletime;
  SmokeOnCycleTime?: Smokeoncycletime;
  u_max?: UMax;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "Dashboard".
 */
export interface Dashboard {
  current?: Current;
  dashboards?: Dashboards;
}
export interface Dashboards {
  [k: string]:
    | {
        [k: string]: unknown | undefined;
      }
    | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "DisplaySettings".
 */
export interface DisplaySettings {
  config?: Config2;
  selected?: Selected1;
  sleep_timeout?: SleepTimeout;
}
export interface Config2 {
  [k: string]:
    | {
        [k: string]: unknown | undefined;
      }
    | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "GlobalSettings".
 */
export interface GlobalSettings {
  augerrate?: Augerrate;
  boot_to_monitor?: BootToMonitor;
  debug_mode?: DebugMode;
  disp_rotation?: DispRotation;
  ext_data?: ExtData;
  first_time_setup?: FirstTimeSetup;
  grill_name?: GrillName;
  prime_ignition?: PrimeIgnition;
  python_exec?: PythonExec;
  units?: Units;
  updated_message?: UpdatedMessage;
  uv?: Uv;
  venv?: Venv;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "HistoryPage".
 */
export interface HistoryPage {
  autorefresh?: Autorefresh;
  clearhistoryonstart?: Clearhistoryonstart;
  datapoints?: Datapoints;
  fidelity_degrees?: FidelityDegrees;
  minutes?: Minutes;
  probe_config?: ProbeConfig;
}
export interface ProbeConfig {
  [k: string]: ProbeChartConfig | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ProbeChartConfig".
 */
export interface ProbeChartConfig {
  bg_color: BgColor;
  bg_color_setpoint?: BgColorSetpoint;
  bg_color_target: BgColorTarget;
  dash_setpoint: DashSetpoint;
  enabled: Enabled1;
  fill: Fill;
  line_color: LineColor;
  line_color_setpoint?: LineColorSetpoint;
  line_color_target: LineColorTarget;
  name: Name;
  type: Type;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "IftttService".
 */
export interface IftttService {
  APIKey?: Apikey;
  enabled?: Enabled2;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "InfluxdbService".
 */
export interface InfluxdbService {
  bucket?: Bucket;
  enabled?: Enabled3;
  org?: Org;
  token?: Token;
  url?: Url;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "KeepWarm".
 */
export interface KeepWarm {
  s_plus?: SPlus;
  temp?: Temp;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "LastUpdated".
 */
export interface LastUpdated {
  time: Time;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ModeData".
 */
export interface ModeData {
  mode: Mode;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ModeResponse".
 */
export interface ModeResponse {
  data?: ModeData | null;
  message?: Message;
  result: Result;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "Modules".
 */
export interface Modules1 {
  display?: Display;
  dist?: Dist;
  grillplat?: Grillplat;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "MqttService".
 */
export interface MqttService {
  broker?: Broker;
  enabled?: Enabled4;
  homeassistant_autodiscovery_topic?: HomeassistantAutodiscoveryTopic;
  id?: Id;
  password?: Password;
  port?: Port;
  update_sec?: UpdateSec;
  username?: Username;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "NotifyServices".
 */
export interface NotifyServices {
  apprise?: AppriseService1;
  ifttt?: IftttService1;
  influxdb?: InfluxdbService1;
  mqtt?: MqttService1;
  onesignal?: OneSignalService;
  pushbullet?: PushbulletService;
  pushover?: PushoverService;
  wled?: WledService;
}
export interface AppriseService1 {
  enabled?: Enabled;
  locations?: Locations;
}
export interface IftttService1 {
  APIKey?: Apikey;
  enabled?: Enabled2;
}
export interface InfluxdbService1 {
  bucket?: Bucket;
  enabled?: Enabled3;
  org?: Org;
  token?: Token;
  url?: Url;
}
export interface MqttService1 {
  broker?: Broker;
  enabled?: Enabled4;
  homeassistant_autodiscovery_topic?: HomeassistantAutodiscoveryTopic;
  id?: Id;
  password?: Password;
  port?: Port;
  update_sec?: UpdateSec;
  username?: Username;
}
export interface OneSignalService {
  app_id?: AppId;
  devices?: Devices;
  enabled?: Enabled5;
  uuid: Uuid;
}
export interface Devices {
  [k: string]: unknown | undefined;
}
export interface PushbulletService {
  APIKey?: Apikey1;
  PublicURL?: Publicurl;
  enabled?: Enabled6;
}
export interface PushoverService {
  APIKey?: Apikey2;
  PublicURL?: Publicurl1;
  UserKeys?: Userkeys;
  enabled?: Enabled7;
}
export interface WledService {
  device_address?: DeviceAddress;
  enabled?: Enabled8;
  event_presets?: WledEventPresets;
  mode_presets?: WledModePresets;
  notify_duration?: NotifyDuration;
  profile_numbers?: WledProfileNumbers;
  suggested_config?: WledSuggestedConfig;
  use_profiles?: UseProfiles;
  use_suggested_presets?: UseSuggestedPresets;
}
export interface WledEventPresets {
  Grill_Error?: GrillError;
  Pellet_Level_Low?: PelletLevelLow;
  Recipe_Next?: RecipeNext;
  Temp_Achieved?: TempAchieved;
  Timer_Expired?: TimerExpired;
}
export interface WledModePresets {
  Hold?: Hold;
  Prime?: Prime;
  Reignite?: Reignite;
  Shutdown?: Shutdown;
  Smoke?: Smoke;
  Startup?: Startup;
  Stop?: Stop;
}
export interface WledProfileNumbers {
  booting?: Booting;
  cooking?: Cooking;
  cooldown?: Cooldown;
  error_fault?: ErrorFault;
  idle?: Idle;
  low_pellets?: LowPellets;
  night_mode?: NightMode;
  overshoot_alarm?: OvershootAlarm;
  preheat?: Preheat;
  probe_alarm?: ProbeAlarm;
  target_reached?: TargetReached;
  timer_done?: TimerDone;
}
export interface WledSuggestedConfig {
  cooking_color?: CookingColor;
  idle_brightness?: IdleBrightness;
  led_count?: LedCount;
  night_mode?: NightMode1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "OneSignalService".
 */
export interface OneSignalService1 {
  app_id?: AppId;
  devices?: Devices;
  enabled?: Enabled5;
  uuid: Uuid;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "PelletLevel".
 */
export interface PelletLevel {
  empty?: Empty;
  full?: Full;
  warning_enabled?: WarningEnabled;
  warning_level?: WarningLevel;
  warning_time?: WarningTime;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "Platform".
 */
export interface Platform {
  buttonslevel?: Buttonslevel;
  current?: Current1;
  dc_fan?: DcFan;
  devices?: _DevicesConfig;
  fan_controller?: _FanControllerConfig;
  ft232h?: _FT232HConfig;
  inputs?: _InputsConfig;
  mcp2221?: _MCP2221Config;
  numato?: _NumatoConfig;
  outputs?: _OutputsConfig;
  real_hw?: RealHw;
  standalone?: Standalone;
  system?: _SystemConfig;
  system_type?: SystemType;
  triggerlevel?: Triggerlevel;
}
export interface _DevicesConfig {
  display?: _DisplayDeviceConfig;
  distance?: _DistanceDeviceConfig;
  input?: _InputDeviceConfig;
}
export interface _DisplayDeviceConfig {
  dc?: Dc;
  led?: Led;
  rst?: Rst;
}
export interface _DistanceDeviceConfig {
  address?: Address;
  device?: Device;
  echo?: Echo;
  i2c_bus?: I2CBus;
  trig?: Trig;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_BasicBus".
 */
export interface _BasicBus {
  kind?: Kind;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_KernelBusNumber".
 */
export interface _KernelBusNumber {
  bus_num: BusNum;
  kind?: Kind1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_KernelAdapterName".
 */
export interface _KernelAdapterName {
  adapter: Adapter;
  kind?: Kind2;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_KernelSerialMatch".
 */
export interface _KernelSerialMatch {
  kind?: Kind3;
  serial: Serial;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_FT232hBus".
 */
export interface _FT232HBus {
  kind?: Kind4;
  url?: Url1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_MCP2221Bus".
 */
export interface _MCP2221Bus {
  kind?: Kind5;
  serial?: Serial1;
}
export interface _InputDeviceConfig {
  down_dt?: DownDt;
  enter_sw?: EnterSw;
  up_clk?: UpClk;
}
export interface _FanControllerConfig {
  address?: Address1;
  chip?: Chip;
  i2c_bus?: I2CBus1;
}
export interface _FT232HConfig {
  url?: Url2;
}
export interface _InputsConfig {
  selector?: Selector;
  shutdown?: Shutdown1;
}
export interface _MCP2221Config {
  serial?: Serial2;
}
export interface _NumatoConfig {
  baudrate?: Baudrate;
  device?: Device1;
}
export interface _OutputsConfig {
  auger?: Auger;
  dc_fan?: DcFan1;
  fan?: Fan;
  igniter?: Igniter;
  power?: Power;
  pwm?: Pwm;
}
export interface _SystemConfig {
  "1WIRE"?: Wire;
  SPI0?: _SPI0Config;
}
export interface _SPI0Config {
  CE0?: Ce0;
  CE1?: Ce1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ProbeMap".
 */
export interface ProbeMap {
  probe_devices?: ProbeDevices;
  probe_info?: ProbeInfo;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ProbeSettings".
 */
export interface ProbeSettings {
  probe_map?: ProbeMap1;
  probe_profiles?: ProbeProfiles;
}
export interface ProbeMap1 {
  probe_devices?: ProbeDevices;
  probe_info?: ProbeInfo;
}
export interface ProbeProfiles {
  [k: string]:
    | {
        [k: string]: unknown | undefined;
      }
    | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "PushbulletService".
 */
export interface PushbulletService1 {
  APIKey?: Apikey1;
  PublicURL?: Publicurl;
  enabled?: Enabled6;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "PushoverService".
 */
export interface PushoverService1 {
  APIKey?: Apikey2;
  PublicURL?: Publicurl1;
  UserKeys?: Userkeys;
  enabled?: Enabled7;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "PwmProfile".
 */
export interface PwmProfile {
  duty_cycle: DutyCycle;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "PwmSettings".
 */
export interface PwmSettings {
  frequency?: Frequency;
  max_duty_cycle?: MaxDutyCycle;
  min_duty_cycle?: MinDutyCycle;
  profiles?: Profiles;
  pwm_control?: PwmControl;
  temp_range_list?: TempRangeList;
  update_time?: UpdateTime;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "Recipe".
 */
export interface Recipe {
  probe_map?: RecipeProbeMap;
}
export interface RecipeProbeMap {
  food?: Food;
  primary?: Primary;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "RecipeProbeMap".
 */
export interface RecipeProbeMap1 {
  food?: Food;
  primary?: Primary;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SafetySettings".
 */
export interface SafetySettings {
  allow_manual_changes?: AllowManualChanges;
  manual_override_time?: ManualOverrideTime;
  maxstartuptemp?: Maxstartuptemp;
  maxtemp?: Maxtemp;
  minstartuptemp?: Minstartuptemp;
  reigniteretries?: Reigniteretries;
  startup_check?: StartupCheck;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SaveFieldError".
 */
export interface SaveFieldError {
  message: Message1;
  path: Path;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ServerInfo".
 */
export interface ServerInfo {
  uuid: Uuid1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SettingsResponse".
 */
export interface SettingsResponse {
  settings: SettingsSchema;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SettingsSchema".
 */
export interface SettingsSchema {
  controller?: ControllerSettings1;
  cycle_data?: CycleData1;
  dashboard?: Dashboard1;
  display?: DisplaySettings1;
  globals?: GlobalSettings1;
  history_page?: HistoryPage1;
  keep_warm?: KeepWarm1;
  lastupdated: LastUpdated;
  modules?: Modules2;
  notify_services?: NotifyServices1;
  pelletlevel?: PelletLevel1;
  platform?: Platform1;
  probe_settings?: ProbeSettings1;
  pwm?: PwmSettings1;
  recipe?: Recipe1;
  safety?: SafetySettings1;
  schema_version?: SchemaVersion;
  server_info: ServerInfo;
  shutdown?: ShutdownSettings;
  smoke_plus?: SmokePlus;
  startup?: StartupSettings;
  versions: Versions;
}
export interface ControllerSettings1 {
  config?: Config1;
  selected?: Selected;
}
export interface CycleData1 {
  LidOpenDetectEnabled?: Lidopendetectenabled;
  LidOpenPauseTime?: Lidopenpausetime;
  LidOpenThreshold?: Lidopenthreshold;
  PMode?: Pmode;
  SmokeOffCycleTime?: Smokeoffcycletime;
  SmokeOnCycleTime?: Smokeoncycletime;
  u_max?: UMax;
}
export interface Dashboard1 {
  current?: Current;
  dashboards?: Dashboards;
}
export interface DisplaySettings1 {
  config?: Config2;
  selected?: Selected1;
  sleep_timeout?: SleepTimeout;
}
export interface GlobalSettings1 {
  augerrate?: Augerrate;
  boot_to_monitor?: BootToMonitor;
  debug_mode?: DebugMode;
  disp_rotation?: DispRotation;
  ext_data?: ExtData;
  first_time_setup?: FirstTimeSetup;
  grill_name?: GrillName;
  prime_ignition?: PrimeIgnition;
  python_exec?: PythonExec;
  units?: Units;
  updated_message?: UpdatedMessage;
  uv?: Uv;
  venv?: Venv;
}
export interface HistoryPage1 {
  autorefresh?: Autorefresh;
  clearhistoryonstart?: Clearhistoryonstart;
  datapoints?: Datapoints;
  fidelity_degrees?: FidelityDegrees;
  minutes?: Minutes;
  probe_config?: ProbeConfig;
}
export interface KeepWarm1 {
  s_plus?: SPlus;
  temp?: Temp;
}
export interface Modules2 {
  display?: Display;
  dist?: Dist;
  grillplat?: Grillplat;
}
export interface NotifyServices1 {
  apprise?: AppriseService1;
  ifttt?: IftttService1;
  influxdb?: InfluxdbService1;
  mqtt?: MqttService1;
  onesignal?: OneSignalService;
  pushbullet?: PushbulletService;
  pushover?: PushoverService;
  wled?: WledService;
}
export interface PelletLevel1 {
  empty?: Empty;
  full?: Full;
  warning_enabled?: WarningEnabled;
  warning_level?: WarningLevel;
  warning_time?: WarningTime;
}
export interface Platform1 {
  buttonslevel?: Buttonslevel;
  current?: Current1;
  dc_fan?: DcFan;
  devices?: _DevicesConfig;
  fan_controller?: _FanControllerConfig;
  ft232h?: _FT232HConfig;
  inputs?: _InputsConfig;
  mcp2221?: _MCP2221Config;
  numato?: _NumatoConfig;
  outputs?: _OutputsConfig;
  real_hw?: RealHw;
  standalone?: Standalone;
  system?: _SystemConfig;
  system_type?: SystemType;
  triggerlevel?: Triggerlevel;
}
export interface ProbeSettings1 {
  probe_map?: ProbeMap1;
  probe_profiles?: ProbeProfiles;
}
export interface PwmSettings1 {
  frequency?: Frequency;
  max_duty_cycle?: MaxDutyCycle;
  min_duty_cycle?: MinDutyCycle;
  profiles?: Profiles;
  pwm_control?: PwmControl;
  temp_range_list?: TempRangeList;
  update_time?: UpdateTime;
}
export interface Recipe1 {
  probe_map?: RecipeProbeMap;
}
export interface SafetySettings1 {
  allow_manual_changes?: AllowManualChanges;
  manual_override_time?: ManualOverrideTime;
  maxstartuptemp?: Maxstartuptemp;
  maxtemp?: Maxtemp;
  minstartuptemp?: Minstartuptemp;
  reigniteretries?: Reigniteretries;
  startup_check?: StartupCheck;
}
export interface ShutdownSettings {
  auto_power_off?: AutoPowerOff;
  shutdown_duration?: ShutdownDuration;
}
export interface SmokePlus {
  duty_cycle?: DutyCycle1;
  enabled?: Enabled9;
  fan_ramp?: FanRamp;
  max_temp?: MaxTemp;
  min_temp?: MinTemp;
  off_time?: OffTime;
  on_time?: OnTime;
}
export interface StartupSettings {
  duration?: Duration;
  prime_on_startup?: PrimeOnStartup;
  pwm_duty_cycle?: PwmDutyCycle;
  smartstart?: SmartStart;
  start_to_mode?: StartToMode;
  startup_exit_temp?: StartupExitTemp;
}
export interface SmartStart {
  enabled?: Enabled10;
  exit_temp?: ExitTemp;
  profiles?: Profiles1;
  temp_range_list?: TempRangeList1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SmartStartProfile".
 */
export interface SmartStartProfile {
  augerontime: Augerontime;
  p_mode: PMode;
  startuptime: Startuptime;
}
export interface StartToMode {
  after_startup_mode?: AfterStartupMode;
  primary_setpoint?: PrimarySetpoint;
  start_to_hold_prompt?: StartToHoldPrompt;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "Versions".
 */
export interface Versions {
  build: Build;
  cookfile: Cookfile;
  recipe: Recipe2;
  server: Server;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SettingsUpdateRequest".
 */
export interface SettingsUpdateRequest {
  flags?: Flags;
  settings?: Settings;
}
export interface Settings {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SettingsUpdateResponse".
 */
export interface SettingsUpdateResponse {
  data: Data;
  errors: Errors;
  message: Message2;
  result: Result1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ShutdownSettings".
 */
export interface ShutdownSettings1 {
  auto_power_off?: AutoPowerOff;
  shutdown_duration?: ShutdownDuration;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SmartStart".
 */
export interface SmartStart1 {
  enabled?: Enabled10;
  exit_temp?: ExitTemp;
  profiles?: Profiles1;
  temp_range_list?: TempRangeList1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SmokePlus".
 */
export interface SmokePlus1 {
  duty_cycle?: DutyCycle1;
  enabled?: Enabled9;
  fan_ramp?: FanRamp;
  max_temp?: MaxTemp;
  min_temp?: MinTemp;
  off_time?: OffTime;
  on_time?: OnTime;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "StartToMode".
 */
export interface StartToMode1 {
  after_startup_mode?: AfterStartupMode;
  primary_setpoint?: PrimarySetpoint;
  start_to_hold_prompt?: StartToHoldPrompt;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "StartupSettings".
 */
export interface StartupSettings1 {
  duration?: Duration;
  prime_on_startup?: PrimeOnStartup;
  pwm_duty_cycle?: PwmDutyCycle;
  smartstart?: SmartStart;
  start_to_mode?: StartToMode;
  startup_exit_temp?: StartupExitTemp;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "WledEventPresets".
 */
export interface WledEventPresets1 {
  Grill_Error?: GrillError;
  Pellet_Level_Low?: PelletLevelLow;
  Recipe_Next?: RecipeNext;
  Temp_Achieved?: TempAchieved;
  Timer_Expired?: TimerExpired;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "WledModePresets".
 */
export interface WledModePresets1 {
  Hold?: Hold;
  Prime?: Prime;
  Reignite?: Reignite;
  Shutdown?: Shutdown;
  Smoke?: Smoke;
  Startup?: Startup;
  Stop?: Stop;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "WledProfileNumbers".
 */
export interface WledProfileNumbers1 {
  booting?: Booting;
  cooking?: Cooking;
  cooldown?: Cooldown;
  error_fault?: ErrorFault;
  idle?: Idle;
  low_pellets?: LowPellets;
  night_mode?: NightMode;
  overshoot_alarm?: OvershootAlarm;
  preheat?: Preheat;
  probe_alarm?: ProbeAlarm;
  target_reached?: TargetReached;
  timer_done?: TimerDone;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "WledService".
 */
export interface WledService1 {
  device_address?: DeviceAddress;
  enabled?: Enabled8;
  event_presets?: WledEventPresets;
  mode_presets?: WledModePresets;
  notify_duration?: NotifyDuration;
  profile_numbers?: WledProfileNumbers;
  suggested_config?: WledSuggestedConfig;
  use_profiles?: UseProfiles;
  use_suggested_presets?: UseSuggestedPresets;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "WledSuggestedConfig".
 */
export interface WledSuggestedConfig1 {
  cooking_color?: CookingColor;
  idle_brightness?: IdleBrightness;
  led_count?: LedCount;
  night_mode?: NightMode1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_DevicesConfig".
 */
export interface _DevicesConfig1 {
  display?: _DisplayDeviceConfig;
  distance?: _DistanceDeviceConfig;
  input?: _InputDeviceConfig;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_DisplayDeviceConfig".
 */
export interface _DisplayDeviceConfig1 {
  dc?: Dc;
  led?: Led;
  rst?: Rst;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_DistanceDeviceConfig".
 */
export interface _DistanceDeviceConfig1 {
  address?: Address;
  device?: Device;
  echo?: Echo;
  i2c_bus?: I2CBus;
  trig?: Trig;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_FT232hConfig".
 */
export interface _FT232HConfig1 {
  url?: Url2;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_FanControllerConfig".
 */
export interface _FanControllerConfig1 {
  address?: Address1;
  chip?: Chip;
  i2c_bus?: I2CBus1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_InputDeviceConfig".
 */
export interface _InputDeviceConfig1 {
  down_dt?: DownDt;
  enter_sw?: EnterSw;
  up_clk?: UpClk;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_InputsConfig".
 */
export interface _InputsConfig1 {
  selector?: Selector;
  shutdown?: Shutdown1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_MCP2221Config".
 */
export interface _MCP2221Config1 {
  serial?: Serial2;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_NumatoConfig".
 */
export interface _NumatoConfig1 {
  baudrate?: Baudrate;
  device?: Device1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_OutputsConfig".
 */
export interface _OutputsConfig1 {
  auger?: Auger;
  dc_fan?: DcFan1;
  fan?: Fan;
  igniter?: Igniter;
  power?: Power;
  pwm?: Pwm;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_SPI0Config".
 */
export interface _SPI0Config1 {
  CE0?: Ce0;
  CE1?: Ce1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_SystemConfig".
 */
export interface _SystemConfig1 {
  "1WIRE"?: Wire;
  SPI0?: _SPI0Config;
}
