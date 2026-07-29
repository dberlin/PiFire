// Typed client for the /api/update/* surface.
//
// Every response uses common/app.py's api_response envelope
// {data, result, message}, including the GETs, so one helper covers the
// surface. Modeled on helpers/admin/adminApi.ts: a refusal (e.g. a pull
// rejected because the system is active) resolves to ok:false rather than
// throwing, since callers render the reason instead of catching an escape.

import type {
  UpdateCheck,
  UpdateResult,
  UpdateStarted,
  UpdateState,
  UpdateStatus,
} from "./updateTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

const url = (baseUrl: string, path: string) => `${baseUrl}/api/update/${path}`;

/** Unpack the envelope into an UpdateResult, whatever the status.
 *
 * A body that is not JSON (a proxy's HTML 404, a dropped connection) must not
 * mask the status the caller branches on, so the parse failure falls back to
 * the status rather than propagating. */
async function unpack<T>(res: Response): Promise<UpdateResult<T>> {
  const body = (await res.json().catch(() => ({}))) as {
    result?: string;
    message?: string;
    data?: T | null;
  };
  return {
    ok: res.ok && body.result === "OK",
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    data: (body.data ?? null) as T | null,
  };
}

/** GET a path under /api/update and unpack its envelope. */
async function get<T>(baseUrl: string, path: string): Promise<UpdateResult<T>> {
  try {
    return await unpack<T>(await fetch(url(baseUrl, path)));
  } catch (e) {
    return { ok: false, status: 0, message: (e as Error).message, data: null };
  }
}

/** POST a JSON body to a path under /api/update and unpack its envelope. */
async function post<T>(
  baseUrl: string,
  path: string,
  body: unknown = {},
): Promise<UpdateResult<T>> {
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

export const fetchUpdateState = (baseUrl = BASE_URL) => get<UpdateState>(baseUrl, "state");
export const fetchUpdateCheck = (baseUrl = BASE_URL) => get<UpdateCheck>(baseUrl, "check");
export const fetchUpdateLog = (commits: number, baseUrl = BASE_URL) =>
  get<{ output: string }>(baseUrl, `log?commits=${commits}`);
export const fetchUpdateStatus = (baseUrl = BASE_URL) => get<UpdateStatus>(baseUrl, "status");

export const refreshBranches = (baseUrl = BASE_URL) =>
  post<UpdateStarted>(baseUrl, "branches/refresh");
export const changeBranch = (target: string, baseUrl = BASE_URL) =>
  post<UpdateStarted>(baseUrl, "branch", { target });
export const pullUpdate = (baseUrl = BASE_URL) => post<UpdateStarted>(baseUrl, "pull");
export const upgradeDeps = (baseUrl = BASE_URL) => post<UpdateStarted>(baseUrl, "upgrade");
