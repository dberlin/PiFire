import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { adminErrorText, maintenanceAction, saveAdminSettings } from "../../helpers/admin/adminApi";
import type { AdminSettings, MaintenanceAction } from "../../helpers/admin/adminTypes";
import { queryKeys } from "../../helpers/query/keys";
import type { SaveStatus } from "../../helpers/settings/useSaveSettings";
import { ConfirmAction } from "../dashboard/ConfirmAction";
import { Toggle } from "../settings/fields/Toggle";

interface Clear {
  key: MaintenanceAction;
  label: string;
  title: string;
  message: string;
}

const CLEARS: Clear[] = [
  {
    key: "clear_history",
    label: "Clear History",
    title: "Clear the stored history?",
    message:
      "The temperature history, the current readings and the cook metrics are all cleared. Cook files you have already saved are not touched.",
  },
  {
    key: "clear_events",
    label: "Clear Event Log",
    title: "Clear the event log?",
    message: "events.log is deleted. Every other log file is left alone.",
  },
  {
    key: "clear_pelletdb",
    label: "Clear Pellet Database",
    title: "Reset the pellet database?",
    message:
      "Every brand, wood, profile and log entry is replaced with the defaults. The current load-out goes with them.",
  },
  {
    key: "clear_pelletdb_log",
    label: "Clear Pellet Log",
    title: "Clear the pellet load log?",
    message:
      "Only the record of which profile was loaded when is cleared. Your profiles, brands and woods are kept.",
  },
];

/**
 * The clears and the two admin toggles.
 *
 * Deliberately NOT gated on Stop mode, matching the server: clearing a pellet
 * log mid-cook is recoverable, and Flask offered all four from any mode. Only
 * the power actions in SystemCard need that guard.
 *
 * Each clear still gets a confirmation, because "recoverable" here means the
 * machine survives, not that the data comes back.
 */
export function MaintenanceCard({
  settings,
  onChanged,
}: {
  settings: AdminSettings;
  /** Refetches the page's state. A clear changes the log list the page shows,
   * and a toggle changes the value these switches read. */
  onChanged: () => void | Promise<void>;
}) {
  const [pending, setPending] = useState<Clear | null>(null);
  const [status, setStatus] = useState<SaveStatus>({ kind: "idle" });
  const queryClient = useQueryClient();

  const runClear = async (clear: Clear) => {
    setPending(null);
    setStatus({ kind: "idle" });
    const result = await maintenanceAction(clear.key);
    if (result.ok) {
      setStatus({ kind: "saved" });
      // clear_history wipes the temperature history, the current readings AND
      // the cook metrics server-side (blueprints/api_admin/routes.py), but
      // `onChanged` only reaches this page's own adminState entry. Without
      // this, /metrics and /history keep serving the just-deleted data for up
      // to their 30s staleTime. Scoped to this one action -- a backup restore
      // or a log clear has no bearing on either cache.
      if (clear.key === "clear_history") {
        await queryClient.invalidateQueries({ queryKey: queryKeys.metrics });
        await queryClient.invalidateQueries({ queryKey: queryKeys.historyRoot });
      }
      await onChanged();
    } else {
      setStatus({ kind: "error", message: adminErrorText(result) });
    }
  };

  const saveToggle = async (fields: Partial<AdminSettings>) => {
    setStatus({ kind: "idle" });
    const result = await saveAdminSettings(fields);
    if (result.ok) {
      setStatus({ kind: "saved" });
      await onChanged();
    } else {
      setStatus({ kind: "error", message: adminErrorText(result) });
    }
  };

  return (
    <section className="pf-admin-card" aria-labelledby="admin-maintenance">
      <h2 className="pf-admin-card-title" id="admin-maintenance">
        Maintenance
      </h2>

      {status.kind === "saved" && (
        <p className="pf-admin-notice" role="status">
          Done.
        </p>
      )}
      {status.kind === "error" && (
        <p className="pf-settings-error-text" role="alert">
          {status.message}
        </p>
      )}

      <div className="pf-admin-actions">
        {CLEARS.map((clear) => (
          <button
            key={clear.key}
            type="button"
            className="pf-admin-btn danger"
            onClick={() => setPending(clear)}
          >
            {clear.label}
          </button>
        ))}
      </div>

      <div className="pf-admin-toggles">
        {/* Saved on the spot rather than behind a Save button: there is no
            draft to build up, and the settings page's SaveBar belongs to a
            form with many fields. Enabling debug mode also raises the control
            settings_update flag server-side, which is how the running control
            process learns about it. */}
        <Toggle
          label="Debug Mode"
          checked={settings.debug_mode}
          onChange={(v) => void saveToggle({ debug_mode: v })}
        />
        <Toggle
          label="Boot to Monitor Mode"
          checked={settings.boot_to_monitor}
          onChange={(v) => void saveToggle({ boot_to_monitor: v })}
        />
      </div>

      <ConfirmAction
        open={pending !== null}
        title={pending?.title ?? ""}
        message={pending?.message}
        onConfirm={() => {
          if (pending) void runClear(pending);
        }}
        onCancel={() => setPending(null)}
      />
    </section>
  );
}
