/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

export type AddAndLoad = boolean;
export type BrandName = string;
export type Comments = string;
export type Rating = number;
export type WoodType = string;
export type Action = "add_profile";
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
export type GrillMode = "startup" | "smoke" | "shutdown" | "stop" | "monitor" | "reignite" | "manual";
export type Operation = "set_mode";
export type Operation1 = "set_primary_setpoint";
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "FiniteNumber".
 */
export type FiniteNumber = number;
export type Enabled = boolean;
export type Operation2 = "set_smoke_plus";
export type Operation3 = "set_p_mode";
export type Value = number;
export type Grams = number;
export type NextMode = ("startup" | "monitor") | null;
export type Operation4 = "prime";
export type Operation5 = "timer_start";
export type Operation6 = "timer_start_with_options";
export type Keepwarm = boolean;
export type Shutdown = boolean;
export type Operation7 = "timer_pause";
export type Operation8 = "timer_stop";
export type Enabled1 = boolean;
export type Operation9 = "timer_shutdown";
export type Enabled2 = boolean;
export type Operation10 = "timer_keep_warm";
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "SystemCommand".
 */
export type SystemCommand = "reboot" | "shutdown" | "restart";
export type Operation11 = "system";
export type Operation12 = "set_units";
export type Units = "F" | "C";
export type Action1 = "toggle" | "true" | "false";
export type Operation13 = "manual_output";
/**
 * This interface was referenced by `PiFireControlWebContracts`'s JSON-Schema
 * via the `definition` "ManualOutput".
 */
export type ManualOutput = "power" | "igniter" | "auger" | "fan";
export type Duty = number;
export type Operation14 = "manual_pwm";
export type NotifyData = NotifyEntry[] | null;
export type Condition = string | null;
export type KeepWarm = boolean | null;
export type Label = string;
export type Reignite = boolean | null;
export type Req = boolean;
export type Shutdown1 = boolean;
export type Triggered = boolean | null;
export type Type = string;
export type NotifyUpdates = NotifyUpdate[] | null;
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
export type Label1 = string;
export type Type1 = string;
export type Control = "success" | "error";
export type Message = string;
export type Result = "success" | "error";
export type Action2 = "delete_log";
export type LogItem = string;
export type Action3 = "delete_profile";
export type Profile = string;
export type Action4 = "edit_brands";
export type DeleteBrand = string | null;
export type DeleteWood = string | null;
export type NewBrand = string | null;
export type NewWood = string | null;
export type BrandName1 = string;
export type Comments1 = string;
export type Profile1 = string;
export type Rating1 = number;
export type WoodType1 = string;
export type Action5 = "edit_profile";
export type Action6 = "edit_woods";
export type Action7 = "hopper_check";
export type Action8 = "load_profile";
export type Data = NotifyEntry[];
export type Message1 = string;
export type Result1 = "OK" | "ERROR";
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
export type Data1 = null;
export type Message2 = string | null;
export type Result2 = "OK" | "Error";
export type DateLoaded = string;
export type EstUsage = number;
export type HopperLevel = number;
export type Pelletid = string;
export type Brand = string;
export type Comments2 = string;
export type Rating2 = number;
export type Wood = string;
export type Brands = string[];
export type Time = number;
export type Deleted = boolean;
export type Pelletid1 = string | null;
export type SchemaVersion = number;
export type Woods = string[];
export type BrandName2 = string;
export type Comments3 = string;
export type Rating3 = number;
export type WoodType2 = string;
export type Uuid = string;
export type Message3 = string | null;
export type Result3 = "OK" | "Error";
export type Uuid1 = string;
export type Message4 = string;
export type Profiles = WledProfileItem[] | null;
export type Description = string;
export type Name = string;
export type Number = number;
export type ProfilesPushed = number | null;
export type Result4 = "success" | "error";
export type Ip = string;
export type LedCount = number | null;
export type Mac = string | null;
export type Name1 = string;
export type Online = boolean | null;
export type Port = number | null;
export type Product = string | null;
export type Version = string | null;
export type Devices = WledDevice[];
export type Message5 = string;
export type Result5 = "success" | "error";
export type DeviceAddress = string;
export type DeviceAddress1 = string;
export type ProfileNumber = number;
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
  shutdown: Shutdown1;
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
export interface Fields {
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
export interface Archive {
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
export interface Log {
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
export interface ProfileNumbers {
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
