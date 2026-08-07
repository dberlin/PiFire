/**
 * The envelope the resolve-don't-throw API modules already share:
 * helpers/admin/adminTypes.ts AdminResult, helpers/metrics/metricsTypes.ts
 * MetricsResult, helpers/update/updateTypes.ts UpdateResult.
 */
export interface ResultEnvelope<T> {
  ok: boolean;
  /** HTTP status, or 0 when the request never reached a server. */
  status: number;
  message: string;
  data: T | null;
}

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Bridge a resolve-don't-throw READ into a react-query fetcher.
 *
 * useQuery decides success or failure by whether the promise REJECTS. An
 * envelope carrying ok:false resolves, so without this a failed read would
 * land in `data` and render as a success holding null.
 *
 * The API modules keep their envelopes: write paths branch on `.ok` and
 * `.message` directly (helpers/admin/adminTypes.ts documents `message` as a
 * machine token the Python tests assert on), and only the query boundary
 * converts.
 *
 * `data === null` under `ok: true` is a broken server contract for a read,
 * which is all this is used for. Rejecting on it is what keeps
 * `useQuery().data` non-nullable for every caller.
 */
export async function unwrap<T>(p: Promise<ResultEnvelope<T>>): Promise<T> {
  const r = await p;
  if (!r.ok || r.data === null) throw new ApiError(r.message, r.status);
  return r.data;
}
