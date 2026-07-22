import { useCallback, useState } from "react";
import { useRevalidator } from "react-router";
import { applySettings, type SettingsFlag } from "./settingsApi";

const BASE_URL = import.meta.env.VITE_PIFIRE_URL || "";

export function useSaveSettings() {
  const revalidator = useRevalidator();
  const [saving, setSaving] = useState(false);
  const save = useCallback(async (delta: object, flags: SettingsFlag[]): Promise<boolean> => {
    setSaving(true);
    const r = await applySettings(BASE_URL, delta, flags);
    setSaving(false);
    if (r.ok) revalidator.revalidate(); // re-run the loader → fresh settings
    return r.ok;
  }, [revalidator]);
  return { save, saving, baseUrl: BASE_URL };
}
