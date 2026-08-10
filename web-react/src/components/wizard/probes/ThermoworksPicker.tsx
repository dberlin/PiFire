import { useState } from "react";
import type { ThermoworksRow } from "../../../helpers/contracts/wizard.gen";
import { scanThermoworks } from "../../../helpers/wizard/wizardApi";

export interface ThermoworksPickerProps {
  value: string;
  email: string;
  password: string;
  baseUrl: string;
  onPick: (row: ThermoworksRow) => void;
}

export function ThermoworksPicker({
  value,
  email,
  password,
  baseUrl,
  onPick,
}: ThermoworksPickerProps) {
  const [loading, setLoading] = useState(false);
  const [rows, setRows] = useState<ThermoworksRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleTest() {
    setLoading(true);
    try {
      const r = await scanThermoworks(baseUrl, email, password);
      setRows(r.rows);
      setError(r.error);
    } finally {
      setLoading(false);
    }
  }

  return (
    <label className="pf-field pf-field-column">
      <span className="pf-field-label">Device Serial</span>
      <input className="pf-input" type="text" value={value} readOnly />
      <button type="button" onClick={() => void handleTest()} disabled={loading}>
        {loading ? "Connecting…" : "Test Connection"}
      </button>
      {error && <p role="alert">{error}</p>}
      {rows?.map((row) => (
        <button type="button" key={row.serial} onClick={() => onPick(row)}>
          {row.label} ({row.serial}) — {row.num_channels} probes
        </button>
      ))}
    </label>
  );
}
