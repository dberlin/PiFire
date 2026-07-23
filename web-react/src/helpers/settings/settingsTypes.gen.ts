/* eslint-disable */
// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types

export type Selected = string;
export type Fanpidenabled = boolean;
export type Holdcycletime = number;
export type Lidopendetectenabled = boolean;
export type Lidopenpausetime = number;
export type Lidopenthreshold = number;
export type Pmode = number;
export type Smokeoffcycletime = number;
export type Smokeoncycletime = number;
export type UMax = number;
export type UMin = number;
export type Current = string;
export type Selected1 = string;
export type SleepTimeout = number;
export type Augerrate = number;
export type BootToMonitor = boolean;
export type DebugMode = boolean;
export type DispRotation = number;
export type ExtData = boolean;
export type FirstTimeSetup = boolean;
export type GlobalControlPanel = boolean;
export type GrillName = string;
export type PageTheme = string;
export type PrimeIgnition = boolean;
export type PythonExec = string;
export type Units = "F" | "C";
export type UpdatedMessage = boolean;
export type Uv = boolean;
export type Venv = boolean;
export type Autorefresh = "on" | "off";
export type Clearhistoryonstart = boolean;
export type Datapoints = number;
export type Minutes = number;
export type BgColor = string;
export type BgColorTarget = string;
export type DashSetpoint = boolean;
export type Enabled = boolean;
export type Fill = boolean;
export type LineColor = string;
export type LineColorTarget = string;
export type Name = string;
export type Type = string;
export type SPlus = boolean;
export type Temp = number;
export type Time = number;
export type Display = string;
export type Dist = string;
export type Grillplat = string;
export type Enabled1 = boolean;
export type Locations = string[];
export type Apikey = string;
export type Enabled2 = boolean;
export type Bucket = string;
export type Enabled3 = boolean;
export type Org = string;
export type Token = string;
export type Url = string;
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
export type Address = number | null;
export type Device = string;
export type Echo = number;
export type I2CBusKind = string;
export type I2CBusNum = string;
export type Trig = number;
export type DownDt = number;
export type EnterSw = number;
export type UpClk = number;
export type Address1 = string;
export type Chip = string;
export type I2CBusKind1 = string;
export type I2CBusNum1 = string;
export type Url1 = string;
export type Selector = number;
export type Shutdown1 = number;
export type Baudrate = number;
export type Device1 = string;
export type Auger = number;
export type DcFan1 = number;
export type Fan = number;
export type Igniter = number;
export type Power = number;
export type Pwm = number;
export type RealHw = boolean;
export type Standalone = boolean;
export type Wire = number | null;
export type Ce0 = number;
export type Ce1 = number;
export type SystemType = string;
export type Triggerlevel = string;
export type ProbeDevices = {
  [k: string]: unknown;
}[];
export type ProbeInfo = {
  [k: string]: unknown;
}[];
export type Frequency = number;
export type MaxDutyCycle = number;
export type MinDutyCycle = number;
export type DutyCycle = number;
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
export type Uuid1 = string;
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
export type Recipe1 = string;
export type Server = string;

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
  server_info: ServerInfo;
  shutdown?: ShutdownSettings;
  smoke_plus?: SmokePlus;
  startup?: StartupSettings;
  versions: Versions;
  [k: string]: unknown;
}
export interface ControllerSettings {
  config?: Config;
  selected?: Selected;
  [k: string]: unknown;
}
export interface Config {
  [k: string]: {
    [k: string]: number | boolean | string;
  };
}
export interface CycleData {
  FanPidEnabled?: Fanpidenabled;
  HoldCycleTime?: Holdcycletime;
  LidOpenDetectEnabled?: Lidopendetectenabled;
  LidOpenPauseTime?: Lidopenpausetime;
  LidOpenThreshold?: Lidopenthreshold;
  PMode?: Pmode;
  SmokeOffCycleTime?: Smokeoffcycletime;
  SmokeOnCycleTime?: Smokeoncycletime;
  u_max?: UMax;
  u_min?: UMin;
  [k: string]: unknown;
}
export interface Dashboard {
  current?: Current;
  dashboards?: Dashboards;
  [k: string]: unknown;
}
export interface Dashboards {
  [k: string]: {
    [k: string]: unknown;
  };
}
export interface DisplaySettings {
  config?: Config1;
  selected?: Selected1;
  sleep_timeout?: SleepTimeout;
  [k: string]: unknown;
}
export interface Config1 {
  [k: string]: {
    [k: string]: unknown;
  };
}
export interface GlobalSettings {
  augerrate?: Augerrate;
  boot_to_monitor?: BootToMonitor;
  debug_mode?: DebugMode;
  disp_rotation?: DispRotation;
  ext_data?: ExtData;
  first_time_setup?: FirstTimeSetup;
  global_control_panel?: GlobalControlPanel;
  grill_name?: GrillName;
  page_theme?: PageTheme;
  prime_ignition?: PrimeIgnition;
  python_exec?: PythonExec;
  units?: Units;
  updated_message?: UpdatedMessage;
  uv?: Uv;
  venv?: Venv;
  [k: string]: unknown;
}
export interface HistoryPage {
  autorefresh?: Autorefresh;
  clearhistoryonstart?: Clearhistoryonstart;
  datapoints?: Datapoints;
  minutes?: Minutes;
  probe_config?: ProbeConfig;
  [k: string]: unknown;
}
export interface ProbeConfig {
  [k: string]: ProbeChartConfig;
}
export interface ProbeChartConfig {
  bg_color: BgColor;
  bg_color_target: BgColorTarget;
  dash_setpoint: DashSetpoint;
  enabled: Enabled;
  fill: Fill;
  line_color: LineColor;
  line_color_target: LineColorTarget;
  name: Name;
  type: Type;
  [k: string]: unknown;
}
export interface KeepWarm {
  s_plus?: SPlus;
  temp?: Temp;
  [k: string]: unknown;
}
export interface LastUpdated {
  time: Time;
  [k: string]: unknown;
}
export interface Modules {
  display?: Display;
  dist?: Dist;
  grillplat?: Grillplat;
  [k: string]: unknown;
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
  [k: string]: unknown;
}
export interface AppriseService {
  enabled?: Enabled1;
  locations?: Locations;
  [k: string]: unknown;
}
export interface IftttService {
  APIKey?: Apikey;
  enabled?: Enabled2;
  [k: string]: unknown;
}
export interface InfluxdbService {
  bucket?: Bucket;
  enabled?: Enabled3;
  org?: Org;
  token?: Token;
  url?: Url;
  [k: string]: unknown;
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
  [k: string]: unknown;
}
export interface OneSignalService {
  app_id?: AppId;
  devices?: Devices;
  enabled?: Enabled5;
  uuid: Uuid;
  [k: string]: unknown;
}
export interface Devices {
  [k: string]: unknown;
}
export interface PushbulletService {
  APIKey?: Apikey1;
  PublicURL?: Publicurl;
  enabled?: Enabled6;
  [k: string]: unknown;
}
export interface PushoverService {
  APIKey?: Apikey2;
  PublicURL?: Publicurl1;
  UserKeys?: Userkeys;
  enabled?: Enabled7;
  [k: string]: unknown;
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
  [k: string]: unknown;
}
export interface WledEventPresets {
  Grill_Error?: GrillError;
  Pellet_Level_Low?: PelletLevelLow;
  Recipe_Next?: RecipeNext;
  Temp_Achieved?: TempAchieved;
  Timer_Expired?: TimerExpired;
  [k: string]: unknown;
}
export interface WledModePresets {
  Hold?: Hold;
  Prime?: Prime;
  Reignite?: Reignite;
  Shutdown?: Shutdown;
  Smoke?: Smoke;
  Startup?: Startup;
  Stop?: Stop;
  [k: string]: unknown;
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
  [k: string]: unknown;
}
export interface WledSuggestedConfig {
  cooking_color?: CookingColor;
  idle_brightness?: IdleBrightness;
  led_count?: LedCount;
  night_mode?: NightMode1;
  [k: string]: unknown;
}
export interface PelletLevel {
  empty?: Empty;
  full?: Full;
  warning_enabled?: WarningEnabled;
  warning_level?: WarningLevel;
  warning_time?: WarningTime;
  [k: string]: unknown;
}
export interface Platform {
  buttonslevel?: Buttonslevel;
  current?: Current1;
  dc_fan?: DcFan;
  devices?: _DevicesConfig;
  fan_controller?: _FanControllerConfig;
  ft232h?: _FT232HConfig;
  inputs?: _InputsConfig;
  numato?: _NumatoConfig;
  outputs?: _OutputsConfig;
  real_hw?: RealHw;
  standalone?: Standalone;
  system?: _SystemConfig;
  system_type?: SystemType;
  triggerlevel?: Triggerlevel;
  [k: string]: unknown;
}
export interface _DevicesConfig {
  display?: _DisplayDeviceConfig;
  distance?: _DistanceDeviceConfig;
  input?: _InputDeviceConfig;
  [k: string]: unknown;
}
export interface _DisplayDeviceConfig {
  dc?: Dc;
  led?: Led;
  rst?: Rst;
  [k: string]: unknown;
}
export interface _DistanceDeviceConfig {
  address?: Address;
  device?: Device;
  echo?: Echo;
  i2c_bus_kind?: I2CBusKind;
  i2c_bus_num?: I2CBusNum;
  trig?: Trig;
  [k: string]: unknown;
}
export interface _InputDeviceConfig {
  down_dt?: DownDt;
  enter_sw?: EnterSw;
  up_clk?: UpClk;
  [k: string]: unknown;
}
export interface _FanControllerConfig {
  address?: Address1;
  chip?: Chip;
  i2c_bus_kind?: I2CBusKind1;
  i2c_bus_num?: I2CBusNum1;
  [k: string]: unknown;
}
export interface _FT232HConfig {
  url?: Url1;
  [k: string]: unknown;
}
export interface _InputsConfig {
  selector?: Selector;
  shutdown?: Shutdown1;
  [k: string]: unknown;
}
export interface _NumatoConfig {
  baudrate?: Baudrate;
  device?: Device1;
  [k: string]: unknown;
}
export interface _OutputsConfig {
  auger?: Auger;
  dc_fan?: DcFan1;
  fan?: Fan;
  igniter?: Igniter;
  power?: Power;
  pwm?: Pwm;
  [k: string]: unknown;
}
export interface _SystemConfig {
  "1WIRE"?: Wire;
  SPI0?: _SPI0Config;
  [k: string]: unknown;
}
export interface _SPI0Config {
  CE0?: Ce0;
  CE1?: Ce1;
  [k: string]: unknown;
}
export interface ProbeSettings {
  probe_map?: ProbeMap;
  probe_profiles?: ProbeProfiles;
  [k: string]: unknown;
}
export interface ProbeMap {
  probe_devices?: ProbeDevices;
  probe_info?: ProbeInfo;
  [k: string]: unknown;
}
export interface ProbeProfiles {
  [k: string]: {
    [k: string]: unknown;
  };
}
export interface PwmSettings {
  frequency?: Frequency;
  max_duty_cycle?: MaxDutyCycle;
  min_duty_cycle?: MinDutyCycle;
  profiles?: Profiles;
  pwm_control?: PwmControl;
  temp_range_list?: TempRangeList;
  update_time?: UpdateTime;
  [k: string]: unknown;
}
export interface PwmProfile {
  duty_cycle: DutyCycle;
  [k: string]: unknown;
}
export interface Recipe {
  probe_map?: RecipeProbeMap;
  [k: string]: unknown;
}
export interface RecipeProbeMap {
  food?: Food;
  primary?: Primary;
  [k: string]: unknown;
}
export interface SafetySettings {
  allow_manual_changes?: AllowManualChanges;
  manual_override_time?: ManualOverrideTime;
  maxstartuptemp?: Maxstartuptemp;
  maxtemp?: Maxtemp;
  minstartuptemp?: Minstartuptemp;
  reigniteretries?: Reigniteretries;
  startup_check?: StartupCheck;
  [k: string]: unknown;
}
export interface ServerInfo {
  uuid: Uuid1;
  [k: string]: unknown;
}
export interface ShutdownSettings {
  auto_power_off?: AutoPowerOff;
  shutdown_duration?: ShutdownDuration;
  [k: string]: unknown;
}
export interface SmokePlus {
  duty_cycle?: DutyCycle1;
  enabled?: Enabled9;
  fan_ramp?: FanRamp;
  max_temp?: MaxTemp;
  min_temp?: MinTemp;
  off_time?: OffTime;
  on_time?: OnTime;
  [k: string]: unknown;
}
export interface StartupSettings {
  duration?: Duration;
  prime_on_startup?: PrimeOnStartup;
  pwm_duty_cycle?: PwmDutyCycle;
  smartstart?: SmartStart;
  start_to_mode?: StartToMode;
  startup_exit_temp?: StartupExitTemp;
  [k: string]: unknown;
}
export interface SmartStart {
  enabled?: Enabled10;
  exit_temp?: ExitTemp;
  profiles?: Profiles1;
  temp_range_list?: TempRangeList1;
  [k: string]: unknown;
}
export interface SmartStartProfile {
  augerontime: Augerontime;
  p_mode: PMode;
  startuptime: Startuptime;
  [k: string]: unknown;
}
export interface StartToMode {
  after_startup_mode?: AfterStartupMode;
  primary_setpoint?: PrimarySetpoint;
  start_to_hold_prompt?: StartToHoldPrompt;
  [k: string]: unknown;
}
export interface Versions {
  build: Build;
  cookfile: Cookfile;
  recipe: Recipe1;
  server: Server;
  [k: string]: unknown;
}
