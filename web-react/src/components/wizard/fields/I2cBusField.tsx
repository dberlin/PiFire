import { useId, useState } from "react";
import {
  BUS_KIND_LABELS,
  emptyBus,
  type I2cBusValue,
  i2cBusError,
  KERNEL_BY_LABELS,
  type KernelBy,
  kernelBy,
} from "../../../helpers/wizard/i2cBusTypes";
import type { ScanResult, SettingsDependency } from "../../../helpers/wizard/wizardTypes";
import { DiscoveryPanel } from "../DiscoveryPanel";

export interface I2cBusFieldProps {
  dep: SettingsDependency;
  value: I2cBusValue;
  onChange: (value: I2cBusValue) => void;
  onScan: (kind: I2cBusValue["kind"]) => Promise<ScanResult>;
}

/** The kernel discovery groups, keyed by the field each fills. Picking a row
 *  writes into the field the radio has selected, so a serial picked from the
 *  serial group produces a serial-addressed bus and not an adapter name. */
const GROUP_FOR: Record<KernelBy, string> = {
  bus_num: "By Bus Number",
  adapter: "By Adapter Name",
  serial: "By Serial",
};

export function I2cBusField({ dep, value, onChange, onScan }: I2cBusFieldProps) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);
  const kindId = useId();
  const by = kernelBy(value);

  if (dep.hidden) return null;

  const error = i2cBusError(value);

  async function handleDiscover() {
    setLoading(true);
    try {
      setResult(await onScan(value.kind));
    } finally {
      setLoading(false);
    }
  }

  function handlePick(picked: string) {
    if (value.kind === "kernel") {
      if (by === "bus_num") onChange({ kind: "kernel", bus_num: Number(picked) });
      else if (by === "serial") onChange({ kind: "kernel", serial: picked });
      else onChange({ kind: "kernel", adapter: picked });
    } else if (value.kind === "ft232h") {
      onChange({ kind: "ft232h", url: picked });
    } else if (value.kind === "mcp2221") {
      onChange({ kind: "mcp2221", serial: picked });
    }
  }

  // Only the group matching the selected radio is offered: the others would
  // write a value into the field the operator did not choose.
  const scoped: ScanResult | null =
    result && value.kind === "kernel"
      ? { ...result, groups: (result.groups ?? []).filter((g) => g.title === GROUP_FOR[by]) }
      : result;

  return (
    <div className="pf-field pf-i2c-bus-field">
      <label className="pf-field-label" htmlFor={kindId}>
        {dep.friendly_name}
      </label>
      <select
        id={kindId}
        className="pf-input"
        value={value.kind}
        onChange={(e) => onChange(emptyBus(e.target.value as I2cBusValue["kind"], by))}
      >
        {(Object.keys(BUS_KIND_LABELS) as I2cBusValue["kind"][]).map((kind) => (
          <option key={kind} value={kind}>
            {BUS_KIND_LABELS[kind]}
          </option>
        ))}
      </select>

      {value.kind === "kernel" && (
        <fieldset className="pf-i2c-bus-kernel">
          {(Object.keys(KERNEL_BY_LABELS) as KernelBy[]).map((option) => (
            <label key={option}>
              <input
                type="radio"
                name={`${kindId}-by`}
                checked={by === option}
                aria-label={KERNEL_BY_LABELS[option]}
                onChange={() => onChange(emptyBus("kernel", option))}
              />
              {KERNEL_BY_LABELS[option]}
            </label>
          ))}
          {"bus_num" in value ? (
            <input
              className="pf-input"
              inputMode="numeric"
              aria-label={KERNEL_BY_LABELS.bus_num}
              value={value.bus_num === null ? "" : String(value.bus_num)}
              onChange={(e) => {
                const n = Number.parseInt(e.target.value, 10);
                onChange({ kind: "kernel", bus_num: Number.isNaN(n) ? null : n });
              }}
            />
          ) : "serial" in value ? (
            <input
              className="pf-input"
              aria-label={KERNEL_BY_LABELS.serial}
              value={value.serial}
              onChange={(e) => onChange({ kind: "kernel", serial: e.target.value })}
            />
          ) : (
            <input
              className="pf-input"
              aria-label={KERNEL_BY_LABELS.adapter}
              value={value.adapter}
              onChange={(e) => onChange({ kind: "kernel", adapter: e.target.value })}
            />
          )}
        </fieldset>
      )}

      {value.kind === "ft232h" && (
        <input
          className="pf-input"
          aria-label="FT232H URL"
          placeholder="blank = the first FT232H found"
          value={value.url}
          onChange={(e) => onChange({ kind: "ft232h", url: e.target.value })}
        />
      )}

      {value.kind === "mcp2221" && (
        <input
          className="pf-input"
          aria-label="MCP2221 serial"
          placeholder="blank = the first MCP2221 found"
          value={value.serial}
          onChange={(e) => onChange({ kind: "mcp2221", serial: e.target.value })}
        />
      )}

      {dep.description && <span className="pf-field-hint">{dep.description}</span>}
      {error && (
        <span role="alert" className="pf-field-error">
          {error}
        </span>
      )}
      {value.kind !== "basic" && (
        <button type="button" onClick={() => void handleDiscover()} disabled={loading}>
          {loading ? "Scanning…" : "Discover"}
        </button>
      )}
      {scoped && <DiscoveryPanel result={scoped} onPick={handlePick} />}
    </div>
  );
}
