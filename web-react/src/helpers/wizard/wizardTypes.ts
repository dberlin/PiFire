import type { ProbeMap, ProbeModuleData, ProbeProfile } from "./probeTypes";

export type WizardSection = "grillplatform" | "display" | "distance" | "probes";
export interface SettingsDependency {
  friendly_name: string;
  description?: string;
  type?: "i2c_bus_num" | "usb_serial_device";
  options?: Record<string, string>;
  /** Manifest fallback, shown as the field's placeholder. Present on every
   *  i2c_bus_num dep, blank on all of them -- the bus is the operator's to
   *  name, and for ft232h/mcp2221 blank already means "the first one found". */
  default?: string;
  /** USB vendor/product ID to narrow a `usb_serial_device` Discover scan to
   *  one kind of board, written the way the manifest writes them ("0x2a19").
   *  Omitted or null means "list every serial device", which is the right
   *  answer for a device whose IDs we do not know. The backend coerces the
   *  hex string to the int pyserial reports -- see common/usb_serial.py. */
  vid?: string | number | null;
  pid?: string | number | null;
  hidden?: boolean;
  settings: string[];
}
export interface ConfigOption {
  option_name: string;
  option_friendly_name: string;
  option_description?: string;
  option_type: "list" | "string";
  list_values?: unknown[];
  list_labels?: string[];
  default?: unknown;
  hidden?: boolean;
}
export interface WizardModuleData {
  friendly_name: string;
  description?: string;
  notes?: string;
  image?: string;
  settings_dependencies: Record<string, SettingsDependency>;
  config?: ConfigOption[];
}
export interface WizardState {
  modules_metadata: {
    grillplatform: Record<string, WizardModuleData>;
    display: Record<string, WizardModuleData>;
    distance: Record<string, WizardModuleData>;
    probes: Record<string, ProbeModuleData>;
  };
  selections: Record<WizardSection, string | null>;
  settings_dep_values: Record<WizardSection, Record<string, string | null>>;
  display_config: Record<string, Record<string, unknown>>;
  probe_map: ProbeMap;
  probe_profiles: ProbeProfile[];
  probes_units: string;
  board_probe_maps: Record<string, ProbeMap>;
  control_mode: string;
  first_time_setup: boolean;
  has_draft: boolean;
}
export interface ModuleValues {
  settings: Record<string, string | null>;
  config: Record<string, unknown>;
}
export interface ScanGroup {
  title: string;
  items: { value: string; label: string }[];
}
export interface ScanResult {
  groups: ScanGroup[];
  error: string | null;
}
export interface InstallStatus {
  percent: number;
  status: string;
  output: string;
}
/** A slice of the installer's transcript. `output` above is one line, replaced
 *  faster than any poll can read it; this is the log file behind it, served
 *  from a byte offset so a poll costs a tick's worth of output rather than the
 *  whole run. `reset` means the offset the client sent no longer points into
 *  the run it is showing (a second install, or rotation) and its buffer must be
 *  replaced rather than appended to. */
export interface InstallLog {
  text: string;
  offset: number;
  reset: boolean;
}
// Client working state (mutable subset submitted at draft/finish):
export interface WizardWorking {
  selections: Record<WizardSection, string | null>;
  settings_dep_values: Record<WizardSection, Record<string, string | null>>;
  display_config: Record<string, Record<string, unknown>>;
  probe_map: ProbeMap;
  probes_units: string;
}
