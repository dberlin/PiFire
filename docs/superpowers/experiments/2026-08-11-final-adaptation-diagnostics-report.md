# Final adaptation diagnostics report

- Incumbents now call `track()` only: runtime history/state advances without ARX RLS, DMC coefficient, or state-space matrix learning. Challengers alone call `observe()`; promotion remains the only atomic role swap.
- Every permitted online frame records incumbent and challenger pre-assimilation absolute innovations. Five-minute decisions persist distinct prediction and braking/coast score pairs, sample counts, snapshots, reasons, and promotion state.
- Structural work is timed at the actual challenger observation. Refresh attempts or accepted refreshes populate `raw_refresh_ms`; policy evaluation time is not reported as refresh time. State-space refresh cadence is 300 seconds.
- ARX forecast envelope limiting applies only to the local affine map; it never mutates region theta, covariance, or RLS factors.
- Real MAK evidence uses chronological fit `[0,16)`, validation `[16,35)`, and untouched test `[35,61)`. Compact state-space candidates are selected only with validation evidence. Test scores are 60/300 seconds when fully supported; 900/1800/3600-second entries are null.
- Simulator model evidence now records 60/300/900/1800/3600-second supported-horizon origin residual vectors and RMSE, maximum absolute error, bias, p90 absolute error, coast/braking error, steady-gain error, and delay error for each arm/domain/mode/initialization row. MPC 600/800/1000-second validation evidence remains separate.
- Follow-up: evaluation buffers are interval-local and are cleared before every five-minute decision. Braking evidence is absent (`null`) when no frame in that interval carries braking/coast status; it is never synthesized from general prediction error.
- Follow-up: every score sample carries the manager role generation. Evaluations reject fewer than two fresh samples and discard data from prior promotions, preventing either overlapping frames or stale arm labels from contributing to a second win.

Verification:

- `./.venv/bin/python -m pytest tests/unit/mpc/linear_mpc_bakeoff -q` — 148 passed.
- `./.venv/bin/python -m ruff check docs/superpowers/experiments/linear_mpc_bakeoff tests/unit/mpc/linear_mpc_bakeoff` — OK.
- `./.venv/bin/python -m docs.superpowers.experiments.linear_mpc_bakeoff --quick` regenerated `docs/superpowers/experiments/_linear_mpc_bakeoff_quick.json` with zero structured failures.

The short quick matrix is 140 seconds and does not reach a five-minute promotion evaluation; its absence of promotions is due to duration, not equal score placeholders. The focused promotion regression verifies two strictly better consecutive score windows atomically promote a challenger and a worse candidate is rejected.


## Final diagnostic completion update

- Both deterministic simulator calibration programs use three 4,800-second PRBS plateaus followed by a 1,200-second coast. Their persisted chronological fit, validation, and untouched test bounds are independent of scenario duration; the MAK simulator retains its fixed legacy fit/validation endpoints while its longer untouched suffix supplies every required horizon.
- Every simulator row now persists raw forecast origins at 60, 300, 900, 1,800, and 3,600 seconds, per-timestamp coast/braking masks, and residual vectors filtered exclusively by those masks. Each populated horizon includes origin and masked-sample counts plus RMSE, maximum absolute error, bias, p90 absolute error, gain error, delay error, and coast/braking error.
- Scheduled ARX, Laguerre DMC, and innovation state-space snapshots all expose the same fitted `steady_gain`, `delay_steps`, and `delay_seconds` contract. Missing gain or delay diagnostics now raise evidence errors rather than silently substituting zero.
- Arm evidence aggregates simulator diagnostics by arm, mode, initialization, and simulator domain. The recommendation uses 60-minute simulator prediction when available; gain, delay, and coast/braking diagnostics participate in validity and Pareto dominance.
- Workstation timing distributions are retained as raw evidence but are marked `not_measured`: concurrent workloads contaminated this run. Runtime cannot disqualify, select, or reject an arm until an isolated rerun; the artifact records that provenance and required follow-up.
- A contaminated timing distribution is omitted from selection and Pareto dimensions unless every compared arm has isolated measured timing. Mixed simulator-diagnostic availability cannot create a zero-error advantage: an unavailable arm is explicitly deferred when other arms have diagnostics, while diagnostic dimensions are omitted only when all contenders lack them.
- Promotion evidence now records each score window's source frame IDs and role generation. A controlled online runner integration drives three 300-second score windows, forces the second valid window to promote, and proves the post-promotion window has generation 1, fresh IDs above 600 seconds, and no overlap with either pre-promotion window.

Verification:

- `python -m pytest tests/unit/mpc/linear_mpc_bakeoff/test_artifact.py tests/unit/mpc/linear_mpc_bakeoff/test_arx.py tests/unit/mpc/linear_mpc_bakeoff/test_dmc.py tests/unit/mpc/linear_mpc_bakeoff/test_state_space.py tests/unit/mpc/linear_mpc_bakeoff/test_final_runner_evidence.py -q` — 62 passed in 143.69 seconds.
- `python -m ruff check docs/superpowers/experiments/linear_mpc_bakeoff tests/unit/mpc/linear_mpc_bakeoff` — OK.
- `python -m pytest tests/unit/mpc/linear_mpc_bakeoff/test_artifact.py tests/unit/mpc/linear_mpc_bakeoff/test_adaptation.py tests/unit/mpc/linear_mpc_bakeoff/test_final_runner_evidence.py -q` — 50 passed in 213.21 seconds; the same command's Ruff check was OK.
- `python -m docs.superpowers.experiments.linear_mpc_bakeoff --quick` regenerated the quick artifact. Programmatic inspection found 144 simulator rows, 720 populated simulator horizon cells, and 79,272 raw origins; all three arms retain real-MAK 60/300 only and null 900/1800/3600 diagnostics.
- The complete bakeoff command ran for 900 seconds and was terminated by its deadline after approximately 94% progress without emitting a failure. It is not claimed as a full-suite pass.