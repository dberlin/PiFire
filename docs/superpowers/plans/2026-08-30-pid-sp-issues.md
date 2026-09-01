# Remaining PID-SP Learning Defects Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the six PID-SP learning and diagnostics defects that still affect the current branch while preserving fail-closed activation, exact-frame evidence, restart safety, and measured-temperature fallback.

**Architecture:** Make the completed `FrameObservation` the single live PID-SP learning input and adapt the existing `LearningTrajectoryFrame` corpus to the same immutable interval type for teardown fitting. Replace branch-ordered point-delay promotion with bounded episode evidence, common rolling validation, explicit model-form selection, and typed outcomes. Reuse the current trajectory, evidence, lifecycle, and persistence infrastructure rather than creating a second cook-history store.

**Tech Stack:** Python 3.11+, NumPy, Pydantic v2 contracts, SQLite model/evidence persistence, pytest, PiFire Hold runtime, GrillSim, MAKGrillSim, Jujutsu.

**Spec:** `/home/dannyb/pid-sp-issues.zip:pid-sp-defects.md`

## Global Constraints

- PID-SP remains fail-closed: no model may affect output until physical, uncertainty, rolling-prediction, cross-episode stability, confirmation, durable-persistence, and restore checks pass.
- Measured temperature remains the fallback whenever no trusted model exists, a predictor disables itself, a checkpoint is rejected, fitting fails, evidence persistence fails, or a model becomes untrustworthy.
- Online and teardown fitting consume the same exact completed intervals. A completed-interval duty must never be represented as a future step beginning at the interval end.
- `LearningTrajectoryFrame` and the existing trajectory repository remain the durable cook-history authority; do not add a parallel PID-SP cook store.
- The delay grid remains internally 5 seconds for integration. Selection is by an uncertainty basin, not by a neighboring point winner.
- Search starts at 0–300 seconds, expands by 150 seconds while the accepted basin touches the upper edge, and stops at the hard safety/work bound of 900 seconds. A basin touching 900 seconds is rejected as `delay-range-exhausted`.
- A delay basin contains every physically valid candidate whose common-validation loss is within 5% of the best candidate of that model form.
- A basin may authorize a predictor only when its moving-block 90% interval is interior, no wider than 60 seconds, and supported by at least two independent completed excitation episodes.
- Compare constrained IPDT, constrained FOPDT, and stable two-real-pole SOPDT on identical rolling-origin folds. Select the least complex form whose mean loss is within one standard error of the lowest mean loss.
- FOPDT bounds remain `GAIN_MIN..GAIN_MAX` and `TAU_MIN..TAU_MAX`. SOPDT must have two finite positive time constants within those physical time bounds; unstable, complex, repeated-degenerate, or effectively integrator-like poles are rejected before comparison.
- `enable_identification` defaults to `true` for PID-SP. An explicit persisted `false` remains authoritative.
- End-of-cook fitting may create only an `accepted-next-cook` checkpoint. It may not authorize output during teardown.
- Use Jujutsu commands only for VCS operations. Never use raw Git commands in this repository.

---

## Current-Branch Issue Ledger

| Archived item | Status | Decisive current evidence | Plan coverage |
|---|---|---|---|
| 1. Learn This Grill default and controller-capability mismatch | **partially applies** | `controller/controllers.json:64-139` still has no PID-SP `enable_identification`; `HoldLearningRuntime._identification_enabled()` still requires explicit `true`; `controller.pid_sp.Controller` still has no `refit_from_cook`. The branch now has a cumulative trajectory and generic stop-fit/persistence machinery, so the archived proposal to invent a complete cook store is stale. | Task 4 |
| 2. Every PID-SP frame becomes `runner-no-observation-outcome` | **still applies** | `controller.runtime.runner._mpc_learning_core_for()` admits only MPC; `SyncControllerRunner.observe_frame()` emits a terminal drop when that core is absent; `controller.pid_sp.Controller` has no `observe_frame`. | Task 1 |
| 3. FOPDT-first model selection | **still applies** | `FOPDTIdentifier._evaluate()` promotes the FOPDT bank first and evaluates IPDT only when no FOPDT winner exists. Restore still defaults a missing `form` to FOPDT. No common-fold structure selector exists. | Task 3 |
| 4. Residual diagnostics use a different candidate set | **still applies** | `FOPDTIdentifier.status()` orders raw `_bank.resid_ew`, while `promote()` uses a gated mask. Status does not expose authoritative IPDT competition or explicit selection blockers. | Task 3 |
| 5. Realized-duty intervals are timestamped as future duty | **partially applies** | `FramedPulseRuntime.report_feedback()` and terminal completion still create end-stamped `AppliedOutput`, and `DutyHistory.record()` interprets its timestamp as a forward step. However, current `FrameObservation` and `LearningTrajectoryFrame` already carry exact start, end, and delivered-duty fields; the archived request for a new interval ledger is superseded by those contracts. | Task 1 |
| 6. Delay range and point-winner logic are not identifiable evidence | **partially applies** | `DELAYS` remains 0–120 seconds; `promote()` still compares adjacent points; exponentially weighted residual banks still allow later flat operation to wash out transition evidence. The current trajectory repository already provides bounded multi-cook retention, exact frames, compatibility partitions, and quarantine, so those portions of the archived proposal are implemented and must be reused. | Tasks 2–4 |
| 7. Duplicate `_holdable()` | **resolved** | Current `FOPDTIdentifier` has one `_holdable()` definition at `controller/fopdt_identifier.py:809`; LSP exposes only that symbol. | No change |

### Stale archive assumptions

- Source line numbers in the archive predate the current cumulative-learning work. Resolve symbols, not archived line numbers.
- PID-SP no longer needs a new durable cook-record subsystem. `common.learning_trajectory.LearningTrajectoryFrame` already owns exact interval data and `LearningTrajectorySegment` already owns compatibility-bound, retained, finalized cook segments.
- Generic Hold teardown fitting, evidence persistence, and cold-restart recovery now exist. The missing work is a PID-SP fitter/lifecycle implementation and capability routing, not another runtime-wide lifecycle.
- The duplicate `_holdable()` defect has already been removed and must not receive a compatibility edit or regression-only production change.

---

### Task 1: Route exact completed frames through PID-SP learning

**Files:**
- Create: `controller/pid_sp_observation.py`
- Modify: `controller/fopdt_identifier.py`
- Modify: `controller/smith_predictor.py`
- Modify: `controller/pid_sp.py`
- Modify: `controller/runtime/runner.py`
- Modify: `controller/runtime/modes/hold_learning.py`
- Test: `tests/unit/controller/test_fopdt_identifier.py`
- Test: `tests/unit/controller/test_pid_sp.py`
- Test: `tests/unit/runtime/test_sync_runner.py`
- Test: `tests/unit/runtime/test_hold_learning_runtime.py`
- Test: `tests/e2e/test_smoke_hold_learning_trajectory.py`

**Interfaces:**
- Consumes: exact `FrameObservation` fields `frame_start_s`, `frame_end_s`, `temp_c`, `delivered_on_s`, `realized_auger_duty`, and `continuous`, plus `LearningTrajectoryFrame` exact interval semantics.
- Produces: `PidSpInterval`, `PidSpObservationOutcome`, `FOPDTIdentifier.observe_interval()`, `SmithPredictor.record_interval()`, and a controller-neutral runner learning capability.

- [ ] **Step 1: Add failing interval and evidence tests**

Add tests that establish the current defects without relying on source text:

```python
def test_interval_duty_belongs_to_the_completed_interval():
    identifier = FOPDTIdentifier(delays=np.array([0.0, 20.0]))
    identifier.observe_interval(0.0, 20.0, 0.25, 100.0)
    identifier.observe_interval(20.0, 40.0, 0.75, 102.0)

    assert identifier.input_average(0.0, 20.0, delay_s=0.0) == pytest.approx(0.25)
    assert identifier.input_average(20.0, 40.0, delay_s=0.0) == pytest.approx(0.75)


def test_pid_sp_completed_frame_returns_typed_observation_outcome(pid_sp_controller, frame_observation):
    outcome = pid_sp_controller.observe_frame(frame_observation)

    assert outcome["controller"] == "pid_sp"
    assert outcome["eligible"] is True
    assert outcome["rejection_reasons"] == ()
    assert outcome["effective_updates"] == 1
```

Add a runtime regression that submits one normal PID-SP completed frame, drains outcomes, and asserts one envelope, no terminal drop, no `runner-no-observation-outcome`, and one `MODEL_OBSERVATION` trace record. Add companion cases for a discontinuity and queue eviction; those must still emit exact recorder gaps.

Run the four new tests. Expected before implementation: missing methods or a PID-SP terminal drop.

- [ ] **Step 2: Define immutable PID-SP interval and outcome contracts**

Create `controller/pid_sp_observation.py` with these public shapes:

```python
@dataclass(frozen=True, slots=True)
class PidSpInterval:
    start_s: float
    end_s: float
    temperature_f: float
    realized_duty: float
    continuous: bool
    observation_sequence: int
    role_generation: int


class PidSpObservationDecision(StrEnum):
    ACCEPTED = "accepted"
    INVALID_PROBE = "invalid-probe"
    NON_CONTROLLER_OUTPUT = "non-controller-output"
    DISCONTINUOUS = "discontinuous"
    INHIBITED = "inhibited"


@dataclass(frozen=True, slots=True)
class PidSpObservationOutcome:
    decision: PidSpObservationDecision
    effective_updates: int
    duty_variance: float
    duty_levels: int
    role_generation: int
    model_digest: str | None

    def as_runner_outcome(self) -> dict[str, object]:
        accepted = self.decision is PidSpObservationDecision.ACCEPTED
        return {
            "controller": "pid_sp",
            "eligible": accepted,
            "rejection_reasons": () if accepted else (self.decision.value,),
            "input_variance": self.duty_variance,
            "input_levels": self.duty_levels,
            "effective_updates": self.effective_updates,
            "role_generation": self.role_generation,
            "model_digest": self.model_digest,
        }
```

Validate finite positive intervals, duty in `[0, 1]`, unique non-negative identities, and Fahrenheit conversion at the `FrameObservation` adapter. `as_runner_outcome()` must return the existing generic keys consumed by `HoldLearningRuntime._parse_outcome()` plus `controller: "pid_sp"`; rejected physical frames return a typed decision with zero updates, not `None`.

- [ ] **Step 3: Replace future-step input with exact intervals**

Add `DutyHistory.record_interval(start_s, end_s, ratio)` and retain `record(timestamp, ratio)` only where commanded future steps are genuinely represented. `record_interval` must reject overlap, preserve gaps, and expose exact integration only across fully covered windows.

Add public methods `FOPDTIdentifier.observe_interval(self, start_s: float, end_s: float, realized_duty: float, temperature_f: float) -> PidSpObservationOutcome` and `SmithPredictor.record_interval(self, start_s: float, end_s: float, realized_duty: float) -> None`.

The identifier records the duty on `[start_s, end_s)`, then evaluates the end temperature. The predictor integrates the same interval. Remove PID-SP identifier input from `Controller.set_output()` and temperature learning from `Controller.update()`; those legacy calls must no longer create a second observation path. Nonterminal progress feedback remains control telemetry only.

- [ ] **Step 4: Generalize runner capability detection**

Replace `_mpc_learning_core_for()` with a runtime-checkable frame-learning protocol requiring `observe_frame()` and `observation_failure()`. Keep MPC-specific estimator seeding and activation behind their existing MPC protocol. Implement the frame capability on `controller.pid_sp.Controller`; do not identify controllers by controller-name strings.

Update synchronous and threaded runner tests to prove both MPC and PID-SP return envelopes and unsupported controllers alone return `runner-no-observation-outcome`.

- [ ] **Step 5: Verify exact evidence behavior**

Run:

```bash
uv run pytest -q \
  tests/unit/controller/test_fopdt_identifier.py \
  tests/unit/controller/test_pid_sp.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_hold_learning_runtime.py \
  tests/e2e/test_smoke_hold_learning_trajectory.py
```

Expected: normal PID-SP frames produce typed model observations; real drops retain exact gap reasons; no test permits duplicate identifier updates.

- [ ] **Step 6: Commit the exact-frame cutover**

```bash
jj describe -m "fix(pid_sp): learn from exact completed frames"
jj new -m "feat(pid_sp): retain delay episode evidence"
```

---

### Task 2: Retain excitation episodes and select delay basins

**Files:**
- Create: `controller/pid_sp_delay_evidence.py`
- Modify: `controller/fopdt_identifier.py`
- Modify: `controller/pid_sp_learning.py`
- Test: `tests/unit/controller/test_pid_sp_delay_evidence.py`
- Test: `tests/unit/controller/test_fopdt_identifier.py`
- Create: `tests/fixtures/pid_sp/2026-08-28-intervals.json`

**Interfaces:**
- Consumes: accepted `PidSpInterval` values from Task 1.
- Produces: immutable `ExcitationEpisode`, `DelayProfile`, `DelayBasin`, and typed delay blockers used by Task 3.

- [ ] **Step 1: Add failing episode and basin tests**

Cover these observable contracts with complete fixtures and assertions:

- `test_flat_rows_do_not_change_a_completed_episode_profile`: freeze the completed profile, append 100 constant-duty intervals, and assert byte-identical profile output.
- `test_adjacent_five_second_points_form_one_basin`: give 185–225-second candidates losses within 5% of the minimum and assert one basin with those bounds.
- `test_upper_edge_expands_from_300_to_450_seconds`: place the accepted basin on the 300-second edge and assert the next evaluated bound is 450 seconds.
- `test_basin_touching_900_seconds_fails_closed`: keep the basin on the final edge and assert blocker `delay-range-exhausted`.
- `test_two_independent_episodes_are_required_for_authorization`: compare identical profiles from one and two episode IDs; only the latter may clear episode count.
- `test_august_28_fixture_reports_provisional_190_225_basin`: load the pinned sanitized fixture and assert the common-support raw basin bounds, moving-block confidence bounds, and no trusted model.

Extract only sanitized exact intervals and temperatures needed by the last test from the archived cookfile. Commit no settings, identity, network, notification, or unrelated diagnostic data. Pin the fixture SHA-256 in the test.

Run these tests before implementation. Expected: imports fail because the episode/basin types do not exist.

- [ ] **Step 2: Implement bounded episode ownership**

Create immutable `ExcitationEpisode` with fields `episode_id: str`, `intervals: tuple[PidSpInterval, ...]`, `transition_at_s: float`, `duty_before: float`, `duty_after: float`, and `terminal_reason: str`. Create `EpisodeAccumulator.observe(interval: PidSpInterval) -> ExcitationEpisode | None` and `EpisodeAccumulator.completed() -> tuple[ExcitationEpisode, ...]`.

Open an episode only after a realized-duty change of at least `MIN_TRANSITION` persists for `MIN_TRANSITION_HOLD`. Close it after the response window, a discontinuity, inhibition, mode boundary, or another sustained transition. Bound memory to the same retained frames/segments already admitted by the trajectory repository; do not let later steady rows mutate a completed episode.

- [ ] **Step 3: Implement adaptive delay profiles**

Create a pure `profile_delays(episodes, model_form, max_delay_s)` function. It must use identical accepted intervals for every delay, rolling-origin episode folds, and moving-block resampling. Expand `max_delay_s` from 300 by 150 while the 5% basin touches the edge; stop and return `delay-range-exhausted` at 900.

`DelayBasin` must expose `lower_s`, `upper_s`, `representative_s`, `confidence_lower_s`, `confidence_upper_s`, `episode_count`, `interior`, and exact blockers. The representative delay is the lowest-validation-loss grid point, but authorization consumes basin width and stability rather than its neighbor margin.

- [ ] **Step 4: Remove point-winner authority**

Delete PID-SP activation authority from `promote(resid_ew, mask)` and from the FOPDT-first `_evaluate()` branch. It may remain only as an internal diagnostic helper if no caller treats its winner as authoritative. Remove the 0–120 production `DELAYS` cap from identifier authorization and make predictor history retention derive from an accepted model's basin upper bound plus `HISTORY_MARGIN_S`.

- [ ] **Step 5: Expose episode and basin diagnostics**

Extend `build_pid_sp_live_learning()` with completed episode count, search bound, profile form, basin bounds, confidence bounds, and one of: `insufficient-excitation-episodes`, `insufficient-confidence-evidence`, `delay-basin-too-wide`, `delay-basin-edge`, `delay-range-exhausted`, or `delay-basin-stable`.

- [ ] **Step 6: Verify and commit delay evidence**

Run:

```bash
uv run pytest -q \
  tests/unit/controller/test_pid_sp_delay_evidence.py \
  tests/unit/controller/test_fopdt_identifier.py \
  tests/unit/controller/test_pid_sp_learning.py
```

Expected: the archived fixture reports a common-support raw basin of 190–225 seconds, a moving-block confidence interval of 125–225 seconds, and remains untrusted; appending flat intervals leaves its completed profile unchanged.

```bash
jj describe -m "feat(pid_sp): retain bounded delay basin evidence"
jj new -m "feat(pid_sp): select validated model structure"
```

---

### Task 3: Select model form on common validation evidence

**Files:**
- Create: `controller/pid_sp_model_selection.py`
- Modify: `controller/fopdt_identifier.py`
- Modify: `controller/smith_predictor.py`
- Modify: `controller/pid_sp_learning.py`
- Modify: `common/controller_model_state.py`
- Test: `tests/unit/controller/test_pid_sp_model_selection.py`
- Test: `tests/unit/controller/test_fopdt_identifier.py`
- Test: `tests/unit/controller/test_smith_predictor.py`
- Test: `tests/unit/controller/test_pid_sp_learning.py`

**Interfaces:**
- Consumes: completed excitation episodes and delay profiles from Task 2.
- Produces: `ModelForm` (`ipdt`, `fopdt`, `sopdt`), `ModelFit`, `ModelComparison`, `SelectedPidSpModel`, and authoritative gated diagnostics.

- [ ] **Step 1: Add failing common-selector tests**

Add deterministic, fully asserted fixtures proving:

- a nonphysical FOPDT cannot preempt a physical IPDT;
- changing form iteration order cannot change the selected model;
- an SOPDT one-step residual win loses when its common rolling error is not better;
- the simplest form within one standard error wins;
- the reported margin equals the gated profile consumed by selection; and
- a form-less checkpoint cannot authorize predictor output.

Also assert every rejected form retains its physical, uncertainty, basin, stability, and validation blockers in the public comparison.

- [ ] **Step 2: Implement immutable fit and comparison types**

Define typed parameter variants for IPDT (`K_i`, `c0`, `theta`), FOPDT (`K`, `tau`, `theta`), and SOPDT (`K`, `tau_1`, `tau_2`, `theta`). `SelectedPidSpModel` must include schema version, form, parameters, delay basin, fold losses, standard error, episode identities, fit-corpus digest, configuration digest, and model digest.

Reject a form before comparison when any physical bound, covariance, delay-basin, pole-stability, or episode-stability gate fails. Never encode a rejected parameter as a clamped accepted parameter.

- [ ] **Step 3: Fit all forms on identical folds**

Use NumPy-only bounded/grid linear algebra already available in the project. Fit every form against the same episode folds and compute one-step plus 3-, 15-, 45-, 90-, and 180-second rolling predictions. Compute each form's scalar loss from those horizons with the same weights; compute uncertainty across folds. SOPDT may participate only when both discrete poles map to finite positive real time constants inside physical bounds.

- [ ] **Step 4: Implement one-standard-error selection**

Sort eligible forms by complexity `ipdt < fopdt < sopdt`. Find the lowest mean validation loss and its standard error; select the first form whose mean is no greater than `best_mean + best_standard_error`. Require parameter and basin stability across two independent episodes before starting the existing 20-decision confirmation window.

Delete the FOPDT-first/IPDT-fallback branch. Confirmation compares complete typed models and resets on material parameter, form, basin, corpus, or configuration changes.

- [ ] **Step 5: Make checkpoints typed and fail closed**

Increment the PID-SP checkpoint schema. Reject form-less checkpoints and unknown forms in `common.controller_model_state` and `Controller.restore_model()`. Restore validates the full typed evidence envelope before `SmithPredictor.trust()`; a rejected checkpoint leaves measured-temperature control active and emits a structured restore rejection.

Extend `SmithPredictor` with stable SOPDT propagation. Retain its temperature/residual safety envelope for every form.

- [ ] **Step 6: Align public diagnostics with selection**

Replace raw-bank `best_residual`/`runner_up_residual` authority with per-form gated best, gated runner-up, basin, rolling loss, standard error, comparison threshold, selected form, confirmation progress, and exact blocker. If raw residuals remain, name them `raw_*` and document them as non-authoritative.

- [ ] **Step 7: Verify and commit model selection**

Run:

```bash
uv run pytest -q \
  tests/unit/controller/test_pid_sp_model_selection.py \
  tests/unit/controller/test_fopdt_identifier.py \
  tests/unit/controller/test_smith_predictor.py \
  tests/unit/controller/test_pid_sp_learning.py
```

Expected: input ordering cannot change selection; the August fixture prefers constrained IPDT but remains blocked by delay uncertainty; no form-less checkpoint reaches predictor output.

```bash
jj describe -m "feat(pid_sp): select models on common validation evidence"
jj new -m "feat(pid_sp): fit cumulative cook evidence"
```

---

### Task 4: Fit cumulative PID-SP evidence at cook teardown

**Files:**
- Modify: `controller/controllers.json`
- Modify: `common/settings_schema.py`
- Modify: `common/settings_migration.py`
- Create: `controller/model_learning/pid_sp_fitting.py`
- Modify: `controller/runtime/model_lifecycle.py`
- Modify: `controller/runtime/runner.py`
- Modify: `controller/runtime/modes/hold_learning.py`
- Modify: `controller/pid_sp.py`
- Modify: `controller/pid_sp_learning.py`
- Test: `tests/unit/controller/test_controller_catalog.py`
- Test: `tests/unit/common/test_settings_migration_mpc_identification.py`
- Test: `tests/unit/runtime/test_hold_refit_trigger.py`
- Test: `tests/unit/runtime/test_model_lifecycle.py`
- Test: `tests/unit/runtime/test_hold_model_persistence.py`
- Test: `tests/unit/runtime/test_smoke_learning_trajectory.py`

**Interfaces:**
- Consumes: finalized compatible `LearningTrajectorySegment` values, Task 3 selector, existing `FitRequest`/`FitResult`, persistence worker, and stop barrier.
- Produces: PID-SP `fit_corpus()` lifecycle support and typed accepted/rejected teardown evidence.

- [ ] **Step 1: Add failing settings and teardown tests**

Add complete catalog/schema/migration and lifecycle tests proving:

- fresh PID-SP settings contain `enable_identification: true`;
- a missing PID-SP key migrates to `true`;
- an explicit persisted `false` survives migration;
- disabled teardown records `disabled` and submits no fit;
- enabled teardown submits the exact finalized fit-corpus identity;
- a rejected fit preserves the incumbent; and
- an accepted fit is unavailable to the ending cook and restores on the next cook.

The acceptance test must distinguish `disabled`, `insufficient`, `rejected`, `failed`, `accepted-next-cook`, and `checkpoint-failure` outcomes.

- [ ] **Step 2: Materialize the PID-SP setting**

Add the PID-SP catalog option:

```json
{
  "option_name": "enable_identification",
  "option_friendly_name": "Learn This Grill",
  "option_description": "Fit validated PID-SP models from complete retained cook evidence for later cooks. [Default=true]",
  "option_type": "bool",
  "option_default": true,
  "option_min": null,
  "option_max": null,
  "option_step": null,
  "hidden": false
}
```

Add a shape migration that inserts `true` only when the PID-SP key is absent. Do not reuse or change the retired MPC identification-choice migration. Preserve explicit `false`.

- [ ] **Step 3: Implement the cumulative PID-SP fitter**

Create `fit_pid_sp_corpus(request, segments, configuration) -> PidSpFitResult`. Convert eligible exact `LearningTrajectoryFrame` values to Task 1 intervals, preserve segment/episode identities, and invoke Task 3 selection on the complete compatible retained corpus. Never replay partial, discontinuous, uncertain-delivery, quarantined, incompatible-digest, or superseded frames.

Return terminal outcomes with exact reasons. `insufficient-delay-identifiability` retains usable episode evidence in the trajectory corpus but creates no checkpoint.

- [ ] **Step 4: Generalize lifecycle fitting without weakening MPC**

Make `ModelLifecycleRunner` dispatch a controller-owned corpus fitter while retaining shared request identity, stale-result checks, worker serialization, persistence ordering, and stop barriers. PID-SP and MPC must use distinct model codecs and validation; neither may deserialize the other's checkpoint.

Implement PID-SP `fit_corpus()`/`refit_from_cook()` as the lifecycle entry point expected by Hold. The stop barrier must finalize the current segment before snapshotting the fit corpus. Teardown success queues an `accepted-next-cook` checkpoint and does not call live `predictor.trust()`.

- [ ] **Step 5: Persist and restore with attributable evidence**

Persist candidate comparison, rejection, confirmation, checkpoint, and restore records through the existing `ModelEvidenceRecord` channels. Bind every record to cook ID, fit request, fit-corpus digest, controller, selected form, candidate digest, parent incumbent, and role generation.

On the next PID-SP setup, restore the accepted checkpoint before output authorization. Any absent, malformed, stale, incompatible, or persistence-failed checkpoint leaves measured-temperature fallback active and retains the incumbent where one exists.

- [ ] **Step 6: Verify and commit cumulative fitting**

Run:

```bash
uv run pytest -q \
  tests/unit/controller/test_controller_catalog.py \
  tests/unit/common/test_settings_migration_mpc_identification.py \
  tests/unit/runtime/test_hold_refit_trigger.py \
  tests/unit/runtime/test_model_lifecycle.py \
  tests/unit/runtime/test_hold_model_persistence.py \
  tests/unit/runtime/test_smoke_learning_trajectory.py
```

Expected: every teardown outcome is explicit; accepted models become available only to a later cook; rejected/failed fits never replace the incumbent.

```bash
jj describe -m "feat(pid_sp): fit cumulative cook evidence at teardown"
jj new -m "test(pid_sp): verify real-cook learning safety"
```

---

### Task 5: Prove real-cook diagnostics and closed-loop safety

**Files:**
- Modify: `tests/e2e/test_smoke_hold_learning_trajectory.py`
- Create: `tests/e2e/test_pid_sp_real_cook_learning.py`
- Modify: `tests/fixtures/pid_sp/2026-08-28-intervals.json`
- Verify: `docs/superpowers/experiments/controller_matrix.py`

**Interfaces:**
- Consumes: Tasks 1–4 production path, sanitized August 28 intervals, GrillSim, and MAKGrillSim.
- Produces: permanent end-to-end evidence for exact observations, provisional basin retention, teardown outcomes, cold restore, and safe model use.

- [ ] **Step 1: Add the permanent raw-evidence E2E**

Replay the sanitized August 28 intervals through production Hold frame completion, runner outcome reconciliation, trajectory persistence, stop finalization, PID-SP corpus fitting, evidence persistence, and cold restart. Assert:

- every normal completed frame has one typed PID-SP observation and no `runner-no-observation-outcome` gap;
- rejected/gapped frames retain exact reasons;
- the fit selects constrained IPDT over nonphysical FOPDT and one-step-only SOPDT;
- the provisional basin is 185–225 seconds;
- the model remains untrusted with `insufficient-delay-identifiability`;
- the exact evidence survives cold restart for a later independent cook.

- [ ] **Step 2: Add independent-episode narrowing and next-cook restore E2E**

Append a deterministic second compatible cook whose independent excitation narrows the basin below 60 seconds and passes common rolling validation. Assert a teardown `accepted-next-cook` checkpoint, no same-cook actuation authorization, durable persistence, next-cook restore before first output, and attributable evidence identities.

Add mismatch arms for incompatible grill/physics digests, a nonphysical fit, and a worse closed-loop challenger. Each must preserve the incumbent or measured-temperature fallback.

- [ ] **Step 3: Run closed-loop simulator acceptance**

Run matched and deliberately mismatched model campaigns on GrillSim and MAKGrillSim across steady 225°F, steady 450°F, and 225→275°F scenarios with seeds 0–9. For a matched accepted model, require no safety event, no fuel-constraint violation, no worse integrated absolute error, and no worse overshoot than measured-temperature PID-SP. For mismatched models, require rejection before output or predictor fallback without violating the same safety/fuel constraints.

Store only deterministic aggregate assertions in the E2E; do not check in generated experiment output.

- [ ] **Step 4: Run final focused verification**

```bash
uv run pytest -q \
  tests/unit/controller/test_fopdt_identifier.py \
  tests/unit/controller/test_pid_sp_delay_evidence.py \
  tests/unit/controller/test_pid_sp_model_selection.py \
  tests/unit/controller/test_pid_sp.py \
  tests/unit/controller/test_pid_sp_learning.py \
  tests/unit/controller/test_smith_predictor.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_hold_learning_runtime.py \
  tests/unit/runtime/test_hold_refit_trigger.py \
  tests/unit/runtime/test_model_lifecycle.py \
  tests/unit/runtime/test_hold_model_persistence.py \
  tests/unit/runtime/test_smoke_learning_trajectory.py \
  tests/e2e/test_smoke_hold_learning_trajectory.py \
  tests/e2e/test_pid_sp_real_cook_learning.py
```

Expected: all listed tests pass with no deselection and the real-cook E2E retains fail-closed `insufficient-delay-identifiability` for the original single cook.

- [ ] **Step 5: Commit the behavioral proof**

```bash
jj describe -m "test(pid_sp): verify real-cook learning safety"
jj status
```

Expected: only the intended PID-SP implementation, tests, fixture, and updated plan are changed across the task changes; unrelated dependency/mobile/web changes remain in ancestors and are untouched.
