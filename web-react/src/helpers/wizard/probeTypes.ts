export interface ProbeProfile {
  A: number;
  B: number;
  C: number;
  id: string;
  name: string;
}
export interface ProbeDevice {
  device: string;
  module: string;
  module_filename: string;
  ports: string[];
  config: Record<string, unknown>; // may hold probes_list?: string[]
}
export type ProbeType = "Primary" | "Food" | "Aux";
export interface Probe {
  name: string;
  label: string;
  type: ProbeType;
  enabled: boolean;
  device: string;
  port: string;
  profile: ProbeProfile | Record<string, never>;
}
export interface ProbeMap {
  probe_devices: ProbeDevice[];
  probe_info: Probe[];
}
export type ProbeFieldType =
  | "list"
  | "int"
  | "float"
  | "string"
  | "i2c_bus_num"
  | "i2c_bus"
  | "probes_list"
  | "bt_address"
  | "usb_serial_device";
export interface ProbeConfigField {
  label: string;
  friendly_name: string;
  description?: string;
  type: ProbeFieldType;
  default?: unknown;
  hidden?: boolean;
  list_values?: unknown[];
  list_labels?: string[];
  min?: number;
  max?: number | "";
  step?: number;
}
export interface ProbeModuleData {
  friendly_name: string;
  filename: string;
  description?: string;
  notes?: string;
  image?: string;
  device_specific: { ports: string[]; type: string; config: ProbeConfigField[] };
}
export interface BtScanRow {
  name: string;
  hw_id: string;
  info: string;
}
export interface ThermoworksRow {
  label: string;
  type: string;
  serial: string;
  num_channels: number;
}
export interface RowsResult<T> {
  rows: T[];
  error: string | null;
}
