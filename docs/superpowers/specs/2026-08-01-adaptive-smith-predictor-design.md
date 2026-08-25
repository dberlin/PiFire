# Adaptive Smith Predictor Design

## Goal

Replace PID-SP's rate-of-change temperature extrapolator with a real Smith
predictor for a first-order-plus-dead-time (FOPDT) pellet-grill plant. The
predictor uses the auger duty that was actually applied, identifies process
gain, time constant, and dead time passively from closed-loop operation,
retains trusted physical parameters across restarts, and falls back to
measured-temperature PID whenever prediction is not trustworthy.

The work also closes two defects the runtime has today, both of which the
predictor's requirements expose: no controller is ever told what duty reached
the auger, and controller diagnostics are published by serializing the
controller's `__dict__`.

## Scope

Covered:

- a numpy-vectorized adaptive FOPDT identifier and a Smith predictor;
- four general controller capabilities -- applied-output feedback, JSON-safe
  diagnostics, and model snapshot/restore -- on `ControllerBase`, forwarded by
  both runners and driven by Hold mode;
- durable per-controller model storage in SQLite;
- MPC consuming both applied-output feedback and the diagnostics hook, without
  losing agreement between its net policy and the NLP that policy approximates;
- PID-SP composing identifier and predictor, using one selected temperature for
  P, I, and D;
- deterministic unit tests plus a before/after GrillSim comparison.

Not covered:

- deliberate plant excitation;
- nonlinear or multi-zone identification;
- fan/combustion modelling;
- live Hold-to-Hold `set_target()` without controller rebuild;
- the high-temperature transition tuning described in
  `2026-07-25-high-temperature-transition-tuning-design.md` (see
  [Deferred](#deferred));
- any change to the other PID variants' control equations.

## Delivery

One spec, two plans.

**Plan A -- generic controller plumbing.** The four capabilities, runner
forwarding, Hold-mode call sites, the SQLite store, and MPC's consumption of
both new hooks. Ships working software on its own: it fixes MPC's state
estimator and stops leaking do-mpc objects into the MQTT payload, and it changes
no PID-SP behavior.

**Plan B -- the adaptive predictor.** The identifier, the predictor, PID-SP's
composition of them, and the controller-metadata changes. Depends entirely on
Plan A.

Two consequences of that split are worth stating so neither plan's implementer
is surprised:

- No shipped controller returns a model snapshot until Plan B, so Plan A's
  persistence call site always no-ops in production. The store earns its
  coverage from unit tests and from a stub controller that does implement the
  hooks -- not from an integration run.
- The GrillSim baseline is captured in Plan A, before its first code change,
  and covers both controllers. Plan A re-runs the matrix for MPC's after
  numbers; Plan B re-runs it for PID-SP's.
- Plan A therefore owns the only step that can rewrite a committed binary:
  regenerating the MPC net policy, and possibly extending the sampler that
  trains it, conditional on a measured loss of net-versus-NLP agreement. Plan B
  touches no artifact.

## Process Model and Smith Equation

The FOPDT plant is a constant offset plus a delayed heat-response state:

```text
T(t) = T_offset + x_d(t)
dx/dt = (K * u - x) / tau
x_d(t) = x(t - theta)
```

where `u` is applied auger duty in `[0, 1]`, `K` is steady-state temperature
gain per unit duty, `tau` is the first-order time constant in seconds, `theta`
is firebox dead time in seconds, and `T_offset` absorbs ambient and other
constant heat-balance terms.

The predictor maintains an undelayed heat-response state `x_hat_0` driven by
current applied duty and a delayed state `x_hat_d` driven by the same history
shifted by `theta`. The controller input is the measured-output Smith form:

```text
T_smith = T_measured + x_hat_0 - x_hat_d
```

The measured term preserves feedback for plant/model mismatch and disturbances;
the state difference removes identified delay from that signal. The unknown
constant offset cancels, so it is estimated for identification but is not part
of the persisted model.

Delayed command changes apply at their exact due timestamps. A command due
between two controller updates splits model integration into segments; delay is
never rounded to the controller interval.

## Architecture

### `controller/applied_output.py` (new)

A leaf module -- stdlib only, importing from neither `controller/*.py` nor
`controller/runtime/**` -- so both layers can use it without an inversion.

```python
class OutputSource(Enum):
    """Why the auger is running at the duty it is running at."""

    CONTROLLER = "controller"  # the controller's request, possibly clamped to [u_min, u_max]
    LID_OPEN = "lid_open"  # lid-open safety pinned the auger
    MANUAL_OVERRIDE = "manual_override"  # a human commanded the auger directly
    FAN_ASSIST = "fan_assist"  # auger held at u_min; the fan PID is controlling
    SEED = "seed"  # actuator state at setup/reconfigure, caused by no command


@dataclass(frozen=True)
class AppliedOutput:
    ratio: float  # duty that reached the auger, [0, 1]
    source: OutputSource
    timestamp: float  # when it was applied
    requested: float | None = None  # the controller's pre-clamp request, when there was one

    @property
    def controller_commanded(self) -> bool:
        return self.source is OutputSource.CONTROLLER
```

`controller_commanded` is derived rather than stored, so it cannot drift out of
agreement with the reason and no call site can assert "not the controller's"
without saying why.

`timestamp` is required. Hold mode holds `now` at every call site, so a threaded
runner cannot stamp a report with the time its worker happened to drain the
queue.

`requested` separates a saturated interval from an unsaturated one. When the
controller asked for 1.4 and the auger ran at `u_max`, that interval says almost
nothing about process gain -- it describes the clamp. It also makes "why is the
grill pinned at maximum" answerable from diagnostics.

Two pure functions live here and are unit-tested directly against the precedence
table:

```python
def classify_output_source(lid_open, manual_override_active, fan_assist_active) -> OutputSource
def seed_output(ratio, timestamp, lid_open, manual_override_active, fan_assist_active, auger_output) -> AppliedOutput
```

Precedence when several conditions hold at once is `MANUAL_OVERRIDE` >
`LID_OPEN` > `FAN_ASSIST` > `CONTROLLER`. A human toggling the auger during a
lid-open pause reads as manual, not as lid-open.

### `controller/base.py`

Four capabilities on `ControllerBase`, all default-inert, joining the existing
`get_control_period` / `commands_fan` / `wants_async` overridable-method idiom.
No `function_list`, no string reflection.

```python
def set_output(self, applied): ...  # applied: AppliedOutput      -> None
def get_status(self): ...  # -> dict | None  (None keeps the legacy __dict__ payload)
def get_model_snapshot(self): ...  # -> dict | None
def restore_model(self, snapshot): ...  # -> bool
```

`controller_commanded=False` does **not** mean "discard this command". The grill
really did run at that duty, so it always enters the model's command history;
what it suppresses is *identification* across the interval, so no estimator ever
computes a temperature slope over time the controller did not drive.

### `controller/runtime/runner.py`

All four are added to the `ControllerRunner` ABC. `SyncControllerRunner`
forwards directly. `ThreadedControllerRunner` appends `AppliedOutput` instances
to a pending list under its lock; the worker drains that list and replays them
in timestamp order **before** calling `update()`, so the core always hears about
a command before it hears the temperature that command caused.

`controller_state()` changes from `dict(self._core.__dict__)` to preferring
`get_status()`, falling back to `__dict__` only when it returns `None`.

### `controller/runtime/modes/hold.py`

Five call sites, each reporting the duty that actually reached the auger:

| Site | Report |
| --- | --- |
| `setup()` | `restore_model()` from the store, then seed with the initial `u_min` ratio as `SEED` |
| per-tick, after the `u_max` clamp | the clamped ratio, source from `classify_output_source`, `requested` = the controller's raw output |
| lid-open detect and lid-open toggle, at both `auger_off()` calls | `0.0`, `LID_OPEN` |
| `controller_update` reconfigure | restore the model into the rebuilt controller, then reseed from current auger state |
| `_on_manual_output` | `1.0`/`0.0`, `MANUAL_OVERRIDE` |

Both lid-open branches report, not just the toggle: `_auger_cycle_tick` in
`modes/base.py` has no lid guard, so the auger genuinely resumes cycling at
`u_min` during a lid-open pause and both transitions are equally real.

`_on_manual_output(name, output)` is a new no-op hook on `ControlMode`, called
from the shared `_apply_manual_overrides` and overridden only by `HoldMode` --
the same shape as the existing `_on_auger_on`. It fires at override *start*, so
the duty history covers the whole override window rather than leaving a hole the
predictor would fill by assuming the last reported duty persisted. The per-tick
report is suppressed while an override is live and resumes at expiry.

Model persistence is a `save()` on the same per-tick path, after the controller
update. No teardown flush.

### `common/controller_model_state.py` (new)

```python
class ControllerModelStore:
    def __init__(self, reader=None, writer=None): ...
    def load(self, name) -> dict | None: ...
    def save(self, name, snapshot) -> bool: ...
```

The whole record lives under one SQLite generic key,
`controller_model_state`, shaped `{"version": 1, "models": {<name>: <snapshot>}}`.
Keyed by controller name, so switching PID-SP and MPC does not cross-contaminate
and each keeps its own learned model.

There is no staging, no flush, no write throttle, and no atomic-replace
sequence. The SQLite transaction is the atomicity, and a write is cheap. The one
guard is that `save()` skips when the revision is not an advance on what is
stored, primed by `load()`, so the per-tick call costs a dict lookup on the
overwhelming majority of ticks where nothing was learned.

Validation is envelope-only: a non-empty dict, an integer `revision >= 0`,
JSON-encodable with `allow_nan=False`, at most 8 KiB. No model field names and
no physics bounds -- the store owns "is this a bounded, JSON-safe record", the
controller owns "do these numbers describe a possible grill" and re-checks in
`restore_model`. Reads are fail-closed: any storage, root-schema, or member
error yields an empty store rather than a half-trusted mix. `read_generic_key`
raises `TypeError` for an absent key, since it calls `json.loads(None)`; that is
caught with everything else.

RLS matrices, model states, and delayed command history are not persisted. They
describe estimator confidence and past time evolution, not fixed grill physics.
A process restart does not reduce trust in identified parameters, so trusted
values have no age expiry and restore as trusted immediately.

### `controller/fopdt_identifier.py` (new)

One recursive-least-squares estimator per dead-time candidate, 0 through 120
seconds in 5-second steps -- 25 candidates. The bank is a single batched numpy
update, not a loop.

For candidate `theta_j`, each accepted observation updates:

```text
(T_k - T_(k-1)) / dt = beta_0 + beta_T * T_(k-1) + beta_u * delayed_average_duty
```

Physical parameters recover as `tau = -1/beta_T`, `K = -beta_u/beta_T`,
`T_offset = -beta_0/beta_T`. Temperature regressors are centered and scaled
before each update and transformed back afterwards; absolute grill temperatures
condition the matrix badly otherwise.

State is stacked across the `N = 25` candidates in `float64`: coefficients
`Theta` shaped `(N, 3)`, covariances `P` shaped `(N, 3, 3)`, exponentially
weighted squared residuals `resid_ew` shaped `(N,)`, regressors `phi` shaped
`(N, 3)`. Only the third regressor column differs per candidate; `[1, T_(k-1)]`
is shared and broadcast. One accepted observation updates the whole bank:

```python
Pphi = np.einsum("nij,nj->ni", P, phi)
denom = LAM + np.einsum("ni,ni->n", phi, Pphi)
gain = Pphi / denom[:, None]
err = y - np.einsum("ni,ni->n", phi, Theta)
Theta += gain * err[:, None]
P = (P - np.einsum("ni,nj->nij", gain, Pphi)) / LAM
P = 0.5 * (P + P.transpose(0, 2, 1))
resid_ew = 0.02 * err**2 + 0.98 * resid_ew
```

Forgetting factor `LAM = 0.9995`; covariances initialize to `1e6 * I`. The
symmetrization each step holds `P` symmetric against accumulated float drift.

Parameter recovery, delta-method relative standard errors for `K` and `tau`, the
trust-gate comparisons, and the winner/runner-up residual test all stay
array-wide. The gates become a boolean mask over `(N,)`; promotion is
`argmin(resid_ew)` with the runner-up from one `np.partition`. No Python loop
over candidates anywhere.

The delayed-duty regressor is vectorized the same way. Applied duty is kept as a
piecewise-constant step function with a running cumulative integral
`I(t) = integral of u dt`; then for all candidates at once

```text
delayed_average_duty = (I(t_k - theta) - I(t_(k-1) - theta)) / dt
```

with `I` evaluated by one `np.searchsorted` plus linear interpolation inside the
containing segment. This is exact for piecewise-constant duty, which is what an
auger produces, and replaces 25 windowed scans with one lookup.

Work and memory are bounded by construction: fixed-size stacked arrays, and duty
history retained only as far back as the largest candidate delay plus one sample
interval.

An interval is rejected when any `AppliedOutput` covering it was not
`controller_commanded`, when `dt` is non-positive or implausible, or when a
temperature is non-finite. A rejection resets the observation anchor, so no
regression row spans a gap the controller did not drive.

Numerical guards: `np.errstate` around the recovery divisions, non-finite
results masking a candidate out rather than propagating, and a per-candidate
reset when its covariance loses positive-definiteness.

The identifier exposes a diagnostic snapshot: accepted-sample counts, excitation
measures, best and runner-up residuals, covariance-derived uncertainty,
candidate estimates, trusted estimates, and trust state.

### `controller/smith_predictor.py` (new)

Owns trusted `K`, `tau`, and `theta`; the undelayed and delayed model states;
the timestamped applied-duty queue sized for the maximum candidate delay; and
the validation and reset logic.

It returns measured temperature until trusted parameters exist. On initial
trust, on restore, and after any safety reset, both branches initialize to equal
states, so the Smith correction starts at exactly zero and control never steps.

Prediction disables immediately when model state or predicted temperature
becomes non-finite, when predicted temperature leaves -100 to 1200 F, or when
the one-step prediction residual exceeds 100 F for four consecutive accepted
observations. Both branches then reinitialize equally and control falls back to
measured temperature. The last valid parameters remain observable in
`get_status()`; use resumes only after safe reinitialization.

Internally canonical Fahrenheit, converting at the boundary, so persisted gain
has one meaning regardless of the configured units. The clock is injected:
PID-SP passes its module-local `time.time`, tests pass a fake.

### `controller/pid_sp.py`

PID-SP composes the identifier and predictor rather than modelling anything
itself.

Removed: the `roc` rate-of-change extrapolator, the `math.exp` predicted-
temperature line, and the `tau` / `theta` config reads.

`update(current)` advances predictor and identifier state and selects one
temperature -- Smith-corrected when trusted, measured otherwise. That single
value drives P, I, and D. The derivative compares consecutive *selected*
temperatures; it never subtracts a measured sample from a predicted one.

Retained: auto-centering on setpoint, the integral clamp to plus/minus center,
the `stable_window` overshoot cut, and PB saturation. `set_target()` resets
target-dependent PID terms and timing while preserving learned parameters, RLS
state, model states, and duty history.

Implements all four capabilities: `set_output`, `get_status`,
`get_model_snapshot` (a serializable trusted model carrying a monotonically
increasing integer `revision`), and `restore_model` (which re-validates the
physics the store deliberately does not judge).

**One existing bug is fixed here.** Today's line 141-142 reads

```python
if error < self.pb and current_time - self.last_set_time < self.cycle_time * 3:
    self.u = self.u * 0.65
```

but at that point `self.u` still holds the previous cycle's output, and the next
statement overwrites `self.u` entirely with `self.p + self.i + self.d`. The
startup reduction has never had any effect. It is applied to the newly
calculated output, which is what it was always for. This changes PID-SP's
behavior for the first three cycles after a setpoint change and moves its
goldens.

### `controller/pid_base.py`

`_calculate_gains` guards `if ti == 0`, which admits a negative `Ti` and yields a
sign-flipped `ki`. It becomes `if ti <= 0`, fixing every PID variant at once with
no effect on any valid configuration.

### `controller/mpc.py`

`get_status()` returns JSON-safe diagnostics instead of leaking do-mpc objects,
numpy arrays, and the estimator into the MQTT notification payload, which
`controller_state()` publishes today.

`set_output` feeds the applied duty into the state estimator. Line 291 currently
reads `self.estimator.update(self._last_Q, y)` -- the firing rate MPC
*commanded*. Hold mode then clamps the derived ratio to `[u_min, u_max]` and
forces `u_min` outright during a lid-open pause, and nothing tells MPC, so its
estimator spends every lid-open interval believing a command that never reached
the auger. This changes MPC's closed-loop behavior and will move its
characterization goldens; regenerating them alongside the before/after GrillSim
numbers is part of the work.

`AppliedOutput.ratio` is an auger duty in `[0, 1]`; the estimator wants a firing
rate in `Q` units. `mpc_allocator.allocate` is affine and therefore invertible,
so `set_output` recovers the applied firing rate as

```
Q_applied = Q_min + (ratio - u_min) / (u_max - u_min) * (Q_max - Q_min)
```

which is exact for any ratio the allocator produced. A lid-open or manual-off
report arrives as `ratio = 0.0`, below `u_min`, and inverts to a `Q` below
`Q_min` -- that is the honest answer and the estimator gets it unmodified. An
estimator told `Q_min` during a pause it did not take is exactly the defect being
fixed.

That splits today's single `_last_Q` into two fields with different meanings, and
the split is load-bearing:

- `self._applied_Q` -- what the plant actually received. Feeds
  `estimator.update(...)`. Seeded from `Q_min` and overwritten by every
  `set_output` report; when nothing reports (the sync path, a controller-only
  unit test) it falls back to the commanded value, which is today's behavior.
- `self._last_Q` -- the previous *policy move*. Remains the commanded,
  `[Q_min, Q_max]`-clamped firing rate, and stays the `except` fallback at line
  300. Holding the previous move is the intent there; a lid-open zero must not
  silently become the new command.

The net policy's `Q_prev` input takes `clip(self._applied_Q, Q_min, Q_max)`, not
`self._last_Q` -- the sampler logs the value it drove the plant with, so applied
is the semantics the `Q_prev` feature was trained on.

### The net must keep agreeing with the NLP

`controller/mpc_net.py` exists to approximate `self.mpc`, and it is only worth
having while the two produce the same firing rate. That agreement -- not
closed-loop performance against a baseline -- is the binding acceptance
criterion for MPC in Plan A. A net that holds temperature well while diverging
from the NLP has stopped being an approximation and become an untested second
controller.

Nothing in the existing machinery protects that property here.
`NetPolicy.matches_config` guards the *calibration*: model constants, tuning
weights, bounds. All of them are unchanged by this work, so a net that has gone
stale in distribution rather than in calibration passes the check silently and
falls back to nothing. The staleness this change can cause is invisible to the
one mechanism built to catch staleness.

The mechanism is concrete, and it runs through the estimator state rather than
around it. `mpc_model` orders the state `[q0 .. q_{n_delay-1}, T_f, T_c, d]`:
the leading `n_delay` entries are the transport-lag chain, and they carry *past
firing rates*. Whatever `set_output` hands the estimator is therefore not merely
an input -- it becomes part of `x_hat`, and stays there for the length of the
chain. `firing_rate` passes `x_hat` straight into the net, normalized by an
`x_mean`/`x_std` fitted on data in which those entries never left
`[Q_min, Q_max]`, and it clips `set_point_c` to the trained span but does not
clip `u_prev` or the state. A lid-open report inverts to a `Q` below `Q_min`,
lands in the lag chain, and the net extrapolates. The NLP does not: it solves
correctly for any state handed to it. That is precisely where the two come
apart.

The damage is bounded but not prevented. `firing_rate` clamps its *return* to
`[Q_min, Q_max]`, so an extrapolating net yields a wrong demand rather than an
absurd one, and no single bad state can command something outside the actuator's
range. A wrong in-range `Q` is still wrong, and it is exactly what the
disagreement metric is built to detect -- the clamp caps the magnitude of a
divergence, it does not keep the two policies together.

That clamp also makes the lid-open slice of the metric degenerate at the
baseline. During the disturbance both policies sit on the `Q_min` floor -- the
NLP through its box constraint, the net through this clamp -- so they agree
exactly, and the measured lid-open disagreement is `0.0` rather than small.
Zero is not evidence the net tracks the NLP through a disturbance; it is
evidence both hit the same wall. A later comparison must therefore read
`rms_all`/`max_all` as the reference and treat any nonzero lid-open figure as
the net *leaving* the floor, which is the predicted symptom rather than a
regression in its own right.

The same argument says the coverage hole predates this change.
`sample_mpc.py::_episode_span` perturbs the solver's command with dither, clips
to `[qmin, qmax]`, and never pauses the auger -- so no training episode contains
a pause of any kind. Today MPC's estimator is told the *commanded* rate during a
lid-open interval while the grill actually cools, which puts `x_hat` off the
trained distribution just as surely, only in a different direction. Lid-open is
already unmodeled. This change relocates the extrapolation rather than
introducing it.

One part of that reasoning has to be stated more carefully, because a
measurement contradicts its loose form. Sub-`Q_min` entries in the lag chain are
*not* new and never were: the KF/EKF measurement update applies its gain to all
`n` states, the leading `n_delay` among them, so the correction alone drives lag
entries below `Q_min` in roughly 22% of samples on an ordinary 225 F hold, with
a minimum near zero. Any claim that `set_output` first admits sub-`Q_min` values
to `x_hat` is simply false.

The claim that survives is narrower and is the one the remedy rests on. Those
existing excursions are *transient corrections* around a commanded rate that
never left `[Q_min, Q_max]`, and the sampler drives the same estimator, so the
net was trained on them -- they are in distribution. What no training episode
contains is a *sustained* interval during which the commanded rate itself is
zero for the length of the transport-lag chain, because `_episode_span` never
pauses the auger. A lid-open pause fills the chain with zeros and holds it
there; a Kalman correction jitters it. Those are different regions of the input
space, and only the first is unreached.

This sharpens rather than weakens the case for extending the sampler: the fix is
specifically pause intervals, not wider dither, since dither already produces
the excursions the net has seen.

**Measure before spending a thousand episodes.** The first MPC step in Plan A is
a replay experiment, cheap enough to run twice:

1. Run the GrillSim lid-open scenario with `policy: nlp`, logging every
   `(x_hat, u_prev, set_point_c)` triple the policy was asked about along with
   the `Q` the NLP returned.
2. Replay those triples through the net and report `|Q_net - Q_nlp|` -- RMS and
   max, over the whole run and over the lid-open segment separately, so a
   localized blowup is not averaged into nothing.
3. Do this on unmodified code first, then again after `set_output` lands.

Two refinements are not optional, because measurement showed the naive form of
each reports the wrong thing.

**Compare before the clamp.** `firing_rate` forces its answer into
`[Q_min, Q_max]`, and the NLP is bounded by the same box, so comparing the two
returned values hides exactly the failure being looked for -- a net demanding
`-63` against an NLP asking `5.0` reads as perfect agreement. The comparison
runs against the net's unclamped output. The clamped difference is worth
reporting too, as what the plant would actually experience, but it is not the
acceptance quantity.

**Exclude the ignition transient.** The largest pointwise disagreement in a cold
run lands about 45 seconds in, and it is four times anything the controller does
afterwards; including it means comparing startups rather than policies. Metrics
that feed the decision come from a warm window, with the cutoff derived from
when the run settles rather than hard-coded.

**The primary quantity is an excursion count, not an RMS.** Count the net's raw
outputs that fall outside `[Q_min, Q_max]`, whole-run and lid-window, with the
worst magnitude on each side. This is a far better instrument than the RMS it
replaces: it reads exactly zero in the lid window on unmodified code across
every seed, and the same measurement on a run where the net had genuinely gone
out of distribution showed demands of `-63` to `-38`. A hard-zero null with an
enormous signal beats a noisy average -- the lid-window RMS carries a 6%
seed-to-seed spread on 24 samples, and its max carries 14%, so a small real
change is indistinguishable from seed noise, and because the comparison is not
paired a null or negative delta cannot be interpreted at all. Warm-window
`rms_all` on the raw difference stays as the secondary quantity.

The gate is relative, for the same reason the GrillSim bar is no-regression: an
absolute threshold invented here would be a number to tune the test against. The
pre-change measurement is the baseline, and the post-change measurement must not
exceed it. Reporting both is the deliverable either way -- the pre-change number
is the first honest measurement of how far the shipped net drifts from the NLP
during a disturbance, and it is worth having on the record regardless of what it
says about this change.

**If the disagreement grows, retraining alone will not fix it.** Regenerating
from the current sampler produces another net that has never seen a pause. The
remedy is ordered:

1. Extend `_episode_span` to emit pause intervals -- with some probability per
   episode, hold the auger off for a realistic lid-open duration while the
   solver keeps commanding, and feed the estimator and the `Q_prev` feature the
   applied value (below `Q_min`) rather than the command. This closes the
   pre-existing hole and is worth doing on its own merits.
2. Regenerate both artifacts:
   `uv run python tools/regenerate_mpc_net.py --mode both --episodes 500 -w <cores>`.
   500 is already the default; `--mode both` covers the fan-off and fan-on nets,
   which are separate files via `net_path_for`.
3. Re-run the replay experiment. The disagreement must come back to at or below
   the pre-change baseline.
4. Re-run the GrillSim matrix for MPC on the new artifact.

A regenerated net must also clear the tool's own acceptance gate before it is
committed -- the fan ablation, `|bias| <= 0.10 C` and `RMS <= 0.72 C` at a 5 s
control period over 110-288 C -- which the tool prints on completion. Sampling
1000 episodes across two modes is the most expensive step in either plan; it
runs in the main checkout with `-w` set to the real core count, never in a
subagent worktree.

If the net still cannot be brought back into agreement, the fallback is to clamp
`_applied_Q` into `[Q_min, Q_max]` before it reaches the estimator. That keeps
the lag chain inside the trained span by construction and still fixes the large
majority of the defect -- during a lid-open pause the estimator would be told
`Q_min` instead of the `Q_max` it is told today, which is nearly the whole span
of the error -- at the cost of an estimator that is approximately rather than
exactly honest. It is the compromise, not the plan, and it needs the measurement
above to justify reaching for it.

### `controller/controllers.json`

The `pid_sp` entry drops the `tau` and `theta` options -- with identification
online, a user-supplied `tau=115` is not merely unused but outside the trusted
band of 300-20000 s -- and gains

```json
"dependencies": {"modules": ["numpy"]}
```

with no `extra`. numpy is not optional the way `do-mpc` is; it is already in the
base install transitively via `scikit-learn` and `scikit-fuzzy`. The same change
adds `numpy` to the top-level `dependencies` list in `pyproject.toml`, making
that transitive reality explicit. With `extra` absent,
`common/controller_deps.py` reports "PiFire has no automatic install for it" --
correct, because numpy missing means a broken install rather than a missing
opt-in. PID-SP needs no `requires_modules(config)` hook: MPC has one because its
need genuinely varies by config, whereas PID-SP always needs numpy, so the
static manifest list is the whole truth.

### `tests/fakes/runner.py`

`FakeControllerRunner` grows the three new forwards. Without them every existing
Hold golden test raises `AttributeError`.

## Confidence and Safety

Prediction stays inactive until a restored or newly trusted model exists. New
identification uses profile-independent gates:

| Gate | Threshold |
| --- | ---: |
| Accepted observation time | at least 3600 s |
| Accepted observations | at least 240 |
| Applied-duty standard deviation | at least 0.05 |
| Sustained duty transition | change of at least 0.05 held for 60 s |
| Observed temperature span | at least 15 F, or Celsius equivalent |
| Process gain | 50-2000 F per unit duty |
| Time constant | 300-20000 s |
| Gain relative standard error | at most 20% |
| Tau relative standard error | at most 25% |
| Winning delay residual | at least 10% below the runner-up |
| Confirmation window | 20 accepted observations |
| Confirmation stability | gain within 5%, tau within 7.5%, unchanged delay candidate |

A delay candidate is never promoted merely for having the lowest residual. If
candidates are statistically indistinguishable, PID-SP stays measured-temperature
PID.

After initial trust, a candidate is a material revision only when gain or tau
moves at least 5% or delay moves at least 5 s. A passing revision blends into
gain and tau with factor 0.1; delay changes only after a full confirmation
window. Estimates outside the confirmation limits are rejected.

All controller output continues through the existing `[u_min, u_max]` clamps.
The predictor cannot bypass actuator bounds.

### Consequence: PID-SP is plain PID at first

Identification is passive by design -- no excitation is injected -- so on a
fresh install the gates cannot clear until roughly an hour of accepted
observations have accumulated, and until then PID-SP is term-for-term identical
to `pid_ac`. A 6-12 hour low-and-slow cook gets the benefit for most of its
length; a one-hour hot-and-fast cook never does. From the second cook onward,
persistence means a trusted model is active from the first tick.

This is accepted rather than worked around. Activation time is a **reported
metric**, not a threshold to tune: if the gates do not clear on a realistic cook,
that is a finding about the design, not a reason to lower them. Seeding the
predictor from the existing `tau`/`theta` options was rejected because the
shipped defaults sit outside the design's own trusted band and there is no `K`
option at all, so a seed would drive control from a model the design would
refuse to trust -- exactly the failure the gates exist to prevent.

## Verification

Two bars, separated because only one of them has a knowable truth.

### Unit tests

The identifier runs against a synthetic, exactly-FOPDT plant where a true answer
exists, so the tolerances are absolute: gain within 10%, tau within 15%, delay
within 5 s. Also proved:

- no promotion under constant duty, insufficient temperature span, or ambiguous
  delay residuals;
- rejection of non-finite, negative-gain, unstable, and out-of-bound estimates;
- a paused interval creates no cross-gap slope observation;
- fixed memory and bounded duty-history retention.

**A scalar oracle guards the vectorization.** The batched bank and the
cumulative-integral duty lookup are the two places where a wrong `einsum`
subscript, a transposed axis, or an off-by-one in `searchsorted` produces
plausible numbers instead of an error. The test module therefore carries its own
deliberately naive reference: a plain Python loop running one 3x3 RLS update per
candidate, and a direct windowed scan that averages duty over
`[t_(k-1) - theta_j, t_k - theta_j]` segment by segment. Both are written from
the equations in this document rather than adapted from the production code -- a
reference derived by refactoring the implementation proves only that it agrees
with itself.

The two are driven with one randomized observation sequence and compared on
`Theta`, `P`, and `resid_ew` across all 25 candidates at a tight float
tolerance. The sequence must exercise what a tidy fixture would miss: variable
`dt`, delay windows straddling several duty segments, windows reaching back
before the start of retained history, and a duty that is constant over some
stretches and stepping over others.

The oracle is itself verified by negative control -- perturbing a constant in the
production path (the forgetting factor, a covariance index, one `einsum`
subscript) must make the comparison fail. A parity test that passes against a
broken implementation is worse than no test.

Predictor tests prove exact undelayed and delayed first-order trajectories,
segmented integration at delay boundaries between controller samples, the
measured-output Smith equation, equal-state initialization with zero initial
correction, and safe fallback on invalid model state.

Plumbing tests prove the `OutputSource` precedence table, that both runners
forward all four capabilities, that the threaded runner replays applied outputs
before `update()`, that `controller_state()` prefers `get_status()`, that each
Hold call site reports the expected `AppliedOutput`, and that the store
round-trips a snapshot, rejects a malformed envelope, and skips a non-advancing
revision. The store's tests use two unrelated snapshot shapes to prove it is
genuinely model-agnostic.

### Integration on GrillSim

`controller/grill_sim.py` is the plant of record. It is a two-state Celsius model
(firepot `C_f=9`, chamber `C_c=300`) with radiative loss and fan-dependent
coefficients -- deliberately *not* FOPDT. Its effective parameters work out to
`tau` around 635 s and `theta` around 25-30 s (20 s transport, plus firepot lag,
plus a 4.5 s probe), with local gain around 761 F/duty at 180 C but around
990 F/duty at 100 C. All three sit inside the trusted bands, but gain nearly
halves across the operating range because of the radiative term.

The baseline is captured **before any code change**, both controllers, 5 seeds:

| Class | Scenarios |
| --- | --- |
| Steady hold | 225, 350, 450 F, at least 3 h each -- anything shorter cannot clear the 3600 s gate |
| Step | 225 to 275 F |
| Capability | 600 F (`H=420` tops out near 688 F, so this is reachable) |
| Disturbance | lid-open at 225 F, exercising the non-`CONTROLLER` sources |
| Persistence | cook 1, snapshot, fresh controller, restore, cook 2 |

Metrics per run: integrated absolute error, percentage within plus/minus 5 F,
directional overshoot, settling time, mean applied duty, standard deviation, and
-- for PID-SP -- identifier activation time and final `K`, `tau`, `theta`.
Settling is the first time temperature enters plus/minus 5 F of setpoint and
stays there for the remainder of the run.

The bar is **no regression**, reported as measured. No target improvement is
set, because a target invites tuning the test.

Identifier accuracy on GrillSim is judged on plausibility only: parameters stay
inside physical bounds, promote once and stay promoted rather than flapping, and
never trip the implausible-residual fallback. Asserting a percentage against a
hand-derived linearization of a plant that has no true FOPDT answer would test
the arithmetic in this document, not the code.

Both controllers run the full matrix. GrillSim runs one controller per scenario,
so a moved metric is attributable without ambiguity even though this change
alters both.

MPC carries a second acceptance criterion the other controllers do not: the net
policy must keep agreeing with the NLP it approximates, measured by the replay
experiment in the `controller/mpc.py` section and gated against the pre-change
disagreement. Every MPC scenario in the matrix runs under `policy: net`; the
lid-open scenario additionally runs under `policy: nlp` to produce the triples
the replay compares against.

Closed-loop trajectories from two separate runs diverge on their own and cannot
settle this question, which is why the gate is pointwise policy disagreement
rather than a net-run-versus-nlp-run trajectory comparison. The matrix answers
whether MPC still controls well; the replay answers whether the net is still the
same policy.

A regenerated artifact is a commit of its own, carrying the acceptance-gate
output, both replay numbers, and both matrices in its message, so the reason for
a binary changing is recoverable later. If MPC cannot be brought back into
agreement on a freshly trained net, that is evidence against the applied-duty
change itself and goes back to the user rather than being tuned around.

## Failure Handling

- Missing or invalid persisted state logs a warning and starts
  measured-temperature PID with fresh identification.
- Identifier numerical failure resets only the affected candidate; repeated bank
  failure resets adaptive state without interrupting PID control.
- Predictor numerical failure disables prediction and reinitializes model
  dynamics; it does not stop the work cycle.
- Persistence failure logs an error and retains the in-memory trusted model.
- Controllers that do not override the four capabilities are unaffected.

## Constraints

- numpy is permitted and declared; the rest of the identifier and predictor is
  standard library.
- Per-update work and memory are bounded and fixed-size.
- No deliberate excitation enters production auger commands.
- No raw controller output enters the command model once the applied value is
  available.
- No compatibility shim retains the rate-of-change predictor.
- Other PID controllers' equations are unchanged, apart from the shared
  `ti <= 0` guard.

## Deferred

`2026-07-25-high-temperature-transition-tuning-design.md` describes a rate-gated
integral-release state that recovers a 450 F overshoot the upstream Smith change
introduced. It is deferred, not rejected: its fix is independent of the
predictor, but every acceptance number in it is calibrated to a simulator we do
not run, and the regression it addresses has not been observed on GrillSim. If
the 450 F cell of the matrix reproduces it, that tuning gets its own plan with
thresholds derived here.

The `requested` field on `AppliedOutput` is what that tuning would key off, so
the data it needs is being recorded from the start.
