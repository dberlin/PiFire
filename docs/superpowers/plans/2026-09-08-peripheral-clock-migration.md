# Peripheral Clock Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove wall-clock duration authority from watchdogs, controller liveness, cooking-action timers, bounded peripheral I/O, notification throttles, and display freshness while preserving calendar provenance and strict learning evidence.

**Architecture:** Execute safety work first: identity-qualified heartbeat/watchdog continuity, then one controller-owned duration timer state machine. Independently migrate local retry/poll/debounce intervals to monotonic callables, and project explicit backend elapsed/remaining snapshots to clients that extrapolate only on their own local clocks. Shared-clock, probe, and learning contracts are named dependencies, not implied completed changes.

**Tech Stack:** Python 3.14+, SQLite transactional control FIFO, Flask/SocketIO, PySide6 and pygame/OLED displays, TypeScript/React/Expo, existing pytest/rstest/Jest/Playwright tooling.

**Spec:** `docs/superpowers/specs/2026-09-08-clock-domain-separation-design.md`

**Integrated baseline:** The coupled learning implementation is now in the working tree: trace 10, observation 4, evidence 6, database 13, required independent wall endpoints, and callable clocks through Hold/runner factories and reconfiguration. References below to Main's “in-flight” work describe the drafting baseline, not remaining implementation. Reconcile those steps against the shared spec before executing; the destination shared `Clock` API and remaining timers are still proposals.

## Global Constraints

- Proposal only: the requested deliverable is an implementation plan. The source-derived audits are not executed reproductions, and no validation commands were run during plan creation.
- Read `AGENTS.md`, this spec, and the six sibling plans before implementation. Preserve safety admission, durable cook identity, estimator seeds, strict replay/trace/evidence validation, mutation expectations, and golden observable contracts.
- Preserve `MODEL_SCHEMA = 7` and `versions 3 through 6 are migration input only.`. PID-SP `Controller.update()` continues returning raw signed historical demand; physical allocation remains bounded. This plan authorizes neither model-version nor controller-return changes.
- Shared clock API: `wall_time() -> float` epoch seconds, `monotonic() -> float` same-boot steady seconds, `sleep(seconds)` unchanged. `RealClock` uses `time.time/time.monotonic`; `ManualClock(wall_start=0.0, monotonic_start=0.0)` moves axes independently; ordinary advance/sleep moves both, wall jump moves wall only.
- `Clock.now` is removed only in the coordinated shared-mode cutover. Local independent services can take `Callable[[], float]` monotonic dependencies rather than importing controller runtime into common/web modules.
- Wall labels, logs, calendar filtering and trace envelopes stay wall. Durations never derive from wall endpoints; never infer a timestamp domain from magnitude or replace `Date.now()` with `performance.now()` while subtracting an epoch.
- Persisted monotonic freshness requires known boot/clock identity. Unknown identity cannot be live. Latest evidence means durable causal order, not greatest wall timestamp.
- Suspend is not active-control exposure. Detect it independently, invalidate stale history, safe-stop and reacquire; do not credit elapsed suspended time to actuators, probes, recovery, recipes, or learning.
- Release/publication follows `scripts/exact_revision_gate.py` exactly; direct `jj git push`, raw Git operations and reused/edited evidence are prohibited.

**Authorization update:** The user approved Main's coupled learning boundary for implementation now: capture, replay/evidence semantics and the necessary pulse/PID-SP timestamp producers. That integration is in flight, not verified complete by this plan. The shared spec explicitly targets trace schema 10 from source schema 9 and trajectory observation schema 4; use production constants after integration, not an invented additional version. `FrameObservation` now has required `wall_start_ms`/`wall_end_ms` provenance alongside strictly monotonic `frame_start_s`/`frame_end_s`; Main owns its builders and fixtures. Remaining work in this plan is documentation-only proposal. Suspend detection, the proposed 0.25s offset tolerance/platform behavior, and any 1s active-loop-gap threshold need separate review and are not in the approved clock correction. Do not alter PID derivatives, Smith correction strategy or predictor history/coverage policy; only the agreed clock coordinates change.

## Dependencies and implementation order

1. **Safety tier A:** Task 1 watchdog/heartbeat/clock identity. The local watchdog fix can land first; persisted heartbeat schema and readers ship together. This task supplies identity primitives to the probe plan and shared-mode plan.
2. **Safety tier A:** Tasks 2–3 cooking-action timers. Recipe/user timer creation, FIFO mutation, expiry, pause/resume, persistence and every UI contract form one migration. Do not call this notification or display cleanup: expiry can advance a recipe, enter Shutdown or lower the Hold setpoint.
3. **Safety tier B:** Task 4 bounded queues and distance sampling/serial operations. Their delays can mask stale hopper data or block work; existing watchdog protections remain.
4. **Operator visibility:** Task 5 display polling/interaction, Task 6 notification throttles, then Task 7 explicit elapsed/remaining UI. Probe plan owns health acquisition/cache, Qt health polling, mobile receipt freshness and last-reading age; do not implement these a second time.
5. **Evidence gate:** Task 8 records remaining learning/replay authority obligations and consumes Main's implementation. It is not permission to defer broken importer admission or causal-order migration and still ship the coherent control-axis cutover.
6. Shared-mode plan supplies status `elapsed_seconds` (mode), `remaining_seconds` (Startup/Reignite/Prime/Shutdown, otherwise null), `lid_open_remaining_seconds`, `cook_elapsed_seconds` (null when provenance is unknown), while keeping epoch `start_time`, `startup_timestamp`, `lid_open_endtime` as provenance. Peripheral Task 7 owns all display/API/browser consumers.
7. PID-SP, pulse scheduling and shared-mode control axes have a joint deployment gate. Shared mode owns pre-actuation continuity check, safe output handling, runtime-generation rotation and state rebuilding. Peripheral owns the common identity helper and timer authority used by that check. Learning/trace boundary is Main-owned and must be complete before that gate is released.

## Producer–consumer inventory

| Priority | Producer/state | All relevant readers/callers and tests |
|---|---|---|
| A | `common/process_mon.py:Process_Monitor.__init__/heartbeat/_heartbeat_check` | `controller/runtime/modes/base.py` constructs 30s control monitor with `restart_control`; preserve `common/system.py` real-hardware recovery gate; `tests/unit/system/test_process_monitor.py`. |
| A | `controller/runtime/heartbeat.py:stamp_control_heartbeat`, `_last_write`, `reset_for_tests` | Idle and active-loop callers; `common/persistence/runtime.py:CONTROL_HEARTBEAT_KEY/read_control_heartbeat/CONTROL_HEARTBEAT_STALE_AFTER`; `blueprints/mobile/socket_io.py:_check_control_status`; `tests/unit/controller/test_heartbeat.py`, `tests/web/test_control_liveness_not_sticky.py`. |
| A | `common/api_commands.py:_cmd_set_timer`, `_timer_start_with_options`; `common/control_delta.py` envelope `_OP_FIELDS`, validators, timer op dispatch; `common/defaults.py` timer defaults | `common/persistence/control.py:execute_control_writes`; `common/persistence/protocols.py`; `controller/runtime/store.py:InMemoryStore/SqliteStore`; active/idle drain calls. `tests/unit/common/test_control_delta_{envelope,timer_ops,apply}.py`, `tests/unit/datastore/test_sqlite_store_parity.py`, `tests/characterization/test_control_delta_seam.py`. |
| A | Recipe timer creation in `controller/runtime/modes/base.py:_setup_recipe_triggers`; user timer commands | `controller/runtime/controller.py` duplicate idle expiry; `notify/notifications.py:check_notify` timer expiry and Shutdown/keep-warm/recipe transitions; `common/web_contracts/core.py:TimerPayload`; `blueprints/mobile/socket_io.py` timer payload; `packages/pifire-core/src/command.ts`; `web-react/src/helpers/timer/{timerState,timerVisibility}.ts`, TimerBar/TimerModal and mobile command consumers. |
| B | `common/common.py:get_system_command_output` wait deadline | `blueprints/api/routes.py` system-command route; `blueprints/api_wizard/routes.py` Bluetooth scan 6s; `common/system.py` system info calls; `tests/unit/common/test_system_command_output_queue.py`, `tests/unit/runtime/test_system_commands.py`. Socket liveness no longer uses this queue. |
| B | `distance/_sampled_base.py:_note_cycle_failed/_sensing_loop/_take_sample`, backoff, sample interval, slow-cycle measurement; `distance/sen0628.py:_recv_packet` | `hcsr04`, `sen0628`, `vl53l0x/l1x/l4cd` via shared bases; `distance/_serial_tof_base.py` serial timeout and open watchdog; existing `distance/_tof_base.py` monotonic read deadline is preserved. Tests listed in Task 4. |
| Visibility | `display/_base_dsi.py`, `display/ili9341f.py` refresh stamps; `display/qtbackend.py` settings/idle timestamps | Actual dashboard data fetch, output/backlight through `display/qtapp.py`; health polling belongs to probe plan. `tests/ui/test_display_feeder.py`, `test_pygame_qt_drivers.py`, `test_qtbackend.py`, `test_qtapp_power.py`. |
| Visibility | `display/_base_fixed.py`, `_base_flex.py`, `_button_input.py`, `_encoder_input.py`, `ili9341f.py` splash/text/menu/debounce producers | `pygame_240x320.py`, `pygame_240x320b.py`, `pygame_64x128.py`, `ssd1306.py`, `ssd1306b.py` consumers; fixed/flex/driver UI tests. |
| Visibility | `notify/mqtt_handler.py` connection retry, `notify/notifications.py` publish timestamps and hopper warning last-check, `notify/influxdb_handler.py` enqueue throttle, `notify/wled_handler.py` cooldown | MQTT callbacks and mode-change bypass, WLED event/state paths, Influx UTC point stamps, persisted notification defaults; notify tests in Task 6. |
| UI contract | Backend epoch cook/mode/lid endpoints and user timer start/paused/end | `display/_base_fixed.py`, `_base_flex.py`, `qtbackend.py`; `web-react/src/helpers/dashboard/{countdowns,cookTime}.ts`; `helpers/clock.ts`, `helpers/timer/timerState.ts`, TimerBar/TimerModal; package dash types and mobile views. |
| Not duplicated | Thermocouple wall/monotonic report mismatch, cloud cache, mobile receipt clocks, `LAST.ts` age | `2026-09-08-probe-clock-contract-migration.md`, all five tasks; these are mandatory dependencies, not exclusions from the release. |
| Evidence gate | `controller/model_learning/trace.py`, `controller/runtime/control_trace_session.py`, `file_mgmt/cookfile.py`; trajectory, evidence, confidence and activation stores/consumers | Main's learning/trace contract and Task 8 ledger below. |

At execution use LSP references before exported signature changes and structural search for dynamic/store protocol dispatch and literal timer/status keys. Include factories, in-memory parity stores, fake stores, serializers, generated wire models, experiments, smoke tools, replay/import tools, fixtures, characterization and mutation consumers. Keep a single current-contract builder importing production constants; historical fixture payloads stay literal. Shared test helpers live under `tests/fakes`, `conftest.py` or `_`-prefixed modules, never collected-test imports.

### Task 1: Safety clock identity, watchdog and persisted liveness

**Files:** create `common/clock_domain.py`; modify `common/process_mon.py`, `controller/runtime/heartbeat.py`, `common/persistence/runtime.py`, socket `_check_control_status`; integrate runtime context construction and continuity hook with the shared-mode owner; add `tests/unit/common/test_clock_domain.py`, extend heartbeat/watchdog/web tests.

**Interfaces:**

**Separate policy gate:** The helper's suspend-offset detector and threshold below are a concrete design proposal, not approved implementation scope. Review them before implementing the persisted freshness/resume portion. The independent `Process_Monitor` monotonic delta and heartbeat write-throttle fixes do not require accepting a new suspend or one-second latency policy. No implementation may substitute an arbitrary gap threshold for the existing watchdog contract.

```python
CLOCK_STAMP_SCHEMA = 1
CONTROL_DISCONTINUITY_SECONDS = 60.0

# ClockStamp is a frozen, validated serializable value with these exact fields:
# schema_version: int; boot_id: str | None; runtime_id: str
# observed_monotonic_s: float; observed_wall_s: float
# suspend_offset_s: float | None
# Booleans/nonfinite coordinates are invalid; missing platform identity stays None.

# RuntimeClockDomain(clock, *, boot_id: str | None, boottime: Callable[[], float])
# capture() -> ClockStamp
# rotate_runtime() -> str
# continuity_lost(previous: ClockStamp, current: ClockStamp,
#                 *, max_active_gap_s: float = CONTROL_DISCONTINUITY_SECONDS) -> bool
# stamp_age_s(stamp: ClockStamp, *, monotonic_s: float,
#             boot_id: str | None, runtime_id: str | None,
#             suspend_offset_s: float | None) -> float | None
```

The supplied `clock` has the shared `wall_time/monotonic` methods; common/web code does not import controller runtime at execution. Linux boot identity comes from `/proc/sys/kernel/random/boot_id`; read once and reject unreadable/empty/invalid UUID. Generate `runtime_id` with UUID4 for each control generation, not from wall time. `capture()` pairs wall/monotonic/`time.clock_gettime(time.CLOCK_BOOTTIME)` and records `boottime - monotonic`; unsupported platform boottime is unknown, not a guessed duration. A web reader has its own local boot/offset samples and accepts report runtime identity only from a current heartbeat.

- [ ] First add the watchdog boundary regression: heartbeat at monotonic 100 and epoch 1,800,000,000; jump wall ±3600; monotonic 129.9 must not set critical error or call recovery; monotonic 130.1 must execute the existing timeout path exactly once. Keep monitor thread stop/join cleanup deterministic. Test elapsed deadline behavior, not calls to `time.monotonic`.
- [ ] Inject `monotonic=time.monotonic` into `Process_Monitor`; use it for constructor stamp, `heartbeat()` and `_heartbeat_check`. Keep daemon lifecycle, relative sleeps, timeout comparison strictness, logs, mode Error, critical_error and hardware-only recovery intact. Do not suppress a genuine timeout because wall moved.
- [ ] Implement `stamp_age_s` so it returns unknown for mismatched/unknown boot or runtime identity, missing/nonfinite offset, future monotonic coordinates or a resume offset discontinuity. Only valid same-domain nonnegative elapsed ages are returned:

```python
def stamp_age_s(stamp, *, monotonic_s, boot_id, runtime_id, suspend_offset_s):
    if (not boot_id or not runtime_id or stamp.boot_id != boot_id
            or stamp.runtime_id != runtime_id or stamp.schema_version != CLOCK_STAMP_SCHEMA):
        return None
    values = (stamp.observed_monotonic_s, monotonic_s,
              stamp.suspend_offset_s, suspend_offset_s)
    if any(value is None or isinstance(value, bool) or not math.isfinite(value) for value in values):
        return None
    if suspend_offset_s - stamp.suspend_offset_s > CONTROL_DISCONTINUITY_SECONDS:
        return None
    age_s = monotonic_s - stamp.observed_monotonic_s
    return age_s if age_s >= 0 else None
```

`continuity_lost` uses this check against `previous` and `current`, plus the optional configured maximum active gap. Wall regression alone is not continuity loss. `rotate_runtime()` changes identity only after the shared-mode safe-stop; a writer must not silently rotate and continue heating with stale history.

- [ ] Replace heartbeat float with `ClockStamp` object serialized at the same key. Keep the wall field for provenance, throttle `_last_write` using monotonic with `None` first-write sentinel, and publish on both idle and active ticks only after the continuity hook has succeeded. Before any first current heartbeat, old reports cannot claim the new generation.
- [ ] Change `read_control_heartbeat` to validate the object, not coerce a legacy scalar. Change `_check_control_status` to explicitly set unknown/down for missing, malformed, foreign-boot, suspended or stale heartbeat; remove optimistic “never stamped, leave previous True” behavior. Proposed UI policy: use “Control status unknown” when no verifiable stamp exists rather than claim alive. Existing boolean wire can remain false until an explicit unknown qualifier is added to the shared health view; no sticky prior true state.
- [X] Publish the first heartbeat at zero; wall rollback cannot suppress it. Missing/scalar/foreign/stale heartbeat is unknown. A reader must invalidate before the writer runs when BOOTTIME-minus-MONOTONIC increases strictly greater than 60 seconds; exactly 60 seconds remains valid. Same-boot restart requires a real heartbeat with the new runtime identity.
- [X] Wire `continuity_lost` before probe/excitation/actuation and idle timer work. Shared mode owns safe outputs, state rebuild, generation rotation and no history backfill. The approved actual monotonic observation-gap threshold is strictly greater than 60 seconds, separate from the 30-second watchdog and 15-second published-heartbeat freshness threshold.

**Representative regression using the planned value type:**

```python
def test_wall_regression_does_not_change_trusted_age():
    boot_a = "9c898927-5e50-4c98-9e50-ae092e623629"
    boot_b = "b857949a-366b-49ed-b9d1-1c8c86e1537e"
    run_a = "01b4a094-8a2c-4780-915d-081f5a850a97"
    stamp = ClockStamp(schema_version=1, boot_id=boot_a, runtime_id=run_a,
                       observed_monotonic_s=100.0, observed_wall_s=1_800_000_000.0,
                       suspend_offset_s=3.0)
    assert stamp_age_s(stamp, monotonic_s=116.0, boot_id=boot_a,
                       runtime_id=run_a, suspend_offset_s=3.0) == 16.0
    assert stamp_age_s(stamp, monotonic_s=116.0, boot_id=boot_b,
                       runtime_id=run_a, suspend_offset_s=3.0) is None
    assert stamp_age_s(stamp, monotonic_s=116.0, boot_id=boot_a,
                       runtime_id=run_a, suspend_offset_s=63.001) is None
```


**Verification at implementation:**

```bash
uv run pytest tests/unit/common/test_clock_domain.py tests/unit/system/test_process_monitor.py tests/unit/controller/test_heartbeat.py tests/web/test_control_liveness_not_sticky.py -q -n 0
```

### Task 2: Specify one duration-based cooking timer authority

**Files:** create `common/timer.py` for the pure timer transition functions and value validation; modify `common/defaults.py`, `common/control_delta.py`, `common/api_commands.py`, `common/persistence/control.py`, `common/persistence/protocols.py`, `controller/runtime/store.py`, recipe setup and idle/active notification callers; extend existing timer/delta/store/notification tests.

**Proposed product policy, requiring review:** A duration starts when the controller accepts it at FIFO drain, not when an HTTP request was created. Pause freezes remaining active seconds; explicit resume starts a new local deadline for that remaining duration. Clean pause remains paused through restart. Any running timer recovered after process restart, boot change, detected suspend or control discontinuity becomes interrupted/paused and disarmed, requiring operator review and explicit resume. Do not count downtime, auto-advance a recipe, enter keep-warm, or claim a completed cook. Controller safety-stop/restart behavior remains owned by shared-mode safety, not by an arbitrary timer callback. An already requested safe shutdown is not undone by pausing its notification timer.

**Durable timer schema:** production `TIMER_SCHEMA_VERSION = 2`; fields `schema_version`, `timer_id` (UUID), `state` (`stopped|running|paused|interrupted|expired`), `remaining_s: float | None`, `checkpoint: ClockStamp | None`, `started_wall_s: float | None`, `paused_wall_s: float | None`, `projected_end_wall_s: float | None`, `action_armed: bool`. Existing notify entry retains shutdown/keep_warm options but only a genuine expiry event, not simply `req=False`, can fire timer actions. Running record's `remaining_s` is remaining at `checkpoint`; it is not continuously decremented in storage. Same-generation elapsed `stamp_age_s` supplies the current remaining value. A persisted checkpoint is restored only as paused/interrupted state, never as a running deadline after restart.

**Pure functions in `common/timer.py`:**

- `remaining_seconds(timer: dict, now: ClockStamp) -> float | None`: for running require same-domain checkpoint and compute `max(0, remaining_s - age)`; for paused/interrupted return retained remaining; stopped/expired return 0; invalid provenance returns None, never immediate expiry.
- `start_timer(seconds: float, now: ClockStamp) -> dict`: new timer_id, running, remaining seconds, checkpoint now, action_armed true, wall metadata now/now+seconds.
- `pause_timer(timer: dict, now: ClockStamp, *, interrupted: bool = False) -> dict`: normal pause computes trusted remaining; interrupted stores the last known checkpoint remainder if continuity is unavailable, marks interrupted and action_armed false.
- `resume_timer(timer: dict, now: ClockStamp) -> dict`: requires known finite nonnegative remaining, paused/interrupted state and explicit current-generation command; rebind checkpoint and set running/armed. Unknown legacy duration cannot resume.
- `expire_timer(timer: dict, now: ClockStamp) -> tuple[dict, bool]`: returns `fired=True` only for currently running, armed, valid same-domain remaining==0; returned state expired/disarmed makes subsequent calls false. Normal pause at zero does not create a deferred auto-action.

Illustrative authority arithmetic, not a full implementation replacement:

```python
age_s = stamp_age_s(timer["checkpoint"], monotonic_s=now.observed_monotonic_s,
                   boot_id=now.boot_id, runtime_id=now.runtime_id,
                   suspend_offset_s=now.suspend_offset_s)
if age_s is None:
    return None
return max(0.0, timer["remaining_s"] - age_s)
```

- [ ] Add regressions to `test_control_delta_timer_ops.py`: `test_timer_wall_jumps_do_not_change_remaining_or_action`, `test_fifo_pause_resume_uses_live_state`, `test_delayed_request_starts_at_drain`, `test_resume_requires_known_remaining`. Use separate request wall provenance and drain ClockStamp. Assert recipe trigger/mode/setpoint through notification tests, not a timer field-copy mock.
- [ ] Bump `CONTROL_DELTA_VERSION` from 1 to 2 and migrate every current builder/validator/applier/store fixture together. Timer ops remove wall-authoritative `at`, carry `requested_wall_s` as metadata and `target_runtime_id` admission identity. `timer.pause` has no duration, start/resume keeps existing seconds/default-60 behavior, with-options keeps explicit seconds/shutdown/keep_warm. `timer.clear` also carries target generation to avoid replaying a stale queued command against a new cook. Existing version-1 persisted queue rows are historical: reject whole envelopes under existing strict version handling; never partially apply mixed-version content.
- [ ] Update `_cmd_set_timer` and `_timer_start_with_options` to enqueue duration intent plus current heartbeat generation. Reject arming/resuming if the controller heartbeat cannot establish a current generation. Preserve endpoint names, options, command units, FIFO branch-at-drain behavior and origin semantics. Logging says requested duration until the controller accepts it; only accepted wall projection may be logged as an estimated end.
- [ ] Change signatures to `apply_control_delta(control, envelope, log=None, *, timer_now: ClockStamp)` and `execute_control_writes(*, timer_now: ClockStamp)`. Pass one drain-cycle sample from runtime, through both store implementations and persistence protocols, and through `_apply_op` to timer appliers. Do not let the web producer calculate monotonic deadlines or import runtime clock ownership into common code. Validate all timer op target IDs before any envelope mutation so a rejected timer generation cannot partially apply unrelated fields.
- [ ] In `common/timer.py`, implement the defined transitions and add a validated timer default to `common/defaults.py`; keep `timer` forbidden in generic control `set` patches. Add remaining and provenance bounds checks at construction, not permissive coercion at expiry.
- [ ] Route `_setup_recipe_triggers` through `start_timer(recipe_minutes * 60, now_stamp)` rather than writing start/end directly. Route explicit recipe pause/resume through the same timer functions and do not recreate a paused step timer on mode reentry. Mode context supplies the same continuity generation as user timers.

**Verification at implementation:**

```bash
uv run pytest tests/unit/common/test_control_delta_envelope.py tests/unit/common/test_control_delta_timer_ops.py tests/unit/common/test_control_delta_apply.py tests/unit/datastore/test_sqlite_store_parity.py tests/characterization/test_control_delta_seam.py -q -n 0
```

### Task 3: Unify expiry, durable restart and client timer contracts

**Files:** modify `notify/notifications.py:check_notify`, `controller/runtime/controller.py` idle expiry, `controller/runtime/modes/base.py` recipe/control drain calls, control startup/restore, `common/web_contracts/core.py:TimerPayload`, socket timer projection, generated TS contracts, shared timer consumers and `packages/pifire-core/src/command.ts` only if type imports change.

**Interfaces:** wire timer becomes `{timerId: string | null, state: 'stopped'|'running'|'paused'|'interrupted'|'expired', remainingS: number | null, current: boolean, startedWallS: number | null, projectedEndWallS: number | null, keepWarm: boolean, shutdown: boolean}`. Current means the controller supplied a valid same-generation projection, not browser connection alone. Old `start/paused/end` authority fields are removed in the coordinated wire cutover, not reinterpreted. `timerId` replaces epoch start as visibility/dismissal identity in `timerVisibility.ts`.

- [ ] Make `check_notify` consume one `ClockStamp` from its controller caller. In the timer branch call `expire_timer`, persist returned timer and disarmed request with recipe/mode/setpoint action state in the same control snapshot, then send the normal notification. Remove the second idle-controller epoch expiry loop entirely; idle and active modes use this same branch. Do not claim transactional exactly-once external notifications; the observable safety requirement is that rechecking/restoring cannot repeat recipe/mode expiry actions.
- [ ] Separate `timer_fired` from the existing `not req` predicate used for other notification types. Interruption/disarm must never look like successful expiry and enter Shutdown/keep-warm. Keep probe/test/limit/reignite behavior unchanged.
- [ ] At each existing periodic control status publication, checkpoint trusted remaining and the paired stamp; persist immediately on start/pause/resume/expiry. On orderly stop, pause and persist before teardown. On unclean restart retain the last durable remainder as an explicitly interrupted estimate; it can overstate remaining by the publication interval, cannot count downtime or silently fire. The UI tells the operator remaining is from the last checkpoint. Do not invent tighter accuracy than the actual checkpoint cadence.
- [ ] Add startup migration before the first queue drain. A legacy epoch running timer becomes `state='interrupted', remaining_s=None, checkpoint=None, action_armed=False`; retain original epoch fields only in a preserved migration diagnostic record, not current authority. A valid legacy paused timer can retain `max(0, end-paused)` as its already-frozen duration but stays interrupted/disarmed until explicit resume. A stopped legacy timer becomes stopped. Reversed/nonfinite legacy endpoints have unknown remaining. Do not derive running remaining from `wall_now` or relabel old deadlines monotonic.
- [ ] Reject pending timer operations whose target generation predates restart; report rejection with queue row ID under existing logging. Do not rearm a restored timer because old FIFO data survives. Store notification options for operator review but leave req/action_armed false until explicit accepted resume/restart.
- [ ] Update TimerPayload, serializer, generator, `timerState.ts`, `timerVisibility.ts`, TimerBar, TimerModal, shared fixture data and mobile consumers in the same revision. Commands still submit seconds; they never submit browser-computed epoch end. Client extrapolation subtracts local receipt elapsed from server `remainingS` only while state running/current and receipt fresh; paused/interrupted stays frozen, unknown renders unknown, zero display never triggers a command.

```typescript
const elapsedS = Math.max(0, (nowMonotonicMs - receivedMonotonicMs) / 1000);
const remainingS = timer.remainingS === null ? null :
  timer.state === "running" && timer.current && receiptCurrent
    ? Math.max(0, timer.remainingS - elapsedS)
    : timer.remainingS;
// A zero display is not timer authority; no shutdown/recipe command is emitted here.
```

If local elapsed is negative, first invalidate receipt; do not rely on this formatting clamp to turn a reset clock into current data.

- [ ] Keep these behavioral regressions: `test_timer_expiry_causes_one_recipe_transition`, `test_shutdown_timer_ignores_wall_step`, `test_keep_warm_timer_uses_elapsed_duration`, `test_restart_disarm_is_not_expiry`, `test_suspend_does_not_credit_timer_progress`, `test_legacy_running_timer_requires_new_duration`, `test_same_boot_process_restart_does_not_autoresume`. Assert state/mode/setpoint/recipe trigger and notification count; test paused restart and explicit resume separately.
- [ ] UI cases: timer identity survives wall jump, pause stays frozen, remaining reaches zero without client action, interrupted/unknown is visible, reconnect reanchors to server remaining, and the duration command payload is unchanged. Remove old tests that merely pin epoch field copies or exact message wording; retain genuine queue ordering and API error contracts.

**Verification at implementation:**

```bash
uv run pytest tests/unit/notify/test_notifications.py tests/unit/common/test_control_delta_timer_ops.py tests/unit/datastore/test_sqlite_store_parity.py tests/unit/runtime/test_control_mode_base.py -q -n 0
bun run --cwd web-react gen:types
bun run --cwd web-react gen:types:check
bun run --cwd packages/pifire-core test tests/command.test.ts
bun run --cwd web-react test tests/unit/helpers/timer/timerState.test.ts tests/unit/components/shell/TimerBar.test.tsx tests/unit/components/shell/TimerModal.test.tsx
bun run --cwd web-react typecheck
bun run --cwd mobile typecheck
```

Before deployment, exercise a 10-second fake-platform timer through the actual HTTP command→FIFO→control tick→notification/action path, pause at 3 seconds, move wall ±3600, resume, and observe expiry after exactly 7 further active seconds. Restart the fake controller mid-timer and observe interrupted/disarmed with no automatic cooking action. Do not run this on live heating hardware.

### Task 4: Bound queue waits, hopper cadence/backoff and serial response budgets

**Files:** modify `common/common.py:get_system_command_output`, `distance/_sampled_base.py`, `distance/sen0628.py`; update focused tests listed below. No change to requests/socket.io timeout arguments or already-monotonic ToF driver logic.

- [ ] Write `test_output_queue_timeout_ignores_wall_steps` in `tests/unit/common/test_system_command_output_queue.py`. Fake sleep advances only the test steady timeline; missing requested response times out at the configured budget despite ±3600 wall changes, and an unrelated queued answer remains available. Preserve the prompt-return matching-response assertion.
- [ ] Set queue endtime and loop comparison using a bound/injected monotonic callable. Keep the 25ms sleep, FIFO deferred-entry restoration, ERROR envelope and timeout units unchanged. Fix the obsolete docstring that lists socket liveness as a queue consumer; it now uses heartbeat, not this round trip.

```python
def get_system_command_output(requested="supported_commands", timeout=1, *, monotonic=time.monotonic):
    system_output = SqliteQueue("queue_systemo")
    deadline_s = monotonic() + timeout
    while monotonic() < deadline_s:
        if not any(_is_output_for(entry, requested) for entry in system_output.list()):
            time.sleep(_SYSTEM_OUTPUT_POLL_INTERVAL)
            continue
        deferred = []
        data = None
        while system_output.length() > 0:
            entry = system_output.pop()
            if entry is None:
                break
            if _is_output_for(entry, requested):
                data = entry
                break
            deferred.append(entry)
        for entry in deferred:
            system_output.push(entry)
        if data is not None:
            return data
    return {
        "command": [requested, None, None, None],
        "result": "ERROR",
        "message": "The requested command output could not be found.",
        "data": {"Response_Was": "To_Fast"},
    }
```

This preserves the existing queue matching algorithm and response envelope while changing the deadline source; the optional callable enables deterministic two-clock boundary regressions.

- [ ] In `_sampled_base`, use its bound `_monotonic` pattern consistently for `_backoff_until`, initial/sample completion `sample_time`, loop `now`, slow-cycle start/end and existing `_cycle_started_at`. Keep first sample/request semantics and `None` backoff sentinel; do not turn zero into an artificial missed-first-call condition. Request sampling still bypasses the periodic interval but not failure backoff, and requests arriving during a read remain pending.
- [ ] `_recv_packet` uses monotonic for deadline and repeated comparison at every response attempt; preserve per-read serial timeout=0.2s, setup retry count (three), setup budget (2s) and runtime budget (0.5s). A single serial read may overrun the outer deadline by its existing bounded timeout; assert that bound, not exactly 0.5s to the microsecond. Do not change malformed/status-failure handling or mask `None` as a new fabricated distance.
- [ ] Add `test_backoff_not_extended_by_wall_rollback`, `test_pending_request_does_not_bypass_backoff_after_jump`, `test_slow_cycle_uses_real_elapsed`, and `test_sen0628_silent_response_budget_survives_clock_step`. Assert sample execution/retained-level behavior, real timeout return, and that wall-only changes do not force sensor reinitialization. Keep existing independent stuck-cycle/open watchdog tests.
- [ ] Restart uses fresh local schedule/backoff state under the existing driver lifecycle. A retained hopper percentage remains last-known after failure; this task does not claim it is a newly measured level or add a fabricated delivered/sample interval after resume.

**Verification at implementation:**

```bash
uv run pytest tests/unit/common/test_system_command_output_queue.py tests/unit/runtime/test_system_commands.py tests/unit/distance/test_hang_safety.py tests/unit/distance/test_tof_base.py tests/unit/distance/test_serial_tof_base.py tests/unit/distance/test_hcsr04.py tests/unit/distance/test_sen0628.py -q -n 0
```

### Task 5: Local display refresh, idle, menu and debounce clocks

**Files:** modify `display/_base_dsi.py`, `ili9341f.py`, `qtbackend.py`, `_base_fixed.py`, `_base_flex.py`, `_button_input.py`, `_encoder_input.py`, `pygame_240x320.py`, `pygame_240x320b.py`, `pygame_64x128.py`, `ssd1306.py`, `ssd1306b.py`; test corresponding fixed/flex/Qt driver harnesses.

- [ ] Add visible refresh regression with a fake backend changing temperature on each fetch: wall rollback does not freeze DSI/ILI9341 cached dashboard temperatures while monotonic advances 0.2s; forward jump does not skip a full refresh cadence. Use existing feeder/driver fixtures and assert displayed value transitions.
- [ ] Move both refresh stamp assignments and gate comparisons to monotonic in DSI/ILI9341. Move Qt settings refresh, `_last_interaction`, `registerInteraction`, idle evaluation to `_monotonic`. Keep `_wall_time` for genuine epoch labels until Task 7 removes their duration arithmetic. Never replace Qt's `_now=time.time` wholesale while `_update_timer_text`, `_update_cook_elapsed`, or `LAST.ts` still consumes epoch values.
- [ ] Apply a paired producer/consumer table, not isolated substitutions:

| State | Producers | Consumers |
|---|---|---|
| `display_timeout` transient text/splash/network | `_base_fixed`, `_base_flex` startup/network/text handlers; pygame/SSD driver overrides | The same base/override draw branches clearing transient display |
| `menu_time` | `_button_input`, `_encoder_input`, per-driver menu entry/update | `_base_fixed`, `pygame_240x320b`, `ssd1306b` menu timeout branches |
| `last_movement` reversal/enter debounce | `_encoder_input`, `ili9341f` accepted movement callbacks | Same callbacks' opposite-direction 0.5s and enter 0.3s checks |

Use one monotonic callable per driver/mixin instance, including initialization/sentinels. Preserve existing threshold strictness and button logic; the second enter-suppression read is not an invitation to redesign debounce. Relative pygame sleeps/delays and QTimer scheduling remain unchanged.

```python
now_s = self._monotonic()
self.menu_time = now_s
self.display_timeout = now_s + timeout_s
# Consumer callbacks use this same injected monotonic source.
```

- [ ] Assert menu/text timeout after the configured elapsed interval under both wall jumps; assert opposite encoder direction is suppressed before 0.5s and accepted afterward, without requiring a same-direction “repair” movement. Add a narrow test under the existing input/driver harness if no dedicated encoder test exists; assert observable navigation, not internal stamps.
- [ ] Qt regression: live settings refresh continues after rollback, Stop-only sleep occurs after configured monotonic idle duration, interaction wakes screen, and active cook stays awake. Probe plan's Qt health baseline must be integrated before this shared file is considered complete; temperature fetch failure must not halt health aging.

**Verification at implementation:**

```bash
uv run pytest tests/ui/test_display_feeder.py tests/ui/test_pygame_qt_drivers.py tests/ui/test_fixed_base_harness_smoke.py tests/ui/test_fixed_base_golden.py tests/ui/test_base_flex_dash_update.py tests/ui/test_qtbackend.py tests/ui/test_qtapp_power.py -q -n 0
```

Launch supported fake display surfaces and interact with menu/encoder/idle path while the injected wall clock jumps. Record actual visual evidence. A static screenshot or only compilation does not prove refresh/debounce behavior.

### Task 6: Notification, telemetry and warning cooldowns

**Files:** modify `notify/mqtt_handler.py`, `notify/notifications.py`, `notify/influxdb_handler.py`, `notify/wled_handler.py`; keep persisted wall histories in `common/defaults.py`/notify entries, and extend existing notify tests.

- [ ] MQTT: move `last_conn_time` capture and five-second connection retry/check gates together to monotonic. Replace the epoch-zero initial/reset sentinel with explicit `None` so first connect and successful connect callback semantics stay immediate at low uptime. `on_connect` clearing the pending state remains authoritative; do not create an unnecessary retry layer.
- [ ] In `notifications.py`, move PID/pellet/base periodic publish stamps and gates to monotonic. Preserve mode-change forced publication and existing interval thresholds. A wall rollback cannot suppress a healthy one-hour stream; forward jump cannot force every interval's accumulated work at once.
- [ ] Influx: use monotonic only for one-second enqueue throttle and first-notification sentinel. Keep `datetime.now(UTC)` for each Influx point's wall timestamp and existing background queued publication. Event messages still follow the existing throttle policy; do not silently bypass it while repairing the clock.
- [ ] WLED: migrate every `last_updated` assignment and each GRILL_STATE cooldown comparison to monotonic. Event notifications continue bypassing state cooldown; the event receipt resets the cooldown, and normal state resumes after genuine elapsed duration.
- [ ] Hopper warnings: keep persisted `item['last_check']` wall history, but add controller-owned in-memory monotonic cooldown keyed by the existing notification `(type,label)` identity. Remove elapsed comparisons against that wall field. First low reading after process restart may emit one warning immediately, then normal warning_time applies; this is the proposed conservative restart choice (repeat warning preferable to suppressing a low-pellet warning for an hour). Settings/entry removal clears that cooldown. Test notification wall `last_check` remains a record only.

```python
last_s = self._last_hopper_warning_s.get((item["type"], item["label"]))
due = last_s is None or now_s - last_s > warning_interval_s
if low_hopper and due:
    send_notifications("Pellet_Level_Low")
    self._last_hopper_warning_s[(item["type"], item["label"])] = now_s
    item["last_check"] = clock.wall_time()
```

The actual notifications entry point is currently a function; attach the cooldown map to the existing controller/notification runtime state passed by its caller, not an unexplained new `self` or a new global singleton. Extend that caller context alongside the timer ClockStamp from Task 3; clear it at runtime generation change.

- [ ] Regressions: `test_mqtt_retry_ignores_wall_jump`, `test_mode_change_still_forces_publish`, `test_influx_wall_timestamp_changes_but_throttle_does_not`, `test_wled_event_cooldown_uses_elapsed_time`, `test_hopper_restart_warns_once_then_cools_down`. Assert actual queued/published notifications and state-profile selection with fake transports; no mock echo of timestamp assignment.

**Verification at implementation:**

```bash
uv run pytest tests/unit/notify/test_mqtt_handler.py tests/unit/notify/test_notifications.py tests/unit/notify/test_influxdb_handler.py tests/unit/notify/test_wled_handler.py tests/unit/notify/test_wled_profiles.py -q -n 0
```

### Task 7: Explicit elapsed/remaining rendering and remaining low-risk clocks

**Files:** consume shared-mode status fields in `blueprints/mobile/socket_io.py`, `common/web_contracts/core.py`, `common/persistence/transforms.py`, `common/api_commands.py` status/API projections and generated core contracts; modify `display/_base_fixed.py`, `_base_flex.py`, `qtbackend.py`; `web-react/src/helpers/dashboard/countdowns.ts`, `cookTime.ts`, `helpers/clock.ts`, `helpers/useLiveState.ts`, timer/UI consumers; `web-react/tests/e2e/helpers.ts`. Probe plan owns mobile receipt clock and `LAST` age.

**Wire interface:** add `durations: {modeElapsedS: number | null, modeRemainingS: number | null, lidRemainingS: number | null, cookElapsedS: number | null, current: boolean, running: boolean}` projected from shared-mode status fields and its `clock_stamp`, captured with the duration values at the admitted tick. Require both a fresh heartbeat for the same runtime generation and valid status-stamp age ≤15s; a new heartbeat cannot rejuvenate old status. The four values are duration snapshots, not absolute instants. `current` is false on identity loss or stale status; `running` indicates local extrapolation is appropriate for the current mode, never permission to control hardware. Mode and cook elapsed are distinct; do not replace startup-based cook time with mode elapsed after a transition.

- [ ] Update socket/Pydantic serialization and generated contracts first, then shared package fixtures and all browser/mobile/Qt/fixed/flex consumers in the same cutover. Trace/history metadata continues exporting wall start/end; operational countdown helpers stop consuming it as duration authority.
- [ ] Account for server-side status age before sending duration snapshots: add validated same-domain status age to running elapsed values and subtract it from running remaining values, with nonnegative remaining bounds. Capture client receipt only after this projection. Alternatively use the freshest admitted status directly when age is zero. Never reset old duration values to age zero merely because the server published a new socket envelope. Add `test_fresh_heartbeat_does_not_rejuvenate_stale_duration_status` asserting `current=False` once status exceeds 15 seconds even when heartbeat continues.
- [ ] Browser/phone stores local `receivedMonotonicMs` for the projection; use the common helper exported by the probe plan from `packages/pifire-core/src/liveConnection.ts`. On a fresh running snapshot, elapsed adds local elapsed, remaining subtracts it. On disconnect, stale receipt, page/app resume, runtime generation change, or local negative delta, freeze and qualify last-reported/unknown until a new payload. Server-side monotonic numbers never enter remote subtraction.
- [ ] Qt/fixed/flex same-host clients either use a valid shared identity-qualified status snapshot or the same receipt-extrapolation rule, never `wall_now - start_time`. Map `_update_cook_elapsed` to `cook_elapsed_seconds`, mode countdown to `remaining_seconds`, lid countdown to `lid_open_remaining_seconds`. Missing provenance renders unknown/retained, not an invented 00:00 live cook.

```typescript
const localElapsedS = (nowMonotonicMs - receivedMonotonicMs) / 1000;
const canAdvance = durations.current && durations.running && receiptCurrent && localElapsedS >= 0;
const cookElapsedS = durations.cookElapsedS === null ? null :
  durations.cookElapsedS + (canAdvance ? localElapsedS : 0);
const modeRemainingS = durations.modeRemainingS === null ? null :
  Math.max(0, durations.modeRemainingS - (canAdvance ? localElapsedS : 0));
```

- [ ] Split `web-react/src/helpers/clock.ts` wall/calendar helper from local receipt/animation clocks. TimerBar migrated in Task 3 must not accidentally keep using `useNow`'s epoch as its receipt clock. Preserve wall `Date` conversions for history tooltips/calendar text.
- [ ] Move demo elapsed in `web-react/src/helpers/useLiveState.ts` to `performance.now` for both start and comparison. Move all E2E timeout budgets in `web-react/tests/e2e/helpers.ts` to monotonic/performance clocks while preserving Date.now-derived unique names (identifiers, not timers). These are lower-risk non-production changes, not claimed control defects.
- [ ] Add UI regressions for equal duration display histories with different server and browser wall jumps, mode transition preserving cook elapsed, stale payload freezing extrapolation, new payload reanchoring, and countdown zero causing no command. Extend `countdowns`, `cookTime`, Dashboard, flex cook-time and Qt tests rather than asserting source text.

**Verification at implementation:**

```bash
uv run pytest tests/ui/test_flex_cook_time_bar.py tests/ui/test_qtbackend.py -q -n 0
bun run --cwd web-react gen:types
bun run --cwd web-react gen:types:check
bun run --cwd web-react test tests/unit/helpers/dashboard/countdowns.test.ts tests/unit/helpers/dashboard/cookTime.test.ts tests/unit/components/dashboard/Dashboard.test.tsx
bun run --cwd web-react typecheck
bun run --cwd web-react typecheck:e2e
bun run --cwd packages/pifire-core typecheck
bun run --cwd mobile typecheck
```

### Task 8: Close remaining evidence/replay dependencies without a second learning implementation

**Owner:** Main's user-approved, in-flight learning/trace contract migration. This task is the cross-plan admission checklist and integrated regressions, not ownership of duplicate production edits. It does not claim Main's integration is already verified. Consume trace schema 10, trajectory observation schema 4 and required `FrameObservation.wall_start_ms/wall_end_ms` from the shared spec and production constants; preserve older schemas' historical admission rules.

| Required dependency | Concrete affected source and acceptance |
|---|---|
| Trace wall envelope versus physical interval | `controller/runtime/control_trace_session.py`, `controller/runtime/modes/hold.py`, `file_mgmt/cookfile.py:_exact_import_segment`: trace-10 envelope `ts_ms` is publication wall metadata, payload solve monotonic and completion wall are explicit. `FrameObservation.frame_start_s/frame_end_s` are monotonic; `wall_start_ms/wall_end_ms` are required actual wall provenance. Do not require envelope==solve monotonic; join revision/application coordinates and explicit monotonic frames. Historical wall-frame schemas, including audit-era schema-seven inputs, cannot be relabeled; ambiguous archives remain non-replayable. |
| Physical trajectory continuity | `common/learning_trajectory.py`, `controller/runtime/learning_trajectory.py`: observation schema 4 validates monotonic duration/ordering independently of actual paired wall endpoints; remove domain guessing and fixed wall-offset inference only with real explicit producers. Preserve schema-2/3 historical mapping checks, signed wall sample-age metadata and canonical digests. Smoke partial exit and Smoke→Hold boundary must survive wall jumps without fabricated wall endpoints or lost delivery. |
| Causal replay/order | `controller/model_learning/trace.py`, `common/persistence/control_trace.py`, cookfile causal-row validation: use insertion/session sequence/revision order, never resort by wall timestamps or reject wall regression alone. Preserve genuine sequence gaps, overlap, identity and physical-history rejection. |
| Latest confidence/activation authority | `common/persistence/model_evidence.py`, `controller/model_learning/confidence.py`, `report.py`, `activation_runtime.py`: later durable blocked decision supersedes earlier allowed decision even when its wall timestamp is smaller. Carry durable ordinal/source identity through in-memory lists and exports; never compare unrelated database IDs across imports. |
| Forecast-to-evidence wall metadata | `controller/runtime/grey_runtime.py`: evidence timestamps derived from completed forecast/frame coordinates must receive actual wall metadata after frame migration, not relabel monotonic milliseconds as epoch. Forecast horizon/sequence math stays physical and seed/admission rules stay strict. |
| Existing safe recovery | `control.py` and trajectory repository `recover_open_segments`: keep closing old open segments as UNCLEAN_RESTART without extending old monotonic endpoints to new uptime; recovery wall time is completion provenance only. |
| Candidate policy questions, not established unsafe outcomes | Trajectory retention `ORDER BY end_wall_ms,segment_id` currently expresses calendar order: proposed capture-order retention must use durable append sequence if approved, never an incidental sort change. Wall-derived evidence IDs need explicit repeated-time collision/idempotence regression. Grey corpus attribution by wall windows needs a rollback association scenario and a causal-identity contract before claiming a model defect. History downsampling remains chart geometry unless an actual authority use is established. |

- [ ] Review Main's landed contract and record the exact fields/identity/sequence used by the above consumers in this table before releasing the shared-axis gate. If any dependency remains unimplemented, keep the coherent PID-SP/pulse/shared-mode cutover gated; local watchdog/throttle patches may still ship separately.
- [ ] Run runtime-produced archive regressions with wall≈1.8e12ms, monotonic≈1e5ms, and nonzero solve-to-publication delay. Assert exact import succeeds for valid new explicit history and rejects ambiguous legacy history rather than weakening admission.
- [ ] Append a blocked confidence decision later in durable order but earlier in wall time; assert active authority is the blocked decision and no stale activation is admitted. Append cross-boot/cross-cook observations and assert refusal, not auto-conversion.
- [ ] Test Smoke partial exit, Smoke→Hold transition and completed-forecast evidence publication across ±3600 wall steps. Assert monotonic delivery and trajectory duration unchanged, provenance records actual wall jump, result revisions remain causal, evidence timestamp is wall and schemas/mutation anchors remain correct.

**Verification commands for Main's integrated implementation, not for plan writing:**

```bash
uv run pytest tests/integration/test_cookfile_learning_diagnostics.py tests/unit/runtime/test_hold_control_trace.py tests/unit/runtime/test_control_trace_session.py tests/unit/common/test_learning_trajectory_contracts.py tests/unit/runtime/test_smoke_learning_trajectory.py tests/unit/runtime/test_hold_trajectory_seed.py tests/unit/mpc/test_model_learning_trace.py tests/unit/controller/test_control_trace_replay.py tests/unit/datastore/test_control_trace_store.py tests/unit/mpc/test_model_confidence.py tests/unit/mpc/test_confidence_bootstrap.py tests/unit/mpc/test_activation_runtime.py -q -n 0
```

Run `uv run pytest tests/unit/persistence/test_model_evidence.py tests/unit/common/test_model_evidence_store.py tests/unit/mpc/test_learning_evidence_clock.py -q -n 0` for persistence and wall-metadata regressions. Use LSP references to the production `commit_model_activation` and `commit_model_activation_phase` transaction methods before integrating the blocked-decision scenario; do not create a permissive authority fixture. Main owns the production migration and existing constructor/fixture edits.

## Explicitly preserved wall uses and no-change areas

- `notify/influxdb_handler.py` UTC point timestamps; `probes/thermoworks_cloud.py` last_poll_time ISO; notification message dates; admin archive names; cookfile comment create/edit dates; metrics export filenames; rollback evidence wall `now_ms`; history calendar windows; mobile HistoryChart and web history tooltip `Date` formatting; pellet log keys and historyAdapter epochs. These remain provenance/calendar semantics.
- `common/current_schema.py:LAST.ts` stays wall; corrected age belongs to the probe plan. Numeric missing-vs-current selection already differs from age, so rollback alone does not transform `None` into a live value.
- `probes/kalman.py`, `_mcp960x_adafruit.py` hardware timing, `distance/_tof_base.py` deadlines, bound sampler watchdog, runner solve timing, calibration/model-fitting wait budgets, and actuation dual capture already use appropriate monotonic or paired clocks. Preserve them; only migrate their interface consumers if the coherent contract requires it.
- Relative sleep, Event.wait, join, asyncio.sleep, QTimer, pygame tick/delay, setTimeout/setInterval, requests/serial/socket.io timeout arguments and the shared 4000ms transport timeout are not explicit wall arithmetic. Do not rewrite them or claim SDK-internal clock behavior was audited.
- `bt_meater.py:_lastUpdate` has a write but no demonstrated duration reader in the scoped audit. Preserve it unless a real consumer is discovered; no speculative bug fix.

## Migration, rollback, safety review and release

**Required product decisions:** approve timer duration beginning at controller drain; interruption/restart/suspend pauses and disarms without counting downtime; unknown legacy running remaining requires an explicit new duration; valid legacy paused remaining may be reviewed/resumed; first low-hopper warning after restart can fire immediately; unknown heartbeat no longer appears optimistically alive. Review the shared-mode proposed active-loop-gap threshold separately from the established 30s watchdog/15s liveness contracts. None of these choices is represented as current product behavior.

**Deployment order:** safely stop active cooking, preserve datastore/evidence backups, deploy the coupled producer/store/schema/UI revisions together, invalidate old runtime generation and pending timer arming commands, migrate timer operational state before first drain, then start fresh heartbeat/probe acquisition. Do not auto-resume an interrupted recipe or output state. Independent local polling/throttle fixes can be rolled out earlier; the timer wire contract, heartbeat schema and shared-axis deployment cannot be partial.

**Rollback:** safe-stop first, restore the matching complete code/UI revision and pre-migration operational timer/heartbeat snapshot; start a new generation and keep timers disarmed until reviewed. Never let old code read new monotonic values as epoch deadlines, nor convert them backward by guessed offsets. Retain all durable cook, learning, trace and evidence data; rollback is not authority to delete them. Preserve legacy diagnostics rather than mutating historical timestamps. No boot/process restart is proof of active elapsed heating.

**Smoke and cleanup:** after the fake-platform timer, watchdog, bounded I/O and actual UI scenarios pass, update existing relevant API/operator docs and changelog with interruption/unknown-age behavior; remove throwaway smoke scripts. Keep only regressions that fail on plausible clock/domain mistakes. Integrate Qt and runtime shared-file edits under one owner, then run project-wide verification once after all slices land.

**Exact-revision commands (executor only):**

```bash
prek run --all-files
uv run pytest tests/unit/test_no_cross_test_imports.py tests/unit/mpc/test_mutation_score.py tests/unit/common/test_current_contract_fixtures.py -q
uv run python scripts/exact_revision_gate.py verify-bookmark --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
```

The contract preflight is separate from, and prerequisite to, the five commands defined by `scripts/exact_revision_gate.py`; the wrapper is the sole release command/evidence authority. Failed, interrupted, timed-out, nonzero, missing, reordered or different-revision commands fail closed and leave later commands not run. Preserve independent preflight stdout/stderr/SHA-256 evidence, schema-v2 `.artifacts/exact-revision/<full-revision>/evidence.json` and every referenced release log; schema-v1 evidence is historical-only. `jj new`/`jj describe` do not run hooks. Only after explicit push authorization:

```bash
uv run python scripts/exact_revision_gate.py push --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
```

The guarded wrapper must revalidate local evidence after confirming remote revision and perform the final publication check. Never hand-edit, reuse, rename or delete evidence from an earlier attempt; no direct `jj git push`, no raw Git. These listed commands have not been run for this documentation assignment.

## Execution record — 2026-09-08

All eight tasks are implemented or consumed from their completed prerequisite
plans. Timer schema 2 and control-delta schema 2 migrated together across FIFO,
store parity, runtime, API, generated contracts, web, mobile and displays.
The shared/probe phases already supplied identity-qualified heartbeat/status
and the strictly-greater-than-60-second discontinuity predicate; the independent
Process_Monitor remains 30 seconds.

Source review found and repaired stale generic commands surviving startup,
retired-clock access after discontinuity, lost recipe completion on reignition,
teardown overwriting timer disarm, and duplicate recipe notification delivery.
Both timer and presentation reviewers finished without remaining findings.

Verified: 2,032 runtime/controller/characterization tests; 2,655
common/datastore/persistence/notification/system/distance tests; 672 UI tests
(one platform skip); 508 learning/evidence integration tests; 214 core, 123
mobile and 1,997 web tests. All four TS typechecks and generated-contract
consistency checks passed. Full-suite warning output includes the deliberate
power-action guard tests and pygame's deprecated image serialization API.

The actual HTTP→FIFO→Controller.tick smoke paused a ten-second timer at three
seconds, moved wall time backward/forward, resumed seven seconds, and observed
one expiry. Restart retained seven saved seconds without auto-expiry.
A subsequent >60-second gap retained the six-second checkpoint, disarmed the
timer, entered Error and turned all fake outputs off. Chromium used the real
API/socket/runtime with fake devices: Pause/Resume worked and a subsequent
gap displayed “Interrupted — remaining from last checkpoint”, Error, and idle
outputs. No physical heating hardware was used.

The Task 8 evidence ledger remains the production contract: trace schema 10
separates envelope wall `ts_ms` from monotonic solve/frame coordinates;
trajectory observation schema 4 carries independently captured wall endpoints;
evidence schema 6 uses durable append order for supersession. The 508-case gate
includes rollback/newer-blocked authority and exact import/replay checks without
weakening historical admission, seeds, digests or mutation anchors.

Final checks: `prek run --all-files` passed; the clock boundary, timer,
duration projection and process-startup regression selection passed 57 tests.
The separate contract preflight passed 37 tests. Its command, exit status,
stdout, stderr and SHA-256 checksums are preserved under
`.artifacts/development-clock-checks/preflight-cW8I9F/`; this is development
evidence, not exact-revision release evidence. Temporary smoke scripts were
removed and local smoke services/browser sessions stopped.

No release commands or push were run. Development checks do not authorize
publication; the exact-revision wrapper remains the sole release authority.
