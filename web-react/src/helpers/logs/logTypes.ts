/** The outcome of one tail poll. `total` is always the server's current
 * stitched size, which the caller must carry into the next poll so rotation
 * can be detected by a shrinking total. */
export type LogDelta =
  | { kind: "appended"; text: string; nextOffset: number; total: number }
  | { kind: "rotated"; text: string; nextOffset: number; total: number }
  | { kind: "unchanged"; nextOffset: number; total: number };
