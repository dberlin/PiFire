export type FileKind = "cookfiles" | "recipes";

/** The per-page choices the server whitelists (blueprints/api_files/routes.py
 * _PER_PAGE_CHOICES), mirroring the Flask lists' dropdown. Anything else 400s. */
export const PER_PAGE_CHOICES = [5, 10, 25, 50, 100] as const;

/** Placeholder shipped with Flask (static/img/pifire-cf-thumb.png), used by
 * both legacy lists when an archive has no thumbnail. */
export const FALLBACK_THUMB = "/static/img/pifire-cf-thumb.png";
