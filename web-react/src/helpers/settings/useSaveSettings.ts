import type { SaveFieldError, SettingsFlag } from "@pifire/core/settings/controllerTypes";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { useRevalidator } from "react-router";
import { normalizeApiBase, queryKeys } from "../query/keys";
import { applySettings } from "./settingsApi";

const BASE_URL = normalizeApiBase(import.meta.env.PUBLIC_PIFIRE_URL || "");

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
  const queryClient = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<SaveStatus>({ kind: "idle" });
  const [errors, setErrors] = useState<SaveFieldError[]>([]);
  const save = useCallback(
    async (delta: object, flags: SettingsFlag[]): Promise<boolean> => {
      setSaving(true);
      setStatus({ kind: "idle" }); // clear the previous outcome for this attempt
      setErrors([]);
      const r = await applySettings(BASE_URL, delta, flags);
      setSaving(false);
      setErrors(r.errors);
      setStatus(
        r.ok ? { kind: "saved" } : { kind: "error", message: normalizeSaveError(r.message) },
      );
      if (r.ok) {
        // Mark the shared entry invalidated BEFORE re-running the loader. The
        // loader primes itself through fetchQuery, which serves a cache entry
        // unchanged as long as it is neither stale-by-time NOR invalidated --
        // so without this, revalidate() would put the PRE-save values back on
        // screen (staleTime is 30s, easily long enough to still be "fresh").
        //
        // settingsRoot is the prefix of all three loader keys (settings, mode,
        // controller metadata), which preserves exactly what revalidate() did
        // before this cache existed: refetch all three.
        await queryClient.invalidateQueries({ queryKey: queryKeys.settingsRoot(BASE_URL) });
        revalidator.revalidate(); // re-run the loader → fresh settings
      }
      return r.ok;
    },
    [revalidator, queryClient],
  );
  return { save, saving, status, errors, baseUrl: BASE_URL };
}
