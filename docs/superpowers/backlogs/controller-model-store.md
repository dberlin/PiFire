# Backlog — controller model store

Deferred work on `common/controller_model_state.py`, the per-controller learned-model
snapshot store. One entry per item, newest first. An entry stays until the work lands
or is explicitly dropped; do not delete an entry to record that it shipped, mark it.

## Open

### The snapshot size cap is bounded far tighter than anything requires

`MAX_SNAPSHOT_BYTES` is 65536. Nothing about the storage layer motivates a bound that
low: the snapshot lands in the `kv` table's `value TEXT` column, and SQLite imposes no
practical limit at that scale. The 64 KiB figure was chosen against a projected
25-candidate RLS bank (~7 KB of plain JSON) and so encodes an assumption about what a
controller may learn, which is the wrong thing for a storage bound to express.

Raise it to the megabyte range. A cap should exist only to stop a runaway producer from
writing without limit — it should not be a number any legitimate model has to be
designed around. If a snapshot ever approaches even a megabyte, compression is the
answer, not a tighter bound; plain JSON is worth keeping at present sizes because a
model that drives a fire should stay readable in the datastore.

Nothing currently gets close to the bound, so this is not urgent — it matters when the
online identifier's persisted state grows past a single bank.

Related: `docs/superpowers/plans/2026-08-02-mpc-online-identification.md` Task A3, which
raised the bound from 8192 to 65536 and is the change this entry supersedes.
