import { createCommand } from "@pifire/core/command";
import type { SettingsSchema } from "@pifire/core/settings/settingsTypes";
import { useState } from "react";
import { useOutletContext, useRevalidator } from "react-router";

import { ConfirmAction } from "../../dashboard/ConfirmAction";
import { Section } from "../fields/Section";
import { Select } from "../fields/Select";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";
const UNIT_OPTIONS = [
  { value: "F", label: "Fahrenheit (°F)" },
  { value: "C", label: "Celsius (°C)" },
];

export function UnitsTab() {
  const { settings } = useOutletContext<{ settings: SettingsSchema; mode: string }>();
  const revalidator = useRevalidator();
  const [units, setUnits] = useState<"F" | "C">(settings.globals?.units === "C" ? "C" : "F");
  const [pending, setPending] = useState<"F" | "C" | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-sync from the loader on revalidation via render-phase adjustment (house
  // style — NOT a useEffect; React Compiler rejects setState-in-effect. Do NOT suppress.)
  const [prevSettings, setPrevSettings] = useState(settings);
  if (settings !== prevSettings) {
    setPrevSettings(settings);
    setUnits(settings.globals?.units === "C" ? "C" : "F");
  }

  const onChange = (v: string) => {
    const next = v === "C" ? "C" : "F";
    if (next !== units) setPending(next); // changing units stops the grill
  };

  const confirmChange = async () => {
    const next = pending!;
    setPending(null);
    const r = await createCommand(BASE_URL).setUnits(next);
    if (r.ok) {
      setError(null);
      setUnits(next);
      revalidator.revalidate();
    } else {
      setError(r.message || "Failed to change units");
    }
  };

  return (
    <>
      <Section title="Units">
        <Select
          label="Temperature Units"
          value={units}
          options={UNIT_OPTIONS}
          onChange={onChange}
        />
        <p className="pf-settings-hint">
          Changing units converts all stored temperatures and <b>stops the grill</b>.
        </p>
        {error && <p className="pf-settings-error-text">{error}</p>}
      </Section>
      <ConfirmAction
        open={pending !== null}
        title={`Switch to °${pending ?? ""}? This will stop the grill.`}
        onCancel={() => setPending(null)}
        onConfirm={confirmChange}
      />
    </>
  );
}
