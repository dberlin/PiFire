import { FALLBACK_THUMB, type FileKind, type FileListing } from "./fileTypes";

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

/** Resolve a listing row's `thumbnail` to a URL, falling back to the shipped
 * placeholder. Both Flask lists do exactly this
 * (cookfile/_cookfile_list.html:22-26, recipes/_recipefile_list.html:30-34). */
export function thumbnailUrl(thumbnail: string, baseUrl: string = BASE_URL): string {
  return thumbnail ? `${baseUrl}/static/img/tmp/${thumbnail}` : `${baseUrl}${FALLBACK_THUMB}`;
}

export interface ListingOptions {
  page?: number;
  perPage?: number;
  reverse?: boolean;
  baseUrl?: string;
}

/**
 * GET /api/files/{kind} — the JSON listing that did not exist before this
 * work. The legacy equivalents return Jinja fragments
 * (blueprints/cookfile/routes.py:267, blueprints/recipes/routes.py:123), which
 * a typed client cannot consume.
 *
 * Defaults deliberately match the Flask pages' first call so the React list
 * opens on the same rows: cookfiles `gotoCFPage(1, true, 10)`
 * (history/js/history.js:337). The recipes page uses reverse=false
 * (recipes.js:84) — its caller passes that explicitly.
 */
export async function fetchFileListing(
  kind: FileKind,
  { page = 1, perPage = 10, reverse = true, baseUrl = BASE_URL }: ListingOptions = {},
): Promise<FileListing> {
  const qs = new URLSearchParams({
    page: String(page),
    per_page: String(perPage),
    reverse: String(reverse),
  });
  const res = await fetch(`${baseUrl}/api/files/${kind}?${qs}`);
  if (!res.ok) throw new Error(`GET /api/files/${kind} failed: HTTP ${res.status}`);
  return (await res.json()) as FileListing;
}
