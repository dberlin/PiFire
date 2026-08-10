/** Generic browser-normalized outcome for /api/admin calls.
 *
 * Wire payloads live in helpers/contracts/operations.gen.ts. This wrapper also
 * represents transport failures, which have no server JSON contract.
 */
export interface AdminResult<T = null> {
  ok: boolean;
  /** HTTP status, or 0 when the request never reached a server. */
  status: number;
  message: string;
  data: T | null;
  /** From a 400's `data.field`: which key the server rejected. */
  field?: string;
  /** From the 409's `data.mode`: the mode that blocked the action. */
  mode?: string;
}
