import { useSettingsFieldErrors } from "../../helpers/settings/fieldErrorContext";
import type { SaveStatus } from "../../helpers/settings/useSaveSettings";

/**
 * The shared settings-tab actions row: Save button, success marker, and the
 * server's rejection message. Purely presentational — each tab owns its own
 * `useSaveSettings()` instance and builds its own delta.
 *
 * A rejected save leaves the refused values on screen deliberately: the backend
 * write is atomic and the store is untouched, so there is no drift to correct,
 * and reverting would destroy the user's typing exactly when they need to fix it.
 */
export function SaveBar({
  onSave,
  saving,
  status,
  dirty = false,
}: {
  onSave: () => void | Promise<void>;
  saving: boolean;
  status: SaveStatus;
  /** This tab holds an edit that has not been committed yet. Kept visible
   *  because the edit now outlives the tab: it survives a switch to another
   *  pill (helpers/settings/settingsDrafts.ts), so "still on screen" no longer
   *  implies "already saved". */
  dirty?: boolean;
}) {
  const fieldErrors = useSettingsFieldErrors();
  return (
    <div className="pf-settings-actions">
      <button className="pf-modal-btn accent" disabled={saving} onClick={onSave}>
        {saving ? "Saving…" : "Save"}
      </button>
      {dirty && !saving && <span className="pf-settings-unsaved-text">Unsaved changes</span>}
      {status.kind === "saved" && <span className="pf-settings-saved">Saved ✓</span>}
      {status.kind === "error" && (
        <p className="pf-settings-error-text" role="alert">
          {status.message}
        </p>
      )}
      {(fieldErrors?.unmatched ?? []).map((e) => (
        <p key={e.path} className="pf-settings-error-text" role="alert">
          {e.path}: {e.message}
        </p>
      ))}
    </div>
  );
}
