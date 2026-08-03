# Backlog — controller model store

Deferred work on `common/controller_model_state.py`, the per-controller learned-model
snapshot store. One entry per item, newest first. An entry stays until the work lands
or is explicitly dropped; do not delete an entry to record that it shipped, mark it.

## Open

### `PROMOTION_BOUNDS` bounds quantities the data cannot determine

`controller/model_promotion.py`'s `PROMOTION_BOUNDS` places a range on each thermal
parameter individually. Five of those parameters are not individually observable.

The grey-box model is invariant under scaling `(C_f, C_c, h_fc, h_amb, K_Q, sigma)` by
one common factor: both state equations are homogeneous in them, so the trajectory of
the one measured state is unchanged — bit-identical at exact-binary factors. Six
parameters, five identifiable degrees of freedom. A cook determines the ratios; the
scale is fixed only by holding one parameter, which is why `update_mpc.py`'s `_FREE`
holds `sigma` (and `C_f`).

Measured with the shipped parameters: the scale factor can range over
**[1.111e-02, 7.143]** — a **643x** span — with every point inside all six bounds,
identical dynamics, and identical effective tau. `sigma`'s `1e-8` ceiling is what binds
at the top. So the per-parameter bounds are considerably weaker than they look: a model
that is correct in every observable respect can be refused for having drifted along a
direction no measurement can see, and two models the bounds treat very differently may
be the same model.

This is not currently a live defect. The tau guard is the part of the policy that
governs braking distance, and it reads `C_c/(h_amb + 4*sigma*(T+273.15)**3)` and
`C_c/h_amb`, both of which are gauge-invariant — unaffected, and the reason nothing has
gone wrong. The exposure is that the bounds check is doing less than its comment claims,
and that a future check reading `C_c` or `h_amb` *alone* rather than as a ratio would be
reading a number the data never pinned.

**A refit-from-previous-output loop runs away along this direction.** Feeding each fit's
output back as the next fit's `init`, against the same real cook, inflates the
parameters monotonically while the error *improves* slightly:

| iteration | K_Q | C_c | h_amb | h_fc | RMSE |
|---|---|---|---|---|---|
| 0 | 27.2 | 9,609 | 2.25 | 0.3855 | 2.42326 |
| 2 | 1,041 | 364,756 | 103.9 | 0.3834 | 2.40358 |
| 4 | 5,159 | 1,807,178 | 516.8 | 0.3833 | 2.40316 |
| 8 | 9,116 | 3,192,954 | 913.5 | 0.3833 | 2.40311 |

That is ~335x in eight iterations, with `h_fc` pinned and RMSE flat to four decimals —
the signature of motion along the unobservable direction, not of learning. `C_c` leaves
`PROMOTION_BOUNDS` at iteration 4, so the bounds do eventually stop it, but only after
the model has drifted two orders of magnitude, and the tau guard never objects because
effective tau is invariant the whole way. Any online identifier that refits from its own
previous output needs a gauge-fixing step (renormalise one parameter, or fit the ratios)
rather than relying on the bounds to catch the drift late.

Note for whoever picks this up: an earlier version of this entry's evidence claimed the
solver's ill-conditioning was a *separate* phenomenon from the gauge, based on a
single-point Jacobian SVD. That was wrong — see the task report's Addendum 4. The
condition number depends entirely on the coordinate convention (4.3e4, 4.2e5 or 5.8e6 at
the same point, depending on what is scaled), so it is not a quantity to reason from
without stating the convention. The runaway table above is the convention-free evidence.

Candidate fix, not a commitment: reparameterise the promotion check onto the five
identifiable ratios and bound those, so every bounded quantity is one a cook can
actually determine. That is a larger change than it sounds — it touches what a stored
snapshot means — so it wants its own design pass rather than being folded into a
bounds-tightening commit. A cheaper interim step is to say in `PROMOTION_BOUNDS`'s own
comment which entries are gauge-dependent, so nobody tightens one believing it
constrains something it does not.

Evidence: `docs/superpowers/experiments/sigma_identifiability.py` and its committed
output; `tests/unit/mpc/test_mpc_calibration.py::test_the_model_is_invariant_under_a_
common_scaling_of_its_parameters` pins the invariance itself.

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
