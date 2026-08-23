# Cookfile Learning Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every new `.pifire` cookfile contain a complete, typed, cook-scoped learning postmortem for PID-SP, MPC, and future learning-capable controllers.

**Architecture:** Add one controller-neutral learning snapshot capability and capture its immutable result on each existing control-update trace. A generic collector combines cook-scoped typed trace records, compatible model-evidence records, and lazy normalized controller reports into one validated `learning_diagnostics.json` member; cookfile code contains no controller branches.

**Tech Stack:** Python 3.14, frozen dataclasses, Pydantic v2, SQLite typed persistence, ZIP/JSON cookfiles, pytest/pytest-xdist/pytest-cov, Jujutsu (`jj`).

**Spec:** `docs/superpowers/specs/2026-08-23-cookfile-learning-diagnostics-design.md`

## Global Constraints

- Use Jujutsu only. Never run raw Git commands. Each task ends with its task-specific `jj desc -m` command followed by `jj new`; keep the working `@` empty between tasks.
- Follow strict TDD: write one observable failing contract, run it and confirm the expected failure, implement the minimum behavior, then run focused tests green.
- The cookfile writer and generic collector must not branch on `pid_sp` or `mpc`.
- Complete means every compatible cook-scoped trace and evidence record in deterministic insertion order; do not sample, cap, summarize, or invent missing records.
- Diagnostic capture must not prevent actuator shutdown, primary cookfile creation, or history cleanup.
- Keep manifest cookfile minimum-readable version `1.5.0`; envelope, trace, and evidence versions evolve independently.
- New or substantially rewritten collector/provider modules require greater than 90% branch coverage.
- Every exported-symbol change requires an LSP reference search before editing and complete caller migration.
- Run project formatting only after behavior is green; do not reformat unrelated files.

---

### Task 1: Controller-neutral atomic learning snapshots

**Files:**
- Modify: `controller/base.py:32-97` and `controller/base.py` `ControllerBase`
- Modify: `controller/pid_sp.py:129-165`
- Modify: `controller/mpc.py:381-432`
- Modify: `controller/runtime/runner.py:379-489`
- Test: `tests/unit/controller/test_controller_capabilities.py`
- Test: `tests/unit/controller/test_pid_sp.py`
- Test: `tests/unit/mpc/test_mpc_controller.py`
- Test: `tests/unit/runtime/test_sync_runner.py`
- Test: `tests/unit/runtime/test_threaded_runner.py`

**Interfaces:**
- Produces: `ControllerLearningDiagnostics(schema_version: int, state: Mapping[str, JsonValue])` with `as_json() -> dict[str, JsonValue]`
- Produces: `ControllerBase.get_learning_diagnostics() -> ControllerLearningDiagnostics | None`
- Produces: `ControllerUpdateResult.learning: ControllerLearningDiagnostics | None`
- Consumes: existing PID-SP `build_pid_sp_live_learning()` and MPC `GreyLearningRuntime.learning_status()`

- [ ] **Step 1: Locate all exported capability and result consumers**

Use LSP references on `ControllerBase`, `ControllerUpdateResult`, `Controller.get_status` in PID-SP, and `Controller.get_status` in MPC. Record every constructor/caller that must receive the new frozen field; do not use text replacement for symbol migration.

- [ ] **Step 2: Write failing default-capability and ownership tests**

Add a contract like:

```python
def test_non_learning_controller_returns_no_learning_diagnostics():
    core = ControllerBase({}, "F", {})
    assert core.get_learning_diagnostics() is None


def test_learning_diagnostics_owns_nested_state():
    source = {"status": "collecting", "gates": [{"passed": False}]}
    snapshot = ControllerLearningDiagnostics(schema_version=1, state=source)
    source["gates"][0]["passed"] = True
    first = snapshot.as_json()
    first["gates"][0]["passed"] = True
    assert snapshot.as_json()["gates"][0]["passed"] is False
```

`ControllerBase` is directly constructible in the existing capability test. The mutation assertion must cross both source-to-snapshot and returned-copy boundaries.

- [ ] **Step 3: Run the capability tests and verify RED**

Run:

```bash
uv run pytest -q \
  tests/unit/controller/test_controller_capabilities.py \
  tests/unit/controller/test_pid_sp.py \
  tests/unit/mpc/test_mpc_controller.py
```

Expected: failure because `ControllerLearningDiagnostics` and `get_learning_diagnostics()` do not exist.

- [ ] **Step 4: Add the frozen capability contract**

In `controller/base.py`, define a frozen, slotted value that recursively freezes owned JSON and returns a fresh JSON tree:

```python
@dataclass(frozen=True, slots=True)
class ControllerLearningDiagnostics:
    schema_version: int
    state: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise ValueError("learning diagnostics schema_version must be positive")
        object.__setattr__(self, "state", _freeze_learning_json(dict(self.state)))

    def as_json(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], _thaw_learning_json(self.state))
```

Add focused `_freeze_learning_json`/`_thaw_learning_json` helpers in the same module. Freeze mappings as `MappingProxyType`, arrays as tuples, and scalars unchanged. Reject non-string keys, unsupported objects, booleans passed where integer schema values are required, NaN, and infinity. Thaw mappings/tuples to fresh dict/list trees.

Add this boring default to `ControllerBase`:

```python
def get_learning_diagnostics(self) -> ControllerLearningDiagnostics | None:
    return None
```

- [ ] **Step 5: Implement PID-SP and MPC capabilities without a second representation**

PID-SP:

```python
def get_learning_diagnostics(self) -> ControllerLearningDiagnostics:
    return ControllerLearningDiagnostics(
        schema_version=1,
        state=build_pid_sp_live_learning(self.identifier.status(), self.predictor.status()),
    )
```

MPC:

```python
def get_learning_diagnostics(self) -> ControllerLearningDiagnostics:
    return ControllerLearningDiagnostics(
        schema_version=1,
        state=self._grey_learning_runtime.learning_status(),
    )
```

Refactor each `get_status()` to call the capability once and use `diagnostics.as_json()` for its existing `learning` field. Do not call a separate status builder that can diverge.

- [ ] **Step 6: Write and verify the atomic-result RED test**

Use a fake core whose `update()` advances a generation and whose capability returns that generation:

```python
def test_completed_result_owns_the_learning_snapshot_from_that_update():
    result = _capture_completed_result(core, 225.0, 7, monotonic_clock=mono, wall_clock=wall)
    core.learning_state["generation"] = 8
    assert result.revision == 7
    assert result.learning.as_json()["generation"] == 7
```

Run the exact test. Expected: failure because `ControllerUpdateResult` has no `learning` field.

- [ ] **Step 7: Capture learning once with the completed controller result**

Add `learning: ControllerLearningDiagnostics | None = None` to `ControllerUpdateResult`. In `_capture_completed_result()`, call `core.get_learning_diagnostics()` immediately after the completed update and store it in the immutable result. Validate the returned type. Both synchronous and threaded runners already publish `ControllerUpdateResult`, so they must not re-read live controller state later. Add the atomic-result contract to `test_sync_runner.py` and the retained-result ownership contract to `test_threaded_runner.py`.

- [ ] **Step 8: Run Task 1 tests green**

Run the focused command from Step 3 plus the runner result test. Expected: all pass with no warnings.

- [ ] **Step 9: Commit Task 1**

```bash
jj desc -m "Add controller learning diagnostics capability"
jj new
jj st
```

Expected resting shape: empty `@`; Task 1 commit at `@-`.

---

### Task 2: Trace schema 6 learning snapshots

**Files:**
- Modify: `common/control_trace.py:30-47, 200-327, 840-1045`
- Modify: `controller/runtime/control_trace_session.py:372-598`
- Modify: `controller/control_trace_replay.py` schema/event validation paths
- Modify: `common/persistence/control_trace.py` only if its compatible-schema validation names schema literals explicitly
- Test: `tests/unit/common/test_control_trace_schema.py`
- Test: `tests/unit/runtime/test_control_trace_session.py`
- Test: `tests/unit/controller/test_control_trace_replay.py`
- Test: `tests/unit/datastore/test_control_trace_store.py`

**Interfaces:**
- Consumes: `ControllerUpdateResult.learning` from Task 1
- Produces: `LearningSnapshotPayload(schema_version: int, state: dict[str, JsonValue])`
- Produces: common control-update field `learning: LearningSnapshotPayload | None`
- Produces: `TRACE_SCHEMA_VERSION = 6`

- [ ] **Step 1: Write failing schema round-trip tests**

Add literal, independently derived expectations:

```python
def test_pid_sp_update_round_trips_owned_learning_snapshot():
    payload = _pid_sp_update(
        learning=LearningSnapshotPayload(
            schema_version=1,
            state={"status": "collecting", "gates": [{"name": "accepted_samples", "passed": False}]},
        )
    )
    record = _record(ControllerType.PID_SP, payload)
    restored = ControlTraceRecord.model_validate_json(record.model_dump_json())
    assert restored.schema_version == 6
    assert restored.payload.learning.state["status"] == "collecting"
```

Add rejection cases for NaN, infinity, a non-string mapping key, and an unsupported object. Add one PID update with `learning=None` to protect controllers without learning.

- [ ] **Step 2: Run schema tests and verify RED**

```bash
uv run pytest -q tests/unit/common/test_control_trace_schema.py
```

Expected: import/constructor failure for `LearningSnapshotPayload` or missing `learning` field.

- [ ] **Step 3: Implement the typed trace contract and schema bump**

Set `TRACE_SCHEMA_VERSION = 6`. Define a frozen Pydantic dataclass payload with a positive schema version and recursively validated JSON state. Add the optional field to `_ControlUpdatePayload`, not separately to PID-SP and MPC payloads:

```python
learning: LearningSnapshotPayload | None = None
```

Extend `ControlTraceRecord.schema_version` to accept compatible historical versions 2 through 6. Do not rewrite or drop older compatible records.

- [ ] **Step 4: Write failing session alignment tests for both controllers**

Construct `ControllerUpdateResult` values containing Task 1 snapshots and call `ControlTraceSession.record_update()`. Assert exact trace payload state, result revision, controller identity, timestamp, and that a stale duplicate reuses the already recorded snapshot rather than reading live state.

- [ ] **Step 5: Run session tests and verify RED**

```bash
uv run pytest -q tests/unit/runtime/test_control_trace_session.py
```

Expected: control-update payload has `learning=None` or constructor mismatch.

- [ ] **Step 6: Serialize the result-owned snapshot**

In `record_update()`, convert only `result.learning`:

```python
learning = (
    None
    if result.learning is None
    else LearningSnapshotPayload(
        schema_version=result.learning.schema_version,
        state=result.learning.as_json(),
    )
)
```

Pass that same value to PID, PID-SP, and MPC update payload constructors. The stale-result path replaces only `result_age_ms`, `stale`, `stale_state`, and `recovered` on the previous payload, so it intentionally preserves the previous snapshot.

- [ ] **Step 7: Update replay/store compatibility and run focused tests**

Run:

```bash
uv run pytest -q \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/controller/test_control_trace_replay.py \
  tests/unit/datastore/test_control_trace_store.py
```

Expected: all pass; seeded schema-5 records remain readable; schema-6 records retain learning state.

- [ ] **Step 8: Commit Task 2**

```bash
jj desc -m "Record learning snapshots in control traces"
jj new
jj st
```

---

### Task 3: Lazy generic final learning reports

**Files:**
- Create: `common/cook_diagnostics.py`
- Modify: `controller/learning_report.py`
- Modify: `controller/pid_sp_learning.py:443-501`
- Modify: `controller/model_learning/report.py:534-592`
- Test: `tests/unit/controller/test_learning_report.py`
- Test: `tests/unit/controller/test_pid_sp_learning.py`
- Test: `tests/unit/mpc/test_model_evidence_report.py`

**Interfaces:**
- Produces: `ControllerLearningReport(controller: str, schema_version: int, revision: str, report: Mapping[str, JsonValue])`
- Produces: provider function `diagnostic_learning_report() -> ControllerLearningReport` in each learning provider module
- Produces: dispatcher `controller_learning_report(controller_name: str) -> ControllerLearningReport | None`
- Preserves: `controller_learning_report_revision(controller_name: str) -> str | None`

- [ ] **Step 1: Write failing generic-dispatch tests**

Extend the existing isolated-module test harness so both fake provider modules export the same function name:

```python
def diagnostic_learning_report():
    calls.append("pid_sp")
    return ControllerLearningReport(
        controller="pid_sp",
        schema_version=1,
        revision="b" * 64,
        report={"status": "idle"},
    )
```

Assert:

```python
report = controller_learning_report("pid_sp")
assert report.controller == "pid_sp"
assert report.report == {"status": "idle"}
assert calls == ["pid_sp"]
```

Also assert an unsupported controller returns `None` without importing either provider and that mutating the provider's source mapping cannot mutate the returned report.

- [ ] **Step 2: Run dispatcher tests and verify RED**

```bash
uv run pytest -q tests/unit/controller/test_learning_report.py
```

Expected: missing `ControllerLearningReport` and `controller_learning_report`.

- [ ] **Step 3: Define the shared owned report envelope**

Start `common/cook_diagnostics.py` with the frozen internal report contract. Enforce non-blank controller/revision, positive schema version, recursively JSON-safe report state, and deep ownership. This module must not import any controller module.

- [ ] **Step 4: Replace controller branches with a lazy provider registry**

Use one data-only mapping:

```python
_PROVIDER_MODULES = {
    "mpc": "controller.model_learning.report",
    "pid_sp": "controller.pid_sp_learning",
}
_PROVIDER_FUNCTION = "diagnostic_learning_report"
```

`controller_learning_report()` imports only the selected module, calls the shared function name, verifies the return type and matching controller name, and returns an owned envelope. `controller_learning_report_revision()` delegates and returns `.revision`.

- [ ] **Step 5: Add provider adapters without duplicating report builders**

PID-SP adapter:

```python
def diagnostic_learning_report() -> ControllerLearningReport:
    report = backend_pid_sp_learning_report()
    return ControllerLearningReport(
        controller="pid_sp",
        schema_version=1,
        revision=report.revision,
        report=report.as_dict(),
    )
```

MPC adapter obtains `report, _records = backend_learning_report()` and wraps `report.as_dict()`. It must not duplicate evidence in the report envelope.

- [ ] **Step 6: Run provider and dispatcher tests green**

```bash
uv run pytest -q \
  tests/unit/controller/test_learning_report.py \
  tests/unit/controller/test_pid_sp_learning.py \
  tests/unit/mpc/test_model_evidence_report.py
```

Expected: all pass; unsupported controllers import no providers.

- [ ] **Step 7: Commit Task 3**

```bash
jj desc -m "Add generic learning report providers"
jj new
jj st
```

---

### Task 4: Validated cook diagnostics collector

**Files:**
- Modify: `common/cook_diagnostics.py`
- Test: create `tests/unit/common/test_cook_diagnostics.py`

**Interfaces:**
- Consumes: `read_control_trace_cook(cook_id)`
- Consumes: `read_model_evidence(cook_id=cook_id)`
- Consumes: `Callable[[str], ControllerLearningReport | None]`
- Produces: `collect_cook_learning_diagnostics(cook_id: str | None, report_provider: LearningReportProvider, *, read_trace: ReadControlTrace = read_control_trace_cook, read_evidence: ReadModelEvidence = read_model_evidence, clock_ms: ClockMs = wall_clock_ms, warn: WarningSink = logger.warning) -> CookLearningDiagnostics`
- Produces: `CookLearningDiagnostics.model_dump(mode="json")` matching envelope schema 1

- [ ] **Step 1: Write the complete happy-path RED contract**

Seed two typed session/update traces in insertion order: PID-SP first, MPC second. Seed compatible model evidence records with schema versions 2 and 3. Inject a provider returning one report per controller. Assert the literal envelope properties:

```python
bundle = collect_cook_learning_diagnostics(
    "cook-7",
    provider,
    read_trace=lambda cook_id: [pid_session, pid_update, mpc_session, mpc_update],
    read_evidence=lambda *, cook_id: [evidence_v2, evidence_v3],
    clock_ms=lambda: 1_787_490_000_000,
    warn=warnings.append,
)
assert bundle.cook_id == "cook-7"
assert bundle.controllers == ("pid_sp", "mpc")
assert [item.controller for item in bundle.reports] == ["pid_sp", "mpc"]
assert [item.schema_version for item in bundle.control_trace.records] == [5, 6]
assert bundle.control_trace.record_schema_versions == (5, 6)
assert bundle.model_evidence.record_schema_versions == (2, 3)
assert bundle.capture_errors == ()
```

The test data must use real `ControlTraceRecord` and `ModelEvidenceRecord` instances, not dictionary mocks.

- [ ] **Step 2: Run the collector test and verify RED**

```bash
uv run pytest -q tests/unit/common/test_cook_diagnostics.py::test_collects_complete_mixed_controller_cook_in_order
```

Expected: missing collector/envelope types.

- [ ] **Step 3: Implement envelope models and the happy path**

Define frozen models for:

- `CookDiagnosticCaptureError`
- `CookControlTrace(records, record_schema_versions)`
- `CookModelEvidence(records, record_schema_versions)`
- `CookLearningDiagnostics`

Derive controllers only from `TraceEventKind.SESSION`, preserving first-seen order. Validate every returned record has the exact requested cook ID. Compute each schema-version tuple as sorted unique values from retained records. Request reports in controller order and include only non-`None` supported reports.

- [ ] **Step 4: Write source-isolation and identity RED tests**

Add separate tests for:

```python
def test_trace_read_failure_keeps_evidence_and_names_error():
    warnings = []

    def failed_trace(cook_id):
        raise RuntimeError("trace unavailable")

    bundle = collect_cook_learning_diagnostics(
        "cook-7",
        provider,
        read_trace=failed_trace,
        read_evidence=lambda *, cook_id: [evidence_v3],
        clock_ms=lambda: 1_787_490_000_000,
        warn=warnings.append,
    )

    assert bundle.control_trace.records == ()
    assert [record.evidence_id for record in bundle.model_evidence.records] == [evidence_v3.evidence_id]
    assert [(error.source, error.code) for error in bundle.capture_errors] == [
        ("control_trace", "control-trace-read-failed")
    ]
    assert warnings == ["control_trace: trace unavailable"]
```

Write equivalent concrete tests for evidence failure (`model-evidence-read-failed`) and `report:mpc` failure (`report-read-failed`).

Also cover:

- `cook_id=None` → no persistence/provider calls, `cook_id is None`, one `cook-identity-invalid` error;
- no session records → trace retained, reports empty, one `trace-session-missing` error;
- cross-cook trace/evidence record → source data excluded and one stable validation error, never silently accepted;
- duplicate session controllers → one ordered controller/report;
- warning callback receives one message per capture error;
- an exception in top-level assembly returns a minimal valid bundle with `collector-failed`.

- [ ] **Step 5: Run failure tests and verify RED**

Run the whole new test module. Expected: failures for unimplemented isolation/fallback paths.

- [ ] **Step 6: Implement no-throw isolated collection**

Each source gets its own narrow `try/except Exception` around only the injected read/provider call, plus a final `except Exception` collector guard. This intentionally includes `sqlite3.Error` while still excluding `BaseException`, `KeyboardInterrupt`, and `SystemExit`. Preserve successful sources. Construct stable codes exactly as named in the tests and send the same detail to `warn`; a failing warning sink is contained and cannot replace the source error.

The public function must always return a validated `CookLearningDiagnostics`; its fallback may contain `cook_id`, capture timestamp, empty sources, and one `collector-failed` error, but no fabricated controller/report/record.

- [ ] **Step 7: Prove branch coverage**

```bash
uv run pytest -q \
  --cov=common.cook_diagnostics \
  --cov-branch \
  --cov-report=term-missing \
  tests/unit/common/test_cook_diagnostics.py
```

Expected: all tests pass and branch coverage is greater than 90%. Add behavior tests for actual uncovered branches; do not use coverage exclusions.

- [ ] **Step 8: Commit Task 4**

```bash
jj desc -m "Collect cook-scoped learning diagnostics"
jj new
jj st
```

---

### Task 5: Cookfile write/read integration

**Files:**
- Modify: `file_mgmt/common.py` optional JSON-member read helper
- Modify: `file_mgmt/cookfile.py:50-204`
- Modify: `controller/runtime/controller.py:444-476`
- Test: `tests/unit/file_mgmt/test_cookfile.py`
- Test: runtime controller test module that owns the Stop/cookfile call contract

**Interfaces:**
- Consumes: `collect_cook_learning_diagnostics()` from Task 4
- Consumes: `controller_learning_report()` from Task 3, injected by the runtime composition root
- Produces: required keyword `create_cookfile(*, learning_report_provider: LearningReportProvider) -> None`
- Produces: optional archive read value `cook_file_struct["learning_diagnostics"]: dict[str, JsonValue] | None`
- Produces: `read_optional_json_file_data(filename, member) -> tuple[JsonValue | None, str]`

- [ ] **Step 1: Run LSP references before changing `create_cookfile`**

Find every production/test callsite. The clean cutover requires every caller to pass the report provider; do not add a compatibility default that can silently create a report-less new cookfile.

- [ ] **Step 2: Write a failing new-archive contract**

Seed history, metrics with one non-blank `id`, typed trace/evidence data, and a fake report provider. Call `create_cookfile(learning_report_provider=provider)`. Open the ZIP and assert:

```python
assert "learning_diagnostics.json" in archive.namelist()
payload = json.loads(archive.read("learning_diagnostics.json"))
assert payload["schema_version"] == 1
assert payload["cook_id"] == metrics_id
assert payload["control_trace"]["records"]
```

Assert normal required members and final history flush still occur.

- [ ] **Step 3: Run the archive test and verify RED**

```bash
uv run pytest -q tests/unit/file_mgmt/test_cookfile.py::test_new_cookfile_contains_complete_learning_diagnostics
```

Expected: unexpected keyword argument or missing ZIP member.

- [ ] **Step 4: Add a reusable optional-member reader**

In `file_mgmt/common.py`, implement an optional JSON member read that distinguishes `KeyError`/missing ZIP member from malformed JSON or archive I/O. Missing returns `(None, "OK")`; malformed present content retains the existing error status behavior.

- [ ] **Step 5: Integrate capture into cookfile creation**

- Add `learning_diagnostics: None` to `_default_cookfilestruct()`.
- Derive the unique metrics cook identity once from the already-read metrics rows; pass `None` for missing/mixed identities.
- Call the generic collector before `flush_history()` with the injected report provider and events logger warning callback.
- Add `learning_diagnostics` to the JSON member write list.
- Serialize the validated model's JSON dump; do not serialize Pydantic/dataclass reprs.
- Keep primary archive creation and cleanup behavior unchanged.

- [ ] **Step 6: Migrate the runtime composition root**

Import `controller_learning_report` in `controller/runtime/controller.py` and call:

```python
create_cookfile(learning_report_provider=controller_learning_report)
```

Update every test fake to accept/assert the keyword. Preserve the narrow existing exception boundary around only cookfile creation.

- [ ] **Step 7: Write and pass legacy-read tests**

Add tests proving:

```python
legacy, status = read_cookfile(old_archive_without_diagnostics)
assert status == "OK"
assert legacy["learning_diagnostics"] is None
```

Also write/read a new archive and assert the returned diagnostics mapping equals the ZIP JSON. A present malformed diagnostics member must report an error; it must not be treated as absent.

- [ ] **Step 8: Write and pass invalid-identity/failure tests**

Cover missing/mixed metrics IDs and injected trace/evidence/report failures. The archive must still exist, required members must be intact, diagnostics must contain the expected structured error, and `flush_history()` must still occur exactly once.

- [ ] **Step 9: Run focused cookfile/runtime tests**

```bash
uv run pytest -q \
  tests/unit/file_mgmt/test_cookfile.py \
  tests/characterization/test_controller_loop_golden.py
```

Expected: all migrated caller contracts pass, including the Stop archive call, failure containment, actuator-off ordering, and history cleanup.

- [ ] **Step 10: Commit Task 5**

```bash
jj desc -m "Embed learning diagnostics in cookfiles"
jj new
jj st
```

---

### Task 6: Archive mutation preservation and API boundaries

**Files:**
- Modify only if tests expose a defect: `file_mgmt/common.py`
- Modify only if tests expose a defect: cookfile routes under `blueprints/api_files/` and `blueprints/cookfile/`
- Test: `tests/unit/file_mgmt/test_cookfile.py`
- Test: `tests/web/test_api_files_cookfile_comments.py`
- Test: `tests/web/test_api_files_cookfile_assets.py`
- Test: `tests/web/test_api_files_cookfile_write.py`
- Test: `tests/web/test_api_files_cookfile_read.py`

**Interfaces:**
- Consumes: optional `learning_diagnostics.json` member from Task 5
- Produces: byte-preservation contract for archive mutations
- Preserves: ordinary list/detail API response sizes unless they already return the full archive

- [ ] **Step 1: Write failing/passing characterization tests before production edits**

Create a cookfile with a distinctive diagnostics byte payload, then run each archive mutation used by title/comment/asset/thumbnail/repair flows. Reopen the ZIP and assert:

```python
assert archive.read("learning_diagnostics.json") == original_diagnostics_bytes
```

Also assert history list/detail JSON does not acquire a new diagnostics field unless the endpoint already returns the complete cookfile object.

- [ ] **Step 2: Run preservation and web tests**

```bash
uv run pytest -q \
  tests/unit/file_mgmt/test_cookfile.py \
  tests/web/test_api_files_cookfile_comments.py \
  tests/web/test_api_files_cookfile_assets.py \
  tests/web/test_api_files_cookfile_write.py \
  tests/web/test_api_files_cookfile_read.py
```

If all new tests pass against existing mutation code, retain the test-only contract and do not rewrite production code. If one fails, the failure must identify the exact operation that drops or rewrites the member.

- [ ] **Step 3: Apply the minimal preservation fix if RED**

Keep `update_json_file_data()` replacement scoped to the named member while copying every other ZIP entry's bytes and metadata unchanged. Do not add diagnostics-specific branches; preservation applies to every unknown member.

- [ ] **Step 4: Re-run preservation tests green**

Expected: exact byte preservation and unchanged ordinary API payloads.

- [ ] **Step 5: Commit Task 6**

```bash
jj desc -m "Preserve cook diagnostics across archive edits"
jj new
jj st
```

A test-only commit is valid if existing generic mutation behavior already passes.

---

### Task 7: Mixed-controller end-to-end archive contract

**Files:**
- Create: `tests/integration/test_cookfile_learning_diagnostics.py`
- Modify only for defects exposed by the integration test: production files from Tasks 1-5

**Interfaces:**
- Consumes: all prior task contracts
- Produces: one end-to-end proof that a shared cook identity survives PID-SP and MPC sessions into one `.pifire`

- [ ] **Step 1: Write the mixed-controller integration test**

Use the real SQLite stores with one cook ID. Append, in order:

1. PID-SP session and update containing a collecting learning snapshot;
2. PID-SP applied/frame records;
3. MPC session and update containing an evaluating snapshot;
4. MPC allocation/frame/applied records;
5. two compatible MPC model-evidence records.

Inject real-shaped normalized report envelopes for both controllers, create the cookfile, and assert:

```python
assert payload["controllers"] == ["pid_sp", "mpc"]
assert [r["controller"] for r in payload["reports"]] == ["pid_sp", "mpc"]
assert [r["controller"] for r in payload["control_trace"]["records"]] == expected_order
assert [r["evidence_id"] for r in payload["model_evidence"]["records"]] == expected_evidence_ids
assert payload["capture_errors"] == []
```

Open the archive through `read_cookfile()` and assert the same envelope. Expectations must be hand-authored literals, not produced by the collector.

- [ ] **Step 2: Run the integration test and verify RED or immediate contract coverage**

```bash
uv run pytest -q tests/integration/test_cookfile_learning_diagnostics.py
```

If it passes immediately because prior tasks fully implemented the contract, perform the mutation check in Step 3. If it fails, fix only the exposed ownership/order defect.

- [ ] **Step 3: Perform mutation checks**

Temporarily mutate each behavior one at a time and confirm the integration test fails:

- sort controllers alphabetically;
- read evidence without `cook_id` filtering;
- drop PID-SP learning snapshots;
- omit `learning_diagnostics.json`.

Restore production code after each expected failure and rerun green. Do not commit mutations.

- [ ] **Step 4: Run cross-domain focused suites**

```bash
uv run pytest -q \
  tests/integration/test_cookfile_learning_diagnostics.py \
  tests/unit/controller/test_pid_sp.py \
  tests/unit/mpc/test_mpc_controller.py \
  tests/unit/common/test_control_trace_schema.py \
  tests/unit/runtime/test_control_trace_session.py \
  tests/unit/common/test_cook_diagnostics.py \
  tests/unit/file_mgmt/test_cookfile.py
```

Expected: all pass.

- [ ] **Step 5: Commit Task 7**

```bash
jj desc -m "Verify mixed-controller cook diagnostics"
jj new
jj st
```

---

### Task 8: Final validation, cleanup, and review

**Files:**
- Modify only for validation defects: files owned by Tasks 1-7
- No new documentation unless implementation materially differs from the approved spec

**Interfaces:**
- Validates the complete approved contract; produces no new feature surface

- [ ] **Step 1: Run focused branch coverage**

```bash
uv run pytest -q \
  --cov=common.cook_diagnostics \
  --cov=controller.learning_report \
  --cov-branch \
  --cov-report=term-missing \
  tests/unit/common/test_cook_diagnostics.py \
  tests/unit/controller/test_learning_report.py \
  tests/integration/test_cookfile_learning_diagnostics.py
```

Expected: greater than 90% branch coverage for each new/substantially rewritten module. Fix uncovered observable contracts with tests; do not add pragmas or test implementation text.

- [ ] **Step 2: Run complete Python tests**

```bash
uv run pytest -q
```

Expected: zero failures. Record passed/skipped counts from fresh output.

- [ ] **Step 3: Run complete JavaScript tests and workspace typechecks**

```bash
bun run test
bun run typecheck
```

Expected: all workspaces exit 0. The feature is Python-only, but generated/shared contract regressions remain release blockers.

- [ ] **Step 4: Run targeted lint/format**

Use the repository's Ruff configuration on changed Python files and Biome only if a TypeScript file changed during implementation. Format after behavioral proof, then rerun the focused tests whose files were formatted.

- [ ] **Step 5: Verify archive behavior with a smoke scenario**

Run one real cookfile creation against an isolated temporary database populated with typed PID-SP and MPC records. Open the resulting ZIP using Python's `zipfile`, validate `learning_diagnostics.json` through `CookLearningDiagnostics`, and print only:

- member present;
- cook ID;
- ordered controllers;
- trace/evidence/report counts;
- capture error count.

Expected: member present, both controllers ordered PID-SP then MPC, non-zero trace/evidence/report counts, zero errors.

- [ ] **Step 6: Request independent code review**

Dispatch a read-only reviewer with the approved spec and the implementation range. Require findings on generic ownership, shutdown safety, complete record retention, schema compatibility, source isolation, archive preservation, and tests. Fix all Critical and Important findings; rerun affected focused tests.

- [ ] **Step 7: Confirm resting Jujutsu shape**

```bash
jj st
jj --no-pager log -r '@ | @-' --no-graph \
  -T 'commit_id.short() ++ "  " ++ bookmarks ++ "  " ++ description.first_line() ++ "\n"'
```

Expected: empty, undescribed `@` above the last implementation commit at `@-`; do not move or push a bookmark until the user chooses integration.
