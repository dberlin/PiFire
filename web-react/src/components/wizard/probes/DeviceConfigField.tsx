import type { ProbeConfigField } from "../../../helpers/wizard/probeTypes";
import { scan } from "../../../helpers/wizard/wizardApi";
import { I2cBusPicker } from "../fields/I2cBusPicker";
import { SelectField } from "../fields/SelectField";
import { UsbSerialPicker } from "../fields/UsbSerialPicker";
import { BluetoothPicker } from "./BluetoothPicker";
import { ThermoworksPicker } from "./ThermoworksPicker";

export interface DeviceConfigFieldProps {
  field: ProbeConfigField;
  value: unknown;
  allValues: Record<string, unknown>;
  availableProbes: string[];
  baseUrl: string;
  onChange: (label: string, value: unknown) => void;
}

export function DeviceConfigField({
  field,
  value,
  allValues,
  availableProbes,
  baseUrl,
  onChange,
}: DeviceConfigFieldProps) {
  const set = (v: unknown) => onChange(field.label, v);

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

  const dep = {
    friendly_name: field.friendly_name,
    description: field.description,
    settings: [] as string[],
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
      const selected = (value as string[] | undefined) ?? [];
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
    case "i2c_bus_num":
      return (
        <I2cBusPicker
          dep={dep}
          value={String(value ?? "")}
          kindValue={String(allValues.i2c_bus_kind ?? "")}
          onChange={set}
          onScan={() => scan(baseUrl, { kind: String(allValues.i2c_bus_kind ?? "") })}
        />
      );
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
