# Fan-mode-aware MPC net policy

## Problem

The MPC's optional pure-numpy net policy (`policy='net'`) approximates the NLP
firing-rate policy. The shipped artifact (`controller/mpc_policy_net.npz`) was
trained only on **fan-off** closed-loop trajectories: the span sampler
(`_episode_span` in `docs/superpowers/experiments/sample_mpc.py`) draws its
DAgger states with `enable_fan=bool(cfg['enable_fan_input'])`, and `cfg` comes
from `_DEFAULTS`, where `enable_fan_input=False`.

Under `enable_fan_input=True` the plant visits a **different state distribution**
(different disturbance/temperature trajectories, because the fan is a real
combustion lever). The net's *target* Q-policy is identical in both regimes —
only the training input distribution differs — so the fan-off net extrapolates
under fan-on and picks up a systematic offset.

Measured (5 s control period, 110–288 °C sweep, `scratchpad/fan_ablation2.py`):

| policy | `enable_fan_input` | RMS (°C) | peak (°C) | \|bias\| (°C) |
|---|---|---|---|---|
| NLP | False | 0.82 | 3.95 | 0.02 |
| NLP | True  | **0.68** | 3.60 | 0.01 |
| net | False | 0.81 | 3.96 | 0.05 |
| net | True  | 0.86 | 3.86 | **0.48** |

The `net + fan-on` cell is the only bad combination (bias up to ~0.9 °C at
110 °C). The NLP handles fan-on fine because it re-optimizes against the
estimator every step; the fixed net does not.

## Goal

Ship **two single-mode net artifacts**, one per fan regime, and load the correct
one automatically. On a build where the needed artifact is missing or stale, fall
back to the NLP policy (existing behavior) rather than silently using the wrong
net. Retrain the fan-on net so `net + fan-on` roughly matches `NLP + fan-on`.

Non-goals: a single combined net with a fan-flag input (rejected — the user
asked for two versions, and two single-mode nets keep each net's input
distribution tight); changing the NLP policy, the allocator, or `control.py`.

## Design

### Artifact selection — suffix convention, one config key

Keep the single `policy_net_path` config key (default
`./controller/mpc_policy_net.npz`). The fan-off net uses it verbatim; the fan-on
net is the `_fan`-suffixed sibling:

- fan-off → `./controller/mpc_policy_net.npz`
- fan-on  → `./controller/mpc_policy_net_fan.npz`

A pure helper in `controller/mpc_net.py`:

```python
def net_path_for(base_path, enable_fan):
    """Fan-off uses base_path; fan-on uses the _fan-suffixed sibling."""
    if not enable_fan:
        return base_path
    root, ext = os.path.splitext(base_path)
    return f'{root}_fan{ext}'
```

No settings.json / wizard schema change; the existing fan-off file stays valid.

### Fan mode embedded in calibration + gated in `matches_config`

Add `enable_fan_input` to the artifact's embedded calibration as an int (0/1),
in `_CALIB_INTS`. `matches_config` already rejects an int key whose value
differs from `cfg`, so adding it to `_CALIB_INTS` makes fan-mode mismatch a
rejection for free. Both artifacts are (re)exported so both carry the flag —
there is no missing-flag case to special-case.

This is belt-and-suspenders on top of path selection: even if the wrong file
sits at the expected path, `matches_config` catches the fan-mode mismatch and
forces the NLP fallback.

### Loader — `controller/mpc.py::_load_net_policy`

Compute the effective path with `net_path_for(cfg['policy_net_path'],
cfg['enable_fan_input'])`, then load exactly as today. Missing file, load error,
or `matches_config` failure → return `None` → NLP fallback. The existing
`[mpc] ... using NLP` log lines name the fan-mode-specific path so a missing
fan-on artifact is diagnosable, e.g.:

```
[mpc] policy=net but artifact not found (./controller/mpc_policy_net_fan.npz); using NLP
```

### Training pipeline (offline, experiments/)

Only the fan mode differs between the two datasets/artifacts; everything else is
identical.

1. `sample_mpc.py` — `sample_span` / `_episode_span` gain an `enable_fan`
   parameter (CLI `--enable-fan`) that overrides the episode Controllers'
   `enable_fan_input`, and the output path is mode-specific
   (`pifire_span.npz` / `pifire_span_fan.npz`).
2. `approxmpc_span.py::build_span_net` — accept a `data_path` argument so it can
   train from either span dataset (default keeps the current `SPAN_NPZ`).
3. `export_span_net.py` — accept output path + fan mode; embed
   `enable_fan_input` into the calibration blob; write
   `mpc_policy_net.npz` (fan-off) or `mpc_policy_net_fan.npz` (fan-on).

Data flow:

```
sample_mpc.py --mode span [--enable-fan]
    -> _ampc_data/pifire_span[_fan].npz
    -> export_span_net.py (build_span_net -> train -> embed calib)
    -> controller/mpc_policy_net[_fan].npz   (committed)
```

### Training scale

Both datasets regenerated fresh at **500 episodes**, identical settings except
fan mode (120 min/episode, span 100–290 °C, dither 8.0, 14 workers). This gives
~142k samples each. The existing local fan-off dataset was ~118k samples
(~415 episode-equivalents); 500 gives comparable-and-slightly-more, and makes
the two nets apples-to-apples. Train 400 epochs (current default).

## Error handling / fallback

The only new failure surface is "fan-on artifact absent or stale". It resolves
to the existing, already-tested path: `_load_net_policy` returns `None`, the
controller builds the NLP, and `update()` runs the NLP each step — correct, just
slower — with a log line naming the missing/mismatched path. `enable_fan_input`
in `matches_config` guarantees a fan-off artifact can never be used in fan-on
mode (or vice-versa) even if mis-placed.

## Testing

- **Unit (`tests/test_mpc_net.py`)**:
  - `net_path_for`: fan-off returns the base path; fan-on inserts `_fan` before
    the extension; handles paths with dots in directory names.
  - `matches_config`: a fan-off artifact's calib is rejected under a fan-on cfg
    and vice-versa; still accepted under the matching mode.
- **Loader (`tests/test_mpc_net.py` or `test_mpc_controller.py`)**:
  - `policy='net'`, `enable_fan_input=True`, fan-on artifact absent → `_net is
    None` (NLP fallback), no exception.
  - both artifacts present → correct one loads for each mode
    (`_net.calib['enable_fan_input']` matches).
- **Closed-loop acceptance gate** (extend `scratchpad/fan_ablation2.py` into a
  repeatable check): the fan-on net must roughly match `NLP + fan-on` and clearly
  beat the mismatched net — aggregate **|bias| ≤ 0.10 °C** and **RMS ≤ 0.72 °C**
  across the 110–288 °C sweep at 5 s control period. If a fresh sample+train run
  misses the gate, iterate (more episodes/epochs) before committing the artifact.

## Deliverables

- Code: `controller/mpc_net.py`, `controller/mpc.py`, and the three experiment
  scripts (`sample_mpc.py`, `approxmpc_span.py`, `export_span_net.py`).
- Committed artifacts: re-exported `controller/mpc_policy_net.npz` (fan-off,
  now carrying the flag) and new `controller/mpc_policy_net_fan.npz` (fan-on).
- Tests as above, all passing.
- The `.npz` sample datasets stay gitignored (as today).
