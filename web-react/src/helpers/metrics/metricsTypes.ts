import type { MetricsPayload } from "@pifire/core/contracts/content";
/**
 * How a read finished. Resolves rather than throws, matching
 * helpers/admin/adminApi.ts: the page renders the failure in place and offers a
 * retry, so an exception would only have to be caught and turned back into this.
 */
export interface MetricsResult {
  ok: boolean;
  /** HTTP status, or 0 when the request never reached a server. */
  status: number;
  message: string;
  data: MetricsPayload | null;
}
