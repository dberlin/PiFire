import type { BtScanRow } from "@pifire/core/contracts/wizard";
import { useState } from "react";

import { scanBluetooth } from "../../../helpers/wizard/wizardApi";

export interface BluetoothPickerProps {
  label: string;
  value: string;
  baseUrl: string;
  onChange: (value: string) => void;
}

export function BluetoothPicker({ label, value, baseUrl, onChange }: BluetoothPickerProps) {
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<BtScanRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleScan() {
    setLoading(true);
    try {
      const r = await scanBluetooth(baseUrl);
      setRows(r.rows);
      setError(r.error);
    } finally {
      setLoading(false);
    }
  }

  return (
    <label className="pf-field pf-field-column">
      <span className="pf-field-label">{label}</span>
      <input
        className="pf-input"
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <span className="pf-field-hint">Turn on your bluetooth device then click Scan.</span>
      <button type="button" onClick={() => void handleScan()} disabled={loading}>
        {loading ? "Scanning…" : "Scan"}
      </button>
      {error && <p role="alert">{error}</p>}
      {rows && rows.length > 0 && (
        <div className="pf-discovery-group-items">
          {rows.map((row) => (
            <button type="button" key={row.hw_id} onClick={() => onChange(row.hw_id)}>
              {row.name} [{row.hw_id}] {row.info}
            </button>
          ))}
        </div>
      )}
    </label>
  );
}
