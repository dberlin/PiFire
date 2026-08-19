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

import type {
  CookFileAsset,
  CookFileChartData,
  CookFileComment,
  CookFileCommentAddRequest,
  CookFileCommentAssetsRequest,
  CookFileCommentDeleteRequest,
  CookFileCommentUpdateRequest,
  CookFileDetail,
  CookFileLabelRequest,
  CookFileRecoverRequest,
  CookFileThumbnailRequest,
  CookFileTitleRequest,
  FileAssetsRequest,
  FileRequest,
} from "@pifire/core/contracts/content";
import { postForm, read, write } from "./apiEnvelope";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export const fetchCookFileDetail = (file: string, baseUrl = BASE_URL) =>
  read<CookFileDetail>("cookfiles", "detail", file, baseUrl);

export const fetchCookFileChart = (file: string, baseUrl = BASE_URL) =>
  read<CookFileChartData>("cookfiles", "chart", file, baseUrl);

/** Download URLs are plain hrefs, not fetches — the browser must own the
 * save dialog. They live under /api/files, which IS proxied in dev
 * (rsbuild.config.ts); HistoryPage's /history/export link is not, and
 * downloads the SPA's index.html in `bun run dev`. */
export const cookFileDownloadUrl = (file: string, baseUrl = BASE_URL) =>
  `${baseUrl}/api/files/cookfiles/download?file=${encodeURIComponent(file)}`;

export const cookFileExportUrl = (file: string, kind: "data" | "events", baseUrl = BASE_URL) =>
  `${baseUrl}/api/files/cookfiles/export?file=${encodeURIComponent(file)}&kind=${kind}`;

export const deleteCookFile = (file: string, baseUrl = BASE_URL) =>
  write<null>("cookfiles", "delete", { file } satisfies FileRequest, baseUrl);

export const setCookFileTitle = (file: string, title: string, baseUrl = BASE_URL) =>
  write<null>("cookfiles", "title", { file, title } satisfies CookFileTitleRequest, baseUrl);

export const renameCookFileLabel = (
  file: string,
  oldLabel: string,
  newLabel: string,
  baseUrl = BASE_URL,
) =>
  write<{ new_label_safe: string }>(
    "cookfiles",
    "label",
    { file, old_label: oldLabel, new_label: newLabel } satisfies CookFileLabelRequest,
    baseUrl,
  );

export const recoverCookFile = (file: string, action: "upgrade" | "repair", baseUrl = BASE_URL) =>
  write<null>("cookfiles", "recover", { file, action } satisfies CookFileRecoverRequest, baseUrl);

export const addCookFileComment = (file: string, text: string, baseUrl = BASE_URL) =>
  write<CookFileComment>(
    "cookfiles",
    "comments",
    { file, action: "add", text } satisfies CookFileCommentAddRequest,
    baseUrl,
  );

export const updateCookFileComment = (file: string, id: string, text: string, baseUrl = BASE_URL) =>
  write<CookFileComment>(
    "cookfiles",
    "comments",
    { file, action: "update", id, text } satisfies CookFileCommentUpdateRequest,
    baseUrl,
  );

export const deleteCookFileComment = (file: string, id: string, baseUrl = BASE_URL) =>
  write<null>(
    "cookfiles",
    "comments",
    { file, action: "delete", id } satisfies CookFileCommentDeleteRequest,
    baseUrl,
  );

export const setCommentAssets = (file: string, id: string, assets: string[], baseUrl = BASE_URL) =>
  write<{ assets: string[] }>(
    "cookfiles",
    "comments/assets",
    { file, id, assets } satisfies CookFileCommentAssetsRequest,
    baseUrl,
  );

export const deleteCookFileAssets = (file: string, assets: string[], baseUrl = BASE_URL) =>
  write<null>("cookfiles", "assets/delete", { file, assets } satisfies FileAssetsRequest, baseUrl);

export const setCookFileThumbnail = (file: string, asset: string, baseUrl = BASE_URL) =>
  write<null>(
    "cookfiles",
    "thumbnail",
    { file, asset } satisfies CookFileThumbnailRequest,
    baseUrl,
  );

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
  const data = await postForm<{ assets: CookFileAsset[] }>(
    "cookfiles",
    "assets/upload",
    form,
    baseUrl,
  );
  return data?.assets ?? [];
}

export async function uploadCookFile(archive: File, baseUrl = BASE_URL): Promise<string> {
  const form = new FormData();
  form.append("file", archive);
  const data = await postForm<{ filename: string }>("cookfiles", "upload", form, baseUrl);
  return data?.filename ?? archive.name;
}

/** Asset URLs. Fullsize at /{id}/{name}, thumbnail at /{id}/thumbs/{name} —
 * the layout read_json_file_data creates (file_mgmt/common.py:71-83) and both
 * Flask templates use. */
export const assetUrl = (parentId: string, name: string, baseUrl = BASE_URL) =>
  `${baseUrl}/static/img/tmp/${parentId}/${name}`;

export const assetThumbUrl = (parentId: string, name: string, baseUrl = BASE_URL) =>
  `${baseUrl}/static/img/tmp/${parentId}/thumbs/${name}`;
