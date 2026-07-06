# Fan-mode-aware MPC net policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship two single-mode MPC net-policy artifacts (fan-off / fan-on), select the correct one at load time by `enable_fan_input`, and retrain the fan-on net so `net + fan-on` matches `NLP + fan-on` instead of carrying a ~0.48 °C bias.

**Architecture:** The net's target Q-policy is identical in both fan regimes; only the training *state distribution* differs (fan-off vs fan-on closed-loop DAgger rollouts). So we keep the network/pipeline unchanged and split by dataset: two `.npz` artifacts, chosen by a `_fan` filename-suffix convention, with the fan mode embedded in each artifact's calibration and gated in `matches_config`. A wrong/stale/missing artifact falls back to the NLP policy (existing path).

**Tech Stack:** Python, numpy/scipy (runtime, pure-numpy inference), torch + multiprocessing (offline training only), do-mpc/CasADi/IPOPT (NLP policy + sampler), pytest.

## Global Constraints

- Runtime inference stays **pure numpy** (no torch/CasADi in `controller/mpc_net.py`). Verbatim from spec.
- Single config key `policy_net_path` (default `./controller/mpc_policy_net.npz`); fan-on path derived by inserting `_fan` before the extension. No settings.json/wizard schema change.
- Fan mode embedded as an int (0/1) under key `enable_fan_input`; `matches_config` must reject a fan-mode mismatch.
- `load()` must tolerate a legacy artifact **missing** `enable_fan_input` by defaulting it to `0` (fan-off) — so the currently-shipped artifact keeps loading (fan-off) and is correctly refused under fan-on.
- Any load failure (missing file, load error, `matches_config` false) → return `None` → NLP fallback. Never raise into the control loop.
- Both artifacts regenerated at **500 episodes**, 120 min/episode, span 100–290 °C, dither 8.0, 400 train epochs. Only fan mode differs.
- Acceptance gate for the fan-on net: aggregate **|bias| ≤ 0.10 °C** and **RMS ≤ 0.72 °C** across the 110–288 °C sweep at 5 s control period.
- Sample `.npz` datasets stay gitignored; the two `controller/mpc_policy_net*.npz` artifacts are committed.
- Commit messages containing backticks: write the message to a file and use `git commit -F <file>` (zsh mangles `-m` with backticks).

---

### Task 1: Fan-mode-aware `NetPolicy` (path helper + calibration flag)

**Files:**
- Modify: `controller/mpc_net.py` (imports; `_CALIB_INTS`; `NetPolicy.load`; add module-level `net_path_for`)
- Test: `tests/test_mpc_net.py`

**Interfaces:**
- Consumes: existing `NetPolicy`, `_CALIB_FLOATS`, `_CALIB_INTS`, `NetPolicy.load`, `NetPolicy.matches_config`.
- Produces:
  - `net_path_for(base_path: str, enable_fan: bool) -> str` — returns `base_path` when `enable_fan` is false, else the `_fan`-suffixed sibling (`root + '_fan' + ext`).
  - `NetPolicy.calib['enable_fan_input']` — int 0/1 (0 for legacy artifacts).
  - `matches_config(cfg)` returns False when `int(cfg['enable_fan_input'])` differs from the artifact's embedded flag.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mpc_net.py`:

```python
from controller.mpc_net import NetPolicy, net_path_for


def test_net_path_for_fan_off_returns_base():
    assert net_path_for('./controller/mpc_policy_net.npz', False) == './controller/mpc_policy_net.npz'


def test_net_path_for_fan_on_inserts_suffix():
    assert net_path_for('./controller/mpc_policy_net.npz', True) == './controller/mpc_policy_net_fan.npz'


def test_net_path_for_handles_dotted_dirs():
    # dots in the directory must not confuse the extension split
    assert net_path_for('/opt/pi.fire/models/net.npz', True) == '/opt/pi.fire/models/net_fan.npz'


def test_legacy_artifact_defaults_to_fan_off():
    # the shipped artifact predates the flag; it must load and read as fan-off (0)
    p = NetPolicy.load(ART)
    assert p.calib['enable_fan_input'] == 0
    assert p.matches_config({**_DEFAULTS, 'enable_fan_input': False})
    assert not p.matches_config({**_DEFAULTS, 'enable_fan_input': True})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_mpc_net.py -k "net_path_for or legacy_artifact" -v`
Expected: FAIL — `ImportError: cannot import name 'net_path_for'` (and, once that import is added, `KeyError: 'enable_fan_input'` from `load`).

- [ ] **Step 3: Implement `net_path_for` and the flag in `controller/mpc_net.py`**

At the top of the file, add `os` to the imports (currently only `import numpy as np`):

```python
import os
import numpy as np
```

Add `'enable_fan_input'` to `_CALIB_INTS` (currently `('n_delay', 'n_horizon')`):

```python
_CALIB_INTS = ('n_delay', 'n_horizon', 'enable_fan_input')
```

In `NetPolicy.load`, replace the ints line so a missing key defaults to 0 (legacy = fan-off). Current line:

```python
        calib.update({k: int(z[k]) for k in _CALIB_INTS})
```

becomes:

```python
        # enable_fan_input was added later; legacy artifacts lack it -> fan-off (0)
        calib.update({k: (int(z[k]) if k in z.files else 0) for k in _CALIB_INTS})
```

Add the module-level helper (place it just after the `_CALIB_*` tuples):

```python
def net_path_for(base_path, enable_fan):
    """Fan-off uses base_path as-is; fan-on uses the _fan-suffixed sibling."""
    if not enable_fan:
        return base_path
    root, ext = os.path.splitext(base_path)
    return f'{root}_fan{ext}'
```

`matches_config` already iterates `_CALIB_INTS` and rejects any key whose `int(cfg[k])` differs — so adding the key to the tuple wires up the fan-mode check with no further change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_mpc_net.py -v`
Expected: PASS (new tests plus the pre-existing artifact tests still green).

- [ ] **Step 5: Commit**

```bash
git add controller/mpc_net.py tests/test_mpc_net.py
git commit -F <msgfile>   # "feat(mpc): fan-mode net path helper + calibration flag"
```

---

### Task 2: Loader selects the artifact by fan mode

**Files:**
- Modify: `controller/mpc.py` — `_load_net_policy` (lines ~171-187)
- Test: `tests/test_mpc_controller.py`

**Interfaces:**
- Consumes: `net_path_for` (Task 1), existing `NetPolicy.load` / `matches_config`, `Controller.__init__` → `self._net`.
- Produces: `Controller(policy='net', enable_fan_input=True)` loads `mpc_policy_net_fan.npz`; if that file is absent, `self._net is None` (NLP fallback), no exception.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mpc_controller.py`:

```python
from controller.mpc import Controller, _DEFAULTS

_CYCLE = {'u_min': 0.1, 'u_max': 0.9, 'HoldCycleTime': 25}


def test_fan_on_falls_back_to_nlp_when_fan_artifact_missing(tmp_path):
    # point at a non-existent base path so neither mode has an artifact
    cfg = {**_DEFAULTS, 'policy': 'net', 'enable_fan_input': True,
           'policy_net_path': str(tmp_path / 'nope.npz')}
    c = Controller(cfg, 'C', dict(_CYCLE))
    assert c._net is None  # cleanly fell back to the NLP policy
    c.set_target(150.0)
    out = c.update(150.0)  # must not raise
    assert out['fan']['duty'] is not None  # fan-on -> allocator returns a duty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/test_mpc_controller.py::test_fan_on_falls_back_to_nlp_when_fan_artifact_missing -v`
Expected: FAIL — before the change the loader looks at `policy_net_path` (the fan-off base, still the shipped file if default) rather than the fan-on sibling; with an overridden missing base it would already fall back, so first confirm failure by temporarily asserting the *path* — simplest: the test passes only once the loader derives the fan-on path. Expected pre-change failure is `AssertionError`/`KeyError` from the un-derived path handling. (If it already passes with a missing base, keep the test — it locks the fallback contract — and proceed.)

- [ ] **Step 3: Update `_load_net_policy` in `controller/mpc.py`**

Replace the path lookup at the top of `_load_net_policy`. Current:

```python
        from controller.mpc_net import NetPolicy

        path = cfg.get('policy_net_path')
        if not path or not os.path.exists(path):
            print(f'[mpc] policy=net but artifact not found ({path}); using NLP')
            return None
```

becomes:

```python
        from controller.mpc_net import NetPolicy, net_path_for

        base = cfg.get('policy_net_path')
        path = net_path_for(base, bool(cfg.get('enable_fan_input'))) if base else base
        if not path or not os.path.exists(path):
            print(f'[mpc] policy=net but artifact not found ({path}); using NLP')
            return None
```

The rest of the method (load, `matches_config`, return) is unchanged — `matches_config` now also enforces the fan-mode flag from Task 1.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_mpc_controller.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add controller/mpc.py tests/test_mpc_controller.py
git commit -F <msgfile>   # "feat(mpc): load fan-mode-specific net artifact with NLP fallback"
```

---

### Task 3: Parameterize the offline training pipeline for fan mode

**Files:**
- Modify: `docs/superpowers/experiments/sample_mpc.py` (`_episode_span`, `sample_span`, `__main__`)
- Modify: `docs/superpowers/experiments/approxmpc_span.py` (`build_span_net`)
- Modify: `docs/superpowers/experiments/export_span_net.py` (`main`)

**Interfaces:**
- Consumes: existing `Controller`, `allocate`, `build_span_net`, `_CALIB_FLOATS`, `_CALIB_INTS`.
- Produces:
  - `sample_mpc.py --mode span --enable-fan [--out PATH]` writes a fan-on span dataset.
  - `build_span_net(epochs=400, batch=4096, data_path=SPAN_NPZ)` trains from an arbitrary dataset.
  - `export_span_net.py --data PATH --out PATH --enable-fan` embeds the correct `enable_fan_input` flag.

These are experiment scripts (not part of the pytest suite); verify by short smoke runs, not committed tests.

- [ ] **Step 1: Thread `enable_fan` through the span sampler**

In `docs/superpowers/experiments/sample_mpc.py`, `_episode_span(arg)` unpacks `ep_seed, minutes, dither, sp_lo, sp_hi = arg`. Extend the tuple with `enable_fan` and build the Controller with the override. Change the unpack line to:

```python
    ep_seed, minutes, dither, sp_lo, sp_hi, enable_fan = arg
```

and the Controller construction from `Controller(dict(_DEFAULTS), 'C', dict(CYCLE))` to:

```python
    c = Controller({**_DEFAULTS, 'enable_fan_input': bool(enable_fan)}, 'C', dict(CYCLE))
```

(The rest of `_episode_span` already reads `cfg['enable_fan_input']` via `c.cfg` when calling `allocate`, so the fan now tracks the mode.)

In `sample_span`, add `enable_fan=False` to the signature and to each arg tuple:

```python
def sample_span(episodes=150, workers=None, seed=0, minutes=120, dither=8.0,
                sp_lo=100.0, sp_hi=290.0, out=OUT_SPAN, enable_fan=False):
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    args = [(seed * 100000 + e, minutes, dither, sp_lo, sp_hi, bool(enable_fan))
            for e in range(episodes)]
```

Add `enable_fan` to the saved metadata so the dataset is self-describing — change the `np.savez_compressed(out, ...)` call to include `enable_fan=np.int64(bool(enable_fan))`, and add `fan={enable_fan}` to the printed summary.

- [ ] **Step 2: Add the CLI flag and default output path**

In `__main__`, add the argument and pass it through. After the existing `ap.add_argument('--sp-hi', ...)` line add:

```python
    ap.add_argument('--enable-fan', action='store_true', help='span: sample with the MPC driving the fan')
    ap.add_argument('--out', default=None, help='override output .npz path')
```

Change the `else:` (span) branch to:

```python
    else:
        sample_span(
            episodes=a.episodes,
            workers=a.workers,
            seed=a.seed,
            dither=a.dither,
            minutes=a.minutes or 120,
            sp_lo=a.sp_lo,
            sp_hi=a.sp_hi,
            out=a.out or (OUT_SPAN.replace('.npz', '_fan.npz') if a.enable_fan else OUT_SPAN),
            enable_fan=a.enable_fan,
        )
```

- [ ] **Step 3: Let `build_span_net` train from an arbitrary dataset**

In `docs/superpowers/experiments/approxmpc_span.py`, change the signature and the first load line of `build_span_net`:

```python
def build_span_net(epochs=400, batch=4096, data_path=SPAN_NPZ):
    z = np.load(data_path)
```

(Everything else in the function is unchanged.)

- [ ] **Step 4: Parameterize `export_span_net.py`**

In `docs/superpowers/experiments/export_span_net.py`, replace the hard-coded `OUT` and `main()` with a CLI. Add at the top after imports:

```python
import argparse
```

Rewrite `main()` to accept data path, output path, and fan mode, and embed the flag explicitly (NOT from `_DEFAULTS`, whose `enable_fan_input` is always False):

```python
def main(data_path, out, enable_fan):
    net, stats = build_span_net(data_path=data_path)
    xm, xs, rm, rs = stats
    layers = [m for m in net.net if isinstance(m, torch.nn.Linear)]
    blob = {'n_layers': len(layers)}
    for i, lin in enumerate(layers):
        blob[f'W{i}'] = lin.weight.detach().numpy().T.astype(np.float32)
        blob[f'b{i}'] = lin.bias.detach().numpy().astype(np.float32)
    blob['x_mean'] = xm.numpy().astype(np.float32)
    blob['x_std'] = xs.numpy().astype(np.float32)
    blob['r_mean'] = np.float32(rm)
    blob['r_std'] = np.float32(rs)
    from controller.mpc_net import _CALIB_FLOATS, _CALIB_INTS
    for k in _CALIB_FLOATS:
        blob[k] = np.float32(_DEFAULTS[k])
    for k in _CALIB_INTS:
        # enable_fan_input reflects the mode this artifact was trained for,
        # not the _DEFAULTS value (always False)
        val = bool(enable_fan) if k == 'enable_fan_input' else _DEFAULTS[k]
        blob[k] = np.int64(val)
    z = np.load(data_path)
    blob['sp_lo'] = np.float32(z['sp_lo'])
    blob['sp_hi'] = np.float32(z['sp_hi'])
    rng = np.random.default_rng(0)
    idx = rng.choice(len(z['u0']), size=64, replace=False)
    X0 = z['X0'][idx]
    UP = z['u_prev'].flatten()[idx]
    TS = z['t_set'].flatten()[idx]
    Xin = np.column_stack([X0, UP, TS])
    with torch.no_grad():
        inp = (torch.tensor(Xin, dtype=torch.float32) - xm) / xs
        resid = net(inp).numpy().flatten() * rs + rm
    Qref = np.clip(Q_ss(X0[:, DIDX], TS) + resid, _DEFAULTS['Q_min'], _DEFAULTS['Q_max'])
    blob['ref_state'] = X0.astype(np.float32)
    blob['ref_uprev'] = UP.astype(np.float32)
    blob['ref_set'] = TS.astype(np.float32)
    blob['ref_Q'] = Qref.astype(np.float32)
    np.savez_compressed(out, **blob)
    sz = os.path.getsize(out) / 1024
    print(f'exported {out} ({sz:.0f} KB): {len(layers)} layers, fan={bool(enable_fan)}, '
          f'span [{blob["sp_lo"]:.0f},{blob["sp_hi"]:.0f}]C')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='./docs/superpowers/experiments/_ampc_data/pifire_span.npz')
    ap.add_argument('--out', default='./controller/mpc_policy_net.npz')
    ap.add_argument('--enable-fan', action='store_true')
    a = ap.parse_args()
    main(a.data, a.out, a.enable_fan)
```

- [ ] **Step 5: Smoke-test the pipeline plumbing (tiny run, not committed)**

Run a 2-episode span sample in each mode and confirm the fan flag round-trips and the fan actually varies only in fan-on:

```bash
cd /home/dannyb/sources/PiFire
./.venv/bin/python docs/superpowers/experiments/sample_mpc.py --mode span --enable-fan \
    -e 2 --minutes 20 --out /tmp/smoke_fan.npz
./.venv/bin/python -c "import numpy as np; z=np.load('/tmp/smoke_fan.npz'); print('fan flag:', int(z['enable_fan']), 'samples:', len(z['u0']))"
```

Expected: prints `fan flag: 1` and a few hundred samples, no exceptions.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/experiments/sample_mpc.py docs/superpowers/experiments/approxmpc_span.py docs/superpowers/experiments/export_span_net.py
git commit -F <msgfile>   # "feat(experiments): fan-mode option in span sampler/trainer/exporter"
```

---

### Task 4: Generate datasets, train, and export both artifacts (acceptance-gated)

**Files:**
- Produce (gitignored): `docs/superpowers/experiments/_ampc_data/pifire_span.npz`, `.../pifire_span_fan.npz`
- Produce (committed): `controller/mpc_policy_net.npz` (fan-off, re-exported with flag), `controller/mpc_policy_net_fan.npz` (fan-on, new)
- Uses: `scratchpad/fan_ablation2.py` (already written) as the acceptance sweep

**Interfaces:**
- Consumes: Task 3 CLIs.
- Produces: two committed artifacts whose `matches_config` passes for their respective fan modes; fan-on net meeting the acceptance gate.

- [ ] **Step 1: Generate both span datasets at 500 episodes**

```bash
cd /home/dannyb/sources/PiFire
./.venv/bin/python docs/superpowers/experiments/sample_mpc.py --mode span -e 500   # fan-off -> pifire_span.npz
./.venv/bin/python docs/superpowers/experiments/sample_mpc.py --mode span --enable-fan -e 500   # fan-on -> pifire_span_fan.npz
```

Expected: each prints `span: 500 episodes ... -> ~142000 samples`, ~4-6 min each on 14 workers. Run in the background if preferred.

- [ ] **Step 2: Train + export both artifacts**

```bash
./.venv/bin/python docs/superpowers/experiments/export_span_net.py \
    --data docs/superpowers/experiments/_ampc_data/pifire_span.npz \
    --out controller/mpc_policy_net.npz
./.venv/bin/python docs/superpowers/experiments/export_span_net.py \
    --data docs/superpowers/experiments/_ampc_data/pifire_span_fan.npz \
    --out controller/mpc_policy_net_fan.npz --enable-fan
```

Expected: two `exported ...` lines, `fan=False` and `fan=True` respectively.

- [ ] **Step 3: Verify artifacts load for their modes**

```bash
./.venv/bin/python -c "
from controller.mpc_net import NetPolicy, net_path_for
from controller.mpc import _DEFAULTS
off = NetPolicy.load('controller/mpc_policy_net.npz')
on = NetPolicy.load(net_path_for('controller/mpc_policy_net.npz', True))
assert off.calib['enable_fan_input'] == 0 and on.calib['enable_fan_input'] == 1
assert off.matches_config({**_DEFAULTS, 'enable_fan_input': False})
assert on.matches_config({**_DEFAULTS, 'enable_fan_input': True})
assert not on.matches_config({**_DEFAULTS, 'enable_fan_input': False})
print('artifacts OK')
"
```

Expected: `artifacts OK`.

- [ ] **Step 4: Run the acceptance sweep (gate)**

Run the existing 4-cell ablation (NLP/net × fan off/on), 5 s control period:

```bash
./.venv/bin/python /tmp/claude-839601109/-home-dannyb-sources-PiFire/59763e70-6791-4f29-9be9-5bf7b4c5d250/scratchpad/fan_ablation2.py 2>&1 | grep -vE "^(INFO|WARNING)"
```

Expected: the `net True` row now shows aggregate **|bias| ≤ 0.10** and **RMS ≤ 0.72** (vs the prior 0.48 / 0.86). If it misses the gate, regenerate with more episodes (e.g. `-e 800`) and/or raise `build_span_net(epochs=600)`, then re-export and re-run before committing.

- [ ] **Step 5: Run the full controller test suite**

Run: `./.venv/bin/python -m pytest tests/ -k mpc -q`
Expected: PASS (the re-exported fan-off artifact still satisfies `test_mpc_net.py`'s torch-reference and default-match tests).

- [ ] **Step 6: Commit the artifacts**

```bash
git add controller/mpc_policy_net.npz controller/mpc_policy_net_fan.npz
git commit -F <msgfile>   # "feat(mpc): retrained fan-off + new fan-on net artifacts (500-episode span)"
```

(Datasets under `_ampc_data/` stay untracked per `.gitignore`.)

---

### Task 5: Committed closed-loop sanity test for the fan-on net

**Files:**
- Modify: `tests/test_mpc_net_loop.py`

**Interfaces:**
- Consumes: `net_path_for`, both committed artifacts, `Controller`, `GrillSim`.
- Produces: a fast, committed regression test that fan-on net control is offset-free (net inference is instant; no NLP in this test).

- [ ] **Step 1: Write the test**

Add to `tests/test_mpc_net_loop.py` (mirror the existing net-loop harness; gate on the fan artifact existing):

```python
import os
from controller.mpc_net import net_path_for

_FAN_ART = net_path_for(
    os.path.join(os.path.dirname(__file__), '..', 'controller', 'mpc_policy_net.npz'), True)


@pytest.mark.skipif(not os.path.exists(_FAN_ART), reason='fan-on net artifact not exported')
def test_fan_on_net_is_offset_free():
    # net policy + enable_fan_input=True should hold offset-free, matching the
    # regime it was trained on (fan-on closed-loop states).
    cfg = {**_DEFAULTS, 'control_period': 25.0, 'policy': 'net', 'enable_fan_input': True}
    c = Controller(cfg, 'C', dict(CYCLE))
    assert c._net is not None and c._net.calib['enable_fan_input'] == 1
    c.set_target(190.0)
    plant = GrillSim(seed=0)
    ts, temps = [], []
    for w in range(int(75 * 60 / 25)):
        out = c.update(plant.measured())
        ratio = float(np.clip(out['cycle_ratio'], CYCLE['u_min'], CYCLE['u_max']))
        fan = out['fan']['duty'] if out['fan']['duty'] is not None else 100.0
        on = int(round(ratio * 25))
        for s in range(25):
            plant.step(auger_on=(s < on), fan_frac=fan / 100.0)
            ts.append(w * 25 + s); temps.append(plant.true_Tc)
    ts, temps = np.array(ts), np.array(temps)
    err = temps[ts >= 1800] - 190.0
    assert abs(np.mean(err)) <= 0.5   # offset-free (fan-on net; prior mismatch was ~0.4-0.9)
    assert np.sqrt(np.mean(err**2)) <= 2.0
```

Confirm `CYCLE`, `_DEFAULTS`, `Controller`, `GrillSim`, `np`, `pytest` are already imported at the top of the file (they are, per the existing net-loop test); add only `os` and the `net_path_for` import.

- [ ] **Step 2: Run the test to verify it passes**

Run: `./.venv/bin/python -m pytest tests/test_mpc_net_loop.py::test_fan_on_net_is_offset_free -v`
Expected: PASS (fan-on net holds 190 °C offset-free).

- [ ] **Step 3: Commit**

```bash
git add tests/test_mpc_net_loop.py
git commit -F <msgfile>   # "test(mpc): closed-loop offset-free check for fan-on net"
```

---

### Task 6: Committed net-regeneration wrapper tool + README (user-requested)

**Files:**
- Create: `tools/regenerate_mpc_net.py`
- Create: `tools/README.md`
- Test: `tests/test_regenerate_mpc_net.py`

**Rationale:** The sample→train→export pipeline is committed but buried in `docs/superpowers/experiments/` among ~25 spike scripts, with no discoverable entry point. Net artifacts go stale (`matches_config` → NLP fallback) after a grill recalibration (`update_mpc.py`), and the only recovery is knowing that three specific scripts form an ordered pipeline. This task adds ONE discoverable, documented wrapper. Decision (user): wrapper only, leave the three stage scripts where they are.

**Interfaces:**
- Produces (pure, testable helpers so the orchestration is unit-tested without running heavy compute):
  - `sample_cmd(py, enable_fan, episodes, workers) -> list[str]` — the `sample_mpc.py --mode span [--enable-fan] -e N [-w W]` argv.
  - `export_cmd(py, enable_fan) -> list[str]` — the `export_span_net.py --data <span[_fan].npz> --out <mpc_policy_net[_fan].npz> [--enable-fan]` argv, deriving the dataset path from the span-dataset convention and the artifact path via `net_path_for('./controller/mpc_policy_net.npz', enable_fan)`.
  - `plan_commands(modes, episodes, workers, skip_sample) -> list[list[str]]` — the ordered argv list for the selected modes (`modes` ⊆ `{False, True}` for fan-off/fan-on).
- CLI: `python tools/regenerate_mpc_net.py [--mode {fan-off,fan-on,both}] [--episodes N] [--workers W] [--skip-sample] [--dry-run]`. Default `--mode both`, `--episodes 500`. `--dry-run` prints the commands (via `plan_commands`) without executing. `--skip-sample` omits the sample step (retrain+export from an existing dataset). After a real (non-dry) run it prints the acceptance-gate reminder.

**Constants (module level), so the test and the CLI agree:**
- `BASE_ARTIFACT = './controller/mpc_policy_net.npz'`
- `SPAN_DATA = './docs/superpowers/experiments/_ampc_data/pifire_span.npz'` and its `_fan` sibling via the same `_fan`-before-`.npz` rule.
- `SAMPLER = 'docs/superpowers/experiments/sample_mpc.py'`, `EXPORTER = 'docs/superpowers/experiments/export_span_net.py'`.

- [ ] **Step 1: Write the failing test**

`tests/test_regenerate_mpc_net.py`:

```python
import sys
sys.path.insert(0, 'tools')
import regenerate_mpc_net as rg


def test_export_cmd_fan_on_uses_fan_paths_and_flag():
    cmd = rg.export_cmd('py', True)
    assert '--enable-fan' in cmd
    assert any(a.endswith('pifire_span_fan.npz') for a in cmd)
    assert any(a.endswith('mpc_policy_net_fan.npz') for a in cmd)


def test_export_cmd_fan_off_uses_base_paths_no_flag():
    cmd = rg.export_cmd('py', False)
    assert '--enable-fan' not in cmd
    assert any(a.endswith('pifire_span.npz') and not a.endswith('_fan.npz') for a in cmd)
    assert any(a.endswith('mpc_policy_net.npz') and not a.endswith('_fan.npz') for a in cmd)


def test_sample_cmd_carries_episodes_and_fan_flag():
    on = rg.sample_cmd('py', True, 500, None)
    assert '--enable-fan' in on and '500' in on and '--mode' in on and 'span' in on
    off = rg.sample_cmd('py', False, 300, 8)
    assert '--enable-fan' not in off and '300' in off and '8' in off


def test_plan_commands_both_orders_sample_before_export_per_mode():
    cmds = rg.plan_commands([False, True], episodes=500, workers=None, skip_sample=False)
    # 4 commands: sample-off, export-off, sample-on, export-on
    assert len(cmds) == 4
    assert 'sample_mpc.py' in ' '.join(cmds[0]) and 'export_span_net.py' in ' '.join(cmds[1])


def test_plan_commands_skip_sample_omits_sampling():
    cmds = rg.plan_commands([True], episodes=500, workers=None, skip_sample=True)
    assert len(cmds) == 1 and 'export_span_net.py' in ' '.join(cmds[0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_regenerate_mpc_net.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'regenerate_mpc_net'`.

- [ ] **Step 3: Implement `tools/regenerate_mpc_net.py`**

Pure helpers build argv from the constants; `net_path_for` (imported from `controller.mpc_net`, adding the repo root to `sys.path`) derives the fan-on artifact path; `main()` parses args, calls `plan_commands`, and either prints them (`--dry-run`) or runs each with `subprocess.run(cmd, check=True)` using `sys.executable`. Deriving the dataset `_fan` path reuses the same suffix rule. After a real run, print: `Acceptance gate: run scratchpad fan ablation; fan-on net should hit |bias|<=0.10C, RMS<=0.72C (5s control period, 110-288C).` Keep it small and single-responsibility.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_regenerate_mpc_net.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Smoke the CLI dry-run**

Run: `./.venv/bin/python tools/regenerate_mpc_net.py --mode both --dry-run`
Expected: prints 4 commands (sample+export for each mode), executes nothing.

- [ ] **Step 6: Write `tools/README.md`**

Document: what the net policy is and that artifacts embed the grey-box calibration; WHEN to regenerate (after recalibrating the grill via `update_mpc.py`, which changes the calibration so `matches_config` fails and the MPC falls back to the NLP policy); the one-command usage (`python tools/regenerate_mpc_net.py --mode both`), the per-mode/`--skip-sample`/`--dry-run` options; that the `.npz` sample datasets are gitignored while the two `controller/mpc_policy_net*.npz` artifacts are committed; and the acceptance gate.

- [ ] **Step 7: Commit**

```bash
git add tools/regenerate_mpc_net.py tools/README.md tests/test_regenerate_mpc_net.py
git commit -F <msgfile>   # "feat(tools): one-command MPC net-policy regeneration wrapper + docs"
```

---

## Self-Review

**Spec coverage:**
- Suffix convention / one config key → Task 1 (`net_path_for`), Task 2 (loader).
- Fan mode in calibration + `matches_config` gate → Task 1.
- Loader path selection + NLP fallback → Task 2.
- Sampler/trainer/exporter fan-mode parameterization → Task 3.
- Train fan-on + re-export fan-off, 500 episodes, acceptance gate → Task 4.
- Tests (unit path/matches_config, loader fallback, closed-loop) → Tasks 1, 2, 5.
- Datasets gitignored, artifacts committed → Task 4 Step 6.

**Placeholder scan:** none — every code/command step is concrete.

**Type consistency:** `net_path_for(base_path, enable_fan) -> str` used identically in Tasks 1, 2, 4, 5. `calib['enable_fan_input']` int 0/1 used consistently. `build_span_net(..., data_path=SPAN_NPZ)` and `export_span_net.main(data_path, out, enable_fan)` match their call sites in Task 4.

**Note vs spec:** the spec said "no missing-flag case"; the plan strengthens `load()` to also tolerate a legacy artifact missing the flag (defaults to fan-off). This is additive safety, consistent with the spec's fallback intent, and avoids breaking the shipped artifact during the transition window before Task 4 re-exports it.
