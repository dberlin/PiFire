import type {
  I2CBusValue,
  ProbeConfigField,
  SettingsDependency,
  WireValue,
} from "@pifire/core/contracts/wizard";

import { type BusKind, isI2CBusValue } from "../../../helpers/wizard/i2cBusTypes";
import { scan } from "../../../helpers/wizard/wizardApi";
import { I2cBusField } from "../fields/I2cBusField";
import { SelectField } from "../fields/SelectField";
import { UsbSerialPicker } from "../fields/UsbSerialPicker";
import { BluetoothPicker } from "./BluetoothPicker";
import { ThermoworksPicker } from "./ThermoworksPicker";

export interface DeviceConfigFieldProps {
  field: ProbeConfigField;
  value: WireValue | undefined;
  allValues: Record<string, WireValue>;
  availableProbes: string[];
  baseUrl: string;
  onChange: (label: string, value: WireValue) => void;
}

export function DeviceConfigField({
  field,
  value,
  allValues,
  availableProbes,
  baseUrl,
  onChange,
}: DeviceConfigFieldProps) {
  const set = (value: WireValue) => onChange(field.label, value);

  // device_serial: hidden in the manifest but always shown because it hosts
  // the Test Connection button (§6 special case).
  if (field.label === "device_serial") {
    return (
      <ThermoworksPicker
        value={String(value ?? "")}
        email={String(allValues.email ?? "")}
        password={String(allValues.password ?? "")}
        baseUrl={baseUrl}
        onPick={(row) => {
          onChange("device_serial", row.serial);
          onChange("num_probes", row.num_channels);
        }}
      />
    );
  }
  if (field.hidden) return null;

  // The pickers take a SettingsDependency; a probe device's ProbeConfigField is
  // the same information under different key names. `default` must be carried
  // across as a string or a usb_serial_device picker loses its placeholder.
  const dep: SettingsDependency = {
    friendly_name: field.friendly_name,
    description: field.description,
    default: typeof field.default === "string" ? field.default : undefined,
    settings: [],
  };

  switch (field.type) {
    case "int":
    case "float":
      return (
        <label className="pf-field">
          <span className="pf-field-label">{field.friendly_name}</span>
          <input
            className="pf-input"
            type="number"
            value={String(value ?? field.default ?? "")}
            min={field.min}
            max={field.max === "" ? undefined : field.max}
            step={field.step}
            onChange={(e) => set(e.target.value)}
          />
        </label>
      );
    case "list": {
      const options = (field.list_values ?? []).map((v, i) => ({
        value: String(v),
        label: field.list_labels?.[i] ?? String(v),
      }));
      return (
        <SelectField
          label={field.friendly_name}
          value={String(value ?? "")}
          options={options}
          onChange={set}
        />
      );
    }
    case "probes_list": {
      const selected = Array.isArray(value)
        ? value.filter((item): item is string => typeof item === "string")
        : [];
      return (
        <label className="pf-field">
          <span className="pf-field-label">{field.friendly_name}</span>
          <select
            className="pf-input"
            multiple
            value={selected}
            onChange={(e) => set(Array.from(e.target.selectedOptions, (o) => o.value))}
          >
            {availableProbes.map((label) => (
              <option key={label} value={label}>
                {label}
              </option>
            ))}
          </select>
        </label>
      );
    }
    case "i2c_bus": {
      const bus: I2CBusValue & { kind: BusKind } = isI2CBusValue(value) ? value : { kind: "basic" };
      return (
        <I2cBusField
          dep={dep}
          value={bus}
          onChange={set}
          onScan={(kind) => scan(baseUrl, { kind })}
        />
      );
    }
    case "bt_address":
      return (
        <BluetoothPicker
          label={field.friendly_name}
          value={String(value ?? "")}
          baseUrl={baseUrl}
          onChange={set}
        />
      );
    case "usb_serial_device":
      return (
        <UsbSerialPicker
          dep={dep}
          value={String(value ?? "")}
          onChange={set}
          // Unfiltered on purpose, unlike ModuleCard's usb_serial_device: `dep`
          // here is synthesized from a ProbeConfigField, which carries no
          // vid/pid, and no probe module declares a usb_serial_device field
          // today. Passing dep.vid would read as a filter while always being
          // undefined -- the exact shape of the bug this scan already had.
          // Give ProbeConfigField vid/pid, and carry them into `dep` above,
          // when a probe module actually needs one.
          onScan={() => scan(baseUrl, { kind: "usb_serial" })}
        />
      );
    default:
      return (
        <label className="pf-field">
          <span className="pf-field-label">{field.friendly_name}</span>
          <input
            className="pf-input"
            type="text"
            value={String(value ?? "")}
            onChange={(e) => set(e.target.value)}
          />
        </label>
      );
  }
}
