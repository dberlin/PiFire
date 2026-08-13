# Controller Model Store - Backlog

Actionable work for the per-controller learned-model snapshot store and its promotion
boundary. Completed work and obsolete model descriptions are removed rather than
retained here; plans and repository history carry that record.

**Last reconciled against live code: 2026-08-13.**

## Make promotion bounds gauge-invariant

The current one-lump grey-box model is unchanged when
`(C_c, h_amb, K_Q, sigma)` are multiplied by one common factor: the measured
trajectory depends on their ratios, not their absolute scale. A cook therefore
identifies three degrees of freedom across those four parameters.

The fitter now controls this ambiguity: `controller/update_mpc.py` holds `h_amb` and
`sigma` and fits only `K_Q`, `C_c`, and `theta`; online fitting starts from the
incumbent and replaces only the fitted fields. That prevents the earlier runaway-fit
failure, but it does not make the durable boundary invariant.
`controller/model_promotion.py::PROMOTION_BOUNDS` and
`controller/mpc_snapshot.py` still accept or reject individual absolute values. Two
parameterizations with identical predictions can therefore receive different
promotion or restore verdicts solely because their common scale differs.

Replace raw bounds for the gauge-dependent fields with bounds on identifiable ratios,
or normalize every candidate to one documented canonical gauge before applying
bounds. Keep direct bounds for independently meaningful quantities such as `T_amb`,
`theta`, and `n_delay`. Update promotion, snapshot restore, API validation, and
calibration contracts together; stored models that remain readable across the
cutover need an explicit versioned normalization path.

The invariant is pinned by `tests/unit/mpc/test_mpc_calibration.py`; the executable
probe remains `docs/superpowers/experiments/sigma_identifiability.py`.

## Raise the snapshot size cap to a runaway guard

`common/controller_model_state.py::MAX_SNAPSHOT_BYTES` remains 65,536, and both the
shared store and grey runtime reject larger UTF-8 JSON snapshots. SQLite stores the
value in `TEXT`; there is no corresponding 64-KiB storage boundary.

Current v4 grey snapshots are well below the cap, so this is not urgent. The issue is
policy: a shared persistence boundary should stop a runaway producer, not constrain a
legitimate future controller representation. Raise the limit to the megabyte range,
keep JSON readable, and preserve strict rejection tests at the new boundary. If a
real snapshot approaches that guard, investigate the producer or add an explicit
versioned encoding rather than silently tightening the cap.
