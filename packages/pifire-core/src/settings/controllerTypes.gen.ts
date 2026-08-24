/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

type Enabled = boolean;
type Locations = string[];
type Hidden = boolean;
type OptionDefault = boolean;
type OptionDescription = string;
type OptionFriendlyName = string;
type OptionMax = number | null;
type OptionMin = number | null;
type OptionName = string;
type OptionStep = number | null;
type OptionType = "bool";
type Attributions = string[];
type Author = string;
type Hidden1 = boolean;
type OptionDefault1 = number;
type OptionDescription1 = string;
type OptionFriendlyName1 = string;
type OptionMax1 = number | null;
type OptionMin1 = number | null;
type OptionName1 = string;
type OptionStep1 = number | null;
type OptionType1 = "float";
type Hidden2 = boolean;
type OptionDefault2 = number;
type OptionDescription2 = string;
type OptionFriendlyName2 = string;
type OptionMax2 = number | null;
type OptionMin2 = number | null;
type OptionName2 = string;
type OptionStep2 = number | null;
type OptionType2 = "int";
type Hidden3 = boolean;
type ListLabels = string[] | null;
type ListValues = (number | boolean | string)[];
type OptionDefault3 = number | boolean | string;
type OptionDescription3 = string;
type OptionFriendlyName3 = string;
type OptionMax3 = number | null;
type OptionMin3 = number | null;
type OptionName3 = string;
type OptionStep3 = number | null;
type OptionType3 = "list";
type Hidden4 = boolean;
type OptionDefault4 = string;
type OptionDescription4 = string;
type OptionFriendlyName4 = string;
type OptionMax4 = number | null;
type OptionMin4 = number | null;
type OptionName4 = string;
type OptionStep4 = number | null;
type OptionType4 = "string";
type Config = (
  | FloatControllerOption
  | IntControllerOption
  | BoolControllerOption
  | ListControllerOption
  | StringControllerOption
)[];
type Contributors = string[];
type Extra = string | null;
type Modules = string[];
type Description = string;
type FriendlyName = string;
type Image = string;
type Link = string;
type ModuleName = string;
type CycleRatioMax = number;
type CC = number;
type KQ = number;
type QW = number;
type RDq = number;
type TAmb = number;
type ControlPeriod = number;
type EnableFanInput = boolean;
type EnableIdentification = boolean;
type EnableOnlineAdaptation = boolean;
type EstQDist = number;
type EstQTemp = number;
type EstRMeas = number;
type Estimator = "ekf" | "kf";
type FanMaxPct = number;
type FanMinPct = number;
type HAmb = number;
type NHorizon = number;
type Sigma = number;
type Theta = number;
type Pb = number;
type Td = number;
type Ti = number;
type Center = number;
type Pb1 = number;
type Td1 = number;
type Ti1 = number;
type CenterFactor = number;
type StableWindow = number;
type Selected = string;
type Lidopendetectenabled = boolean;
type Lidopenpausetime = number;
type Lidopenthreshold = number;
type Pmode = number;
type Smokeoffcycletime = number;
type Smokeoncycletime = number;
type UMax = number;
type Current = string;
type Selected1 = string;
type SleepTimeout = number;
type Augerrate = number;
type BootToMonitor = boolean;
type DebugMode = boolean;
type DispRotation = number;
type ExtData = boolean;
type FirstTimeSetup = boolean;
type GrillName = string;
type PrimeIgnition = boolean;
type PythonExec = string;
type Units = "F" | "C";
type UpdatedMessage = boolean;
type Uv = boolean;
type Venv = boolean;
type Autorefresh = "on" | "off";
type Clearhistoryonstart = boolean;
type Datapoints = number;
type FidelityDegrees = number;
type Minutes = number;
type BgColor = string;
type BgColorSetpoint = string | null;
type BgColorTarget = string;
type DashSetpoint = boolean;
type Enabled1 = boolean;
type Fill = boolean;
type LineColor = string;
type LineColorSetpoint = string | null;
type LineColorTarget = string;
type Name = string;
type Type = string;
type Apikey = string;
type Enabled2 = boolean;
type Bucket = string;
type Enabled3 = boolean;
type Org = string;
type Token = string;
type Url = string;
type SPlus = boolean;
type Temp = number;
type Time = number;
type Mode = string;
type Message = string;
type Result = "OK" | "ERROR";
type Display = string;
type Dist = string;
type Grillplat = string;
type Broker = string;
type Enabled4 = boolean;
type HomeassistantAutodiscoveryTopic = string;
type Id = string;
type Password = string;
type Port = string;
type UpdateSec = string;
type Username = string;
type AppId = string;
type Enabled5 = boolean;
type Uuid = string;
type Apikey1 = string;
type Publicurl = string;
type Enabled6 = boolean;
type Apikey2 = string;
type Publicurl1 = string;
type Userkeys = string;
type Enabled7 = boolean;
type DeviceAddress = string;
type Enabled8 = boolean;
type GrillError = number;
type PelletLevelLow = number;
type RecipeNext = number;
type TempAchieved = number;
type TimerExpired = number;
type Hold = number;
type Prime = number;
type Reignite = number;
type Shutdown = number;
type Smoke = number;
type Startup = number;
type Stop = number;
type NotifyDuration = number;
type Booting = number;
type Cooking = number;
type Cooldown = number;
type ErrorFault = number;
type Idle = number;
type LowPellets = number;
type NightMode = number;
type OvershootAlarm = number;
type Preheat = number;
type ProbeAlarm = number;
type TargetReached = number;
type TimerDone = number;
type CookingColor = string;
type IdleBrightness = number;
type LedCount = number;
type NightMode1 = boolean;
type UseProfiles = boolean;
type UseSuggestedPresets = boolean;
type Empty = number;
type Full = number;
type WarningEnabled = boolean;
type WarningLevel = number;
type WarningTime = number;
type Buttonslevel = string;
type Current1 = string;
type DcFan = boolean;
type Dc = number;
type Led = number;
type Rst = number;
type Address = string | number | null;
type Device = string;
type Echo = number;
type I2CBus =
  | _BasicBus
  | _KernelBusNumber
  | _KernelAdapterName
  | _KernelSerialMatch
  | _FT232HBus
  | _MCP2221Bus;
type Kind = "basic";
type BusNum = number;
type Kind1 = "kernel";
type Adapter = string;
type Kind2 = "kernel";
type Kind3 = "kernel";
type Serial = string;
type Kind4 = "ft232h";
type Url1 = string;
type Kind5 = "mcp2221";
type Serial1 = string;
type Trig = number;
type DownDt = number;
type EnterSw = number;
type UpClk = number;
type Address1 = string;
type Chip = string;
type I2CBus1 =
  | _BasicBus
  | _KernelBusNumber
  | _KernelAdapterName
  | _KernelSerialMatch
  | _FT232HBus
  | _MCP2221Bus;
type Url2 = string;
type Selector = number | null;
type Shutdown1 = number | null;
type Serial2 = string;
type Baudrate = number;
type Device1 = string;
type Auger = number | string | null;
type DcFan1 = number | string | null;
type Fan = number | string | null;
type Igniter = number | string | null;
type Power = number | string | null;
type Pwm = number | string | null;
type RealHw = boolean;
type Standalone = boolean;
type Wire = number | null;
type Ce0 = number;
type Ce1 = number;
type SystemType = string;
type Triggerlevel = string;
type ProbeDevices = {
  [k: string]: unknown | undefined;
}[];
type ProbeInfo = {
  [k: string]: unknown | undefined;
}[];
type DutyCycle = number;
type Frequency = number;
type MaxDutyCycle = number;
type MinDutyCycle = number;
type Profiles = PwmProfile[];
type PwmControl = boolean;
type TempRangeList = number[];
type UpdateTime = number;
type Food = string[];
type Primary = string;
type AllowManualChanges = boolean;
type ManualOverrideTime = number;
type Maxstartuptemp = number;
type Maxtemp = number;
type Minstartuptemp = number;
type Reigniteretries = number;
type StartupCheck = boolean;
type Message1 = string;
type Path = string;
type Uuid1 = string;
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SettingsFlag".
 */
export type SettingsFlag =
  | "settings_update"
  | "controller_update"
  | "distance_update"
  | "probe_profile_update";
type SchemaVersion = number;
type AutoPowerOff = boolean;
type ShutdownDuration = number;
type DutyCycle1 = number;
type Enabled9 = boolean;
type FanRamp = boolean;
type MaxTemp = number;
type MinTemp = number;
type OffTime = number;
type OnTime = number;
type Duration = number;
type PrimeOnStartup = number;
type PwmDutyCycle = number;
type Enabled10 = boolean;
type ExitTemp = number;
type Augerontime = number;
type PMode = number;
type Startuptime = number;
type Profiles1 = SmartStartProfile[];
type TempRangeList1 = number[];
type AfterStartupMode = "Smoke" | "Hold";
type PrimarySetpoint = number;
type StartToHoldPrompt = boolean;
type StartupExitTemp = number;
type InferencePolicy = "off" | "observe" | "enforce";
type Build = number;
type Cookfile = string;
type Recipe2 = string;
type Server = string;
type Flags = SettingsFlag[];
type Data =
  | SettingsSchema
  | {
      [k: string]: unknown | undefined;
    };
type Errors = SaveFieldError[];
type Message2 = string;
type Result1 = "success" | "error";

export interface PiFireControllerWebContracts {
  [k: string]: unknown | undefined;
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
interface Config1 {
  [k: string]:
    | {
        [k: string]: (number | boolean | string) | undefined;
      }
    | undefined;
}
interface Dashboards {
  [k: string]:
    | {
        [k: string]: unknown | undefined;
      }
    | undefined;
}
interface Config2 {
  [k: string]:
    | {
        [k: string]: unknown | undefined;
      }
    | undefined;
}
interface ProbeConfig {
  [k: string]: ProbeChartConfig | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "ProbeChartConfig".
 */
interface ProbeChartConfig {
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
 * via the `definition` "LastUpdated".
 */
interface LastUpdated {
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
interface AppriseService1 {
  enabled?: Enabled;
  locations?: Locations;
}
interface IftttService1 {
  APIKey?: Apikey;
  enabled?: Enabled2;
}
interface InfluxdbService1 {
  bucket?: Bucket;
  enabled?: Enabled3;
  org?: Org;
  token?: Token;
  url?: Url;
}
interface MqttService1 {
  broker?: Broker;
  enabled?: Enabled4;
  homeassistant_autodiscovery_topic?: HomeassistantAutodiscoveryTopic;
  id?: Id;
  password?: Password;
  port?: Port;
  update_sec?: UpdateSec;
  username?: Username;
}
interface OneSignalService {
  app_id?: AppId;
  devices?: Devices;
  enabled?: Enabled5;
  uuid: Uuid;
}
interface Devices {
  [k: string]: unknown | undefined;
}
interface PushbulletService {
  APIKey?: Apikey1;
  PublicURL?: Publicurl;
  enabled?: Enabled6;
}
interface PushoverService {
  APIKey?: Apikey2;
  PublicURL?: Publicurl1;
  UserKeys?: Userkeys;
  enabled?: Enabled7;
}
interface WledService {
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
interface WledEventPresets {
  Grill_Error?: GrillError;
  Pellet_Level_Low?: PelletLevelLow;
  Recipe_Next?: RecipeNext;
  Temp_Achieved?: TempAchieved;
  Timer_Expired?: TimerExpired;
}
interface WledModePresets {
  Hold?: Hold;
  Prime?: Prime;
  Reignite?: Reignite;
  Shutdown?: Shutdown;
  Smoke?: Smoke;
  Startup?: Startup;
  Stop?: Stop;
}
interface WledProfileNumbers {
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
interface WledSuggestedConfig {
  cooking_color?: CookingColor;
  idle_brightness?: IdleBrightness;
  led_count?: LedCount;
  night_mode?: NightMode1;
}
interface _DevicesConfig {
  display?: _DisplayDeviceConfig;
  distance?: _DistanceDeviceConfig;
  input?: _InputDeviceConfig;
}
interface _DisplayDeviceConfig {
  dc?: Dc;
  led?: Led;
  rst?: Rst;
}
interface _DistanceDeviceConfig {
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
interface _BasicBus {
  kind?: Kind;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_KernelBusNumber".
 */
interface _KernelBusNumber {
  bus_num: BusNum;
  kind?: Kind1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_KernelAdapterName".
 */
interface _KernelAdapterName {
  adapter: Adapter;
  kind?: Kind2;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_KernelSerialMatch".
 */
interface _KernelSerialMatch {
  kind?: Kind3;
  serial: Serial;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_FT232hBus".
 */
interface _FT232HBus {
  kind?: Kind4;
  url?: Url1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "_MCP2221Bus".
 */
interface _MCP2221Bus {
  kind?: Kind5;
  serial?: Serial1;
}
interface _InputDeviceConfig {
  down_dt?: DownDt;
  enter_sw?: EnterSw;
  up_clk?: UpClk;
}
interface _FanControllerConfig {
  address?: Address1;
  chip?: Chip;
  i2c_bus?: I2CBus1;
}
interface _FT232HConfig {
  url?: Url2;
}
interface _InputsConfig {
  selector?: Selector;
  shutdown?: Shutdown1;
}
interface _MCP2221Config {
  serial?: Serial2;
}
interface _NumatoConfig {
  baudrate?: Baudrate;
  device?: Device1;
}
interface _OutputsConfig {
  auger?: Auger;
  dc_fan?: DcFan1;
  fan?: Fan;
  igniter?: Igniter;
  power?: Power;
  pwm?: Pwm;
}
interface _SystemConfig {
  "1WIRE"?: Wire;
  SPI0?: _SPI0Config;
}
interface _SPI0Config {
  CE0?: Ce0;
  CE1?: Ce1;
}
interface ProbeMap1 {
  probe_devices?: ProbeDevices;
  probe_info?: ProbeInfo;
}
interface ProbeProfiles {
  [k: string]:
    | {
        [k: string]: unknown | undefined;
      }
    | undefined;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "PwmProfile".
 */
interface PwmProfile {
  duty_cycle: DutyCycle;
}
interface RecipeProbeMap {
  food?: Food;
  primary?: Primary;
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
interface ServerInfo {
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
interface SettingsSchema {
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
  thermocouple_health?: ThermocoupleHealthSettings;
  versions: Versions;
}
interface ControllerSettings1 {
  config?: Config1;
  selected?: Selected;
}
interface CycleData1 {
  LidOpenDetectEnabled?: Lidopendetectenabled;
  LidOpenPauseTime?: Lidopenpausetime;
  LidOpenThreshold?: Lidopenthreshold;
  PMode?: Pmode;
  SmokeOffCycleTime?: Smokeoffcycletime;
  SmokeOnCycleTime?: Smokeoncycletime;
  u_max?: UMax;
}
interface Dashboard1 {
  current?: Current;
  dashboards?: Dashboards;
}
interface DisplaySettings1 {
  config?: Config2;
  selected?: Selected1;
  sleep_timeout?: SleepTimeout;
}
interface GlobalSettings1 {
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
interface HistoryPage1 {
  autorefresh?: Autorefresh;
  clearhistoryonstart?: Clearhistoryonstart;
  datapoints?: Datapoints;
  fidelity_degrees?: FidelityDegrees;
  minutes?: Minutes;
  probe_config?: ProbeConfig;
}
interface KeepWarm1 {
  s_plus?: SPlus;
  temp?: Temp;
}
interface Modules2 {
  display?: Display;
  dist?: Dist;
  grillplat?: Grillplat;
}
interface NotifyServices1 {
  apprise?: AppriseService1;
  ifttt?: IftttService1;
  influxdb?: InfluxdbService1;
  mqtt?: MqttService1;
  onesignal?: OneSignalService;
  pushbullet?: PushbulletService;
  pushover?: PushoverService;
  wled?: WledService;
}
interface PelletLevel1 {
  empty?: Empty;
  full?: Full;
  warning_enabled?: WarningEnabled;
  warning_level?: WarningLevel;
  warning_time?: WarningTime;
}
interface Platform1 {
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
interface ProbeSettings1 {
  probe_map?: ProbeMap1;
  probe_profiles?: ProbeProfiles;
}
interface PwmSettings1 {
  frequency?: Frequency;
  max_duty_cycle?: MaxDutyCycle;
  min_duty_cycle?: MinDutyCycle;
  profiles?: Profiles;
  pwm_control?: PwmControl;
  temp_range_list?: TempRangeList;
  update_time?: UpdateTime;
}
interface Recipe1 {
  probe_map?: RecipeProbeMap;
}
interface SafetySettings1 {
  allow_manual_changes?: AllowManualChanges;
  manual_override_time?: ManualOverrideTime;
  maxstartuptemp?: Maxstartuptemp;
  maxtemp?: Maxtemp;
  minstartuptemp?: Minstartuptemp;
  reigniteretries?: Reigniteretries;
  startup_check?: StartupCheck;
}
interface ShutdownSettings {
  auto_power_off?: AutoPowerOff;
  shutdown_duration?: ShutdownDuration;
}
interface SmokePlus {
  duty_cycle?: DutyCycle1;
  enabled?: Enabled9;
  fan_ramp?: FanRamp;
  max_temp?: MaxTemp;
  min_temp?: MinTemp;
  off_time?: OffTime;
  on_time?: OnTime;
}
interface StartupSettings {
  duration?: Duration;
  prime_on_startup?: PrimeOnStartup;
  pwm_duty_cycle?: PwmDutyCycle;
  smartstart?: SmartStart;
  start_to_mode?: StartToMode;
  startup_exit_temp?: StartupExitTemp;
}
interface SmartStart {
  enabled?: Enabled10;
  exit_temp?: ExitTemp;
  profiles?: Profiles1;
  temp_range_list?: TempRangeList1;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "SmartStartProfile".
 */
interface SmartStartProfile {
  augerontime: Augerontime;
  p_mode: PMode;
  startuptime: Startuptime;
}
interface StartToMode {
  after_startup_mode?: AfterStartupMode;
  primary_setpoint?: PrimarySetpoint;
  start_to_hold_prompt?: StartToHoldPrompt;
}
interface ThermocoupleHealthSettings {
  inference_policy?: InferencePolicy;
}
/**
 * This interface was referenced by `PiFireControllerWebContracts`'s JSON-Schema
 * via the `definition` "Versions".
 */
interface Versions {
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
interface Settings {
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
