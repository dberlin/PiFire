/** Browser-normalized outcome for /api/update calls.
 *
 * Wire payloads live in helpers/contracts/operations.gen.ts. This wrapper also
 * represents transport failures, which have no server JSON contract.
 */
export interface UpdateResult<T> {
  ok: boolean;
  status: number;
  message: string;
  data: T | null;
}
