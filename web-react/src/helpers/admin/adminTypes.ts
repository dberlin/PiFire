// Shapes the /api/admin/* surface reads and writes.
//
// `system` mirrors common/system.py's gather_system_info() return value, which
// falls back to the literal string "Unknown" for anything the platform could
// not probe -- there is no null here, so a card renders these straight.

/** One entry of gather_system_info()'s `network_info` map, keyed by interface. */
export interface NetworkInterface {
  ip_address: string;
  mac_address: string;
}

export interface CpuInfo {
  hardware: string;
  model: string;
  model_name: string;
  cores: string;
  frequency: string;
}

export interface HardwareInfo {
  total_ram: string;
  available_ram: string;
  cpu_info: CpuInfo;
}

export interface SystemInfo {
  /** Raw `uptime(1)` output, newline included. */
  uptime: string;
  os_info: string;
  network_info: Record<string, NetworkInterface>;
  hardware_info: HardwareInfo;
}

/** The two globals the admin page owns. Every other setting lives in /settings. */
export interface AdminSettings {
  debug_mode: boolean;
  boot_to_monitor: boolean;
}

/** GET /api/admin/backups, and the `backups` member of the state payload.
 * Bare filenames only -- the server never puts a path in a response. */
export interface BackupListing {
  settings: string[];
  pelletdb: string[];
}

export type BackupKind = keyof BackupListing;

export interface AdminState {
  system: SystemInfo;
  settings: AdminSettings;
  backups: BackupListing;
  logs: string[];
  /** The live control mode. Carried in this payload rather than fetched
   * separately because every destructive control on the page is disabled
   * unless it reads "Stop", and two round trips could disagree. */
  mode: string;
}

/** The three that can take the machine away from you. */
export type SystemAction = "reboot" | "shutdown" | "restart";

/** Destructive but recoverable, and deliberately NOT gated on Stop mode --
 * blueprints/api_admin/routes.py offers all four from any mode, as Flask did. */
export type MaintenanceAction =
  | "clear_history"
  | "clear_events"
  | "clear_pelletdb"
  | "clear_pelletdb_log";

/** Every admin call resolves to one of these rather than throwing.
 *
 * A refusal is a normal outcome on this page -- the grill being lit is the
 * expected reason a reboot does not happen -- so the caller branches on `ok`
 * instead of catching. `message` is the server's machine token
 * ("not_stopped", "bad_request", "not_found"), which tests/web assert on;
 * run it through adminErrorText() before showing it to anyone.
 */
export interface AdminResult<T = null> {
  ok: boolean;
  /** HTTP status, or 0 when the request never reached a server. */
  status: number;
  message: string;
  data: T | null;
  /** From a 400's `data.field`: which key the server rejected. */
  field?: string;
  /** From the 409's `data.mode`: the mode that blocked the action. */
  mode?: string;
}
