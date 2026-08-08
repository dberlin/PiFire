import { useId, useState } from "react";
import type { ScanResult, SettingsDependency } from "../../../helpers/wizard/wizardTypes";
import { DiscoveryPanel } from "../DiscoveryPanel";

export interface UsbSerialPickerProps {
  dep: SettingsDependency;
  value: string;
  onChange: (value: string) => void;
  onScan: () => Promise<ScanResult>;
}

export function UsbSerialPicker({ dep, value, onChange, onScan }: UsbSerialPickerProps) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ScanResult | null>(null);
  const listId = useId();

  if (dep.hidden) return null;

  // Flask never used a <select> here either: _macro_wizard_card.html:38-39
  // dispatches usb_serial_device to render_input_usb_serial_device, a free-text
  // <input> plus a Discover button (_macro_probes_config.html:587-597). The one
  // manifest dep of this type lists four /dev/tty* paths, but a board can
  // enumerate as any path (/dev/ttyACM2, a by-id symlink, ...), so a select over
  // those four made the unlisted -- and entirely normal -- cases unreachable.
  //
  // `options`, when present, becomes non-binding <datalist> suggestions.
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
          placeholder={typeof dep.default === "string" ? dep.default : ""}
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
      {/* Legacy rendered the dep description in the card's Description column
          (_macro_wizard_card.html:48); it documents the expected path format. */}
      {dep.description && <span className="pf-field-hint">{dep.description}</span>}
      <button type="button" onClick={handleDiscover} disabled={loading}>
        {loading ? "Scanning…" : "Discover"}
      </button>
      {result && (
        <DiscoveryPanel
          result={result}
          onPick={onChange}
          onRefresh={() => void handleDiscover()}
          onClose={() => setResult(null)}
        />
      )}
    </div>
  );
}
