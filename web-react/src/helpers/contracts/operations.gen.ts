/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

export type BootToMonitor = boolean;
export type DebugMode = boolean;
export type BootToMonitor1 = boolean;
export type DebugMode1 = boolean;
export type Pelletdb = string[];
export type Settings = string[];
export type Logs = string[];
export type Mode = string;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "Reading".
 */
export type Reading = string | number;
export type IpAddress = string;
export type MacAddress = string;
export type Architecture = string;
export type Bits = string;
export type Name = string;
export type PrettyName = string;
export type Version = string;
export type VersionCodename = string;
export type VersionId = string;
export type Uptime = string;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "FiniteNumber".
 */
export type FiniteNumber = number;
export type Ready = boolean;
export type Samples = number;
export type Probe = string;
export type Reference = string;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "BackupKind".
 */
export type BackupKind = "settings" | "pelletdb";
export type Filename = string;
export type File = string;
export type File1 = string;
export type Offset = number;
export type Reset = boolean;
export type Text = string;
export type A = number;
export type B = number;
export type C = number;
export type Chart = CoefficientPoint[];
export type ChartOk = boolean;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "Segment".
 */
export type Segment = "High" | "Medium" | "Low";
export type Points = TunerPoint[];
export type Action = "factory_reset";
export type Bytes = number;
export type Members = string[];
export type Stem = string;
export type Removed = string[];
export type Families = LogFamily[];
export type Logs1 = string[];
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "MaintenanceAction".
 */
export type MaintenanceAction = "clear_history" | "clear_events" | "clear_pelletdb" | "clear_pelletdb_log";
export type ApplyTo = string | null;
export type Name1 = string;
export type Applied = string | null;
export type Id = string;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "SystemAction".
 */
export type SystemAction = "reboot" | "shutdown" | "restart";
export type Probe1 = string;
export type Tuning = boolean;
export type Mode1 = string;
export type Open = boolean;
export type Restored = boolean;
export type Open1 = boolean;
export type Target = string;
export type Behind = number;
export type Current = string;
export type Output = string;
export type Started = boolean;
export type Branch = string;
export type Branches = string[];
export type Detached = string | null;
export type RemoteUrl = string;
export type RemoteVersion = string;
export type Version1 = string;
export type WebUiBuildFailed = boolean;
export type WebUiStale = boolean;
export type Output1 = string;
export type Percent = number;
export type Status = string;

export interface PiFireOperationsWebContracts {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "AdminSettings".
 */
export interface AdminSettings {
  boot_to_monitor: BootToMonitor;
  debug_mode: DebugMode;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "AdminSettingsUpdate".
 */
export interface AdminSettingsUpdate {
  boot_to_monitor?: BootToMonitor1;
  debug_mode?: DebugMode1;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "AdminState".
 */
export interface AdminState {
  backups: BackupListing;
  logs: Logs;
  mode: Mode;
  settings: AdminSettings;
  system: SystemInfo;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "BackupListing".
 */
export interface BackupListing {
  pelletdb: Pelletdb;
  settings: Settings;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "SystemInfo".
 */
export interface SystemInfo {
  hardware_info: HardwareInfo;
  network_info: NetworkInfo;
  os_info: OsInfo;
  uptime: Uptime;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "HardwareInfo".
 */
export interface HardwareInfo {
  available_ram: Reading;
  cpu_info: CpuInfo;
  total_ram: Reading;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "CpuInfo".
 */
export interface CpuInfo {
  cores: Reading;
  frequency: Reading;
  hardware: Reading;
  model: Reading;
  model_name: Reading;
}
export interface NetworkInfo {
  [k: string]: NetworkInterface | undefined;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "NetworkInterface".
 */
export interface NetworkInterface {
  ip_address: IpAddress;
  mac_address: MacAddress;
}
/**
 * The guaranteed display fields plus intentionally open /etc/os-release data.
 *
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "OsInfo".
 */
export interface OsInfo {
  ARCHITECTURE: Architecture;
  BITS: Bits;
  NAME: Name;
  PRETTY_NAME: PrettyName;
  VERSION: Version;
  VERSION_CODENAME: VersionCodename;
  VERSION_ID: VersionId;
  [k: string]: string | undefined;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "AutoStatus".
 */
export interface AutoStatus {
  current_temp: FiniteNumber | null;
  current_tr: FiniteNumber | null;
  high_temp: FiniteNumber;
  high_tr: FiniteNumber;
  low_temp: FiniteNumber;
  low_tr: FiniteNumber;
  medium_temp: FiniteNumber;
  medium_tr: FiniteNumber;
  ready: Ready;
  samples: Samples;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "AutoStatusRequest".
 */
export interface AutoStatusRequest {
  probe: Probe;
  reference: Reference;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "BackupCreateRequest".
 */
export interface BackupCreateRequest {
  kind: BackupKind;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "BackupCreated".
 */
export interface BackupCreated {
  filename: Filename;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "BackupRestoreRequest".
 */
export interface BackupRestoreRequest {
  file: File;
  kind: BackupKind;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "BackupRestored".
 */
export interface BackupRestored {
  file: File1;
  kind: BackupKind;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "BuildLog".
 */
export interface BuildLog {
  offset: Offset;
  reset: Reset;
  text: Text;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "CoefficientPoint".
 */
export interface CoefficientPoint {
  x: FiniteNumber;
  y: FiniteNumber;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "Coefficients".
 */
export interface Coefficients {
  a: A;
  b: B;
  c: C;
  chart: Chart;
  chart_ok: ChartOk;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "CoefficientsRequest".
 */
export interface CoefficientsRequest {
  points: Points;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "TunerPoint".
 */
export interface TunerPoint {
  segment: Segment;
  temp: FiniteNumber;
  trohms: FiniteNumber;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "EmptyOperationRequest".
 */
export interface EmptyOperationRequest {}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "FactoryResetResponse".
 */
export interface FactoryResetResponse {
  action: Action;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "LogFamily".
 */
export interface LogFamily {
  bytes: Bytes;
  members: Members;
  stem: Stem;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "LogsDeleted".
 */
export interface LogsDeleted {
  removed: Removed;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "LogsMetadata".
 */
export interface LogsMetadata {
  families: Families;
  logs: Logs1;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "MaintenanceActionRequest".
 */
export interface MaintenanceActionRequest {
  action: MaintenanceAction;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "MaintenanceActionResponse".
 */
export interface MaintenanceActionResponse {
  action: MaintenanceAction;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "ProfileInput".
 */
export interface ProfileInput {
  a: FiniteNumber;
  apply_to?: ApplyTo;
  b: FiniteNumber;
  c: FiniteNumber;
  name: Name1;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "SavedProfile".
 */
export interface SavedProfile {
  applied: Applied;
  id: Id;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "SystemActionRequest".
 */
export interface SystemActionRequest {
  action: SystemAction;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "SystemActionResponse".
 */
export interface SystemActionResponse {
  action: SystemAction;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "TrReading".
 */
export interface TrReading {
  probe: Probe1;
  trohms: FiniteNumber | null;
  tuning: Tuning;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "TunerSession".
 */
export interface TunerSession {
  mode: Mode1;
  open: Open;
  restored: Restored;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "TunerSessionRequest".
 */
export interface TunerSessionRequest {
  open: Open1;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "UpdateBranchRequest".
 */
export interface UpdateBranchRequest {
  target: Target;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "UpdateCheck".
 */
export interface UpdateCheck {
  behind: Behind;
  current: Current;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "UpdateLog".
 */
export interface UpdateLog {
  output: Output;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "UpdateStarted".
 */
export interface UpdateStarted {
  started: Started;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "UpdateState".
 */
export interface UpdateState {
  branch: Branch;
  branches: Branches;
  detached: Detached;
  remote_url: RemoteUrl;
  remote_version: RemoteVersion;
  version: Version1;
  web_ui_build_failed: WebUiBuildFailed;
  web_ui_stale: WebUiStale;
}
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "UpdateStatus".
 */
export interface UpdateStatus {
  output: Output1;
  percent: Percent;
  status: Status;
}
