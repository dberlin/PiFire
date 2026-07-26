// Typed client for the /api/files/cookfiles/* surface.
//
// Every call names a cook file by its BARE FILENAME. The server resolves it
// through common/file_browser.py's resolve_managed_file, which realpath-
// contains it to the configured history folder. Nothing here ever sends a
// path, and the endpoints refuse one — unlike the legacy /cookfile blueprint,
// which takes a filesystem path from the client and uses it unvalidated.
//
// Write responses use common/app.py's api_response envelope
// {data, result, message} with result === "OK" on success, the same contract
// helpers/pellets/pelletsApi.ts speaks. Read responses (detail, chart) are
// bare payloads with an HTTP status, matching /api/history/chart.

import type { HistoryAnnotation, HistoryDataset, HistoryProbeMapper } from "../history/historyApi";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** A cook file's metadata.json, plus the two epoch fields the endpoint adds.
 *
 * `starttime`/`endtime` are HH:MM:SS display strings (the Flask template can
 * only ever show a time of day — common/app.py:289-290 overwrites the epochs
 * in place). `*_epoch` are the raw millisecond epochs, kept so a client can
 * render a date. */
export interface CookFileMetadata {
  title: string;
  units: string;
  thumbnail: string;
  id: string;
  version: string;
  starttime: string;
  endtime: string;
  starttime_epoch: number;
  endtime_epoch: number;
}

/** One row of events.json. The `_c` fields were computed at cook time by
 * process_metrics (common/common.py) and stored — they are not recomputed
 * on read. */
export interface CookFileEvent {
  id: number;
  mode: string;
  starttime_c: string;
  endtime_c: string;
  augerontime_c: string;
  estusage_m: string;
  estusage_i: string;
  pellet_level_start: number;
  pellet_level_end: number;
  timeinmode: string;
}

export interface CookFileTotals {
  augerontime: string;
  estusage_m: string;
  estusage_i: string;
  cooktime: string;
  pellet_level_start: number;
  pellet_level_end: number;
}

export interface CookFileComment {
  id: string;
  text: string;
  date: string;
  time: string;
  edited: string;
  assets: string[];
}

export interface CookFileAsset {
  id: string;
  filename: string;
  type: string;
}

/** Probe key -> display label, per series role. The rename table edits
 * `probes` only, which is all the Flask table offers
 * (cookfile/index.html:162). */
export interface CookFileLabels {
  probes: Record<string, string>;
  targets: Record<string, string>;
  primarysp: Record<string, string>;
}

export interface CookFileDetail {
  filename: string;
  metadata: CookFileMetadata;
  graph_labels: CookFileLabels;
  events: CookFileEvent[];
  /** Empty when the cook has fewer than two events: prepare_event_totals
   * indexes events[-2] unconditionally, so the endpoint reports nothing
   * rather than 500ing on a truncated cook. */
  event_totals: CookFileTotals | Record<string, never>;
  comments: CookFileComment[];
  assets: CookFileAsset[];
}

/** GET /api/files/cookfiles/chart. Structurally a subset of HistoryChartData:
 * no `graph_labels`, no `minutes`. `time_labels` is `unknown[]` on purpose —
 * live history always writes numeric epochs, but an upgraded or hand-built
 * cook file can carry "12:00:00" strings, and a string divided by 1000 is NaN,
 * which uPlot renders as nothing at all. cookfileAdapter guards it. */
export interface CookFileChartData {
  time_labels: unknown[];
  chart_data: HistoryDataset[];
  probe_mapper: HistoryProbeMapper;
  annotations: Record<string, HistoryAnnotation>;
}

/** The 422 body: the file opened but would not load. `errortype` drives the
 * repair/convert prompt, exactly as cookfile/cferror.html branches on it. */
export interface CookFileError {
  status: number;
  message: string;
  errortype: "version" | "asset" | "other" | null;
}

export class CookFileRequestError extends Error {
  readonly detail: CookFileError;
  constructor(detail: CookFileError) {
    super(detail.message);
    this.name = "CookFileRequestError";
    this.detail = detail;
  }
}

async function toError(res: Response): Promise<CookFileError> {
  //  A 404 from a proxy (or an HTML error page) is not JSON. Never let a parse
  //  failure mask the status the caller has to branch on.
  const body = (await res.json().catch(() => ({}))) as {
    message?: string;
    data?: { errortype?: CookFileError["errortype"] };
  };
  return {
    status: res.status,
    message: body.message ?? `HTTP ${res.status}`,
    errortype: body.data?.errortype ?? null,
  };
}

async function read<T>(path: string, file: string, baseUrl: string): Promise<T> {
  const res = await fetch(
    `${baseUrl}/api/files/cookfiles/${path}?file=${encodeURIComponent(file)}`,
  );
  if (res.ok) return (await res.json()) as T;
  throw new CookFileRequestError(await toError(res));
}

/** POST helper for every JSON write endpoint. */
async function write<T>(path: string, body: unknown, baseUrl: string): Promise<T> {
  const res = await fetch(`${baseUrl}/api/files/cookfiles/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new CookFileRequestError(await toError(res));
  const envelope = (await res.json()) as { result?: string; message?: string; data?: T };
  if (envelope.result !== "OK") {
    throw new CookFileRequestError({
      status: res.status,
      message: envelope.message ?? "rejected",
      errortype: null,
    });
  }
  return envelope.data as T;
}

/** Shared tail for the two multipart uploads, which cannot go through write():
 * the body is FormData, so no JSON Content-Type may be set. */
async function postForm<T>(path: string, form: FormData, baseUrl: string): Promise<T> {
  const res = await fetch(`${baseUrl}/api/files/cookfiles/${path}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new CookFileRequestError(await toError(res));
  const envelope = (await res.json()) as { result?: string; message?: string; data?: T };
  if (envelope.result !== "OK") {
    throw new CookFileRequestError({
      status: res.status,
      message: envelope.message ?? "rejected",
      errortype: null,
    });
  }
  return envelope.data as T;
}

export const fetchCookFileDetail = (file: string, baseUrl = BASE_URL) =>
  read<CookFileDetail>("detail", file, baseUrl);

export const fetchCookFileChart = (file: string, baseUrl = BASE_URL) =>
  read<CookFileChartData>("chart", file, baseUrl);

/** Download URLs are plain hrefs, not fetches — the browser must own the
 * save dialog. They live under /api/files, which IS proxied in dev
 * (rsbuild.config.ts); HistoryPage's /history/export link is not, and
 * downloads the SPA's index.html in `bun run dev`. */
export const cookFileDownloadUrl = (file: string, baseUrl = BASE_URL) =>
  `${baseUrl}/api/files/cookfiles/download?file=${encodeURIComponent(file)}`;

export const cookFileExportUrl = (file: string, kind: "data" | "events", baseUrl = BASE_URL) =>
  `${baseUrl}/api/files/cookfiles/export?file=${encodeURIComponent(file)}&kind=${kind}`;

export const deleteCookFile = (file: string, baseUrl = BASE_URL) =>
  write<null>("delete", { file }, baseUrl);

export const setCookFileTitle = (file: string, title: string, baseUrl = BASE_URL) =>
  write<null>("title", { file, title }, baseUrl);

export const renameCookFileLabel = (
  file: string,
  oldLabel: string,
  newLabel: string,
  baseUrl = BASE_URL,
) =>
  write<{ new_label_safe: string }>(
    "label",
    { file, old_label: oldLabel, new_label: newLabel },
    baseUrl,
  );

export const recoverCookFile = (file: string, action: "upgrade" | "repair", baseUrl = BASE_URL) =>
  write<null>("recover", { file, action }, baseUrl);

export const addCookFileComment = (file: string, text: string, baseUrl = BASE_URL) =>
  write<CookFileComment>("comments", { file, action: "add", text }, baseUrl);

export const updateCookFileComment = (file: string, id: string, text: string, baseUrl = BASE_URL) =>
  write<CookFileComment>("comments", { file, action: "update", id, text }, baseUrl);

export const deleteCookFileComment = (file: string, id: string, baseUrl = BASE_URL) =>
  write<null>("comments", { file, action: "delete", id }, baseUrl);

export const setCommentAssets = (file: string, id: string, assets: string[], baseUrl = BASE_URL) =>
  write<{ assets: string[] }>("comments/assets", { file, id, assets }, baseUrl);

export const deleteCookFileAssets = (file: string, assets: string[], baseUrl = BASE_URL) =>
  write<null>("assets/delete", { file, assets }, baseUrl);

export const setCookFileThumbnail = (file: string, asset: string, baseUrl = BASE_URL) =>
  write<null>("thumbnail", { file, asset }, baseUrl);

/** Multipart: the archive name rides as a form field and each image as a
 * repeated `assets` part, matching request.files.getlist("assets"). */
export async function uploadCookFileAssets(
  file: string,
  images: File[],
  baseUrl = BASE_URL,
): Promise<CookFileAsset[]> {
  const form = new FormData();
  form.append("file", file);
  for (const image of images) form.append("assets", image);
  const data = await postForm<{ assets: CookFileAsset[] }>("assets/upload", form, baseUrl);
  return data?.assets ?? [];
}

export async function uploadCookFile(archive: File, baseUrl = BASE_URL): Promise<string> {
  const form = new FormData();
  form.append("file", archive);
  const data = await postForm<{ filename: string }>("upload", form, baseUrl);
  return data?.filename ?? archive.name;
}

/** Asset URLs. Fullsize at /{id}/{name}, thumbnail at /{id}/thumbs/{name} —
 * the layout read_json_file_data creates (file_mgmt/common.py:71-83) and both
 * Flask templates use. */
export const assetUrl = (parentId: string, name: string, baseUrl = BASE_URL) =>
  `${baseUrl}/static/img/tmp/${parentId}/${name}`;

export const assetThumbUrl = (parentId: string, name: string, baseUrl = BASE_URL) =>
  `${baseUrl}/static/img/tmp/${parentId}/thumbs/${name}`;
