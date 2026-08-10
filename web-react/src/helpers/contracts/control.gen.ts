// biome-ignore-all lint/suspicious/noEmptyInterface: Generated from closed empty JSON objects.
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

type AddAndLoad = boolean;
type BrandName = string;
type Comments = string;
type Rating = number;
type WoodType = string;
type Action = "add_profile";
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "CommandRequest".
 */
export type CommandRequest =
  | SetModeCommandRequest
  | SetPrimarySetpointCommandRequest
  | SetSmokePlusCommandRequest
  | SetPModeCommandRequest
  | PrimeCommandRequest
  | TimerStartCommandRequest
  | TimerStartWithOptionsCommandRequest
  | TimerPauseCommandRequest
  | TimerStopCommandRequest
  | TimerShutdownCommandRequest
  | TimerKeepWarmCommandRequest
  | SystemCommandRequest
  | SetUnitsCommandRequest
  | ManualOutputCommandRequest
  | ManualPwmCommandRequest;
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "GrillMode".
 */
export type GrillMode =
  | "startup"
  | "smoke"
  | "shutdown"
  | "stop"
  | "monitor"
  | "reignite"
  | "manual";
type Operation = "set_mode";
type Operation1 = "set_primary_setpoint";
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "FiniteNumber".
 */
type FiniteNumber = number;
type Enabled = boolean;
type Operation2 = "set_smoke_plus";
type Operation3 = "set_p_mode";
type Value = number;
type Grams = number;
type NextMode = ("startup" | "monitor") | null;
type Operation4 = "prime";
type Operation5 = "timer_start";
type Operation6 = "timer_start_with_options";
type Keepwarm = boolean;
type Shutdown = boolean;
type Operation7 = "timer_pause";
type Operation8 = "timer_stop";
type Enabled1 = boolean;
type Operation9 = "timer_shutdown";
type Enabled2 = boolean;
type Operation10 = "timer_keep_warm";
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "SystemCommand".
 */
export type SystemCommand = "reboot" | "shutdown" | "restart";
type Operation11 = "system";
type Operation12 = "set_units";
type Units = "F" | "C";
type Action1 = "toggle" | "true" | "false";
type Operation13 = "manual_output";
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "ManualOutput".
 */
export type ManualOutput = "power" | "igniter" | "auger" | "fan";
type Duty = number;
type Operation14 = "manual_pwm";
type NotifyData = NotifyEntry[] | null;
type Condition = string | null;
type KeepWarm = boolean | null;
type Label = string;
type Reignite = boolean | null;
type Req = boolean;
type Shutdown1 = boolean | null;
type Triggered = boolean | null;
type Type = string;
type NotifyUpdates = NotifyUpdate[] | null;
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "JsonValue".
 */
export type JsonValue =
  | boolean
  | number
  | string
  | JsonValue[]
  | {
      [k: string]: JsonValue;
    }
  | null;
type Label1 = string;
type Type1 = string;
type Control = "success" | "error";
type Message = string;
type Result = "success" | "error";
type Action2 = "delete_log";
type LogItem = string;
type Action3 = "delete_profile";
type Profile = string;
type Action4 = "edit_brands";
type DeleteBrand = string | null;
type DeleteWood = string | null;
type NewBrand = string | null;
type NewWood = string | null;
type BrandName1 = string;
type Comments1 = string;
type Profile1 = string;
type Rating1 = number;
type WoodType1 = string;
type Action5 = "edit_profile";
type Action6 = "edit_woods";
type Action7 = "hopper_check";
type Action8 = "load_profile";
type Data = NotifyEntry[];
type Message1 = string;
type Result1 = "OK" | "ERROR";
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletActionRequest".
 */
export type PelletActionRequest =
  | LoadPelletProfileRequest
  | HopperCheckRequest
  | EditPelletBrandsRequest
  | EditPelletWoodsRequest
  | AddPelletProfileRequest
  | EditPelletProfileRequest
  | DeletePelletProfileRequest
  | DeletePelletLogRequest;
type Data1 = null;
type Message2 = string | null;
type Result2 = "OK" | "Error";
type DateLoaded = string;
type EstUsage = number;
type HopperLevel = number;
type Pelletid = string;
type Brand = string;
type Comments2 = string;
type Rating2 = number;
type Wood = string;
type Brands = string[];
type Time = number;
type Deleted = boolean;
type Pelletid1 = string | null;
type SchemaVersion = number;
type Woods = string[];
type BrandName2 = string;
type Comments3 = string;
type Rating3 = number;
type WoodType2 = string;
type Uuid = string;
type Message3 = string | null;
type Result3 = "OK" | "Error";
type Uuid1 = string;
type Message4 = string;
type Profiles = WledProfileItem[] | null;
type Description = string;
type Name = string;
type Number = number;
type ProfilesPushed = number | null;
type Result4 = "success" | "error";
type Ip = string;
type LedCount = number | null;
type Mac = string | null;
type Name1 = string;
type Online = boolean | null;
type Port = number | null;
type Product = string | null;
type Version = string | null;
type Devices = WledDevice[];
type Message5 = string;
type Result5 = "success" | "error";
type DeviceAddress = string;
type DeviceAddress1 = string;
type ProfileNumber = number;
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "_CommandRequestUnion".
 */
export type _CommandRequestUnion =
  | SetModeCommandRequest
  | SetPrimarySetpointCommandRequest
  | SetSmokePlusCommandRequest
  | SetPModeCommandRequest
  | PrimeCommandRequest
  | TimerStartCommandRequest
  | TimerStartWithOptionsCommandRequest
  | TimerPauseCommandRequest
  | TimerStopCommandRequest
  | TimerShutdownCommandRequest
  | TimerKeepWarmCommandRequest
  | SystemCommandRequest
  | SetUnitsCommandRequest
  | ManualOutputCommandRequest
  | ManualPwmCommandRequest;
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "_PelletActionUnion".
 */
export type _PelletActionUnion =
  | LoadPelletProfileRequest
  | HopperCheckRequest
  | EditPelletBrandsRequest
  | EditPelletWoodsRequest
  | AddPelletProfileRequest
  | EditPelletProfileRequest
  | DeletePelletProfileRequest
  | DeletePelletLogRequest;

export interface PiFireControlWebContracts {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "AddPelletProfileData".
 */
export interface AddPelletProfileData {
  add_and_load: AddAndLoad;
  brand_name: BrandName;
  comments: Comments;
  rating: Rating;
  wood_type: WoodType;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "AddPelletProfileRequest".
 */
export interface AddPelletProfileRequest {
  action: Action;
  data: AddPelletProfileData;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "SetModeCommandRequest".
 */
export interface SetModeCommandRequest {
  mode: GrillMode;
  operation: Operation;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "SetPrimarySetpointCommandRequest".
 */
export interface SetPrimarySetpointCommandRequest {
  operation: Operation1;
  temperature: FiniteNumber;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "SetSmokePlusCommandRequest".
 */
export interface SetSmokePlusCommandRequest {
  enabled: Enabled;
  operation: Operation2;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "SetPModeCommandRequest".
 */
export interface SetPModeCommandRequest {
  operation: Operation3;
  value: Value;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PrimeCommandRequest".
 */
export interface PrimeCommandRequest {
  grams: Grams;
  next_mode?: NextMode;
  operation: Operation4;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "TimerStartCommandRequest".
 */
export interface TimerStartCommandRequest {
  operation: Operation5;
  seconds: FiniteNumber;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "TimerStartWithOptionsCommandRequest".
 */
export interface TimerStartWithOptionsCommandRequest {
  operation: Operation6;
  options: TimerOptionsPayload;
  seconds: FiniteNumber;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "TimerOptionsPayload".
 */
export interface TimerOptionsPayload {
  keepWarm: Keepwarm;
  shutdown: Shutdown;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "TimerPauseCommandRequest".
 */
export interface TimerPauseCommandRequest {
  operation: Operation7;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "TimerStopCommandRequest".
 */
export interface TimerStopCommandRequest {
  operation: Operation8;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "TimerShutdownCommandRequest".
 */
export interface TimerShutdownCommandRequest {
  enabled: Enabled1;
  operation: Operation9;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "TimerKeepWarmCommandRequest".
 */
export interface TimerKeepWarmCommandRequest {
  enabled: Enabled2;
  operation: Operation10;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "SystemCommandRequest".
 */
export interface SystemCommandRequest {
  command: SystemCommand;
  operation: Operation11;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "SetUnitsCommandRequest".
 */
export interface SetUnitsCommandRequest {
  operation: Operation12;
  units: Units;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "ManualOutputCommandRequest".
 */
export interface ManualOutputCommandRequest {
  action?: Action1;
  operation: Operation13;
  output: ManualOutput;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "ManualPwmCommandRequest".
 */
export interface ManualPwmCommandRequest {
  duty: Duty;
  operation: Operation14;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "ControlPatchRequest".
 */
export interface ControlPatchRequest {
  notify_data?: NotifyData;
  notify_updates?: NotifyUpdates;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "NotifyEntry".
 */
export interface NotifyEntry {
  condition?: Condition;
  eta?: FiniteNumber | null;
  keep_warm?: KeepWarm;
  label: Label;
  reignite?: Reignite;
  req: Req;
  shutdown?: Shutdown1;
  target?: FiniteNumber | null;
  triggered?: Triggered;
  type: Type;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "NotifyUpdate".
 */
export interface NotifyUpdate {
  fields: Fields;
  label: Label1;
  type: Type1;
}
interface Fields {
  [k: string]: JsonValue;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "ControlPatchResponse".
 */
export interface ControlPatchResponse {
  control: Control;
  message: Message;
  result: Result;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "DeletePelletLogRequest".
 */
export interface DeletePelletLogRequest {
  action: Action2;
  data: PelletLogReference;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletLogReference".
 */
export interface PelletLogReference {
  log_item: LogItem;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "DeletePelletProfileRequest".
 */
export interface DeletePelletProfileRequest {
  action: Action3;
  data: PelletProfileReference;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletProfileReference".
 */
export interface PelletProfileReference {
  profile: Profile;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "EditPelletBrandsRequest".
 */
export interface EditPelletBrandsRequest {
  action: Action4;
  data: PelletVocabularyEdit;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletVocabularyEdit".
 */
export interface PelletVocabularyEdit {
  delete_brand?: DeleteBrand;
  delete_wood?: DeleteWood;
  new_brand?: NewBrand;
  new_wood?: NewWood;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "EditPelletProfileData".
 */
export interface EditPelletProfileData {
  brand_name: BrandName1;
  comments: Comments1;
  profile: Profile1;
  rating: Rating1;
  wood_type: WoodType1;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "EditPelletProfileRequest".
 */
export interface EditPelletProfileRequest {
  action: Action5;
  data: EditPelletProfileData;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "EditPelletWoodsRequest".
 */
export interface EditPelletWoodsRequest {
  action: Action6;
  data: PelletVocabularyEdit;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "EmptyPelletActionData".
 */
export interface EmptyPelletActionData {}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "HopperCheckRequest".
 */
export interface HopperCheckRequest {
  action: Action7;
  data: EmptyPelletActionData;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "LoadPelletProfileRequest".
 */
export interface LoadPelletProfileRequest {
  action: Action8;
  data: PelletProfileReference;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "NotifyListResponse".
 */
export interface NotifyListResponse {
  data: Data;
  message: Message1;
  result: Result1;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletActionResponse".
 */
export interface PelletActionResponse {
  data?: Data1;
  message?: Message2;
  result: Result2;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletCurrent".
 */
export interface PelletCurrent {
  date_loaded: DateLoaded;
  est_usage: EstUsage;
  hopper_level: HopperLevel;
  pelletid: Pelletid;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletDbSchema".
 */
export interface PelletDbSchema {
  archive: Archive;
  brands: Brands;
  current: PelletCurrent;
  lastupdated: PelletLastUpdated;
  log: Log;
  schema_version?: SchemaVersion;
  woods: Woods;
}
interface Archive {
  [k: string]: PelletProfile;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletProfile".
 */
export interface PelletProfile {
  brand: Brand;
  comments: Comments2;
  rating: Rating2;
  wood: Wood;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletLastUpdated".
 */
export interface PelletLastUpdated {
  time: Time;
}
interface Log {
  [k: string]: PelletLogEntry;
}
/**
 * This interface was referenced by `Log`'s JSON-Schema definition
 * via the `patternProperty` "^\d+$".
 *
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletLogEntry".
 */
export interface PelletLogEntry {
  deleted: Deleted;
  pelletid: Pelletid1;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletProfileFields".
 */
export interface PelletProfileFields {
  brand_name: BrandName2;
  comments: Comments3;
  rating: Rating3;
  wood_type: WoodType2;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletRestData".
 */
export interface PelletRestData {
  pellets: PelletDbSchema;
  uuid: Uuid;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletRestResponse".
 */
export interface PelletRestResponse {
  data: PelletRestData;
  message?: Message3;
  result: Result3;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "PelletSocketPayload".
 */
export interface PelletSocketPayload {
  pellets: PelletDbSchema;
  uuid: Uuid1;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "WledActionResponse".
 */
export interface WledActionResponse {
  message: Message4;
  profiles?: Profiles;
  profiles_pushed?: ProfilesPushed;
  result: Result4;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "WledProfileItem".
 */
export interface WledProfileItem {
  description: Description;
  name: Name;
  number: Number;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "WledDevice".
 */
export interface WledDevice {
  ip: Ip;
  led_count?: LedCount;
  mac?: Mac;
  name: Name1;
  online?: Online;
  port?: Port;
  product?: Product;
  version?: Version;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "WledDiscoverResponse".
 */
export interface WledDiscoverResponse {
  devices: Devices;
  message: Message5;
  result: Result5;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "WledPushProfilesRequest".
 */
export interface WledPushProfilesRequest {
  device_address: DeviceAddress;
  profile_numbers?: ProfileNumbers;
}
interface ProfileNumbers {
  [k: string]: number;
}
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "WledTestProfileRequest".
 */
export interface WledTestProfileRequest {
  device_address: DeviceAddress1;
  profile_number?: ProfileNumber;
}
