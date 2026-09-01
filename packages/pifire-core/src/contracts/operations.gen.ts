/* oxlint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

type BootToMonitor = boolean;
type DebugMode = boolean;
type BootToMonitor1 = boolean;
type DebugMode1 = boolean;
type Pelletdb = string[];
type Settings = string[];
type Logs = string[];
type Mode = string;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "Reading".
 */
export type Reading = string | number;
type IpAddress = string;
type MacAddress = string;
type Architecture = string;
type Bits = string;
type Name = string;
type PrettyName = string;
type Version = string;
type VersionCodename = string;
type VersionId = string;
type Uptime = string;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "FiniteNumber".
 */
type FiniteNumber = number;
type Ready = boolean;
type Samples = number;
type Probe = string;
type Reference = string;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "BackupKind".
 */
export type BackupKind = "settings" | "pelletdb";
type Filename = string;
type File = string;
type File1 = string;
type Offset = number;
type Reset = boolean;
type Text = string;
type A = number;
type B = number;
type C = number;
type Chart = CoefficientPoint[];
type ChartOk = boolean;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "Segment".
 */
export type Segment = "High" | "Medium" | "Low";
type Points = TunerPoint[];
type Action = "factory_reset";
type Bytes = number;
type Members = string[];
type Stem = string;
type Removed = string[];
type Families = LogFamily[];
type Logs1 = string[];
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "MaintenanceAction".
 */
export type MaintenanceAction =
  | "clear_history"
  | "clear_events"
  | "clear_pelletdb"
  | "clear_pelletdb_log";
type ApplyTo = string | null;
type Name1 = string;
type Applied = string | null;
type Id = string;
/**
 * This interface was referenced by `PiFireOperationsWebContracts`'s JSON-Schema
 * via the `definition` "SystemAction".
 */
export type SystemAction = "reboot" | "shutdown" | "restart";
type Probe1 = string;
type Tuning = boolean;
type Mode1 = string;
type Open = boolean;
type Restored = boolean;
type Open1 = boolean;
type Target = string;
type Behind = number;
type Current = string;
type Output = string;
type Started = boolean;
type Branch = string;
type Branches = string[];
type Detached = string | null;
type ManualDependencyActions = string[];
type RemoteUrl = string;
type RemoteVersion = string;
type RestartPending = boolean;
type Version1 = string;
type WebUiBuildFailed = boolean;
type WebUiStale = boolean;
type Output1 = string | null;
type Percent = number | null;
type Status = string | null;

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
interface NetworkInfo {
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
  manual_dependency_actions: ManualDependencyActions;
  remote_url: RemoteUrl;
  remote_version: RemoteVersion;
  restart_pending: RestartPending;
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
