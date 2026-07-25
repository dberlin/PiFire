import { useCallback, useState } from "react";
import { useRevalidator } from "react-router";
import { applySettings, type SettingsFlag } from "./settingsApi";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export type SaveStatus = { kind: "idle" } | { kind: "saved" } | { kind: "error"; message: string };

// The backend prefixes every validation rejection with this; it is noise once
// the text sits under a Save button that visibly failed.
const PREFIX = "Settings update failed: ";

/**
 * Turn the server's rejection message into something worth showing. The dotted
 * field path pydantic emits is kept verbatim — it is the actionable part.
 * An empty red gap would be a worse bug than the one this fixes, so a blank
 * message falls back to a generic sentence.
 */
export function normalizeSaveError(message: string): string {
  const stripped = message.startsWith(PREFIX) ? message.slice(PREFIX.length) : message;
  return stripped.trim() || "Save failed.";
}

export function useSaveSettings() {
  const revalidator = useRevalidator();
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<SaveStatus>({ kind: "idle" });
  const save = useCallback(
    async (delta: object, flags: SettingsFlag[]): Promise<boolean> => {
      setSaving(true);
      setStatus({ kind: "idle" }); // clear the previous outcome for this attempt
      const r = await applySettings(BASE_URL, delta, flags);
      setSaving(false);
      setStatus(
        r.ok ? { kind: "saved" } : { kind: "error", message: normalizeSaveError(r.message) },
      );
      if (r.ok) revalidator.revalidate(); // re-run the loader → fresh settings
      return r.ok;
    },
    [revalidator],
  );
  return { save, saving, status, baseUrl: BASE_URL };
}
