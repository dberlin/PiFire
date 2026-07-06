# tools/

## `regenerate_mpc_net.py` — MPC net-policy regeneration

### What this is

PiFire's Model Predictive Controller (`controller/mpc_*.py`) can use a learned
neural-net policy as a fast approximation of the full MPC optimization. The
policy is trained offline against a grey-box thermal model that is
parameterized by the grill's calibration (see `update_mpc.py`). The trained
weights, the input normalization stats, and a snapshot of the calibration used
to train them are all embedded in the exported artifact:

- `controller/mpc_policy_net.npz` — fan-off policy.
- `controller/mpc_policy_net_fan.npz` — fan-on policy (used when the grill has
  a controllable fan).

At load time, the controller checks the artifact's embedded calibration
against the grill's *current* calibration (`matches_config`). If they no
longer match, the net is considered stale and the controller silently falls
back to the slower NLP-based MPC solver instead of using it.

### When to regenerate

Regenerate the net-policy artifact(s) whenever the grill's calibration
changes — most commonly after running `update_mpc.py` to recalibrate. A
recalibration changes the physical parameters the net was trained against, so
`matches_config` will fail on the old artifact and the controller quietly
degrades to the NLP fallback until a fresh net is exported.

### One-command usage

```bash
python tools/regenerate_mpc_net.py --mode both
```

This chains, for each requested fan mode, the two committed pipeline stages:

1. `docs/superpowers/experiments/sample_mpc.py` — runs a DAgger sampling pass
   against the live grill/simulation to build a training dataset.
2. `docs/superpowers/experiments/export_span_net.py` — trains a small torch
   net on that dataset and exports the pure-numpy runtime artifact consumed by
   `controller/mpc_net.py`.

Both stages are pre-existing, committed scripts; this wrapper only
orchestrates them so there is one discoverable entry point instead of having
to know the pipeline order among the ~25 other spike scripts in
`docs/superpowers/experiments/`.

### Options

```
python tools/regenerate_mpc_net.py [--mode {fan-off,fan-on,both}]
                                    [--episodes N] [--workers W]
                                    [--skip-sample] [--dry-run]
```

- `--mode` — which policy/policies to regenerate. `fan-off`, `fan-on`, or
  `both` (default: `both`).
- `--episodes` — number of sampling episodes to run (default: `500`).
- `--workers` — parallel sampling workers (default: sampler's own default).
- `--skip-sample` — skip the sampling stage and retrain/export from the
  existing dataset on disk (useful for re-exporting after a code-only change
  to the exporter, without re-running the ~30-minute sampling pass).
- `--dry-run` — print the commands that would run (via the same
  `plan_commands` used for real runs) without executing anything.

Regeneration is compute-heavy (sampling + training can take on the order of
30 minutes per fan mode); use `--dry-run` first to confirm what will run.

### Datasets vs. artifacts

- The intermediate sample datasets
  (`docs/superpowers/experiments/_ampc_data/pifire_span[_fan].npz`) are
  **gitignored** — they're large, reproducible, and not needed at runtime.
- The exported runtime artifacts (`controller/mpc_policy_net.npz` and
  `controller/mpc_policy_net_fan.npz`) **are committed** — they're what the
  controller actually loads.

### Acceptance gate

After a real (non-dry) run, the tool prints a reminder of the acceptance
bar a regenerated fan-on net should clear before being trusted in place of
the NLP fallback:

> Acceptance gate: run scratchpad fan ablation; fan-on net should hit
> \|bias\|<=0.10C, RMS<=0.72C (5s control period, 110-288C).
