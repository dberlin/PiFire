import { useState } from "react";
import type { ScanResult, SettingsDependency } from "../../../helpers/wizard/wizardTypes";
import { DiscoveryPanel } from "../DiscoveryPanel";
import { SelectField } from "./SelectField";

export interface I2cBusPickerProps {
  dep: SettingsDependency;
  value: string;
  /**
   * The current value of the paired "kind" setting for this bus, supplied
   * explicitly by the caller (e.g. the detected/selected chip kind). This is
   * NOT derived from `dep.settings` via a `_num` -> `_kind` name substitution.
   */
  kindValue: string;
  onChange: (value: string) => void;
  onScan: () => Promise<ScanResult>;
}

export function I2cBusPicker({ dep, value, kindValue, onChange, onScan }: I2cBusPickerProps) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);

  if (dep.hidden) return null;

  const options = Object.entries(dep.options ?? {}).map(([optValue, label]) => ({
    value: optValue,
    label,
  }));

  async function handleDiscover() {
    setLoading(true);
    try {
      setResult(await onScan());
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="pf-field-column">
      <SelectField label={dep.friendly_name} value={value} options={options} onChange={onChange} />
      <span className="pf-field-hint">Detected kind: {kindValue}</span>
      <button type="button" onClick={handleDiscover} disabled={loading}>
        {loading ? "Scanning…" : "Discover"}
      </button>
      {result && <DiscoveryPanel result={result} onPick={onChange} />}
    </div>
  );
}
