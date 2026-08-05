# Final adaptation diagnostics report

- Incumbents now call `track()` only: runtime history/state advances without ARX RLS, DMC coefficient, or state-space matrix learning. Challengers alone call `observe()`; promotion remains the only atomic role swap.
- Every permitted online frame records incumbent and challenger pre-assimilation absolute innovations. Five-minute decisions persist distinct prediction and braking/coast score pairs, sample counts, snapshots, reasons, and promotion state.
- Structural work is timed at the actual challenger observation. Refresh attempts or accepted refreshes populate `raw_refresh_ms`; policy evaluation time is not reported as refresh time. State-space refresh cadence is 300 seconds.
- ARX forecast envelope limiting applies only to the local affine map; it never mutates region theta, covariance, or RLS factors.
- Real MAK evidence uses chronological fit `[0,16)`, validation `[16,35)`, and untouched test `[35,61)`. Compact state-space candidates are selected only with validation evidence. Test scores are 60/300 seconds when fully supported; 900/1800/3600-second entries are null.
- Simulator model evidence now records 60/300/900/1800/3600-second supported-horizon origin residual vectors and RMSE, maximum absolute error, bias, p90 absolute error, coast/braking error, steady-gain error, and delay error for each arm/domain/mode/initialization row. MPC 600/800/1000-second validation evidence remains separate.

Verification:

- `./.venv/bin/python -m pytest tests/unit/mpc/linear_mpc_bakeoff -q` — 148 passed.
- `./.venv/bin/python -m ruff check docs/superpowers/experiments/linear_mpc_bakeoff tests/unit/mpc/linear_mpc_bakeoff` — OK.
- `./.venv/bin/python -m docs.superpowers.experiments.linear_mpc_bakeoff --quick` regenerated `docs/superpowers/experiments/_linear_mpc_bakeoff_quick.json` with zero structured failures.

The short quick matrix is 140 seconds and does not reach a five-minute promotion evaluation; its absence of promotions is due to duration, not equal score placeholders. The focused promotion regression verifies two strictly better consecutive score windows atomically promote a challenger and a worse candidate is rejected.
