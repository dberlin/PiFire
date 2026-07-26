/** One row of a managed-folder listing (GET /api/files/{cookfiles,recipes}).
 *
 * `filename` is a BARE name, never a path — the server refuses anything that
 * resolves outside the configured folder (common/file_browser.py
 * resolve_managed_file), and it is the identity used by every other endpoint
 * and by the /cookfiles/:filename route.
 *
 * `thumbnail` is a RELATIVE "{parentId}/{name}" produced by
 * file_mgmt/media.py's unpack_thumb, to be prefixed with /static/img/tmp/ —
 * or "" when the archive has no thumbnail or could not be opened. Use
 * thumbnailUrl() rather than concatenating at each call site. */
export interface FileListItem {
  filename: string;
  title: string;
  thumbnail: string;
}

export interface FileListing {
  items: FileListItem[];
  page: number;
  last_page: number;
  per_page: number;
  reverse: boolean;
  total: number;
}

export type FileKind = "cookfiles" | "recipes";

/** The per-page choices the server whitelists (blueprints/api_files/routes.py
 * _PER_PAGE_CHOICES), mirroring the Flask lists' dropdown. Anything else 400s. */
export const PER_PAGE_CHOICES = [5, 10, 25, 50, 100] as const;

/** Placeholder shipped with Flask (static/img/pifire-cf-thumb.png), used by
 * both legacy lists when an archive has no thumbnail. */
export const FALLBACK_THUMB = "/static/img/pifire-cf-thumb.png";
