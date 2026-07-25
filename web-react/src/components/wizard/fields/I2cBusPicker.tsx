import { useId, useState } from "react";
import type { ScanResult, SettingsDependency } from "../../../helpers/wizard/wizardTypes";
import { DiscoveryPanel } from "../DiscoveryPanel";

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
  const listId = useId();

  if (dep.hidden) return null;

  // Flask never used a <select> for this type: _macro_wizard_card.html:36-37
  // dispatches i2c_bus_num to render_input_i2c_bus_num, which is a free-text
  // <input> plus a Discover button (_macro_probes_config.html:542-552). It has
  // to be free text -- the accepted values are an adapter NAME ("CP2112",
  // "MCP2221"), a "serial:<ISERIAL>" match, a bare /dev/i2c-N number, a pyftdi
  // URL, or an MCP2221 serial. No enumerable option list covers that, which is
  // why all 8 grillplatform and all 5 probe i2c_bus_num deps in
  // wizard_manifest.json ship with no `options` key at all. Rendering a select
  // over that empty map produced a control with nothing to choose, and a fresh
  // install could not be completed.
  //
  // `options`, when a dep does carry it, becomes non-binding <datalist>
  // suggestions rather than the only permitted values.
  const suggestions = Object.keys(dep.options ?? {});

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
      <label className="pf-field">
        <span className="pf-field-label">{dep.friendly_name}</span>
        <input
          className="pf-input"
          type="text"
          value={value}
          placeholder={dep.default ?? ""}
          list={suggestions.length > 0 ? listId : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      {suggestions.length > 0 && (
        <datalist id={listId}>
          {suggestions.map((optValue) => (
            <option key={optValue} value={optValue}>
              {dep.options?.[optValue]}
            </option>
          ))}
        </datalist>
      )}
      {/* The dep description is the only place the "CP2112" / "serial:<ISERIAL>"
          syntax is explained; legacy showed it in the card's Description column
          (_macro_wizard_card.html:48). Mandatory now the control is free text. */}
      {dep.description && <span className="pf-field-hint">{dep.description}</span>}
      <span className="pf-field-hint">Detected kind: {kindValue}</span>
      <button type="button" onClick={handleDiscover} disabled={loading}>
        {loading ? "Scanning…" : "Discover"}
      </button>
      {result && <DiscoveryPanel result={result} onPick={onChange} />}
    </div>
  );
}
