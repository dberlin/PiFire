# Repair the Failing Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return `tests/` to fully green at HEAD (`54309b18b`) by repairing five independent defects that four recent commits left behind.

**Architecture:** All twenty failures are stale *test-side* expectations, not production regressions — every root cause was traced to a specific commit and reproduced deterministically (see Diagnosis). Four of the five clusters are pure assertion updates; the fifth removes floating-point optimizer output that one machine's solver run had been frozen into an end-to-end test. No production source file is modified by this plan.

**Tech Stack:** Python 3.14, pytest (+ pytest-xdist, enabled by default in `pyproject.toml`), Pillow 12.3.0, acados/CasADi via the gitignored `controller/_native` build, jujutsu (colocated with git), ruff.

**Spec:** This document. The Diagnosis section below is the spec — each task cites the evidence that justifies it.

## Global Constraints

- Run tests with `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest …`. A bare `python`/`pytest` gives false failures (PySide6 lives only in `.venv`).
- To run a single test, add `-n0` — the repo enables xdist by default and `-p no:xdist` errors out with `unrecognized arguments: -n`.
- Format changed files with `.venv/bin/ruff format <files>` before every commit. **Never** `uvx ruff` — the repo pins ruff <0.16.
- Commit with jujutsu, not git: run `jj new` **before** the first Write of each task, then `jj describe --stdin` at the end. Never `jj squash` after editing — edits are already in `@`.
- Never put backticks inside a double-quoted shell argument (zsh eats them). Use a quoted heredoc or `--stdin`.
- Do not modify any file under `controller/`, `common/`, or `display/`. This plan is test-side only; if a task seems to need a production edit, stop and report.

---

## Diagnosis

Baseline: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q` → **20 failed, 8395 passed, 5 skipped**.

### Cluster A — trace schema literals left at 6 (7 failures)

`e7b505a2d` ("Fix MPC learning persistence…") bumped `common/control_trace.py:34` `TRACE_SCHEMA_VERSION` from 6 to 7 and updated most tests, but missed three files that hardcode the literal `6`:

| File | Line | Assertion | Failures |
|---|---|---|---|
| `tests/unit/runtime/test_control_trace_session.py` | 425, 476 | `== (6, controller, ts)` | 5 (parametrized) |
| `tests/unit/datastore/test_control_trace_store.py` | 187 | `== [6, 6]` | 1 |
| `tests/integration/test_cookfile_learning_diagnostics.py` | 395 | `== [6]` | 1 |

All three build their records through helpers that default to the current version, so the literal is incidental, not a deliberate wire-format pin. The deliberate pin already exists and passes: `tests/unit/common/test_control_trace_schema.py:1136` asserts `TRACE_SCHEMA_VERSION == 7`.

### Cluster B — golden pixel hashes recaptured in a foreign environment (10 failures)

`ebb77e779` ("…fixed-display text fitting") rewrote `display/_base_fixed.py::_display_text` to shrink the font until the label fits inside a 4px margin, and re-captured 10 entries in `tests/ui/fixtures/fixed_base_golden.json` — despite that file's docstring stating `CAPTURE_GOLDEN=1` must never be used again after the initial commit.

Three findings prove the recapture ran on a different machine, not that this machine is wrong:

1. **This machine reproduces the original frozen baseline bit-for-bit.** Monkeypatching `_display_text` back to its pre-`ebb77e779` body reproduces all ten pre-`ebb77e779` hashes exactly.
2. **HEAD's code renders deterministically here** (same hash on three consecutive runs) and matches *neither* the old nor the committed-new hashes for the 5 `text:network_error` cases.
3. **Five entries changed that the code change cannot reach.** The 5 `current:shutdown` cases do not route through `_display_text` — under both the old and the new implementation this machine renders them identically, equal to their *pre-*`ebb77e779` hashes. Yet `ebb77e779` changed those five entries too. Only an environment difference (font or Pillow build) explains that.

The behavioral guarantee the fitting change was written for is already covered and passing: `tests/ui/test_fixed_base_harness_smoke.py::test_long_text_fits_inside_every_fixed_viewport` asserts the rendered bbox stays inside the margin on all five viewports. The golden file is therefore only a change-detector.

### Cluster C — one operator checkpoint, two revision bumps (1 failure)

`397b12729` ("Fix restored v6 challenger provenance") inserted `_adopt_prepared_checkpoint_lineage(preparation)` at `controller/model_learning/grey_runtime.py:633`, which does `_model_revision += 1` (line 596), into `_persist_reviewed_candidate_checkpoint`, which already bumped at line 654. Traced live: `0 → 1 → 2` within a single `poll_learning_off_path` call.

The same commit changed two tests from `1` to `2` to match (`test_grey_learning_runtime.py::test_reviewed_checkpoint_is_durable_idempotent_and_confidence_ordered`, `test_model_evidence_report.py::test_real_operator_evaluation_persists_reviewed_assessment_for_restart_report`) and left a third asserting `1` (`tests/web/test_api_model_evidence.py:509,511`) — the failing one.

**Ruling: keep two bumps, update the missed test.** `common/controller_model_state.py:22-24` documents the contract as *monotonically non-decreasing across process restarts* — "revision is the only signal it has for 'is this newer'" — and every consumer compares with `<=`/`<` only (`controller_model_state.py:202,213,222,242`). Skipping a number satisfies the contract. Both readings are contract-legal, so the tie breaks on intent and risk: `397b12729` deliberately encoded `2` twice, and touching revision arithmetic would inject an unverified variable into the Cluster E e2e test, which also asserts on revision (`test_mpc_online_learning_e2e.py:1378`). Verified in a scratch tree: with production untouched and the web test set to `2`, `tests/web/test_api_model_evidence.py` + `tests/unit/mpc/` = **904 passed**.

### Cluster D — stale test double missing a field production now reads (1 failure)

Same commit: `_poll_learning_off_path_locked` (`grey_runtime.py:1061`) calls `_adopt_prepared_checkpoint_lineage(prepared)` on whatever `getattr(delivery, "preparation", …)` returns, and that method dereferences `preparation.candidate.request` (line 582). The fake in `test_identity_rebind_fences_and_discards_an_inflight_old_generation_candidate` returns `SimpleNamespace(accepted=True, candidate_pair=pair)` — no `.candidate` — so the polling thread dies with `AttributeError: 'types.SimpleNamespace' object has no attribute 'candidate'`. The real `CandidatePreparation` (`controller/runtime/model_fitting.py:815`) carries `candidate: GreyFitSuccess`. Fix by pinning the fake to the live shape. Verified working in a scratch tree.

### Cluster E — one machine's optimizer output frozen into an e2e test (1 failure)

`397b12729` added `test_restored_v6_checkpoint_rebinds_exact_passive_candidate_provenance`, which waits 90s for `candidate_digest == _EXACT_PASSIVE_CANDIDATE_DIGEST` and then compares the fitted thermal parameters for exact equality.

Instrumentation shows the fit **succeeds** — status `evaluating`, `fit_status` `succeeded`, all four checks (`identifiability`, `native_build`, `native_dry_solve`, `target_timing`) passed, correct window `21:140`, 120 samples. Only the frozen numbers differ, and only in the last few significant figures:

| Value | Frozen in test | This machine | Relative delta |
|---|---|---|---|
| `C_c` | 1767.5013593870272 | 1767.5009984548972 | 2.0e-7 |
| `K_Q` | 288.2098500448781 | 288.2098394336364 | 3.7e-8 |
| `theta` | 52.241101540886156 | 52.24103941880264 | 1.2e-6 |
| `rmse` | 1.6228871053911238 | 1.6228871053900966 | 6.3e-13 |
| `nfev` / `samples` / `band_c` | 9 / 120 / (100.667, 109.111) | identical | 0 |
| digest | `d333486d…` | `5187cfb5…` (stable across 3 runs) | — |

That is solver convergence noise from a different `controller/_native` acados build (a gitignored build artifact), not a behavior change. The other two tests in the file pass because they compare against runtime-derived digests. `input_variance` (0.055502661581373174, asserted at line 1295) comes from the replayed inputs and matches exactly — the portable values are portable.

**Approach: derive the digest at runtime and cross-check it everywhere it appears** (stronger provenance evidence than a literal), and compare optimizer output within tolerance.

---

## File Structure

Every task touches a disjoint set of files. No production source is modified.

- `tests/unit/runtime/test_control_trace_session.py`, `tests/unit/datastore/test_control_trace_store.py`, `tests/integration/test_cookfile_learning_diagnostics.py` — Task 1
- `tests/web/test_api_model_evidence.py` — Task 2
- `tests/unit/mpc/test_mpc_controller.py` — Task 3
- `tests/ui/fixtures/fixed_base_golden.json`, `tests/ui/test_fixed_base_golden.py` — Task 4
- `tests/e2e/test_mpc_online_learning_e2e.py` — Task 5

## Parallelization

All five tasks are independent and may run concurrently. There is no shared file and no ordering dependency.

- Concurrency requires **isolated jj workspaces** — disjoint file sets alone are not enough, because concurrent sessions commit to the same branch. Give each parallel worker its own `jj workspace add` workspace, and copy `.lsp.json` plus run `bun install` in it (both gitignored, so `workspace add` skips them).
- Each worker must review/commit only its own files (`jj describe` after splitting out anything another session left in `@`).
- **Task 5 is the long pole**: each verification run takes ~90-100s. Start it first if running tasks in sequence.
- Recommended split if using two workers: worker A takes Tasks 1+2+3 (fast, ~2 min total), worker B takes Tasks 4+5.

---

### Task 1: Point trace-schema assertions at TRACE_SCHEMA_VERSION

**Files:**
- Modify: `tests/unit/runtime/test_control_trace_session.py:7-19` (import block), `:425`, `:476`
- Modify: `tests/unit/datastore/test_control_trace_store.py:180`, `:187`
- Modify: `tests/integration/test_cookfile_learning_diagnostics.py:12-22` (import block), `:395`

**Interfaces:**
- Consumes: `TRACE_SCHEMA_VERSION` from `common.control_trace` (currently `7`).
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Run the failing tests to see the current red**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/datastore/test_control_trace_store.py \
  tests/integration/test_cookfile_learning_diagnostics.py \
  -q --no-header -p no:randomly -n0
```

Expected: 7 failures, each an `assert 7 != 6` style mismatch (`assert (7, <ControllerType.PID: 'pid'>, 2000) == (6, …)`, `assert [7, 7] == [6, 6]`, `assert [7] == [6]`).

- [ ] **Step 2: Add the import to `tests/unit/runtime/test_control_trace_session.py`**

The file already imports from `common.control_trace` at line 7. Add `TRACE_SCHEMA_VERSION` to that alphabetically-sorted block — it sorts after `RecorderGapPayload` and before names starting with `Tr`+, so place it where ruff's isort ordering puts it (run `.venv/bin/ruff format` in Step 6; if ordering is wrong `ruff check` will say so).

```python
from common.control_trace import (
    CalibrationEventType,
    CalibrationTracePayload,
    ControllerBranch,
    ControllerType,
    InhibitReason,
    ModelEventPayload,
    ModelEventType,
    MpcFailureState,
    MpcUpdatePayload,
    PidSpUpdatePayload,
    PidUpdatePayload,
    RecorderGapPayload,
    TRACE_SCHEMA_VERSION,
    # … keep every other existing name in this block unchanged
)
```

- [ ] **Step 3: Replace the two literals in `test_control_trace_session.py`**

Line 425 (in `test_record_update_builds_exact_controller_payloads`):

```python
    assert (update.schema_version, update.controller, update.ts_ms) == (TRACE_SCHEMA_VERSION, controller, 2_000)
```

Line 476 (in `test_record_update_aligns_result_owned_learning_snapshot_with_session_record`):

```python
    assert (update.schema_version, update.controller, update.ts_ms) == (TRACE_SCHEMA_VERSION, controller, 2_345)
```

- [ ] **Step 4: Fix `tests/unit/datastore/test_control_trace_store.py`**

`TRACE_SCHEMA_VERSION` is already imported at line 10. Replace the assertion at line 187:

```python
    assert [record.schema_version for record in restored] == [TRACE_SCHEMA_VERSION, TRACE_SCHEMA_VERSION]
```

The test name at line 180 now lies — schema 6 is no longer the current version, and what this test actually covers is that the *current* schema round-trips learning state. Rename it:

```python
def test_current_schema_store_round_trip_retains_learning_state(ds):
```

Grep for the old name before committing to be sure nothing references it:

```bash
grep -rn "test_schema_six_store_round_trip_retains_learning_state" tests/ docs/ pyproject.toml
```

Expected: no hits after the rename.

- [ ] **Step 5: Fix `tests/integration/test_cookfile_learning_diagnostics.py`**

Add `TRACE_SCHEMA_VERSION` to the existing `from common.control_trace import (` block at line 12, then replace line 395:

```python
    assert trace["record_schema_versions"] == [TRACE_SCHEMA_VERSION]
```

Leave line 412 (`assert evidence["record_schema_versions"] == [2, 3]`) alone — that is the *model-evidence* schema, a different and unrelated version series.

- [ ] **Step 6: Format, then run the tests to verify they pass**

```bash
.venv/bin/ruff format tests/unit/runtime/test_control_trace_session.py \
  tests/unit/datastore/test_control_trace_store.py \
  tests/integration/test_cookfile_learning_diagnostics.py
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/datastore/test_control_trace_store.py \
  tests/integration/test_cookfile_learning_diagnostics.py \
  tests/unit/common/test_control_trace_schema.py \
  -q --no-header -p no:randomly -n0
```

Expected: all pass, 0 failures. `test_control_trace_schema.py` is included to confirm the deliberate `TRACE_SCHEMA_VERSION == 7` pin still holds.

- [ ] **Step 7: Commit**

```bash
jj describe --stdin <<'MSG'
Point trace-schema assertions at TRACE_SCHEMA_VERSION

The bump to schema 7 left three test files asserting the literal 6.
These assertions check record identity, not the wire format; the
deliberate version pin lives in test_control_trace_schema.py.
MSG
```

---

### Task 2: Match the web checkpoint test to the two-bump revision contract

**Files:**
- Modify: `tests/web/test_api_model_evidence.py:509`, `:511`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing other tasks depend on.

Background (do not re-litigate; see Diagnosis, Cluster C): production is correct. `common/controller_model_state.py:22` requires only that `revision` be monotonically non-decreasing, and `397b12729` deliberately encoded the doubled value in two sibling tests. This test is the one it missed.

- [ ] **Step 1: Run the failing test to see the current red**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  "tests/web/test_api_model_evidence.py::test_operator_evaluation_persists_restart_checkpoint_consumed_by_unmocked_activation_route" \
  -q --no-header -p no:randomly -n0
```

Expected: FAIL with `assert 2 == 1` at line 509.

- [ ] **Step 2: Update both assertions**

Lines 509 and 511, in `test_operator_evaluation_persists_restart_checkpoint_consumed_by_unmocked_activation_route`:

```python
    assert checkpoint["revision"] == 2
    assert migrate_grey_learning_snapshot(checkpoint)["revision"] == 2
```

Add a comment above the first one so the next reader does not "fix" it back:

```python
    # One operator-reviewed checkpoint advances the revision twice: once when
    # the candidate lineage is adopted, once when the checkpoint is persisted.
    # The store only ever compares revisions for recency, so the gap is inert.
    assert checkpoint["revision"] == 2
```

- [ ] **Step 3: Run the test to verify it passes**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  "tests/web/test_api_model_evidence.py::test_operator_evaluation_persists_restart_checkpoint_consumed_by_unmocked_activation_route" \
  -q --no-header -p no:randomly -n0
```

Expected: 1 passed.

- [ ] **Step 4: Run the sibling suites to confirm no contradiction remains**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/web/test_api_model_evidence.py tests/unit/mpc/ -q --no-header -p no:randomly -n0
```

Expected: all pass (≈904 passed, 1 deselected). This is the check that the two sibling tests asserting `== 2` still agree.

- [ ] **Step 5: Format and commit**

```bash
.venv/bin/ruff format tests/web/test_api_model_evidence.py
jj describe --stdin <<'MSG'
Expect two revision advances per reviewed checkpoint

Adopting the candidate lineage and persisting the checkpoint each
advance the model revision. Two sibling tests already record that; this
one was missed. The store compares revisions only for recency.
MSG
```

---

### Task 3: Give the racing-learning fake a real CandidatePreparation

**Files:**
- Modify: `tests/unit/mpc/test_mpc_controller.py:15` (import), `:22-30` (import block), `:462-492` (the test's fake)

**Interfaces:**
- Consumes: `CandidatePreparation.accepted_for_test(candidate, candidate_pair, incumbent_pair, timing)` from `controller.runtime.model_fitting:843`; `GreyFitSuccess` (fields: `request, config, rmse_c, max_error_c, identifiability, sample_count, temperature_band_c, nfev`); `FitRequest(request_id, origin, window, candidate_generation)` from `controller.model_learning.contracts:119`; `LiveLearningIdentity.window(first_seq, last_seq)`.
- Produces: nothing other tasks depend on.

This exact edit was prototyped and verified green in a scratch tree.

- [ ] **Step 1: Run the failing test to see the current red**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  "tests/unit/mpc/test_mpc_controller.py::test_identity_rebind_fences_and_discards_an_inflight_old_generation_candidate" \
  -q --no-header -p no:randomly -n0
```

Expected: FAIL. The polling thread raises `AttributeError: 'types.SimpleNamespace' object has no attribute 'candidate'` from `grey_runtime.py:582`, and the test then fails at `delivery, _evaluation = controller.poll_learning_off_path()`.

- [ ] **Step 2: Extend the imports**

Line 15 — add the two contract names:

```python
from controller.model_learning.contracts import CandidateOrigin, FitRequest, FrameObservation
```

Lines 22-30 — add `CandidatePreparation` to the existing block (it already imports `CandidatePair`, `GreyFitSuccess`, `TargetTimingEvidence`):

```python
from controller.runtime.model_fitting import (
    CandidatePair,
    CandidatePreparation,
    FitSubmission,
    GreyFitMessage,
    GreyFitSuccess,
    GreyLearningOrchestrator,
    TargetTimingEvidence,
    TriggerConfig,
)
```

- [ ] **Step 3: Bind the runtime so the fake can read the live config**

At the top of `test_identity_rebind_fences_and_discards_an_inflight_old_generation_candidate` (line 463), after the `_make` call:

```python
    controller, _estimator, _solver = _make(monkeypatch)
    runtime = controller._grey_learning_runtime
    preparing = threading.Event()
```

- [ ] **Step 4: Replace the fake's `poll_fit_off_path` with one that returns the real preparation type**

Replace the whole `def poll_fit_off_path(self, **_kwargs):` method inside `class RacingLearning` (lines ~485-492) with:

```python
        def poll_fit_off_path(self, *, live_identity=None, **_kwargs):
            self.calls += 1
            pair = old_pair if self.calls == 1 else new_pair
            if self.calls == 1:
                preparing.set()
                assert release.wait(2.0)
            preparation = CandidatePreparation.accepted_for_test(
                candidate=GreyFitSuccess(
                    request=FitRequest(
                        request_id=f"{self.calls:064d}",
                        origin=CandidateOrigin.PASSIVE_ONLINE,
                        window=live_identity.window(0, 119),
                        candidate_generation=live_identity.candidate_generation,
                    ),
                    config=runtime._active_components().controller.config,
                    rmse_c=1.0,
                    max_error_c=2.0,
                    identifiability=1.0,
                    sample_count=120,
                    temperature_band_c=(80.0, 120.0),
                    nfev=4,
                ),
                candidate_pair=pair,
                incumbent_pair=CandidatePair(Closable(), Closable()),
                timing=TargetTimingEvidence("candidate-dry-solve", 3, 1.0, 25.0),
            )
            self.prepared = preparation
            return SimpleNamespace(preparation=preparation)
```

Leave `evaluate_ready_off_path`, `update_identity`, and `close` unchanged — `update_identity` reads `self.prepared.candidate_pair.controller/.estimator`, and the real `CandidatePreparation` exposes both.

- [ ] **Step 5: Run the test to verify it passes**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  "tests/unit/mpc/test_mpc_controller.py::test_identity_rebind_fences_and_discards_an_inflight_old_generation_candidate" \
  -q --no-header -p no:randomly -n0
```

Expected: 1 passed, with **no** `PytestUnhandledThreadExceptionWarning` in the output. A pass that still emits that warning means a thread is dying silently — investigate rather than accept it.

- [ ] **Step 6: Run the whole MPC unit suite**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/mpc/ -q --no-header -p no:randomly -n0
```

Expected: all pass.

- [ ] **Step 7: Format and commit**

```bash
.venv/bin/ruff format tests/unit/mpc/test_mpc_controller.py
jj describe --stdin <<'MSG'
Pin the racing-learning fake to the real preparation shape

The off-path poll now adopts the prepared checkpoint lineage, which
reads preparation.candidate.request. The fake returned a bare namespace
without it, so the polling thread died mid-test.
MSG
```

---

### Task 4: Restore the fixed-display golden baseline to this environment

**Files:**
- Modify: `tests/ui/fixtures/fixed_base_golden.json` (10 entries)
- Modify: `tests/ui/test_fixed_base_golden.py:1-20` (module docstring)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing other tasks depend on.

**Do NOT run `CAPTURE_GOLDEN=1`.** Blind recapture is what caused this defect. The exact ten values are given below; write them by hand and let the test verify them.

- [ ] **Step 1: Run the golden test to see the current red**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/ui/test_fixed_base_golden.py -q --no-header -p no:randomly -n0
```

Expected: exactly 10 failures, all `pixel hash changed for …`, covering the 5 `text:network_error` and 5 `current:shutdown` cases.

- [ ] **Step 2: Write the 5 `current:shutdown` entries back to their pre-`ebb77e779` values**

These five screens never route through `_display_text`, so the fitting change could not have altered them; `ebb77e779` changed them anyway. Restore them in `tests/ui/fixtures/fixed_base_golden.json`:

```json
  "240x240:current:shutdown:0": "84d985d5fa0f74fccf8b8258092d8dfe48ad6b982075e2f527ff9784ee4bf2ac",
  "240x320:current:shutdown:0": "0b99357eda60cc0fc1a84a4681327798cf880d445068e1f298b1ef479d658d04",
  "240x320:current:shutdown:90": "8c12a0c870989269717f12e71731d430f1faf48e0ba71b27c50ffd4f113b9df1",
  "320x480:current:shutdown:0": "ec974ffc28445283392a981fa4676e096407a9b489ad44adeb480e7ce9f129bb",
  "320x480:current:shutdown:90": "c74172f39e518fe6266e164cc33bbac1f7ebbc193f12059fdb401b7328b14b39",
```

- [ ] **Step 3: Re-baseline the 5 `text:network_error` entries**

These five *did* legitimately change — `_display_text` now shrinks the label to fit inside the 4px margin. Set them to what this environment renders:

```json
  "240x240:text:network_error:0": "bd762bac0f863c15a75394854bfeb222e80ec5be86a79846f47e93ce7d0a04d4",
  "240x320:text:network_error:0": "35093d19172b3cd5ea80b65b98a6921bd6b71032e64d25363df6529a809b9970",
  "240x320:text:network_error:90": "f3e4a3f17966feaaf2a5535ff5a129f0b3f154902073baa02f20455455480b7a",
  "320x480:text:network_error:0": "08f75039a3aeb9e1d132c9bc09b8c32caf422f1003a3325964fbe0b2cd7d4786",
  "320x480:text:network_error:90": "3d5e2a3276dfcb669782e985b7ce9d83b0c5ef5da768337aef40d3d3884cdfb0",
```

Keep the file sorted by key and keep the trailing newline — the test writes it as `json.dumps(dict(sorted(...)), indent=2) + "\n"`, so match that shape.

- [ ] **Step 4: Verify the diff touches exactly ten keys and nothing else**

```bash
jj diff --stat tests/ui/fixtures/fixed_base_golden.json
jj diff tests/ui/fixtures/fixed_base_golden.json | grep -c '^+  "'
```

Expected: 10 added lines, 10 removed, in one file.

- [ ] **Step 5: Record the second re-baseline in the docstring**

The module docstring at `tests/ui/test_fixed_base_golden.py:1-20` says `CAPTURE_GOLDEN=1` must never be used again "except for that documented re-baseline" (the 240x240 one). Document this second one so the exception stays explicit. Insert after the existing re-baseline sentence:

```
Second documented re-baseline: the five `text:network_error` cases moved
when `_display_text` gained margin-aware font fitting. Their layout is
guarded behaviorally by
tests/ui/test_fixed_base_harness_smoke.py::test_long_text_fits_inside_every_fixed_viewport;
these hashes only detect unintended change. The five `current:shutdown`
cases do not route through `_display_text` and must never move with it.
```

- [ ] **Step 6: Run the golden test and the fitting test to verify they pass**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/ui/test_fixed_base_golden.py tests/ui/test_fixed_base_harness_smoke.py \
  -q --no-header -p no:randomly -n0
```

Expected: all pass, 0 failures. If any test *skips* with "trebuc.ttf not installed", stop — the machine is missing msttcorefonts and cannot validate this task; the fixture must only ever be edited on a machine where these tests actually run.

- [ ] **Step 7: Commit**

```bash
jj describe --stdin <<'MSG'
Restore the fixed-display golden baseline to this environment

Five text cases moved when _display_text gained margin-aware fitting;
five shutdown cases never route through it and were changed by a
recapture on a machine with different font metrics.
MSG
```

---

### Task 5: Stop freezing one machine's solver output in the passive-provenance e2e

**Files:**
- Modify: `tests/e2e/test_mpc_online_learning_e2e.py:77` (delete constant), `:79-87` (rename constant), `:1180-1187`, `:1220`, `:1222-1227`, `:1311`, `:1359`, `:1385`; add one module-level helper

**Interfaces:**
- Consumes: `pytest.approx`; existing module names `_EXACT_V6_CANDIDATE_DIGEST`, `_EXACT_V6_ACTIVE_DIGEST`.
- Produces: nothing other tasks depend on.

Each verification run takes ~90-100s when it fails and ~30-60s when it passes. Budget accordingly.

- [ ] **Step 1: Run the failing test to see the current red**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  "tests/e2e/test_mpc_online_learning_e2e.py::test_restored_v6_checkpoint_rebinds_exact_passive_candidate_provenance" \
  -q --no-header -p no:randomly -n0
```

Expected: FAIL after ~90s with `Failed: timed out waiting for the exact passive 21-140 grey fit`.

- [ ] **Step 2: Delete the unportable digest constant and rename the parameter reference**

Delete line 77 entirely:

```python
_EXACT_PASSIVE_CANDIDATE_DIGEST = "d333486d3422662680163cf0900957db8f7366756a0acdbf90da26c093c5430a"
```

Rename the parameter dict at line 79 so its name stops promising exactness (keep the values — they become the reference point for the tolerance comparison):

```python
_PASSIVE_PARAMETERS_REFERENCE = {
    "C_c": 1767.5013593870272,
    "K_Q": 288.2098500448781,
    "T_amb": 20.0,
    "h_amb": 0.5,
    "n_delay": 8,
    "sigma": 1.4e-09,
    "theta": 52.241101540886156,
}
```

Leave `_EXACT_V6_ACTIVE_DIGEST`, `_EXACT_V6_CANDIDATE_DIGEST`, and `_EXACT_PASSIVE_CONFIGURATION_DIGEST` alone — those are digests of stored fixtures and static configuration, and they reproduce exactly.

- [ ] **Step 3: Add the tolerance helper next to the constants**

Add immediately after the constant block (before `_LOGGER`):

```python
def _assert_passive_parameters(actual: Mapping[str, Any]) -> None:
    """Compare fitted thermal parameters within solver reproducibility tolerance.

    The values are a converged optimum, not a fixed point: a different acados
    build reproduces them only to about six significant figures (worst observed
    relative deviation 1.2e-6, on theta). Exact equality would pin the suite to
    one machine's floating-point result.
    """

    assert actual.keys() == _PASSIVE_PARAMETERS_REFERENCE.keys()
    for key, expected in _PASSIVE_PARAMETERS_REFERENCE.items():
        if isinstance(expected, float):
            assert actual[key] == pytest.approx(expected, rel=1e-5), key
        else:
            assert actual[key] == expected, key
```

`Mapping` and `Any` are already imported in this file; if `ruff check` reports otherwise, add them to the existing `typing`/`collections.abc` imports.

- [ ] **Step 4: Derive the digest at runtime in the wait predicate**

Replace the `_wait_until` block at lines 1177-1189:

```python
        fit_state = cast(
            dict[str, Any],
            _wait_until(
                lambda: (
                    state
                    if (state := dict(core.get_learning_diagnostics().state)).get("status") == "evaluating"
                    and state.get("fit_status") == "succeeded"
                    and isinstance(state.get("candidate_digest"), str)
                    else None
                ),
                timeout_s=90.0,
                description="the passive 21-140 grey fit",
            ),
        )
        passive_candidate_digest = cast(str, fit_state["candidate_digest"])
        # The point of this test: the passive fit must produce its own candidate,
        # not rebind either identity carried in by the restored v6 checkpoint.
        assert passive_candidate_digest != _EXACT_V6_CANDIDATE_DIGEST
        assert passive_candidate_digest != _EXACT_V6_ACTIVE_DIGEST
```

- [ ] **Step 5: Replace the three remaining frozen-output comparisons**

Line 1220 — the live challenger parameters:

```python
        _assert_passive_parameters(cast(Mapping[str, Any], live_challenger["parameters"]))
```

Lines 1222-1227 — the metadata block. `band_c` and `samples` come from the replayed rows and `nfev` reproduced exactly, so keep those strict; only `rmse` needs tolerance:

```python
        metadata = cast(dict[str, Any], live_challenger["metadata"])
        assert metadata.keys() == {"band_c", "nfev", "rmse", "samples"}
        assert metadata["band_c"] == [100.66666666666667, 109.11111111111111]
        assert metadata["nfev"] == 9
        assert metadata["samples"] == 120
        assert metadata["rmse"] == pytest.approx(1.6228871053911238, rel=1e-9)
```

Line 1385 — the post-restart challenger parameters:

```python
        _assert_passive_parameters(cast(Mapping[str, Any], challenger["parameters"]))
```

- [ ] **Step 6: Cross-check the derived digest everywhere it appears**

Line 1311 — the durable fit-lifecycle evidence:

```python
    assert fit_evidence_records[1].model_digest == passive_candidate_digest
```

Line 1359 — inside the expected provenance dict:

```python
        "candidate_digest": passive_candidate_digest,
```

`passive_candidate_digest` is bound in Step 4 inside the `try:` block; lines 1311 and 1359 are in the same function scope after the `try`/`finally`, so the name resolves. Asserting one runtime-derived digest in all three places is what makes this a provenance test rather than a numeric-reproduction test — do not weaken any of the three to a truthiness check.

- [ ] **Step 7: Run the test to verify it passes**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  "tests/e2e/test_mpc_online_learning_e2e.py::test_restored_v6_checkpoint_rebinds_exact_passive_candidate_provenance" \
  -q --no-header -p no:randomly -n0
```

Expected: 1 passed.

If it instead fails at line 1348 on `"configuration_digest": _EXACT_PASSIVE_CONFIGURATION_DIGEST`, that assertion was previously unreachable (the test died at line 1179 before ever evaluating it). It is expected to hold — the configuration digest hashes static MPC configuration, and the sibling `_EXACT_V6_ACTIVE_DIGEST` was confirmed to reproduce exactly on this machine. If it does not hold, bind it the same way: read the value from the window identity the test already has in hand and assert it is equal across the pre-restart and post-restart records, rather than to a literal.

- [ ] **Step 8: Run the whole e2e file**

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest \
  tests/e2e/test_mpc_online_learning_e2e.py -q --no-header -p no:randomly -n0
```

Expected: 3 passed (the other two already pass and must stay passing).

- [ ] **Step 9: Format and commit**

```bash
.venv/bin/ruff format tests/e2e/test_mpc_online_learning_e2e.py
jj describe --stdin <<'MSG'
Derive the passive candidate digest instead of freezing it

The fitted thermal parameters are a converged optimum that a different
acados build reproduces only to about six significant figures. Assert
one runtime-derived digest across all three places it appears, which
tests provenance rather than one machine's floating-point result.
MSG
```

---

## Final Verification

After all five tasks are merged, run the full suite exactly as the baseline was measured:

```bash
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q --no-header
```

Expected: **0 failed, 8415 passed, 5 skipped** (the 8395 baseline passes plus the 20 repaired). Confirm the count of failures is zero by reading the summary line — do not infer success from the absence of a traceback.

Then confirm nothing under production source was touched:

```bash
jj diff --stat | grep -v '^tests/\|^docs/' || echo "test-side only"
```

Expected: `test-side only`.

## Out of Scope — Report, Do Not Fix

Found while diagnosing Cluster C; no test currently covers it, so it is not part of this plan. Raise it with the user after the suite is green:

`controller/model_learning/grey_runtime.py:660,668` — when `_persist_reviewed_candidate_checkpoint` bails out (`get_model_snapshot()` returns `None`, or the store refuses the save), it restores `self._model_revision = previous_revision`, which undoes only the *second* bump. The `+= 1` from `_adopt_prepared_checkpoint_lineage` at line 596 stays applied, as do `_checkpoint_preparation`, `_checkpoint_preparation_key`, `_checkpoint_candidate_identity`, and `_checkpoint_origin`. A failed persist therefore leaves the runtime holding a candidate lineage it never durably wrote. Harmless under the monotonic-revision contract, but the lineage state is a real leak.
