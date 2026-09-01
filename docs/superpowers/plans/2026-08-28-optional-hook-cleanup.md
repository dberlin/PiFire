# Optional Hook Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace stringly typed optional dispatch at owned PiFire hook boundaries with direct typed calls, while preserving genuine plugin, third-party, compatibility, and best-effort cleanup boundaries and refusing to hide broken MPC learning wiring behind no-op methods.

**Architecture:** Phase A is a low-risk owned-hook cleanup: use the existing neutral probe base method directly, require candidate retirement, complete the trajectory observer/seed capabilities, and make Hold call exact owned context and runner surfaces directly. Phase B is a separately approved, larger runner-adapter migration: retain local/non-MPC controller compatibility behind one ingress adapter, validate a required MPC-learning Protocol once at construction, and only then remove repeated runner capability probes. Required learning operations never receive neutral defaults.

**Tech Stack:** Python 3.14, `typing.Protocol`/`runtime_checkable`, pytest, Ruff, Jujutsu.

**Spec:** This plan incorporates the completed architecture review at `agent://OptionalHookPatternReview`; its boundary decisions are restated below so execution does not depend on hidden context.

## Mandatory predecessor gate

Optional-hook cleanup **MUST NOT begin** until `docs/superpowers/plans/2026-08-28-sqlite-utils-migrations.md` (`# Selective sqlite-utils Migration Registry Implementation Plan`) is fully implemented, focused macOS and Linux verification and independent review are complete, all migration Jujutsu changes are committed, and the integration owner has pushed the tracked bookmark.

The migration implementer must hand off the exact pushed Jujutsu change ID and verification evidence. Start this work from a fresh workspace rooted at, or descending from, that pushed change. Do not overlap migration edits to `common/datastore.py`, `pyproject.toml`, `uv.lock`, or datastore/concurrency tests. None of those files belongs to this plan.

## Global constraints

- Use Jujutsu only. Before each task, describe the fresh working-copy change; after focused GREEN and review, run `jj new` so every task is an independently reviewable change.
- TDD is mandatory. Each production edit follows an observed RED that fails for the intended missing-hook behavior, not a source-text assertion.
- A non-`None` object stored under an owned exact type must satisfy that type. Missing required methods fail visibly; `getattr(..., None)` must not reinterpret broken wiring as “unsupported.”
- A base no-op or neutral return is allowed only when unsupported behavior is harmless for every subclass. Existing examples include `ProbeInterface.get_thermocouple_samples() -> {}`, `ControllerBase.get_learning_diagnostics() -> None`, trace projections, `ControllerBase.close()`, and `ControllerRunner` seed-hook defaults.
- Never add neutral defaults for MPC identity binding, observation failure conversion, observation submission, fit polling, corpus-fit ticket scheduling/consumption/failure, candidate retirement, trajectory replay, or trajectory seed anchoring. Their absence is a wiring defect.
- Phase A is the default deliverable and must be complete, verified, independently reviewed, committed, and safe to push without Phase B.
- Phase B is optional. Do not start it without explicit owner approval after the Phase A review. Its compatibility policy is fixed by this plan; approval is a scope gate, not an invitation to redesign it mid-task.
- Preserve every genuine open boundary listed in **Retained dynamic-dispatch exceptions**. Do not fold opportunistic cleanup of those sites into either phase.
- Do not add a static AST/source-text test banning `getattr`. Behavioral tests must distinguish harmless unsupported behavior from broken required wiring.
- On macOS use the existing `.venv`; do not install/build `bluepy`. Use the repository’s existing Linux handoff for final bluepy-inclusive verification.

## Retained dynamic-dispatch exceptions

These uses are intentional and remain unchanged unless a later dedicated plan tightens their external contracts:

| Boundary | Exact site | Why `getattr` remains |
|---|---|---|
| Controller module plugin | `common/controller_deps.py:required_modules_for` | `requires_modules(config)` is an optional module-level hook on a dynamically imported controller plugin; there is no class base default. |
| External hardware backend | `common/i2c_bus.py:_LockedI2C.deinit` | Backends come from different third-party hardware libraries and do not share a PiFire lifecycle base. |
| Injected worker lifecycle | `controller/runtime/model_fitting.py:GreyLearningOrchestrator.start`, `.close` | `worker` is deliberately injected/`Any`; lightweight workers may omit lifecycle operations. |
| Unknown candidate components | `controller/runtime/model_fitting.py:_close_if_owned`, `GreyLearningOrchestrator._release_prepared`; `controller/model_learning/grey_runtime.py:GreyLearningRuntime.restore_model` | Candidate controller/estimator values are `Any`, ownership is conditional, and cleanup must be best effort. |
| Failure-path logger | `controller/runtime/controller.py:Controller.cleanup` | A malformed or failing logger must never prevent later persistence/hardware owners from closing. |
| Arbitrary equality operand | `common/persistence/model_evidence.py:ModelActivationPair.__eq__` | `other: object` intentionally accepts a duck-typed `to_dict()` projection. |
| Additive store staging | `controller/runtime/model_persistence.py:ModelPersistenceWorker._stage_checkpoint_owned` | `_ModelStore` requires `save_outcome`; `stage_owned` remains an optional enhancement for minimal/older stores. |
| Local/non-MPC controller compatibility | `controller/runtime/runner.py` until Phase B | Dynamically imported local cores and lightweight test cores are a real compatibility surface. Phase B centralizes, rather than deletes, that boundary. |

## File and interface map

### Phase A production files

- `probes/main.py`: call the benign owned `ProbeInterface.get_thermocouple_samples()` default directly.
- `controller/model_learning/grey_runtime.py`: call required `GreyLearningOrchestrator.retire_evaluated_candidate()` directly.
- `controller/runtime/modes/hold_learning.py`: make `_LearningTrajectoryObserver` describe replay, anchor, and barrier capabilities exactly; direct-call replay and anchor when the observer is present.
- `controller/runtime/modes/hold.py`: define and validate the narrow estimator-seed capability, use `ControllerContext.learning_trajectory` directly, and use benign `ControllerRunner` seed defaults directly.

### Phase A tests

- `tests/unit/probes/test_thermocouple_orchestration.py`
- `tests/unit/probes/test_base.py` (verification only; its neutral-default test already exists)
- `tests/unit/mpc/test_mpc_controller.py`
- `tests/unit/mpc/test_model_evidence_report.py` (regression verification)
- `tests/unit/runtime/test_hold_learning_runtime.py`
- `tests/unit/runtime/test_hold_trajectory_seed.py`
- `tests/unit/runtime/test_hold_control_trace.py`
- `tests/unit/runtime/conftest.py`

### Optional Phase B files

- `controller/base.py`: add only harmless result-projection defaults that the compatibility adapter can mirror.
- `controller/runtime/runner.py`: define the construction adapter and required MPC-learning Protocol, validate MPC ingress, and replace repeated runner capability probes with direct dispatch through the one-time capability result.
- `tests/unit/runtime/test_sync_runner.py`
- `tests/unit/runtime/test_threaded_runner.py`
- `tests/unit/runtime/test_controller_build_failure.py`
- `tests/unit/runtime/test_model_persistence.py` (verification only; proves optional store staging still works)

---

# Phase A — Low-risk owned hooks

### Task 1: Call the owned probe sample hook directly

**Files:**
- Modify: `probes/main.py:ProbesMain.read_probes`
- Test: `tests/unit/probes/test_thermocouple_orchestration.py:_Device`, `_main`, and new malformed-owned-hook test
- Verify: `tests/unit/probes/test_base.py:test_non_thermocouple_probe_has_no_junction_samples`

**Interfaces:**
- Consumes: `ProbeInterface.get_thermocouple_samples(self) -> Mapping[str, ThermocoupleJunctionSample]`, whose owned neutral default is `{}`.
- Produces: direct dispatch from `ProbesMain.read_probes`; a subclass that shadows the required callable with a non-callable fails visibly.

- [ ] **Step 1: Describe the task change**

```bash
jj describe -m "Call owned probe sample hook directly"
```

- [ ] **Step 2: Write the failing malformed-owned-hook test**

Add beside the existing orchestration tests:

```python
def test_read_probes_rejects_a_broken_owned_sample_hook() -> None:
    probe = _probe("device", "port", "Grill", "Primary")
    device = _Device("device", [probe], samples={})
    device.get_thermocouple_samples = None
    main = _main([probe], [device])

    with pytest.raises(TypeError, match="NoneType.*not callable"):
        main.read_probes(now=1.0)
```

This intentionally models a broken subclass, not an unsupported device. Unsupported production devices inherit the base `{}` implementation.

- [ ] **Step 3: Run RED**

```bash
.venv/bin/pytest -q \
  tests/unit/probes/test_thermocouple_orchestration.py::test_read_probes_rejects_a_broken_owned_sample_hook \
  -n0
```

Expected: FAIL because current `getattr` treats the non-callable hook as absent and no `TypeError` is raised.

- [ ] **Step 4: Replace only the redundant probe**

In `ProbesMain.read_probes`, replace:

```python
get_samples = getattr(device, "get_thermocouple_samples", None)
samples = get_samples() if get_samples is not None else {}
```

with:

```python
samples = device.get_thermocouple_samples()
```

Do not change dynamic module loading, `device_info` normalization, health filtering, or any third-party backend cleanup.

- [ ] **Step 5: Run GREEN and the neutral-base regression**

```bash
.venv/bin/pytest -q \
  tests/unit/probes/test_thermocouple_orchestration.py \
  tests/unit/probes/test_base.py \
  -n0
```

Expected: all pass. The base test proves a legitimate unsupported sample hook still returns `{}`.

- [ ] **Step 6: Format, inspect, review, and close the boundary**

```bash
.venv/bin/ruff format probes/main.py tests/unit/probes/test_thermocouple_orchestration.py
.venv/bin/ruff check probes/main.py tests/unit/probes/test_thermocouple_orchestration.py
jj status
jj diff -r @
```

Request an independent review of this change. The reviewer must confirm that only the owned sample hook changed and that plugin loading remains dynamic. After approval:

```bash
jj new
```

---

### Task 2: Make blocked-candidate retirement required

**Files:**
- Modify: `controller/model_learning/grey_runtime.py:GreyLearningRuntime._poll_learning_off_path_locked`
- Test: `tests/unit/mpc/test_model_evidence_report.py:test_real_evaluation_blocker_persists_rejection_context_before_retirement`

**Interfaces:**
- Consumes: `GreyLearningOrchestrator.retire_evaluated_candidate(decision: Any) -> bool`.
- Produces: every evaluation with blockers attempts retirement directly while holding `_learning_evaluation_lock`; a missing method is an immediate wiring error.
- Invariant: retirement still happens after durable evaluation/rejection evidence is persisted and before `_learning_candidate_pair` is cleared.

- [ ] **Step 1: Describe the task change**

```bash
jj describe -m "Require blocked MPC candidate retirement"
```

- [ ] **Step 2: Parameterize the existing durable blocker regression**

Change the existing test declaration:

```python
@pytest.mark.parametrize("retirement_hook", ["present", "missing"])
def test_real_evaluation_blocker_persists_rejection_context_before_retirement(
    ds,
    retirement_hook: str,
) -> None:
```

Keep its real controller, persisted challenger, preparation, evaluation, and local `_Learning` setup unchanged. Immediately after `learning = _Learning()`, remove the hook only for the broken-wiring case:

```python
learning = _Learning()
if retirement_hook == "missing":
    delattr(_Learning, "retire_evaluated_candidate")
```

At the existing poll site, split the missing-hook RED from the unchanged full evidence assertions:

```python
controller._grey_learning_runtime._learning = learning
controller._grey_learning_runtime._grey_evaluation_payload = (
    lambda *_args, **_kwargs: SimpleNamespace()
)
if retirement_hook == "missing":
    try:
        with pytest.raises(
            AttributeError,
            match="retire_evaluated_candidate",
        ):
            controller.poll_learning_off_path(
                live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
            )
    finally:
        controller.close()
    return

controller.poll_learning_off_path(
    live_origin=CandidateOrigin.OPERATOR_CALIBRATION,
)
```

Leave every existing assertion after the present-hook poll unchanged. The present parameter continues to prove durable evidence is written before retirement and the candidate is released.

- [ ] **Step 3: Run RED**

```bash
.venv/bin/pytest -q \
  'tests/unit/mpc/test_model_evidence_report.py::test_real_evaluation_blocker_persists_rejection_context_before_retirement[missing]' \
  -n0
```

Expected: FAIL with “DID NOT RAISE AttributeError” because the current optional dispatch silently skips retirement.


- [ ] **Step 4: Make retirement direct**

Replace the callable probe in `_poll_learning_off_path_locked`:

```python
if blockers:
    retire = getattr(learning, "retire_evaluated_candidate", None)
    if callable(retire):
        retire(evaluation)
```

with:

```python
if blockers:
    learning.retire_evaluated_candidate(evaluation)
```

Do not add `retire_evaluated_candidate` to `ControllerBase`, a runner base, or a broad Protocol with a no-op body.

- [ ] **Step 5: Run GREEN and lifecycle regressions**

```bash
.venv/bin/pytest -q \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/mpc/test_model_evidence_report.py \
  -n0
```

Expected: all pass. The evidence-report test must still show the exact rejected evaluation in `learning.retired`, persisted rejection evidence, and no remaining candidate.

- [ ] **Step 6: Format, inspect, and independently review**

```bash
.venv/bin/ruff format \
  controller/model_learning/grey_runtime.py \
  tests/unit/mpc/test_model_evidence_report.py
.venv/bin/ruff check \
  controller/model_learning/grey_runtime.py \
  tests/unit/mpc/test_model_evidence_report.py
jj status
jj diff -r @
```

The reviewer must check ordering under `_learning_evaluation_lock`, evidence persistence before release, and the absence of a new no-op. After approval:

```bash
jj new
```

---

### Task 3: Complete and directly consume the trajectory observer Protocol

**Files:**
- Modify: `controller/runtime/modes/hold_learning.py:_LearningTrajectoryObserver`, `HoldLearningRuntime.submit_completed_observation`
- Test: `tests/unit/runtime/test_hold_learning_runtime.py:_runtime` and new warm-up trajectory doubles/tests

**Interfaces:**
- Produces exact observer surface:

```python
class _LearningTrajectoryObserver(Protocol):
    def observe_hold_frame(
        self,
        observation: FrameObservation,
        *,
        replay_only: bool = False,
    ) -> None: ...

    def estimator_seed_anchor(self) -> tuple[int, float] | None: ...

    def barrier(self, timeout: float = 2.0) -> bool: ...
```

- Consumes: a non-`None` observer that can replay an exact frame, publish its resulting `(monotonic_end_ms, chamber_temperature_c)` anchor, and fence persistence through `barrier()`.
- Invariant: warm-up decrements only after replay advances the anchor to the exact frame end and the observation is valid/continuous.

- [ ] **Step 1: Describe the task change**

```bash
jj describe -m "Complete Hold trajectory observer capability"
```

- [ ] **Step 2: Let the test factory inject a trajectory observer**

Change the test helper signature and constructor call:

```python
def _runtime(
    *,
    opened: bool = True,
    runner: _Runner | None = None,
    persistence: _Persistence | None = None,
    logger: _LifecycleLogger | None = None,
    learning_trajectory=None,
):
    trace, recorder = _trace(opened=opened)
    actual_runner = _Runner() if runner is None else runner
    actual_persistence = _Persistence() if persistence is None else persistence
    runtime = HoldLearningRuntime(
        runner=actual_runner,
        model_store=None,
        persistence=actual_persistence,
        trace=trace,
        controller_name="mpc",
        logger=_LifecycleLogger() if logger is None else logger,
        initial_generation=actual_runner.generation,
        learning_trajectory=learning_trajectory,
    )
    return runtime, actual_runner, actual_persistence, trace, recorder
```

- [ ] **Step 3: Write RED/GREEN observer doubles and tests**

```python
class _ReplayTrajectory:
    def __init__(self) -> None:
        self.replays: list[tuple[FrameObservation, bool]] = []
        self.anchor: tuple[int, float] | None = None

    def observe_hold_frame(
        self,
        observation: FrameObservation,
        *,
        replay_only: bool = False,
    ) -> None:
        self.replays.append((observation, replay_only))
        self.anchor = (
            round(observation.frame_end_s * 1_000),
            observation.temp_c,
        )

    def estimator_seed_anchor(self) -> tuple[int, float] | None:
        return self.anchor

    def barrier(self, timeout: float = 2.0) -> bool:
        del timeout
        return True


class _ReplayWithoutAnchor:
    def observe_hold_frame(
        self,
        observation: FrameObservation,
        *,
        replay_only: bool = False,
    ) -> None:
        del observation, replay_only

    def barrier(self, timeout: float = 2.0) -> bool:
        del timeout
        return True


def test_seed_warmup_requires_the_complete_trajectory_observer() -> None:
    runtime, *_ = _runtime(learning_trajectory=_ReplayWithoutAnchor())
    runtime.set_seed_warmup_remaining(1)

    with pytest.raises(AttributeError, match="estimator_seed_anchor"):
        runtime.submit_completed_observation((0, 20_000), _observation())


def test_seed_warmup_replays_exactly_before_decrementing() -> None:
    trajectory = _ReplayTrajectory()
    runtime, runner, *_ = _runtime(learning_trajectory=trajectory)
    observation = _observation(frame_start_s=0.0, frame_end_s=20.0)
    runtime.set_seed_warmup_remaining(1)

    runtime.submit_completed_observation((0, 20_000), observation)

    assert trajectory.replays == [(observation, True)]
    assert trajectory.estimator_seed_anchor() == (
        round(observation.frame_end_s * 1_000),
        observation.temp_c,
    )
    assert runtime.seed_warmup_remaining == 0
    assert runner.submissions == []
```

- [ ] **Step 4: Run RED**

```bash
.venv/bin/pytest -q \
  tests/unit/runtime/test_hold_learning_runtime.py::test_seed_warmup_requires_the_complete_trajectory_observer \
  tests/unit/runtime/test_hold_learning_runtime.py::test_seed_warmup_replays_exactly_before_decrementing \
  -n0
```

Expected: the incomplete-observer test fails because current dynamic dispatch suppresses the missing anchor. The complete behavior test may already pass and remains the positive contract.

- [ ] **Step 5: Complete the Protocol and remove the two probes**

Retain the existing `barrier()` member. Add the replay keyword and anchor member exactly as shown in **Interfaces**. In the warm-up branch use:

```python
trajectory = self._learning_trajectory
replayed_exactly = False
if trajectory is not None:
    trajectory.observe_hold_frame(observation, replay_only=True)
    anchor = trajectory.estimator_seed_anchor()
    replayed_exactly = (
        isinstance(anchor, tuple)
        and anchor[0] == round(observation.frame_end_s * 1_000)
    )
```

Keep the later reconciled-observation direct call and its existing exception-to-warning behavior unchanged; it protects control from trajectory persistence failures, not from a missing method.

Update the existing `_TrajectoryObserver` test double to the same surface rather than leaving a partial fake:

```python
class _TrajectoryObserver:
    def __init__(self) -> None:
        self.observations: list[FrameObservation] = []
        self.anchor: tuple[int, float] | None = None

    def observe_hold_frame(
        self,
        observation: FrameObservation,
        *,
        replay_only: bool = False,
    ) -> None:
        del replay_only
        self.observations.append(observation)
        self.anchor = (round(observation.frame_end_s * 1_000), observation.temp_c)

    def estimator_seed_anchor(self) -> tuple[int, float] | None:
        return self.anchor

    def barrier(self, timeout: float = 2.0) -> bool:
        del timeout
        return True
```

- [ ] **Step 6: Run GREEN and the complete Hold-learning module**

```bash
.venv/bin/pytest -q tests/unit/runtime/test_hold_learning_runtime.py -n0
```

- [ ] **Step 7: Format, inspect, and independently review**

```bash
.venv/bin/ruff format \
  controller/runtime/modes/hold_learning.py \
  tests/unit/runtime/test_hold_learning_runtime.py
.venv/bin/ruff check \
  controller/runtime/modes/hold_learning.py \
  tests/unit/runtime/test_hold_learning_runtime.py
jj status
jj diff -r @
```

The reviewer must confirm the Protocol matches all three consumed methods, replay uses the keyword argument, and a missing anchor no longer silently leaves warm-up wedged. After approval:

```bash
jj new
```

---

### Task 4: Validate the estimator-seed capability and make Hold calls direct

**Files:**
- Modify: `controller/runtime/modes/hold.py:_bind_trajectory_trace`, `_estimator_seed_requirements`, `_seed_runner_before_first_solve`
- Modify: `tests/unit/runtime/conftest.py:_ExactSeedSource`
- Modify: `tests/unit/runtime/test_hold_trajectory_seed.py:_SeedSource` and new incomplete-capability test
- Test: `tests/unit/runtime/test_hold_control_trace.py`

**Interfaces:**
- Produces a runtime-checkable narrow capability in `hold.py`:

```python
@runtime_checkable
class _EstimatorSeedSource(Protocol):
    def estimator_seed_anchor(self) -> tuple[int, float] | None: ...

    def seed_for(
        self,
        theta: float,
        n_delay: int,
        at_ms: int,
        measured_temp_c: float,
    ) -> EstimatorSeed: ...
```

- Consumes: exact `ControllerContext.learning_trajectory: LearningTrajectoryRuntime | None`; benign `ControllerRunner.estimator_seed_requirements()` and `bind_estimator_seed_source()` defaults.
- Invariant: `None` trajectory means a deliberate cold start; a non-`None` incomplete trajectory is broken MPC wiring and is rejected before seed generation.

- [ ] **Step 1: Describe the task change**

```bash
jj describe -m "Use typed Hold trajectory and runner seed hooks"
```

- [ ] **Step 2: Write the failing incomplete-capability test**

Add to `tests/unit/runtime/test_hold_trajectory_seed.py`:

```python
def test_hold_rejects_an_incomplete_mpc_seed_source(hold_cycle) -> None:
    runner = _OrderedSeedRunner([]).script([_ordered_runner_result()])
    hold = hold_cycle(runner, controller="mpc")
    hold.setup()
    hold.ctx.learning_trajectory = SimpleNamespace(
        seed_for=lambda **_kwargs: _seed(),
    )

    try:
        with pytest.raises(
            TypeError,
            match="learning trajectory is missing the estimator seed capability",
        ):
            hold.on_tick(10.0, 110.0, hold.grill.get_output_status())
    finally:
        hold.teardown(110.0)
```

Add `from types import SimpleNamespace` to `test_hold_trajectory_seed.py`. In `hold.py`, extend the typing import with `TYPE_CHECKING`, `Protocol`, and `runtime_checkable`, add `EstimatorSeed` under `if TYPE_CHECKING:`, and quote the Protocol return annotation as `"EstimatorSeed"` so the existing lazy runtime import remains unchanged.

- [ ] **Step 3: Run RED**

```bash
.venv/bin/pytest -q \
  tests/unit/runtime/test_hold_trajectory_seed.py::test_hold_rejects_an_incomplete_mpc_seed_source \
  -n0
```

Expected: FAIL because current code treats the missing anchor as an optional condition and cold-starts.

- [ ] **Step 4: Define and validate `_EstimatorSeedSource`**

In `_seed_runner_before_first_solve`, read the exact context field:

```python
source = self.ctx.learning_trajectory
if source is not None and not isinstance(source, _EstimatorSeedSource):
    raise TypeError(
        "learning trajectory is missing the estimator seed capability"
    )
```

Keep invalid returned anchors on the existing measured-temperature fallback; capability presence and data validity are different concerns.

- [ ] **Step 5: Replace owned Hold probes with direct calls**

Make these exact changes without widening scope:

1. `_bind_trajectory_trace`: `trajectory = self.ctx.learning_trajectory`; call `trajectory.mark_trace_unavailable(reason)` directly in both failure branches.
2. `_estimator_seed_requirements`: call `runner.estimator_seed_requirements()` directly; retain value validation and the configured fallback.
3. `_seed_runner_before_first_solve`: call `source.estimator_seed_anchor()` and `source.seed_for(...)` directly after the capability check.
4. Call `runner.bind_estimator_seed_source(candidate_seed)` directly. Its `ControllerRunner` base implementation intentionally ignores unsupported binding.
5. When estimator-seed trace publication fails and the trajectory is non-`None`, call `trajectory.mark_trace_unavailable("estimator-seed-trace-publication-failed")` directly.
6. Replace `getattr(self.ctx, "learning_trajectory", None)` at the `HoldLearningRuntime` construction site with `self.ctx.learning_trajectory`.

Do not remove exception handling around seed generation, binding, trace publication, or cold-start seeding; those convert real runtime/data failures into explicit uncertain/absent evidence while control continues.

- [ ] **Step 6: Complete intentional trajectory test doubles**

Add to `_ExactSeedSource` in `tests/unit/runtime/conftest.py`:

```python
def estimator_seed_anchor(self) -> tuple[int, float] | None:
    return None


def mark_trace_unavailable(self, reason: str) -> None:
    del reason
```

Its existing `seed_for`, `bind_trace_session`, and replay-aware `observe_hold_frame` remain.

Add the production-consumed trace/observer members to `_SeedSource` in `test_hold_trajectory_seed.py` while preserving its event assertions:

```python
def bind_trace_session(
    self,
    session_id,
    cook_id,
    publish_segment,
    *,
    failure_handler=None,
) -> bool:
    del session_id, cook_id, publish_segment, failure_handler
    return True


def mark_trace_unavailable(self, reason: str) -> None:
    self.events.append(f"trajectory:unavailable:{reason}")


def observe_hold_frame(self, observation, *, replay_only: bool = False) -> None:
    del observation, replay_only


def barrier(self, timeout: float = 2.0) -> bool:
    del timeout
    return True
```

Do not add these methods to generic `object`/`Any` fakes that intentionally model external boundaries.

- [ ] **Step 7: Run GREEN and Hold regressions**

```bash
.venv/bin/pytest -q \
  tests/unit/runtime/test_hold_trajectory_seed.py \
  tests/unit/runtime/test_hold_learning_runtime.py \
  tests/unit/runtime/test_hold_control_trace.py \
  -n0
```

Expected: all pass. Existing seed ordering, exact anchor, short/absent/uncertain status, trace binding, and evidence fail-closed assertions remain unchanged.

- [ ] **Step 8: Format, inspect, and independently review Phase A**

```bash
.venv/bin/ruff format \
  controller/runtime/modes/hold.py \
  tests/unit/runtime/conftest.py \
  tests/unit/runtime/test_hold_trajectory_seed.py
.venv/bin/ruff check \
  probes/main.py \
  controller/model_learning/grey_runtime.py \
  controller/runtime/modes/hold_learning.py \
  controller/runtime/modes/hold.py \
  tests/unit/probes/test_thermocouple_orchestration.py \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/runtime/test_hold_learning_runtime.py \
  tests/unit/runtime/conftest.py \
  tests/unit/runtime/test_hold_trajectory_seed.py
jj status
jj diff -r @
```

Request an independent Phase A review. Required review questions:

- Does every non-`None` owned trajectory/candidate object receive direct required calls?
- Are probe and runner neutral defaults limited to harmless unsupported behavior?
- Can missing replay, anchor, or retirement wiring still be silently ignored?
- Are every plugin/external/`Any`/logger/equality/store exception listed above untouched?
- Is no runner-wide compatibility assumption changed in Phase A?

After approval:

```bash
jj new
```

---

## Phase A verification and push boundary

Run this before deciding whether to request Phase B:

```bash
.venv/bin/pytest -q \
  tests/unit/probes/test_base.py \
  tests/unit/probes/test_thermocouple_orchestration.py \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/mpc/test_model_evidence_report.py \
  tests/unit/runtime/test_hold_learning_runtime.py \
  tests/unit/runtime/test_hold_trajectory_seed.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_model_persistence.py \
  -n0
```

Then run the repository’s standard full macOS suite without attempting to install `bluepy`, followed by the standard Linux bluepy-inclusive suite from the sqlite-utils predecessor handoff. Compare failures to that predecessor’s recorded baseline; investigate every changed-path failure.

Have a fresh reviewer inspect the complete Phase A stack, not only Task 4. After all review findings are fixed and verification is green, push the tracked bookmark with Jujutsu. Phase A is complete at this point; Phase B is not required for its delivery.

---

# Phase B — Optional runner compatibility adapter

**STOP GATE:** Execute Tasks 5–7 only after Phase A is reviewed and the owner explicitly approves the larger runner migration. Use a separate Jujutsu stack based on the reviewed Phase A tip. Do not interleave Phase B with Phase A fixes.

## Fixed compatibility policy

1. Dynamically imported non-MPC/local controllers remain supported even when they do not inherit `ControllerBase`.
2. One `_ControllerCoreCompatibilityAdapter` at runner ingress supplies only benign result/status/trace/close projections. It is the sole compatibility `getattr` boundary for those hooks.
3. Required baseline controller operations (`update`, target, control-period/async/fan/actuation, output, calibration commands when requested, model snapshot/restore) delegate directly and fail through existing construction/reconfigure containment if absent.
4. A selected production MPC core must satisfy `_MpcLearningCore` before `_build_core` returns `"Active"`. Missing capability makes the selected MPC core inactive and allows existing safe fallback behavior.
5. `_MpcLearningCore` is detected once. Sync/threaded runners store that capability and make direct calls through it. Non-MPC runners keep `None` and skip learning work explicitly; they do not receive learning no-ops.
6. Directly constructed lightweight test cores remain valid when they are not entering production `_build_core` as selected MPC. Tests that intend to model production MPC ingress must provide the complete capability.

### Task 5: Establish the construction adapter and required MPC-learning Protocol

**Files:**
- Modify: `controller/base.py:ControllerBase`
- Modify: `controller/runtime/runner.py` near `_ActivationCore`, `_capture_completed_result`, `_build_core`, `_wrap`
- Test: `tests/unit/runtime/test_sync_runner.py`
- Test: `tests/unit/runtime/test_controller_build_failure.py`

**Interfaces:**
- Produces `@runtime_checkable _MpcLearningCore(Protocol)` with no implementations and these required members:
  - `estimator_seed_requirements() -> tuple[float, int]`
  - `bind_estimator_seed_source(Callable[[float, int], object] | None) -> None`
  - `bind_learning_identity(session_id: str, cook_id: str | None, role_generation: int) -> None`
  - `observe_frame(FrameObservation) -> object`
  - `observation_failure(FrameObservation, BaseException) -> object`
  - `poll_learning_off_path(*, live_origin: CandidateOrigin | None = None) -> object`
  - `schedule_corpus_fit(CandidateOrigin) -> bool`
  - `_schedule_corpus_fit_ticket(CandidateOrigin) -> str | None`
  - `_consume_terminal_corpus_fit_ticket(str, CandidateOrigin) -> bool`
  - `fail_corpus_fit(str, BaseException | str) -> None`
  - `get_learning_diagnostics() -> ControllerLearningDiagnostics`
- Produces `_ControllerCoreCompatibilityAdapter`, which owns the remaining compatibility probes for capture/status/trace/result registration/close only.

- [ ] **Step 1: Describe the optional task change**

```bash
jj describe -m "Centralize controller core compatibility at ingress"
```

- [ ] **Step 2: Write RED construction-policy tests**

Add behavioral tests proving all three policy branches:

```python
def test_non_mpc_local_core_keeps_neutral_optional_projections(monkeypatch) -> None:
    class LocalCore:
        def __init__(self, config, units, cycle_data, *, logger=None):
            del config, units, cycle_data, logger
            self.target = None

        def set_target(self, value):
            self.target = value

        def update(self, _temperature):
            return 0.25

        def wants_async(self):
            return False

        def commands_fan(self):
            return False

        def actuation_mode(self):
            return ActuationMode.FRAMED_PULSE

        def get_control_period(self):
            return None

    monkeypatch.setattr(
        runner_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(Controller=LocalCore),
    )
    settings = {
        "controller": {"selected": "local", "config": {"local": {}}},
        "globals": {"units": "C"},
        "cycle_data": {},
    }

    core, status = _build_core(settings, {"primary_setpoint": 100})
    result = _capture_completed_result(
        core,
        90.0,
        1,
        monotonic_clock=iter((1.0, 1.1)).__next__,
        wall_clock=lambda: 2.0,
    )

    assert status == "Active"
    assert result.diagnostics is None
    assert result.learning is None
    assert result.allocation is None
    assert result.baseline_allocation is None
    assert result.calibration is None
```

```python
def test_selected_mpc_core_missing_learning_capability_is_inactive(monkeypatch) -> None:
    class IncompleteMpcCore(ControllerBase):
        def wants_async(self):
            return True

    monkeypatch.setattr(
        runner_module.importlib,
        "import_module",
        lambda _name: SimpleNamespace(Controller=IncompleteMpcCore),
    )
    logger = _Logger()

    core, status = _build_core(
        _settings(),
        {"primary_setpoint": 225},
        logger=logger,
    )

    assert core is None
    assert status == "Inactive"
    assert any("missing required MPC learning capability" in message for message in logger.exceptions)
```

Also add a complete fake implementing every `_MpcLearningCore` member and assert `_build_core` returns `"Active"`. This test must use exact signatures from **Interfaces** and record each direct call; no `__getattr__` test fake is allowed.

- [ ] **Step 3: Run RED**

```bash
.venv/bin/pytest -q \
  tests/unit/runtime/test_sync_runner.py::test_non_mpc_local_core_keeps_neutral_optional_projections \
  tests/unit/runtime/test_controller_build_failure.py::test_selected_mpc_core_missing_learning_capability_is_inactive \
  tests/unit/runtime/test_controller_build_failure.py::test_complete_mpc_learning_capability_builds_active \
  -n0
```

Expected: adapter/protocol imports are absent and incomplete MPC currently reaches Active.

- [ ] **Step 4: Add only harmless owned base defaults**

Add neutral `ControllerBase.trace_baseline_allocation() -> AllocationResult | None`, `trace_calibration() -> CalibrationDecision | None`, and `register_calibration_result(result) -> None`. These are completed-result projections/notifications where unsupported is harmless. Do not add any member from `_MpcLearningCore` to `ControllerBase`.

- [ ] **Step 5: Implement the adapter and validation**

- The adapter delegates unknown required operations to the wrapped core.
- It implements capture/status fallback, the four trace projections, calibration-result registration, and close with the current compatibility semantics.
- `_capture_completed_result` calls the adapter/base surface directly.
- `_build_core` validates the raw selected MPC object with `isinstance(core, _MpcLearningCore)` before adaptation. On failure, close best effort, log a construction exception naming the missing capability class, and return `(None, "Inactive")` so existing `build_runner` fallback applies.
- Adapt every successful core before it reaches `_wrap` or a reconfigure install. Preserve `_ActivationCore` detection on the wrapped/delegated object.

Do not put identity, observation, fit, or ticket no-ops on the adapter.

- [ ] **Step 6: Run GREEN and construction regressions**

```bash
.venv/bin/pytest -q \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_controller_build_failure.py \
  -n0
```

- [ ] **Step 7: Format, inspect, and independently review**

```bash
.venv/bin/ruff format \
  controller/base.py \
  controller/runtime/runner.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_controller_build_failure.py
.venv/bin/ruff check \
  controller/base.py \
  controller/runtime/runner.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_controller_build_failure.py
jj status
jj diff -r @
```

The reviewer must reject any MPC learning no-op, validation after Active publication, or compatibility break for the non-MPC local core. After approval:

```bash
jj new
```

---

### Task 6: Convert Sync runner learning dispatch to the validated capability

**Files:**
- Modify: `controller/runtime/runner.py:SyncControllerRunner`
- Test: `tests/unit/runtime/test_sync_runner.py`

**Interfaces:**
- Consumes: `_MpcLearningCore | None` detected once for the installed core.
- Produces: direct seed requirements/binding, identity binding, observation, corpus-fit scheduling, and failure calls for validated MPC; explicit non-learning behavior for other cores.

- [ ] **Step 1: Describe the task change**

```bash
jj describe -m "Direct Sync runner learning through MPC capability"
```

- [ ] **Step 2: Write RED tests for direct required calls**

Use the complete recording MPC fake from Task 5. Assert `bind_evidence_context`, `observe_frame`, `schedule_corpus_fit`, `estimator_seed_requirements`, and `bind_estimator_seed_source` each append the expected call. Add one non-MPC local core assertion showing evidence context remains valid in the runner buffer but no core learning call occurs.

Add a malformed production-MPC regression that removes `observation_failure` and assert rejection occurs at `_build_core`, not later when an observation raises.

- [ ] **Step 3: Run RED**

```bash
.venv/bin/pytest -q tests/unit/runtime/test_sync_runner.py -n0
```

Expected: the new recording assertions expose repeated optional dispatch and the missing one-time capability field.

- [ ] **Step 4: Store and use the capability**

At each Sync core install, store:

```python
self._learning_core = (
    core if isinstance(core, _MpcLearningCore) else None
)
```

Then replace repeated hook lookup with direct calls on `_learning_core`. Branch only on `None` to distinguish non-learning cores. Keep observation terminal-drop behavior for non-learning cores; do not synthesize successful learning outcomes. Preserve reconfigure ordering and close the replaced adapter/core exactly once.

- [ ] **Step 5: Run GREEN**

```bash
.venv/bin/pytest -q \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/mpc/test_mpc_controller.py \
  -n0
```

- [ ] **Step 6: Format, inspect, and independently review**

```bash
.venv/bin/ruff format controller/runtime/runner.py tests/unit/runtime/test_sync_runner.py
.venv/bin/ruff check controller/runtime/runner.py tests/unit/runtime/test_sync_runner.py
jj status
jj diff -r @
```

The reviewer must verify every required call is through `_learning_core`, non-MPC behavior is explicit, and reconfiguration refreshes the capability atomically with `_core`. After approval:

```bash
jj new
```

---

### Task 7: Convert Threaded runner learning dispatch to the validated capability

**Files:**
- Modify: `controller/runtime/runner.py:ThreadedControllerRunner._learning_loop`, `_loop`, seed methods, corpus-fit scheduling/failure/polling, reconfiguration
- Test: `tests/unit/runtime/test_threaded_runner.py:FakeCore` and focused learning/corpus-fit tests
- Verify: `tests/unit/runtime/test_model_persistence.py`

**Interfaces:**
- Consumes: `_MpcLearningCore | None` installed under the same lock as `_core`.
- Produces: direct identity binding, observation/failure conversion, seed binding, fit polling, private ticket schedule/consume, and failure calls for validated MPC.
- Invariant: the control thread never waits on fit work; capability replacement is atomic; terminal tickets are consumed once; non-learning cores remain valid without receiving learning no-ops.

- [ ] **Step 1: Describe the task change**

```bash
jj describe -m "Direct threaded learning through MPC capability"
```

- [ ] **Step 2: Split the representative test doubles by intent**

Keep `FakeCore` as a non-learning compatibility core. Add `FakeMpcLearningCore(FakeCore)` implementing every `_MpcLearningCore` method explicitly and recording identity, observations, failures, seed-source binding, fit schedules, polls, ticket consumption, and fit failures. Migrate only tests that exercise MPC learning/corpus-fit behavior to `FakeMpcLearningCore`; do not bulk-add no-op learning methods to `FakeCore`.

- [ ] **Step 3: Write RED atomic-capability tests**

Add focused tests that assert:

1. an observation exception calls `FakeMpcLearningCore.observation_failure` directly and produces one terminal outcome;
2. corpus fit schedules one ticket, polls off path, and consumes the matching terminal ticket once;
3. reconfigure swaps `_core` and `_learning_core` under the same lock before queued identity/seed/observation work reaches the replacement;
4. ordinary `FakeCore` can still run/close but never schedules/polls learning;
5. a production `_build_core` MPC missing the ticket-consumption member is inactive.

- [ ] **Step 4: Run RED**

```bash
.venv/bin/pytest -q \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_controller_build_failure.py \
  -n0
```

Expected: the new capability/atomicity assertions fail while threaded code still probes each method independently.

- [ ] **Step 5: Replace threaded probes without changing scheduling semantics**

- Store `_learning_core` beside `_core` under `_lock` on initial construction and reconfigure.
- In `_learning_loop`, take a stable local capability reference for each dispatch and call `poll_learning_off_path`, `_schedule_corpus_fit_ticket`, and `_consume_terminal_corpus_fit_ticket` directly.
- In `_loop`, call `observe_frame`, `observation_failure`, and `bind_estimator_seed_source` directly on the captured capability.
- In corpus-fit submission/failure paths, branch once on `_learning_core is None`; otherwise call schedule/fail/ticket operations directly.
- Preserve all current queue, generation, lock, wakeup, timeout, terminal-drop, and shutdown behavior.
- Leave data-shape `getattr` on returned evaluation/delivery objects alone; this task is hook cleanup, not result-schema migration.

- [ ] **Step 6: Run GREEN and persistence compatibility**

```bash
.venv/bin/pytest -q \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_controller_build_failure.py \
  tests/unit/runtime/test_model_persistence.py \
  -n0
```

The model-persistence module must continue passing with `_Store.save_outcome` only; `stage_owned` remains optional.

- [ ] **Step 7: Format, inspect, and independently review Phase B**

```bash
.venv/bin/ruff format \
  controller/runtime/runner.py \
  tests/unit/runtime/test_threaded_runner.py
.venv/bin/ruff check \
  controller/base.py \
  controller/runtime/runner.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_controller_build_failure.py
jj status
jj diff -r @
```

The independent reviewer must explicitly trace identity, observation failure, poll, schedule-ticket, consume-ticket, and fail paths. Reject any implementation that makes one of them optional or adds it as a base/adapter no-op. After approval:

```bash
jj new
```

---

## Final verification and independent review

### If Phase A only was approved

Use the **Phase A verification and push boundary** above. Confirm no Phase B files changed beyond incidental predecessor content.

### If Phase B was approved

Run focused verification first:

```bash
.venv/bin/pytest -q \
  tests/unit/probes/test_base.py \
  tests/unit/probes/test_thermocouple_orchestration.py \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/mpc/test_model_evidence_report.py \
  tests/unit/runtime/test_hold_learning_runtime.py \
  tests/unit/runtime/test_hold_trajectory_seed.py \
  tests/unit/runtime/test_hold_control_trace.py \
  tests/unit/runtime/test_sync_runner.py \
  tests/unit/runtime/test_threaded_runner.py \
  tests/unit/runtime/test_controller_build_failure.py \
  tests/unit/runtime/test_model_persistence.py \
  tests/unit/runtime/test_hold_model_persistence.py \
  -n0
```

Then run the repository’s standard full macOS suite without `bluepy` and the standard Linux bluepy-inclusive suite inherited from the sqlite-utils migration handoff. Record exact pass/fail/skip counts and compare every failure to the predecessor baseline.

Request a fresh final review of the entire cleanup stack. The reviewer’s evidence must answer:

1. Which hooks now call directly, and why is each object owned/typed?
2. Which neutral base/adapter defaults remain, and why is unsupported behavior harmless?
3. Where is MPC learning capability validated before Active publication?
4. Can a missing identity, observation-failure, poll, ticket, fail, retirement, replay, or anchor method be silently ignored?
5. Are plugin module detection, external I2C deinit, injected-worker/candidate cleanup, logger failure paths, arbitrary equality, and optional store staging unchanged?
6. Do non-MPC local controllers and representative lightweight runner cores still work through the single compatibility boundary?
7. Did Phase B preserve queueing, lock, generation, shutdown, and fallback behavior?

Fix every accepted finding in a new Jujutsu change, rerun the smallest failing scope to RED/GREEN when behavior changes, rerun the complete applicable verification block, and obtain reviewer re-approval. Only then push the tracked bookmark with Jujutsu.
