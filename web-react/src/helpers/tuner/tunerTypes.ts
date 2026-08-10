/** Browser-normalized outcome for /api/tuner calls.
 *
 * Wire payloads live in helpers/contracts/operations.gen.ts. This wrapper also
 * represents transport failures, which have no server JSON contract.
 */
export interface TunerResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
  /** From a 409's data.mode: the mode that blocked the session. */
  mode?: string;
  /** From a 400's data.field. */
  field?: string;
}
