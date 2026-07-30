import type { ProbeMap, ProbeModuleData, ProbeProfile } from "./probeTypes";

export type WizardSection = "grillplatform" | "display" | "distance" | "probes";
export interface SettingsDependency {
  friendly_name: string;
  description?: string;
  type?: "i2c_bus_num" | "usb_serial_device";
  options?: Record<string, string>;
  /** Manifest fallback (e.g. "CP2112"). Present on every i2c_bus_num dep. */
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
// Client working state (mutable subset submitted at draft/finish):
export interface WizardWorking {
  selections: Record<WizardSection, string | null>;
  settings_dep_values: Record<WizardSection, Record<string, string | null>>;
  display_config: Record<string, Record<string, unknown>>;
  probe_map: ProbeMap;
  probes_units: string;
}
