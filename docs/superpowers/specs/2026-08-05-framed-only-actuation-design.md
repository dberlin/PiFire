# Framed-Only Hold Actuation — Design

**Date:** 2026-08-05  
**Status:** Approved  
**Scope:** Hold-mode controller actuation, control traces, replay, and calibration

## 1. Context

The active controller catalog contains PID, PID-SP, and MPC. PID and PID-SP inherit `PIDControllerBase.actuation_mode()`, and MPC implements the same contract directly; all three return `ActuationMode.FRAMED_PULSE`.

`ActuationMode.FIXED_CYCLE` therefore no longer selects an active production controller. It remains only in fallback/default behavior, the retired Hold scheduler branch, historical trace parsing/replay/calibration, legacy experiments, and tests.

Maintaining both branches has a cost: every Hold change must preserve two timing disciplines even though production can select only one. The fixed-cycle path also reintroduces the long-window `u_min` floor that framed pulse scheduling was introduced to avoid.

## 2. Decision

PiFire Hold mode will support framed-pulse actuation only.

`ActuationMode` remains as the explicit controller and serialized trace contract, but its only value is `FRAMED_PULSE`. Keeping the enum avoids unrelated API churn and preserves an assertion about the scheduler that produced a trace. The fixed-cycle value, scheduler behavior, trace payload, replay logic, calibration logic, executable experiments, and tests are removed together.

Existing schema-2 trace rows are intentionally incompatible. Normal readers stop exposing them immediately. Existing bounded recorder maintenance deletes them off the controller hot path.

## 3. Goals

1. Make framed pulse scheduling the only Hold actuation path.
2. Remove `ActuationMode.FIXED_CYCLE` and every executable producer or consumer of it.
3. Preserve PID, PID-SP, and MPC controller math and solve cadence except where cadence was previously derived from the retired scheduler branch.
4. Preserve pulse inhibit semantics: manual, lid, safety, and stale-command frames do not accumulate catch-up credit.
5. Preserve applied-output feedback using realized hardware output.
6. Invalidate and prune historical fixed-cycle traces without delaying controller execution.
7. Leave no compatibility alias, hidden fallback, or deprecated fixed-cycle path.
8. Remove the fixed-cycle-only fan-assist path, `FanPidEnabled` setting/UI, and `OutputSource.FAN_ASSIST`; Smoke Plus remains unchanged.

## 4. Non-goals

- Removing `ActuationMode` itself.
- Redesigning PID, PID-SP, MPC, or the coupled allocator.
- Changing pulse quantum or frame timing.
- Removing `HoldCycleTime`, `u_min`, or `u_max` globally. Other operating modes and configuration surfaces are outside this cutover and must be evaluated independently.
- Rewriting committed JSON evidence. Historical artifacts and isolated mathematical comparison fixtures may retain the text `fixed_cycle`; production/runtime code may not produce it.
- Adding a new actuation mode.

## 5. Controller contract

`ControllerBase.actuation_mode()` returns `ActuationMode.FRAMED_PULSE`. Active controllers may inherit that implementation. Redundant identical overrides should be removed where doing so does not obscure controller-specific behavior.

`ControllerRunner` continues to expose a typed `ActuationMode` for trace construction, but it has no fixed fallback. Missing custom implementations inherit the framed base contract; invalid return types remain errors.

`get_control_period()` remains the controller solve-cadence contract. A `None` period in Hold resolves to the framed scheduler's frame duration, never `HoldCycleTime`.

## 6. Hold runtime

Hold initialization always creates `PulseScheduler(self.grill.auger_timing())`, clears legacy cycle timing state, initializes pulse telemetry, and turns the auger off before accepting a scheduled pulse.

The runtime path is single and explicit:

1. Obtain the latest atomic controller result.
2. Normalize and allocate auger/fan demand.
3. Submit the command revision to the framed scheduler.
4. Advance complete two-second quanta.
5. Apply lid/manual/safety/stale inhibits without retaining delivery credit.
6. Record requested and realized frame quantities.
7. Feed realized output back to the controller.

The fixed-cycle timer/update branch, fixed-cycle trace bookkeeping, and Hold-only fixed-cycle helpers are deleted. Shared cycle helpers used by Smoke, Startup, Prime, or SmartStart remain.

## 7. Trace schema cutover

`TRACE_SCHEMA_VERSION` advances from 2 to 3.

Schema 3:

- accepts only `ActuationMode.FRAMED_PULSE`;
- removes `FixedCycleFramePayload` from the discriminated payload union;
- removes fixed-cycle-only timing fields from session/update payloads where they no longer express a live contract;
- removes the fixed-cycle-only `FAN_ASSIST` output source;
- retains pulse slot/frame timing and requested/realized output fields;
- validates PID, PID-SP, and MPC diagnostics against the framed mode only.

Replay validates only framed pulse frames and their allocation/applied-output relationships. Calibration accepts only framed MPC sessions and no longer contains a fixed-cycle reconstruction or warm-up rule.

No schema-2 compatibility model or alias is retained.

## 8. Invalidation and pruning

Trace read queries filter to `TRACE_SCHEMA_VERSION`. A schema-2 row therefore cannot make an otherwise valid schema-3 session read fail and cannot be selected for replay or calibration.

A bounded datastore operation deletes rows whose `schema_version` is older than the current version. It never deletes rows from a newer schema, so running an older binary cannot destroy future trace data. `ControlTraceRecorder` invokes one bounded batch as part of each eligible retention-maintenance pass, using the same degraded-retention warning discipline as age-based pruning. Later passes continue until no older rows remain.

The operation must:

- validate its positive batch limit;
- delete at most that limit per transaction;
- never run in the controller result, scheduler advance, or hardware output path;
- report failures without interrupting Hold;
- be deterministic and covered by datastore and recorder tests.

## 9. Experiments and artifacts

Executable scripts under `docs/superpowers/experiments/` must not import or emit `ActuationMode.FIXED_CYCLE`. A legacy comparison that genuinely needs fixed-cycle simulation must either use an isolated mathematical fixture with no production runtime dependency or be retired. Existing committed JSON output remains historical evidence and is not rewritten merely to erase the old string.

## 10. Error handling

- An invalid actuation-mode return remains a configuration/programming error.
- Schema-3 rows are invisible to normal readers and asynchronously pruned; they are not partially decoded.
- A failed incompatible-row prune sets the recorder's retention-degraded warning and retries later.
- Scheduler or hardware failures continue through existing safety handling; this cutover adds no fallback scheduler.

## 11. Verification

Focused behavioral verification must cover:

1. PID, PID-SP, and MPC all report framed-pulse actuation.
2. A controller inheriting `ControllerBase` defaults to framed pulse.
3. Hold always creates and uses `PulseScheduler`.
4. Low requested duty is represented over successive frames without a fixed `u_min` floor.
5. Manual, lid, safety, and stale-command inhibits discard credit and do not catch up.
6. Requested versus realized duty and applied-output feedback remain correct.
7. Schema-3 records serialize and round-trip; schema-2 rows are excluded.
8. Incompatible-row pruning is bounded, retried, and non-fatal.
9. Replay and calibration accept valid framed sessions and reject incomplete/corrupt relationships.
10. No production/runtime source imports `ActuationMode.FIXED_CYCLE`, selects a fixed Hold scheduler, or emits a fixed-cycle trace; isolated historical comparison fixtures remain permitted.
11. Settings defaults, Pydantic schema, generated web types/defaults, and Work Mode UI contain no `FanPidEnabled`.

After focused tests, run the platform-compatible Python suite and the frontend typecheck, generated-type drift check, focused Work Mode settings test, full frontend unit suite, and production build.

## 12. Risks and controls

### Hidden fixed-cycle caller

A custom controller may have relied on `ControllerBase`'s old default. The new default is framed, making the behavior change explicit and safe rather than preserving an unavailable scheduler.

### Cadence drift

PID previously inherited a `None` control period. Hold must resolve that to the pulse frame duration after the fixed branch disappears. A cadence test pins this contract.

### Historical trace query failure

Filtering by schema before Pydantic decoding prevents incompatible rows from poisoning reads. Bounded pruning then removes the inaccessible data.

### Accidental settings removal

`HoldCycleTime`, `u_min`, and `u_max` have consumers outside framed Hold. This change removes only Hold fixed-cycle dependencies; broader settings cleanup requires separate evidence.

### Apparent legacy strings

Committed experiment JSON may still contain `fixed_cycle`. Verification distinguishes executable source from immutable historical evidence.

## 13. Acceptance criteria

The cutover is complete when:

- `ActuationMode` has no `FIXED_CYCLE` member;
- no production or executable experiment path implements fixed-cycle Hold actuation;
- Hold has one framed scheduler path;
- schema-2 rows are excluded and boundedly pruned;
- fixed-cycle trace/replay/calibration types and branches are gone;
- fixed-cycle-only fan assist is absent from runtime state, applied-output classification, settings, generated contracts, and UI;
- all active controllers and inherited defaults select framed pulse;
- focused behavioral tests, the platform-compatible Python suite, and the frontend gates pass;
- no compatibility shim or deprecated alias remains.
