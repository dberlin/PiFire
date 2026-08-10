// Typed client for the /api/admin/* surface.
//
// NOTHING here sends a path. Backups are named by bare filename and resolved
// server-side through common/file_browser.py's resolve_managed_file; the log
// endpoints take no name at all. That is the whole reason blueprints/api_admin
// exists in preference to Flask's /admin, which concatenated a client string
// onto a folder.
//
// Every response uses common/app.py's api_response envelope
// {data, result, message} -- including the GETs, so one helper covers the
// surface. Unlike helpers/files/apiEnvelope.ts, these calls RESOLVE to a
// failure instead of throwing one: on this page a refusal is an expected
// outcome (the grill is lit, so the reboot does not happen), and every caller
// has to render the reason rather than escape past it.

import type { ApiEnvelope } from "../contracts/core.gen";
import type {
  AdminSettingsUpdate,
  AdminState,
  BackupCreateRequest,
  BackupCreated,
  BackupKind,
  BackupListing,
  BackupRestoreRequest,
  BackupRestored,
  FactoryResetResponse,
  LogsDeleted,
  LogsMetadata,
  MaintenanceAction,
  MaintenanceActionRequest,
  MaintenanceActionResponse,
  SystemAction,
  SystemActionRequest,
  SystemActionResponse,
} from "../contracts/operations.gen";
import type { AdminResult } from "./adminTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

const url = (baseUrl: string, path: string) => `${baseUrl}/api/admin/${path}`;


/** Unpack the envelope into an AdminResult, whatever the status.
 *
 * A body that is not JSON (a proxy's HTML 404, a dropped connection) must not
 * mask the status the caller branches on, so the parse failure falls back to
 * the status rather than propagating. */
async function unpack<T>(res: Response): Promise<AdminResult<T>> {
  const body = (await res.json().catch(() => ({}))) as Partial<ApiEnvelope>;
  const detail = (body.data ?? null) as (T & { field?: string; mode?: string }) | null;
  return {
    ok: res.ok && body.result === "OK",
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    data: detail,
    field: detail?.field,
    mode: detail?.mode,
  };
}

async function get<T>(baseUrl: string, path: string): Promise<AdminResult<T>> {
  try {
    return await unpack<T>(await fetch(url(baseUrl, path)));
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

async function post<T>(baseUrl: string, path: string, body: unknown = {}): Promise<AdminResult<T>> {
  try {
    const res = await fetch(url(baseUrl, path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return await unpack<T>(res);
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

/** Turn a refusal into copy for a human.
 *
 * The server's `message` is a machine token by design -- tests/web assert on
 * it, so it must not drift into prose there. This is the one place that
 * translation lives; a card never matches on the token itself. */
export function adminErrorText(result: AdminResult<unknown>): string {
  switch (result.message) {
    case "not_stopped":
      return `The grill must be stopped first — it is currently in ${result.mode || "another"} mode.`;
    case "not_found":
      return "That file is no longer on the server.";
    case "bad_request":
      return result.field
        ? `The server refused that request: ${result.field}.`
        : "The server refused that request.";
    default:
      return result.message;
  }
}

// ---------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------

/** Everything the page renders, in one read.
 *
 * Not free: state_payload() calls gather_system_info(), which probes the
 * platform and writes the readings back into control. Fetch it on mount and
 * after a change, never on a timer. */
export const fetchAdminState = (baseUrl = BASE_URL) => get<AdminState>(baseUrl, "state");

// ---------------------------------------------------------------------------
// the destructive four
// ---------------------------------------------------------------------------

/** Reboot, shut down, or restart the PiFire scripts.
 *
 * POST is enforced server-side: /api/cmd/reboot answered a bare GET until
 * 2026-07-27, which made a link or a prefetch enough to power the box off.
 * Refused with 409 `not_stopped` unless the grill is stopped. */
export const systemAction = (action: SystemAction, baseUrl = BASE_URL) => {
  const body: SystemActionRequest = { action };
  return post<SystemActionResponse>(baseUrl, "system", body);
};

/** Reset settings, control, history AND the pellet database to defaults, then
 * restart. The pellet database goes too -- every profile and every log entry --
 * which is a deliberate ruling, not a side effect. Also 409-gated. */
export const factoryReset = (baseUrl = BASE_URL) =>
  post<FactoryResetResponse>(baseUrl, "factory-reset", {});

// ---------------------------------------------------------------------------
// maintenance and toggles
// ---------------------------------------------------------------------------

/** One of the four clears. Not mode-gated: clearing a pellet log mid-cook is
 * recoverable, so the server allows it from any mode. */
export const maintenanceAction = (action: MaintenanceAction, baseUrl = BASE_URL) => {
  const body: MaintenanceActionRequest = { action };
  return post<MaintenanceActionResponse>(baseUrl, "maintenance", body);
};

/** Save one or both admin toggles. An unknown key is refused with 400
 * `data.field`, so this is typed as a subset of AdminSettings rather than a
 * bare Record. Toggling debug_mode also raises the control settings_update
 * flag server-side, which is how the running control process learns. */
export const saveAdminSettings = (fields: AdminSettingsUpdate, baseUrl = BASE_URL) =>
  post<AdminSettingsUpdate>(baseUrl, "settings", fields);

// ---------------------------------------------------------------------------
// backups
// ---------------------------------------------------------------------------

export const fetchBackups = (baseUrl = BASE_URL) => get<BackupListing>(baseUrl, "backups");

export const createBackup = (kind: BackupKind, baseUrl = BASE_URL) => {
  const body: BackupCreateRequest = { kind };
  return post<BackupCreated>(baseUrl, "backups/create", body);
};

/** Restore from a backup already in the folder.
 *
 * A settings restore restarts the server and is 409-gated; a pellet-database
 * restore does neither, because settings are read once at boot by processes
 * this request cannot reach whereas the pellet database is re-read on demand. */
export const restoreBackup = (kind: BackupKind, file: string, baseUrl = BASE_URL) => {
  const body: BackupRestoreRequest = { kind, file };
  return post<BackupRestored>(baseUrl, "backups/restore", body);
};

/** Upload a .json backup into the folder. Multipart, so it cannot go through
 * post(): setting a JSON Content-Type would strip the boundary. */
export async function uploadBackup(
  kind: BackupKind,
  file: File,
  baseUrl = BASE_URL,
): Promise<AdminResult<BackupCreated>> {
  const form = new FormData();
  form.append("kind", kind);
  form.append("backup", file);
  try {
    const res = await fetch(url(baseUrl, "backups/upload"), { method: "POST", body: form });
    return await unpack<BackupCreated>(res);
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

/** Href for a browser download. Not fetched: letting the anchor carry it keeps
 * the file out of JS memory and gives the user the browser's own save dialog. */
export const backupDownloadUrl = (kind: BackupKind, file: string, baseUrl = BASE_URL) =>
  `${url(baseUrl, "backups/download")}?kind=${encodeURIComponent(kind)}&file=${encodeURIComponent(file)}`;

// ---------------------------------------------------------------------------
// logs
// ---------------------------------------------------------------------------

export const fetchLogs = (baseUrl = BASE_URL) => get<LogsMetadata>(baseUrl, "logs");

/** Delete every .log. Answers with the names that actually went -- Flask ran
 * `rm logs/*.log` inside a bare `except:`, where a failure was indistinguishable
 * from success, so the caller should render this list rather than assume. */
export const deleteLogs = (baseUrl = BASE_URL) =>
  post<LogsDeleted>(baseUrl, "logs/delete", {});

/** Href for the zip of every log. Same anchor reasoning as backupDownloadUrl. */
export const logsDownloadUrl = (baseUrl = BASE_URL) => url(baseUrl, "logs/download");
