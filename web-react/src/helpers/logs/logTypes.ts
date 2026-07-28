/** One rotation family: `events.log` plus its `.1`/`.2`/`.3` backups. */
export interface LogFamily {
  stem: string;
  /** Member filenames, OLDEST first -- the order the server stitches them. */
  members: string[];
  /** Total stitched size, which is also the end offset of the byte stream. */
  bytes: number;
}

/** The outcome of one tail poll. `total` is always the server's current
 * stitched size, which the caller must carry into the next poll so rotation
 * can be detected by a shrinking total. */
export type LogDelta =
  | { kind: "appended"; text: string; nextOffset: number; total: number }
  | { kind: "rotated"; text: string; nextOffset: number; total: number }
  | { kind: "unchanged"; nextOffset: number; total: number };
