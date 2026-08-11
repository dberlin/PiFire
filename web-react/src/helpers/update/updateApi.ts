// Typed client for the /api/update/* surface.
//
// Every response uses common/app.py's api_response envelope
// {data, result, message}, including the GETs, so one helper covers the
// surface. Modeled on helpers/admin/adminApi.ts: a refusal (e.g. a pull
// rejected because the system is active) resolves to ok:false rather than
// throwing, since callers render the reason instead of catching an escape.

import type { ApiEnvelope } from "../contracts/core.gen";
import type {
  BuildLog,
  UpdateBranchRequest,
  UpdateCheck,
  UpdateLog,
  UpdateStarted,
  UpdateState,
  UpdateStatus,
} from "../contracts/operations.gen";
import type { UpdateResult } from "./updateTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

const url = (baseUrl: string, path: string) => `${baseUrl}/api/update/${path}`;

/** Unpack the envelope into an UpdateResult, whatever the status.
 *
 * A body that is not JSON (a proxy's HTML 404, a dropped connection) must not
 * mask the status the caller branches on, so the parse failure falls back to
 * the status rather than propagating. */
async function unpack<T>(res: Response): Promise<UpdateResult<T>> {
  const body = (await res.json().catch(() => ({}))) as Partial<ApiEnvelope>;
  return {
    ok: res.ok && body.result === "OK",
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    data: (body.data as T | null | undefined) ?? null,
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
  get<UpdateLog>(baseUrl, `log?commits=${commits}`);
export const fetchUpdateStatus = (baseUrl = BASE_URL) => get<UpdateStatus>(baseUrl, "status");

export const refreshBranches = (baseUrl = BASE_URL) =>
  post<UpdateStarted>(baseUrl, "branches/refresh");
export const changeBranch = (target: string, baseUrl = BASE_URL) => {
  const body: UpdateBranchRequest = { target };
  return post<UpdateStarted>(baseUrl, "branch", body);
};
export const pullUpdate = (baseUrl = BASE_URL) => post<UpdateStarted>(baseUrl, "pull");
export const upgradeDeps = (baseUrl = BASE_URL) => post<UpdateStarted>(baseUrl, "upgrade");
export const rebuildWebUi = (baseUrl = BASE_URL) => post<UpdateStarted>(baseUrl, "rebuild-web-ui");
export const rebuildAcados = (baseUrl = BASE_URL) => post<UpdateStarted>(baseUrl, "rebuild-acados");

/** One incremental read of the last web UI build's output.
 *
 * Rejects rather than resolving to ok:false, unlike the rest of this module:
 * its caller is StreamingLogPanel, which treats a failed read as a tick to skip
 * and retries on the next one. A refusal object would have to be unwrapped into
 * exactly that. */
export async function fetchBuildLog(offset: number, baseUrl = BASE_URL): Promise<BuildLog> {
  const r = await get<BuildLog>(baseUrl, `buildlog?offset=${offset}`);
  if (!r.ok || !r.data) throw new Error(r.message);
  return r.data;
}

/** The same transcript as a file. A plain URL, for an <a download> -- there is
 *  nothing to unwrap and nothing to hold in state. */
export const buildLogDownloadUrl = (baseUrl = BASE_URL) => url(baseUrl, "buildlog/download");
