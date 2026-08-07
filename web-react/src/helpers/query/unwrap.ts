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
  /** From a 400's `data.field`: which key the server rejected. */
  field?: string;
  /** From a 409's `data.mode`: the mode that blocked the action. */
  mode?: string;
}

/**
 * Everything a failed envelope says about ITSELF -- `ok` is implied by having
 * failed at all, and `data` is what a failure does not have.
 *
 * Named and spread rather than listed positionally so that a field added to
 * ResultEnvelope reaches the query boundary by construction. The lossy version
 * of this took `(message, status)`, which meant `field`/`mode` were dropped
 * silently by every fetcher the moment adminApi.ts:47-48 started populating
 * them.
 */
export type ApiErrorDetail = Omit<ResultEnvelope<unknown>, "ok" | "data">;

export class ApiError extends Error {
  readonly status: number;
  /** From a 400's `data.field`: which key the server rejected. */
  readonly field?: string;
  /** From a 409's `data.mode`: the mode that blocked the action. */
  readonly mode?: string;
  constructor(detail: ApiErrorDetail) {
    super(detail.message);
    this.name = "ApiError";
    this.status = detail.status;
    this.field = detail.field;
    this.mode = detail.mode;
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
 *
 * The whole failure detail crosses, not just `message`/`status`: adminApi.ts's
 * unpack() lifts `field` and `mode` off `data` for EVERY call including the
 * GETs (adminApi.ts:35-49), and adminErrorText() branches on both, so dropping
 * them here downgraded "it is currently in Smoke mode" to "in another mode" at
 * the one boundary the caller cannot look past.
 */
export async function unwrap<T>(p: Promise<ResultEnvelope<T>>): Promise<T> {
  const r = await p;
  //  `r` on the success-with-null-data path is a broken contract, not a
  //  refusal, so it has no field/mode to carry -- spreading the same shape
  //  either way is what keeps that from needing two constructors.
  if (!r.ok || r.data === null)
    throw new ApiError({ status: r.status, message: r.message, field: r.field, mode: r.mode });
  return r.data;
}
