/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

type Error = string | null;
type HwId = string;
type Info = string;
type Name = string;
type Rows = BtScanRow[];
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "WireValue".
 */
export type WireValue =
  | WireScalar
  | I2CBusValue
  | WireValue[]
  | {
      [k: string]: WireValue;
    }
  | null;
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "WireScalar".
 */
export type WireScalar = number | boolean | string;
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "I2CBusValue".
 */
export type I2CBusValue =
  | _BasicBus
  | _KernelBusNumber
  | _KernelAdapterName
  | _KernelSerialMatch
  | _FT232HBus
  | _MCP2221Bus
  | _IncompleteKernelBusNumber;
type Kind = "basic";
type BusNum = number;
type Kind1 = "kernel";
type Adapter = string;
type Kind2 = "kernel";
type Kind3 = "kernel";
type Serial = string;
type Kind4 = "ft232h";
type Url = string;
type Kind5 = "mcp2221";
type Serial1 = string;
type BusNum1 = null;
type Kind6 = "kernel";
type Device = string;
type Module = string;
type ModuleFilename = string;
type Ports = string[];
type ProbeDevices = ProbeDevice[];
type Detail = string;
type Ok = boolean;
type Hidden = boolean;
type ListLabels = string[];
type ListValues = WireValue[];
type OptionDescription = string;
type OptionFriendlyName = string;
type OptionName = string;
type OptionType = "list" | "string";
type Offset = number;
type Reset = boolean;
type Text = string;
type Output = string | null;
type Percent = number | null;
type Status = string | null;
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ModuleSettingValue".
 */
export type ModuleSettingValue = I2CBusValue | string | null;
type Module1 = string;
type Section = string;
type Device1 = string;
type Enabled = boolean;
type Label = string;
type Name1 = string;
type Port = string;
type Profile = ProbeProfile | _EmptyProbeProfile;
type A = number;
type B = number;
type C = number;
type Id = string;
type Name2 = string;
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeType".
 */
export type ProbeType = "Primary" | "Food" | "Aux";
type Description = string;
type FriendlyName = string;
type Hidden1 = boolean;
type Label1 = string;
type ListLabels1 = string[];
type ListValues1 = WireValue[];
type Max = number | "";
type Min = number;
type Step = number;
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeFieldType".
 */
export type ProbeFieldType =
  "list" | "int" | "float" | "string" | "i2c_bus" | "probes_list" | "bt_address" | "usb_serial_device";
type ProbeDevices1 = ProbeDevice[];
type ProbeInfo = Probe[];
type AptDependencies = string[];
type CommandList = string[][];
type Default = boolean;
type Description1 = string;
type Config2 = ProbeConfigField[];
type Ports1 = string[];
type Type = string;
type Filename = string;
type FriendlyName1 = string;
type Image = string;
type Notes = string;
type PyDependencies = string[];
type Default1 = I2CBusValue | string;
type Description2 = string;
type FriendlyName2 = string;
type Hidden2 = boolean;
type Pid = string | number | null;
type Settings1 = string[];
type Type1 = "usb_serial_device" | "mcp2221_serial" | "i2c_bus";
type Vid = string | number | null;
type Error1 = string | null;
type Rows1 = unknown[];
type Kind7 = string;
type Pid1 = string | number | null;
type Vid1 = string | number | null;
type Error2 = string | null;
type Label2 = string;
type Value = string;
type Items = _ScanItem[];
type Title = string;
type Groups = _ScanGroup[];
type Label3 = string;
type NumChannels = number;
type Serial2 = string;
type Type2 = string;
type Error3 = string | null;
type Rows2 = ThermoworksRow[];
type Clear = boolean;
type ProbesUnits = string;
type ProbesUnits1 = string;
type AptDependencies1 = string[];
type CommandList1 = string[][];
type Config3 = ConfigOption[];
type Default2 = boolean;
type Description3 = string;
type Filename1 = string;
type FriendlyName3 = string;
type Image1 = string;
type Notes1 = string;
type PyDependencies1 = string[];
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "WizardSection".
 */
export type WizardSection = "grillplatform" | "display" | "distance" | "probes";
type ControlMode = string;
type FirstTimeSetup = boolean;
type HasDraft = boolean;
type ProbeProfiles = ProbeProfile[];
type ProbesUnits2 = string;

export interface PiFireWizardWebContracts {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "BtRowsResult".
 */
export interface BtRowsResult {
  error: Error;
  rows: Rows;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "BtScanRow".
 */
export interface BtScanRow {
  hw_id: HwId;
  info: Info;
  name: Name;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "BusKindsValidationRequest".
 */
export interface BusKindsValidationRequest {
  probe_devices?: ProbeDevices;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeDevice".
 */
export interface ProbeDevice {
  config: Config;
  device: Device;
  module: Module;
  module_filename: ModuleFilename;
  ports: Ports;
}
export interface Config {
  [k: string]: WireValue;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_BasicBus".
 */
interface _BasicBus {
  kind?: Kind;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_KernelBusNumber".
 */
interface _KernelBusNumber {
  bus_num: BusNum;
  kind?: Kind1;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_KernelAdapterName".
 */
interface _KernelAdapterName {
  adapter: Adapter;
  kind?: Kind2;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_KernelSerialMatch".
 */
interface _KernelSerialMatch {
  kind?: Kind3;
  serial: Serial;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_FT232hBus".
 */
interface _FT232HBus {
  kind?: Kind4;
  url?: Url;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_MCP2221Bus".
 */
interface _MCP2221Bus {
  kind?: Kind5;
  serial?: Serial1;
}
/**
 * The one intentionally incomplete bus value a saved wizard draft carries.
 *
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_IncompleteKernelBusNumber".
 */
export interface _IncompleteKernelBusNumber {
  bus_num: BusNum1;
  kind: Kind6;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "BusKindsValidationResponse".
 */
export interface BusKindsValidationResponse {
  detail?: Detail;
  ok: Ok;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ConfigOption".
 */
export interface ConfigOption {
  default?:
    | WireScalar
    | I2CBusValue
    | WireValue[]
    | {
        [k: string]: WireValue;
      }
    | null;
  hidden?: Hidden;
  list_labels?: ListLabels;
  list_values?: ListValues;
  option_description?: OptionDescription;
  option_friendly_name: OptionFriendlyName;
  option_name: OptionName;
  option_type: OptionType;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "InstallLog".
 */
export interface InstallLog {
  offset: Offset;
  reset: Reset;
  text: Text;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "InstallStatus".
 */
export interface InstallStatus {
  output: Output;
  percent: Percent;
  status: Status;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ModuleValues".
 */
export interface ModuleValues {
  config: Config1;
  settings: Settings;
}
interface Config1 {
  [k: string]: WireValue;
}
interface Settings {
  [k: string]: ModuleSettingValue;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ModuleValuesRequest".
 */
export interface ModuleValuesRequest {
  module?: Module1;
  section?: Section;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "Probe".
 */
export interface Probe {
  device: Device1;
  enabled: Enabled;
  label: Label;
  name: Name1;
  port: Port;
  profile: Profile;
  type: ProbeType;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeProfile".
 */
export interface ProbeProfile {
  A: A;
  B: B;
  C: C;
  id: Id;
  name: Name2;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_EmptyProbeProfile".
 */
export interface _EmptyProbeProfile {}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeConfigField".
 */
export interface ProbeConfigField {
  default?:
    | WireScalar
    | I2CBusValue
    | WireValue[]
    | {
        [k: string]: WireValue;
      }
    | null;
  description?: Description;
  friendly_name: FriendlyName;
  hidden?: Hidden1;
  label: Label1;
  list_labels?: ListLabels1;
  list_values?: ListValues1;
  max?: Max;
  min?: Min;
  step?: Step;
  type: ProbeFieldType;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeMap".
 */
export interface ProbeMap {
  probe_devices: ProbeDevices1;
  probe_info: ProbeInfo;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeModuleCatalog".
 */
export interface ProbeModuleCatalog {
  modules: Modules;
  requires_install: RequiresInstall;
}
interface Modules {
  [k: string]: ProbeModuleData;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeModuleData".
 */
export interface ProbeModuleData {
  apt_dependencies?: AptDependencies;
  command_list?: CommandList;
  default?: Default;
  description?: Description1;
  device_specific: _ProbeDeviceMetadata;
  filename: Filename;
  friendly_name: FriendlyName1;
  image?: Image;
  notes?: Notes;
  py_dependencies?: PyDependencies;
  settings_dependencies?: SettingsDependencies;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_ProbeDeviceMetadata".
 */
export interface _ProbeDeviceMetadata {
  config: Config2;
  ports: Ports1;
  type: Type;
}
interface SettingsDependencies {
  [k: string]: SettingsDependency;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "SettingsDependency".
 */
export interface SettingsDependency {
  default?: Default1;
  description?: Description2;
  friendly_name: FriendlyName2;
  hidden?: Hidden2;
  options?: Options;
  pid?: Pid;
  settings: Settings1;
  type?: Type1;
  vid?: Vid;
}
interface Options {
  [k: string]: string;
}
interface RequiresInstall {
  [k: string]: boolean;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "RowsResult".
 */
export interface RowsResult {
  error: Error1;
  rows: Rows1;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ScanRequest".
 */
export interface ScanRequest {
  kind?: Kind7;
  pid?: Pid1;
  vid?: Vid1;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ScanResult".
 */
export interface ScanResult {
  error: Error2;
  groups: Groups;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_ScanGroup".
 */
export interface _ScanGroup {
  items: Items;
  title: Title;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_ScanItem".
 */
export interface _ScanItem {
  label: Label2;
  value: Value;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ThermoworksRow".
 */
export interface ThermoworksRow {
  label: Label3;
  num_channels: NumChannels;
  serial: Serial2;
  type: Type2;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ThermoworksRowsResult".
 */
export interface ThermoworksRowsResult {
  error: Error3;
  rows: Rows2;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "WizardDraftRequest".
 */
export interface WizardDraftRequest {
  clear?: Clear;
  display_config?: DisplayConfig;
  probe_map?: ProbeMap;
  probes_units?: ProbesUnits;
  selections?: Selections;
  settings_dep_values?: SettingsDepValues;
}
interface DisplayConfig {
  [k: string]: {
    [k: string]: WireValue;
  };
}
interface Selections {
  [k: string]: string | null;
}
interface SettingsDepValues {
  [k: string]: {
    [k: string]: ModuleSettingValue;
  };
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "WizardFinishRequest".
 */
export interface WizardFinishRequest {
  display_config?: DisplayConfig1;
  probe_map?: ProbeMap | null;
  probes_units?: ProbesUnits1;
  selections?: Selections1;
  settings_dep_values?: SettingsDepValues1;
}
interface DisplayConfig1 {
  [k: string]: {
    [k: string]: WireValue;
  };
}
interface Selections1 {
  [k: string]: string | null;
}
interface SettingsDepValues1 {
  [k: string]: {
    [k: string]: ModuleSettingValue;
  };
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "WizardModuleData".
 */
export interface WizardModuleData {
  apt_dependencies?: AptDependencies1;
  command_list?: CommandList1;
  config?: Config3;
  default?: Default2;
  description?: Description3;
  filename?: Filename1;
  friendly_name: FriendlyName3;
  image?: Image1;
  notes?: Notes1;
  py_dependencies?: PyDependencies1;
  settings_dependencies: SettingsDependencies1;
}
interface SettingsDependencies1 {
  [k: string]: SettingsDependency;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "WizardState".
 */
export interface WizardState {
  board_probe_maps: BoardProbeMaps;
  control_mode: ControlMode;
  display_config: DisplayConfig2;
  first_time_setup: FirstTimeSetup;
  has_draft: HasDraft;
  modules_metadata: _WizardModulesMetadata;
  probe_map: ProbeMap;
  probe_profiles: ProbeProfiles;
  probes_units: ProbesUnits2;
  selections: Selections2;
  settings_dep_values: SettingsDepValues2;
}
interface BoardProbeMaps {
  [k: string]: ProbeMap;
}
interface DisplayConfig2 {
  [k: string]: {
    [k: string]: WireValue;
  };
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_WizardModulesMetadata".
 */
export interface _WizardModulesMetadata {
  display: Display;
  distance: Distance;
  grillplatform: Grillplatform;
  probes: Probes;
}
interface Display {
  [k: string]: WizardModuleData;
}
interface Distance {
  [k: string]: WizardModuleData;
}
interface Grillplatform {
  [k: string]: WizardModuleData;
}
interface Probes {
  [k: string]: ProbeModuleData;
}
interface Selections2 {
  [k: string]: string | null;
}
interface SettingsDepValues2 {
  [k: string]: {
    [k: string]: ModuleSettingValue;
  };
}
