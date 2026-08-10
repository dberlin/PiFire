/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

export type Error = string | null;
export type HwId = string;
export type Info = string;
export type Name = string;
export type Rows = BtScanRow[];
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
export type Kind = "basic";
export type BusNum = number;
export type Kind1 = "kernel";
export type Adapter = string;
export type Kind2 = "kernel";
export type Kind3 = "kernel";
export type Serial = string;
export type Kind4 = "ft232h";
export type Url = string;
export type Kind5 = "mcp2221";
export type Serial1 = string;
export type BusNum1 = null;
export type Kind6 = "kernel";
export type Device = string;
export type Module = string;
export type ModuleFilename = string;
export type Ports = string[];
export type ProbeDevices = ProbeDevice[];
export type Detail = string;
export type Ok = boolean;
export type Hidden = boolean;
export type ListLabels = string[];
export type ListValues = WireValue[];
export type OptionDescription = string;
export type OptionFriendlyName = string;
export type OptionName = string;
export type OptionType = "list" | "string";
export type Offset = number;
export type Reset = boolean;
export type Text = string;
export type Output = string | null;
export type Percent = number | null;
export type Status = string | null;
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ModuleSettingValue".
 */
export type ModuleSettingValue = I2CBusValue | string | null;
export type Module1 = string;
export type Section = string;
export type Device1 = string;
export type Enabled = boolean;
export type Label = string;
export type Name1 = string;
export type Port = string;
export type Profile = ProbeProfile | _EmptyProbeProfile;
export type A = number;
export type B = number;
export type C = number;
export type Id = string;
export type Name2 = string;
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeType".
 */
export type ProbeType = "Primary" | "Food" | "Aux";
export type Description = string;
export type FriendlyName = string;
export type Hidden1 = boolean;
export type Label1 = string;
export type ListLabels1 = string[];
export type ListValues1 = WireValue[];
export type Max = number | "";
export type Min = number;
export type Step = number;
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "ProbeFieldType".
 */
export type ProbeFieldType =
  "list" | "int" | "float" | "string" | "i2c_bus" | "probes_list" | "bt_address" | "usb_serial_device";
export type ProbeDevices1 = ProbeDevice[];
export type ProbeInfo = Probe[];
export type AptDependencies = string[];
export type CommandList = string[][];
export type Default = boolean;
export type Description1 = string;
export type Config2 = ProbeConfigField[];
export type Ports1 = string[];
export type Type = string;
export type Filename = string;
export type FriendlyName1 = string;
export type Image = string;
export type Notes = string;
export type PyDependencies = string[];
export type Default1 = I2CBusValue | string;
export type Description2 = string;
export type FriendlyName2 = string;
export type Hidden2 = boolean;
export type Pid = string | number | null;
export type Settings1 = string[];
export type Type1 = "usb_serial_device" | "mcp2221_serial" | "i2c_bus";
export type Vid = string | number | null;
export type Error1 = string | null;
export type Rows1 = unknown[];
export type Kind7 = string;
export type Pid1 = string | number | null;
export type Vid1 = string | number | null;
export type Error2 = string | null;
export type Label2 = string;
export type Value = string;
export type Items = _ScanItem[];
export type Title = string;
export type Groups = _ScanGroup[];
export type Label3 = string;
export type NumChannels = number;
export type Serial2 = string;
export type Type2 = string;
export type Error3 = string | null;
export type Rows2 = ThermoworksRow[];
export type Clear = boolean;
export type ProbesUnits = string;
export type ProbesUnits1 = string;
export type AptDependencies1 = string[];
export type CommandList1 = string[][];
export type Config3 = ConfigOption[];
export type Default2 = boolean;
export type Description3 = string;
export type Filename1 = string;
export type FriendlyName3 = string;
export type Image1 = string;
export type Notes1 = string;
export type PyDependencies1 = string[];
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "WizardSection".
 */
export type WizardSection = "grillplatform" | "display" | "distance" | "probes";
export type ControlMode = string;
export type FirstTimeSetup = boolean;
export type HasDraft = boolean;
export type ProbeProfiles = ProbeProfile[];
export type ProbesUnits2 = string;

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
export interface _BasicBus {
  kind?: Kind;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_KernelBusNumber".
 */
export interface _KernelBusNumber {
  bus_num: BusNum;
  kind?: Kind1;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_KernelAdapterName".
 */
export interface _KernelAdapterName {
  adapter: Adapter;
  kind?: Kind2;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_KernelSerialMatch".
 */
export interface _KernelSerialMatch {
  kind?: Kind3;
  serial: Serial;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_FT232hBus".
 */
export interface _FT232HBus {
  kind?: Kind4;
  url?: Url;
}
/**
 * This interface was referenced by `PiFireWizardWebContracts`'s JSON-Schema
 * via the `definition` "_MCP2221Bus".
 */
export interface _MCP2221Bus {
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
export interface Config1 {
  [k: string]: WireValue;
}
export interface Settings {
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
export interface Modules {
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
export interface SettingsDependencies {
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
export interface Options {
  [k: string]: string;
}
export interface RequiresInstall {
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
export interface DisplayConfig {
  [k: string]: {
    [k: string]: WireValue;
  };
}
export interface Selections {
  [k: string]: string | null;
}
export interface SettingsDepValues {
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
export interface DisplayConfig1 {
  [k: string]: {
    [k: string]: WireValue;
  };
}
export interface Selections1 {
  [k: string]: string | null;
}
export interface SettingsDepValues1 {
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
export interface SettingsDependencies1 {
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
export interface BoardProbeMaps {
  [k: string]: ProbeMap;
}
export interface DisplayConfig2 {
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
export interface Display {
  [k: string]: WizardModuleData;
}
export interface Distance {
  [k: string]: WizardModuleData;
}
export interface Grillplatform {
  [k: string]: WizardModuleData;
}
export interface Probes {
  [k: string]: ProbeModuleData;
}
export interface Selections2 {
  [k: string]: string | null;
}
export interface SettingsDepValues2 {
  [k: string]: {
    [k: string]: ModuleSettingValue;
  };
}
