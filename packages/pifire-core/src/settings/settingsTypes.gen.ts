/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

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
export type Units = "F" | "C";
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
type Enabled = boolean;
type Fill = boolean;
type LineColor = string;
type LineColorSetpoint = string | null;
type LineColorTarget = string;
type Name = string;
type Type = string;
type SPlus = boolean;
type Temp = number;
type Time = number;
type Display = string;
type Dist = string;
type Grillplat = string;
type Enabled1 = boolean;
type Locations = string[];
type Apikey = string;
type Enabled2 = boolean;
type Bucket = string;
type Enabled3 = boolean;
type Org = string;
type Token = string;
type Url = string;
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
type Frequency = number;
type MaxDutyCycle = number;
type MinDutyCycle = number;
type DutyCycle = number;
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
type SchemaVersion = number;
type Uuid1 = string;
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
export type AfterStartupMode = "Smoke" | "Hold";
type PrimarySetpoint = number;
type StartToHoldPrompt = boolean;
type StartupExitTemp = number;
type InferencePolicy = "off" | "observe" | "enforce";
type Build = number;
type Cookfile = string;
type Recipe1 = string;
type Server = string;

export interface SettingsSchema {
  controller?: ControllerSettings;
  cycle_data?: CycleData;
  dashboard?: Dashboard;
  display?: DisplaySettings;
  globals?: GlobalSettings;
  history_page?: HistoryPage;
  keep_warm?: KeepWarm;
  lastupdated: LastUpdated;
  modules?: Modules;
  notify_services?: NotifyServices;
  pelletlevel?: PelletLevel;
  platform?: Platform;
  probe_settings?: ProbeSettings;
  pwm?: PwmSettings;
  recipe?: Recipe;
  safety?: SafetySettings;
  schema_version?: SchemaVersion;
  server_info: ServerInfo;
  shutdown?: ShutdownSettings;
  smoke_plus?: SmokePlus;
  startup?: StartupSettings;
  thermocouple_health?: ThermocoupleHealthSettings;
  versions: Versions;
}
export interface ControllerSettings {
  config?: Config;
  selected?: Selected;
}
interface Config {
  [k: string]:
    | {
        [k: string]: number | boolean | string | undefined;
      }
    | undefined;
}
export interface CycleData {
  LidOpenDetectEnabled?: Lidopendetectenabled;
  LidOpenPauseTime?: Lidopenpausetime;
  LidOpenThreshold?: Lidopenthreshold;
  PMode?: Pmode;
  SmokeOffCycleTime?: Smokeoffcycletime;
  SmokeOnCycleTime?: Smokeoncycletime;
  u_max?: UMax;
}
export interface Dashboard {
  current?: Current;
  dashboards?: Dashboards;
}
interface Dashboards {
  [k: string]:
    | {
        [k: string]: unknown | undefined;
      }
    | undefined;
}
export interface DisplaySettings {
  config?: Config1;
  selected?: Selected1;
  sleep_timeout?: SleepTimeout;
}
interface Config1 {
  [k: string]:
    | {
        [k: string]: unknown | undefined;
      }
    | undefined;
}
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
export interface HistoryPage {
  autorefresh?: Autorefresh;
  clearhistoryonstart?: Clearhistoryonstart;
  datapoints?: Datapoints;
  fidelity_degrees?: FidelityDegrees;
  minutes?: Minutes;
  probe_config?: ProbeConfig;
}
interface ProbeConfig {
  [k: string]: ProbeChartConfig | undefined;
}
/**
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "ProbeChartConfig".
 */
export interface ProbeChartConfig {
  bg_color: BgColor;
  bg_color_setpoint?: BgColorSetpoint;
  bg_color_target: BgColorTarget;
  dash_setpoint: DashSetpoint;
  enabled: Enabled;
  fill: Fill;
  line_color: LineColor;
  line_color_setpoint?: LineColorSetpoint;
  line_color_target: LineColorTarget;
  name: Name;
  type: Type;
}
export interface KeepWarm {
  s_plus?: SPlus;
  temp?: Temp;
}
/**
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "LastUpdated".
 */
export interface LastUpdated {
  time: Time;
}
export interface Modules {
  display?: Display;
  dist?: Dist;
  grillplat?: Grillplat;
}
export interface NotifyServices {
  apprise?: AppriseService;
  ifttt?: IftttService;
  influxdb?: InfluxdbService;
  mqtt?: MqttService;
  onesignal?: OneSignalService;
  pushbullet?: PushbulletService;
  pushover?: PushoverService;
  wled?: WledService;
}
export interface AppriseService {
  enabled?: Enabled1;
  locations?: Locations;
}
export interface IftttService {
  APIKey?: Apikey;
  enabled?: Enabled2;
}
export interface InfluxdbService {
  bucket?: Bucket;
  enabled?: Enabled3;
  org?: Org;
  token?: Token;
  url?: Url;
}
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
export interface OneSignalService {
  app_id?: AppId;
  devices?: Devices;
  enabled?: Enabled5;
  uuid: Uuid;
}
interface Devices {
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
export interface PelletLevel {
  empty?: Empty;
  full?: Full;
  warning_enabled?: WarningEnabled;
  warning_level?: WarningLevel;
  warning_time?: WarningTime;
}
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
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "_BasicBus".
 */
export interface _BasicBus {
  kind?: Kind;
}
/**
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "_KernelBusNumber".
 */
export interface _KernelBusNumber {
  bus_num: BusNum;
  kind?: Kind1;
}
/**
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "_KernelAdapterName".
 */
export interface _KernelAdapterName {
  adapter: Adapter;
  kind?: Kind2;
}
/**
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "_KernelSerialMatch".
 */
export interface _KernelSerialMatch {
  kind?: Kind3;
  serial: Serial;
}
/**
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "_FT232hBus".
 */
interface _FT232HBus {
  kind?: Kind4;
  url?: Url1;
}
/**
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
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
interface _FT232HConfig {
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
export interface ProbeSettings {
  probe_map?: ProbeMap;
  probe_profiles?: ProbeProfiles;
}
interface ProbeMap {
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
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "PwmProfile".
 */
export interface PwmProfile {
  duty_cycle: DutyCycle;
}
export interface Recipe {
  probe_map?: RecipeProbeMap;
}
export interface RecipeProbeMap {
  food?: Food;
  primary?: Primary;
}
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
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "ServerInfo".
 */
export interface ServerInfo {
  uuid: Uuid1;
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
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
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
export interface ThermocoupleHealthSettings {
  inference_policy?: InferencePolicy;
}
/**
 * This interface was referenced by `SettingsSchema`'s JSON-Schema
 * via the `definition` "Versions".
 */
export interface Versions {
  build: Build;
  cookfile: Cookfile;
  recipe: Recipe1;
  server: Server;
}
