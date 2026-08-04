# Shared Controller Control-Trace Design

**Date:** 2026-08-04  
**Status:** Approved for planning

## 1. Goal

Persist enough typed evidence to reconstruct a control-quality failure across the complete retained controller pipeline:

1. PID, PID-SP, or MPC computation;
2. MPC's coupled combustion allocation when applicable;
3. fixed-cycle or framed-pulse scheduling;
4. physical auger/fan application and safety inhibition;
5. applied-output feedback into the next controller update.

The trace is shared infrastructure for the only supported controllers: `pid`, `pid_sp`, and `mpc`. It replaces MPC's three-column CSV logger. There is no parallel file logger and no migration of old CSV files.

## 2. Storage authority

SQLite is the sole trace store, using the same `pifire.db` and `common.datastore` connection authority as the rest of PiFire.

Add this logical table shape:

```sql
CREATE TABLE control_trace (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms          INTEGER NOT NULL,
    session_id     TEXT NOT NULL,
    cook_id        TEXT,
    controller     TEXT NOT NULL,
    event_kind     TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    payload        TEXT NOT NULL CHECK(json_valid(payload))
);
```

Indexes support the real reads:

- `(session_id, id)` for replay in insertion order;
- `(cook_id, id)` for calibration/export of one cook;
- `(ts_ms)` for 30-day retention pruning;
- `(controller, event_kind, ts_ms)` only if query-plan evidence shows it is needed. Do not add speculative indexes.

The database schema version advances. Existing databases create the new empty table; no data migration is required.

## 3. Retention

Trace rows are retained for 30 days by timestamp. A bounded prune runs through the same recorder during startup and at most daily thereafter. It deletes rows older than `now - 30 days` in bounded batches and repeats until caught up.

Retention is time-based, not a row count: a high-frequency failure must not evict recent context prematurely, and a quiet installation must not retain data indefinitely.

Pruning also occurs during recorder startup so an installation that was offline for weeks is corrected without waiting for a new threshold. Tests use an injected clock; no wall-clock sleeps.

## 4. Typed record contract

Internal discriminants use enums, never ad-hoc strings:

- `ControllerType`: `PID`, `PID_SP`, `MPC`;
- `TraceEventKind`: `SESSION`, `CONTROL_UPDATE`, `ALLOCATION`, `ACTUATION_FRAME`, `APPLIED_OUTPUT`, `SAFETY_EVENT`, `MODEL_EVENT`, `RECORDER_GAP`;
- `ActuationMode`: `FIXED_CYCLE`, `FRAMED_PULSE`;
- existing `OutputSource` for controller/manual/lid/fan-assist provenance;
- explicit enums for safety/inhibit and model events.

Runtime producers create immutable, slotted dataclasses. Pydantic is the validation and serialization boundary:

- a versioned `ControlTraceRecord` Pydantic model validates indexed envelope fields;
- its payload is a discriminated union of Pydantic dataclasses, one per event kind;
- JSON serialization rejects NaN and infinity;
- database reads validate the stored schema before returning typed records;
- an unsupported future schema version is reported, not guessed at.

No controller or runtime code writes arbitrary dictionaries directly to the table.

## 5. Session record

One `SESSION` record opens every Hold controller session and captures replay context once rather than duplicating it on every sample:

- controller type and controller configuration after sanitization;
- temperature units and control period;
- model snapshot revision/provenance where applicable;
- cycle authority (`u_min`, `u_max`, `HoldCycleTime`) for fixed-cycle controllers;
- pulse slot/frame authority for MPC;
- fan authority, PWM capability, and fan min/max bounds;
- active setpoint and ambient assumption;
- software/build/schema versions.

Secrets and unrelated settings are never copied into the trace.

## 6. Common control-update record

Every completed controller update records:

- monotonic and wall timestamps;
- result revision and age;
- control period and observed `dt`;
- setpoint and measured primary temperature;
- raw controller output and bounded requested output;
- active actuation mode;
- prior interval's requested and realized auger duty;
- requested and applied fan duty where available;
- output-source and inhibit enums;
- controller-specific diagnostic payload.

The common envelope allows PID/PID-SP/MPC records to be queried uniformly.

## 7. Controller-specific diagnostics

### 7.1 PID

Record the values needed to recompute one update:

- error;
- proportional, integral, and derivative contributions;
- integral accumulator and clamp state;
- derivative input/state;
- proportional band/gains and center used;
- previous temperature and update timestamp;
- raw and final output.

### 7.2 PID-SP

Record the PID fields plus:

- measured rate of change;
- predicted temperature and predicted error;
- `tau`, `theta`, stable window, and center factor used;
- new-target state, target-change temperature/time;
- which reset/overshoot branch fired.

Branch identity uses an enum or boolean fields, not parsed prose.

### 7.3 MPC

Record:

- measured temperature and full state-estimate vector with stable field names;
- disturbance estimate;
- model revision/provenance;
- raw policy firing load, equilibrium feed-forward, residual move, and bounded firing load;
- policy kind and failure state;
- solve start/end or duration;
- result age, deadline miss count, and stale/recovered transition;
- predicted feasibility/steady load where calculated.

A varying delay-state vector remains a JSON array paired with names; it is not spread into variable SQL columns.

## 8. Allocation record

MPC emits an `ALLOCATION` record for every accepted firing-load result:

- normalized scalar combustion load;
- auger duty requested by the coupled allocator;
- fan duty requested by the same allocator;
- `u_max`, fan min/max, and whether MPC had fan authority;
- clamp decisions and their enum reason;
- allocator/schema revision.

This record proves whether a bad physical command originated in MPC or in the allocator. PID/PID-SP do not emit allocation records because their scalar output is already auger duty.

## 9. Actuation records

### 9.1 Fixed cycle

One `ACTUATION_FRAME` record per PID/PID-SP Hold cycle captures:

- requested/raw duty;
- bounded duty and the `u_min`/`u_max` bounds used;
- cycle start/end, on-time, and off-time;
- actual auger-on seconds and transition count;
- fan-assist state;
- manual/lid inhibition and actual output state.

### 9.2 Framed pulse

One record per MPC scheduler frame captures:

- 2 s pulse slot and 20 s frame length;
- command/result revision used;
- requested combustion load and requested auger duty;
- fractional on-time credit before and after the frame;
- scheduled on-time and actual delivered on-time;
- transition count and actual end state;
- requested/applied fan duty;
- any skipped frame, stale command, manual/lid/safety inhibit, or reset reason.

Skipped runtime frames are recorded as skipped; they are never replayed as delayed heat.

## 10. Applied-output and event records

`APPLIED_OUTPUT` records carry exactly what is fed back to the controller: realized auger duty, realized/inverted combustion load when applicable, actual fan duty, interval boundaries, sample completeness, and `OutputSource`.

`SAFETY_EVENT` records cover lid detection/clear, manual takeover/release, Stop/Error, universal temperature guard, controller fallback/reconfigure, and scheduler reset.

`MODEL_EVENT` records cover model restore/adopt/reject/refit and model-schema invalidation.

## 11. Batched recorder

`ControlTraceRecorder` owns a bounded in-memory batch. Producers append already-typed dataclasses; no separate writer thread, queue, or storage mechanism exists.

Hold calls `flush_due(now)` after actuation. Every five seconds it validates and writes the accumulated records to SQLite in one transaction. A successful flush clears the batch. Teardown performs one final best-effort flush, but normal persistence occurs continuously throughout the cook.

A failed flush retains its records for the next interval and surfaces one deduplicated warning; recovery clears the warning. A bounded emergency cap prevents unbounded memory growth during a prolonged database outage. If the cap is reached, the recorder counts the lost records and persists one `RECORDER_GAP` with the count and time range when storage recovers. Silent loss is prohibited.

Thirty-day pruning runs at recorder startup and at most once per day thereafter, in bounded delete batches. Normal record flushing is not deferred behind pruning.

## 12. Accessors and tooling

All reads/writes go through typed datastore accessors:

- append a batch of `ControlTraceRecord`;
- read a session/cook in insertion order;
- query a bounded time range;
- prune before a timestamp;
- delete a session for tests/administration.

`controller.update_mpc` stops accepting production CSV log paths. It selects a cook/session from SQLite, validates typed trace rows, and extracts MPC applied-output samples for fitting. Missing, gapped, inhibited, or mixed-schema intervals are reported explicitly.

A replay tool consumes one session and verifies:

- controller result revisions are monotone;
- allocator outputs match recorded inputs/bounds;
- scheduler frame accounting reproduces scheduled duty;
- applied-output intervals reconcile with actual delivered on-time;
- safety events explain resets/inhibits.

## 13. Settings cutover

Remove MPC `log_data` and `log_path` options from `controller/controllers.json`, defaults, generated frontend types, fixtures, and tests. Delete the CSV writer/reader code.

Tracing is always available as shared runtime infrastructure and requires no per-controller path setting. The database's fixed 30-day retention is the storage bound.

No old CSV is imported, converted, deleted from user storage, or silently consulted.

## 14. Verification

Required proof:

1. Pydantic round trips every event dataclass and rejects invalid/non-finite payloads.
2. Database accessors return typed records in insertion order.
3. Rows older than 30 days are pruned while boundary/new rows remain.
4. Records flush in one ordered SQLite transaction every five seconds during an active cook.
5. Flush failure retains the batch; emergency-cap overflow emits a gap record and one warning.
6. PID and PID-SP traces contain recomputable controller terms.
7. MPC traces connect solve → allocation → frame → applied feedback by revision.
8. Manual, lid, Stop/Error, fallback, and reconfigure paths emit/reset correctly.
9. Replay detects a deliberately corrupted frame and accepts an intact trace.
10. Calibration extraction from seeded database records reproduces the committed MAK fit.
11. No runtime CSV logger, reader, path setting, or parallel trace store remains.
