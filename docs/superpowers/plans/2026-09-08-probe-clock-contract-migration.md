# Probe Clock Contract Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make probe acquisition, health freshness, cloud-cache admission, and every web/Qt/mobile reader agree on explicit clock domains without presenting retained observations as current.

**Architecture:** Acquisition and recovery intervals use same-boot monotonic seconds; wall timestamps remain event provenance. Persisted health and last-reading records carry a versioned identity-qualified monotonic stamp, and server projections send an age plus an explicit validity verdict rather than exporting a deadline for remote subtraction. Phone/browser receipt clocks are local and cannot make a stale producer report current.

**Tech Stack:** Python 3.14+, existing Pydantic/SQLite contracts, Flask-SocketIO, PySide6/Qt Quick, React/TypeScript and Expo/React Native; existing pytest, rstest, and Jest suites.

**Spec:** `docs/superpowers/specs/2026-09-08-clock-domain-separation-design.md`

**Integrated baseline:** The coupled learning implementation is now in the working tree: trace 10, observation 4, evidence 6, database 13, required independent wall endpoints, and callable clocks through Hold/runner factories and reconfiguration. References below to Main's “in-flight” work describe the drafting baseline, not remaining implementation. Reconcile those steps against the shared spec before executing; the destination shared `Clock` API and remaining timers are still proposals.

## Global Constraints

- This is a proposal requested for review, not authorization to implement. Source audit findings were not dynamically reproduced during plan writing; no tests, builds, formatters, or linters were run here.
- Read `AGENTS.md` and the spec before execution. Preserve evidence admission, durable cook identity, estimator seed requirements, replay validation, mutation expectations, and safety behavior.
- Preserve the exact anchors `MODEL_SCHEMA = 7` and `versions 3 through 6 are migration input only.`; this plan changes neither model schema nor PID-SP's raw signed public `Controller.update()` return.
- Shared API is `Clock.wall_time() -> float`, `Clock.monotonic() -> float`, and unchanged `sleep(seconds)`. `RealClock` uses `time.time` and `time.monotonic`. `ManualClock(wall_start=0.0, monotonic_start=0.0)` has independent axes; ordinary `advance`/`sleep` advance both, `jump_wall(delta)` changes wall only.
- Remove ambiguous `Clock.now` only in the coordinated shared-mode migration. This plan never silently changes its meaning or creates an epoch-to-monotonic numeric heuristic.
- Monotonic values persisted without known boot/runtime identity cannot establish live freshness. A future monotonic stamp, failed identity lookup, or resume discontinuity is unknown, not age zero.
- Wall provenance may move backward. It neither orders observations nor establishes physical duration. Report freshness and measurement validity are different contracts.
- PID-SP, pulse scheduling, and shared-mode axes have a joint deployment gate. The active-mode probe call-site cutover depends on that gate and the Main-owned learning/trace contract; it must not claim those are already implemented.

**Authorization update:** Main is implementing the user-approved coupled learning capture/replay/evidence boundary and necessary pulse/PID-SP timestamp producers now. This plan depends on that in-flight integration, not on an assumed completed fix. The shared design specifies trace schema 10 from current source schema 9, trajectory observation schema 4, and required `FrameObservation.wall_start_ms/wall_end_ms` beside monotonic frame coordinates. Consume its production constants and constructors; do not edit Main's fixtures or propose another trace version. Remaining probe work here is a proposal. Suspend detection, offset tolerance/platform handling and any one-second loop-gap policy remain separately review-gated; none is authorized as part of the current clock correction.

## Ownership and dependencies

1. `2026-09-08-peripheral-clock-migration.md`, Task 1, owns new `common/clock_domain.py`: `CLOCK_STAMP_SCHEMA = 1`, a validated `ClockStamp` serialized as `{schema_version, boot_id, runtime_id, observed_monotonic_s, observed_wall_s, suspend_offset_s}`, and `stamp_age_s(stamp, *, monotonic_s, boot_id, runtime_id, suspend_offset_s) -> float | None`. `runtime_id` names an active-control generation, rotating on process restart or suspend recovery. `suspend_offset_s` is paired `CLOCK_BOOTTIME - CLOCK_MONOTONIC`; discrepancy greater than 0.25 seconds invalidates freshness. It also owns the identity-qualified heartbeat whose live generation readers use. Probe work consumes this primitive; do not create a second identity helper.
2. Shared-mode plan owns coherent monotonic excitation and `ctx.clock` use in `controller/runtime/modes/base.py` and idle controller setup. Coordinate that file boundary; this plan owns the probe signatures and all report consumers.
3. Main owns the in-flight learning/trace wall-envelope versus physical-interval migration, durable ordering, model evidence authority, historical admission and the necessary pulse/PID-SP producers. This plan consumes genuine monotonic delivered-heat input from that boundary and shared-mode integration; it neither duplicates nor bypasses learning evidence, and does not modify PID/Smith numerical policy or existing predictor interval coverage.
4. Qt health polling in Task 4 belongs here. Peripheral Task 5 owns its settings, idle, and duration rendering. At implementation, one integration owner edits `display/qtbackend.py` after both slices supply their desired changes; no concurrent same-file writes.
5. Local cloud cache Task 2 can be reviewed independently; persisted report Tasks 1/3/4/5 must ship together with generated contracts and mobile release support. Until all supported clients understand unknown freshness, hold activation of the new wire contract rather than invent an age.

## Grounded producer–consumer inventory

| Boundary | Files and symbols to change or intentionally preserve |
|---|---|
| Low-level acquisition | `probes/_mcp960x_adafruit.py:MCP960xProbe.read_all_ports`, `HardwareFaultLatch`; `probes/base.py:ProbeInterface.get_thermocouple_health/get_thermocouple_samples`; inherited MCP9600/MCP9601 drivers. Driver recovery already samples monotonic: preserve it, migrate report field names and injectable clocks together. |
| Health internal model | `probes/thermocouple_health.py:ThermocoupleHealthReport`, class constructors, `as_dict`, `HardwareFaultLatch.update`; `probes/thermocouple_inference.py:ThermocoupleInferenceEngine.observe/reset`, `fuse_thermocouple_health`. All recovery/admission stamps stay monotonic. |
| Orchestration and actual callers | `probes/main.py:ProbesMain.read_probes`, `set_thermocouple_inference_policy`, `_reproject_cached_health_without_inference`, `get_device_info`; `controller/runtime/modes/base.py:_read_probes_with_excitation`, settings reload; `controller/runtime/controller.py` idle policy/read paths; `tests/fakes/probes.py`. Active modes currently pass wall `now` into an API whose default is monotonic; policy-OFF currently defaults to wall and stamps cached reports anew. |
| Cloud producer and reader | `probes/thermoworks_cloud.py:poll_once`, `ThermoworksCloudDevice._main/get_channel_celsius`, `ReadProbes.read_all_ports`. Keep `last_poll_time` ISO UTC status; change only local receipt expiry and invalidation. |
| Persistence and API | `controller/runtime/modes/base.py` writes `probe_device_info`; `common/persistence/runtime.py:read_probe_status`; `blueprints/mobile/socket_io.py:_project_thermocouple_health` and dash/probe projections; `common/web_contracts/core.py:ThermocoupleHealthFreshnessView`, `ProbeStatusPayload`; `common/web_contracts/registry.py`; generated `packages/pifire-core/src/contracts/core.gen.ts`. |
| Qt | `display/qtbackend.py:project_thermocouple_health`, `ProbeHealthModel.update/advance_freshness/invalid_labels`, backend `_poll_health/poll`; `display/qtapp.py` health-fetch wiring; `display/qml/components/ProbeHealthBanner.qml` and `display/qml/screens/ProbeHealthScreen.qml` health roles/messages. |
| Shared TS/web/mobile | `packages/pifire-core/src/dashboard/probeHealth.ts`, `probeStatus.ts`, `deriveView.ts`; `web-react/src/components/settings/tabs/ProbesTab.tsx` and dashboard health consumers; `mobile/src/useLive.ts`, `mobile/app/_layout.tsx`, dashboard probe rows. `lastPayloadAt` currently uses `Date.now`; both callers and advancing clock must migrate. |
| Last-known numeric reading | `common/current_schema.py:LastReading`, `build_current`, `_carry_last_readings`, snapshots; `controller/runtime/store.py` current writer; `display/staleness.py:resolve_reading`; `display/_base_flex.py`, `display/qtbackend.py:FoodProbeModel`; socket `_get_probe_data` age projection. `LAST.ts` remains epoch provenance, not a freshness clock. |
| Test/fixture migration | `tests/unit/probes/test_thermocouple_health.py`, `test_thermocouple_inference.py`, `test_probe_health_aggregation.py`, `test_thermocouple_orchestration.py`, `test_thermoworks_cloud_probe.py`; `tests/unit/runtime/test_control_mode_base.py`; `tests/characterization/test_mode_transitions.py`; `tests/fakes/probes.py`; `tests/web/test_socket_dash_payload_fields.py`, `test_socket_live_contract.py`, `test_socket_probe_staleness.py`; `tests/ui/test_qtquick_probe_health.py`, `test_qtbackend.py`, `test_probe_staleness.py`; package/mobile/web tests below. |

Before implementation, run LSP references for every changed exported symbol and structural searches for dictionary keys `observed_at`, `thermocouple_health`, `LAST`, and `lastPayloadAt`. Plan-time LSP references for `ThermocoupleHealthReport` identified driver, inference, aggregation, mode characterization, fake-probe, and socket-fixture consumers; do not restrict the cutover to orchestration tests. Regenerate wire types from Python, never hand-edit generated files. Shared current fixtures import production version constants; literal legacy fixtures remain literal historical inputs. No collected test imports another collected test.

### Task 1: Specify and migrate the durable acquisition stamp

**Files:** modify `probes/thermocouple_health.py`, `probes/thermocouple_inference.py`, `probes/main.py`, `probes/_mcp960x_adafruit.py`, `probes/base.py`, the runtime callers and fake above; consume `common/clock_domain.py` from peripheral Task 1.

**Interfaces:** internal `ThermocoupleHealthReport.observed_monotonic_s: float` replaces `observed_at` without a compatibility alias. Add `clock_stamp: ClockStamp | None = None`; hardware/inference reports can be unbound internally, but `ProbesMain` binds each newly acquired report to a real stamp at publication. Serialized device health is a version-2 report: `report_schema_version: 2`, `clock_stamp` containing the full serialized Task-1 `ClockStamp` object or null, explicit `observed_monotonic_s`, and existing state/faults/evidence/temperature_valid/detail. The monotonic stamp and explicit report coordinate must agree; reject disagreement instead of trusting one.

- [ ] Add `test_active_mode_health_ages_with_distinct_epoch_and_uptime` in orchestration tests. Start wall at 1,800,000,000 and monotonic at 100, acquire once through the actual active-mode call path, stop acquisition but continue projections, advance monotonic to 116, and assert the original healthy report is retained with `current is False` and age 16. Repeat with wall jumps of ±3600 while monotonic follows the same history.
- [ ] Rename exported report field via LSP, updating constructors, fusion, transition defaults, serializers, fake probes, direct test construction, and generated literal report payloads. Keep inference evidence, primary latches, enforcement and transition notifications unchanged.
- [ ] Replace ambiguous optional `now` on the orchestration boundary with `monotonic_s` and `wall_s`, sampled once by the runtime owner. A standalone `ProbesMain` takes the same clock/identity dependencies and fills omitted pairs by explicit clock methods; never allow a wall instant through a parameter used by inference.

Illustrative call-site change (to integrate with the shared-mode owner):

```python
sensor_data = self.probe_complex.read_probes(
    excitation=excitation,
    monotonic_s=ctx.clock.monotonic(),
    wall_s=ctx.clock.wall_time(),
)
```

`ProbesMain` constructs `ClockStamp` with those paired instants and the current generation; `replace(report, observed_monotonic_s=monotonic_s, clock_stamp=stamp)` applies only after that pass actually obtains the device report. Do not treat policy changes or repeated `get_device_info()` calls as new acquisition.

- [ ] Change policy-OFF reprojection to retain the last acquisition stamp, while updating policy/outcome immediately. Clearing software inference must not renew stale hardware evidence. Where no prior acquisition exists, publish `clock_stamp=None` with unknown freshness, not a freshly timed healthy report. Add `test_policy_change_does_not_renew_cached_acquisition` asserting policy becomes off while age remains 20 and `current` remains false.
- [ ] Add clock-generation invalidation to the probe lifecycle: on generation change clear inference history, pending delivered heat, cached acquisition stamps, and hardware clean-recovery accumulation; retain confirmed primary safety latches under the existing acknowledgment policy. Require newly observed clean intervals before auxiliary recovery. Do not simulate clean time during suspend.
- [ ] Preserve the genuine invalid-reading contract: a confirmed hardware fault still yields `None` in the corresponding output group and current enforcement still stops the primary path. Neither unknown freshness nor UI qualification may erase a latched fault in control authority.

**Verification at implementation:**

```bash
uv run pytest tests/unit/probes/test_thermocouple_health.py tests/unit/probes/test_thermocouple_inference.py tests/unit/probes/test_probe_health_aggregation.py tests/unit/probes/test_thermocouple_orchestration.py tests/unit/runtime/test_control_mode_base.py tests/characterization/test_mode_transitions.py -q -n 0
```

Expected: the new active-mode aging case fails on the old wall-fed production path, then passes with real distinct axes; enforcement/latching assertions remain unchanged.

### Task 2: Make cloud-cache expiry receipt-monotonic

**Files:** modify `probes/thermoworks_cloud.py:ThermoworksCloudDevice.__init__/_main/get_channel_celsius`, `tests/unit/probes/test_thermoworks_cloud_probe.py`.

**Interfaces:** add keyword-only `monotonic=time.monotonic` dependency; cache tuples become `(celsius, received_monotonic_s)`, process-local and never restored. Keep `datetime.now(UTC)` solely for `status['last_poll_time']`. Resume invalidation clears the cache before any active-control read is admitted.

- [ ] Adapt the existing fresh/stale test to populate through the polling/cache producer, not merely manufacture tuples. Add `test_cloud_outage_does_not_revive_cache_on_wall_rollback`: cache 100°C, miss polls, reach age `3 * poll_interval + 0.001`, roll wall back, and assert `get_channel_celsius` and `ReadProbes` still return `None`. Add the forward-step counterpart before the expiry boundary and assert the reading remains valid until the monotonic deadline.
- [ ] Implement receipt expiry without clamping invalid negative deltas into freshness:

```python
celsius, received_monotonic_s = entry
age_s = self._monotonic() - received_monotonic_s
if age_s < 0 or age_s > self.poll_interval * _STALE_MULTIPLIER:
    return None
return celsius
```

Capture the receipt monotonic instant alongside the existing UTC status time when successful poll results are committed under `_lock`. A failed/missing channel must not renew its previous receipt. Preserve successful `None` temperature semantics. Fresh receipt means a recent successful cloud response, not proof the vendor remeasured a probe; do not manufacture vendor sample identity.

- [ ] Preserve the inclusive existing boundary (`age == 3 * interval` valid, just greater stale), lock coverage, background sleeps/backoff, and first-read missing-cache behavior. Clear cache on stop/restart/resume; a new connection alone does not create measurements.

**Verification at implementation:** `uv run pytest tests/unit/probes/test_thermoworks_cloud_probe.py -q -n 0`.

### Task 3: Migrate server and shared wire freshness together

**Files:** modify `blueprints/mobile/socket_io.py`, `common/persistence/runtime.py`, `common/web_contracts/core.py`, `common/web_contracts/registry.py`, shared TS health/probe projections, web health panels; regenerate `packages/pifire-core/src/contracts/core.gen.ts` using `web-react/scripts/gen-types.ts`.

**Interfaces:** `ThermocoupleHealthFreshnessView` becomes `{current: bool, lastReportedAgeS: number | null, reason: 'current' | 'stale' | 'unknown-clock' | 'retained'}`. Remote clients receive server-computed age, never subtract server monotonic instants from local clocks. `lastReportedAgeS=null` means unknowable, not zero. `current` requires a fresh identity-qualified heartbeat for the same runtime generation as the report, plus a valid report age ≤15 seconds.

- [ ] Centralize raw-stamp validation through the peripheral helper in both socket and Qt projections. Preserve report fields for unknown identity instead of dropping an alarming report or presenting an invented zero age. Compute the current verdict as:

```python
age_s = stamp_age_s(
    stamp,
    monotonic_s=clock.monotonic(),
    boot_id=local_boot_id,
    runtime_id=live_heartbeat_runtime_id,
    suspend_offset_s=local_suspend_offset_s,
)
current = heartbeat_current and age_s is not None and age_s <= 15.0
reason = "unknown-clock" if age_s is None else "current" if current else "stale"
```

`live_heartbeat_runtime_id` is accepted only from the validated current heartbeat. Missing/legacy heartbeat cannot lend identity to a report. Validate finite, nonnegative ages and required stamp schema, not timestamp magnitude.

- [ ] Add `test_legacy_wall_observed_at_is_retained_unknown`, `test_previous_boot_health_is_not_current`, `test_previous_runtime_health_is_not_current`, and `test_resume_offset_invalidates_report_before_next_writer_tick` to socket/Qt contract tests. Each asserts retained state/fault text, `current=False`, and null age where identity is unknowable. A raw `observed_at=1800000000` never passes as live.
- [ ] Change `packages/pifire-core/src/dashboard/probeHealth.ts` projected age type to `number | null`; propagate reason and “Last reported”/“Age unknown” through web/Qt/mobile. In `ProbesTab.tsx`, branch before `Math.round`; never format null as `0s`. Preserve invalid-label decisions' existing distinction between authoritative current report and retained UI report.
- [ ] Regenerate contracts and migrate all consumers in this commit. Old report blobs are read-only historical input: preserve their raw data and project unknown until a fresh producer overwrites the operational key. Do not rewrite old epochs into guessed uptime or change stored reports on a read.

**Verification at implementation:**

```bash
uv run pytest tests/web/test_socket_live_contract.py tests/web/test_socket_dash_payload_fields.py tests/ui/test_qtquick_probe_health.py -q -n 0
bun run --cwd web-react gen:types
bun run --cwd web-react gen:types:check
bun run --cwd packages/pifire-core test tests/probeHealth.test.ts tests/probeStatus.test.ts tests/deriveView.test.ts
bun run --cwd packages/pifire-core typecheck
bun run --cwd web-react typecheck
```

### Task 4: Age retained Qt/mobile health even when polling stops

**Files:** modify `display/qtbackend.py`, `display/qtapp.py` only if constructor injection is needed, `mobile/src/useLive.ts`, `mobile/app/_layout.tsx`; tests `tests/ui/test_qtbackend.py`, `tests/ui/test_qtapp_power.py`, `mobile/tests/useLive.test.tsx`, `mobile/tests/AppHealthLayout.test.tsx`, `mobile/tests/DashboardProbeRow.test.tsx`.

**Interfaces:** Qt gets `_monotonic` for poll/receipt intervals and `_wall_time` for the wall-only sites left by the peripheral plan. Rename mobile `lastPayloadAt` to `lastPayloadMonotonicMs` repository-wide via LSP; preserve `null` as the first-real-payload sentinel. Add `monotonicNowMs = () => performance.now()` in `packages/pifire-core/src/liveConnection.ts` (exported there) for all app receipt consumers; browser and phone timelines are never persisted or compared to server coordinates.

- [ ] Replace Qt `_poll_health` scheduling with monotonic capture, retaining the last successful projection and its receipt baseline. Age a cached row from that baseline on every `poll`, including throttled polls, health exceptions, and temperature fetch failure. Perform freshness advancement before early returns for missing temperature data. Avoid cumulatively adding the same age twice; a new server projection resets its baseline.
- [ ] For unknown age keep null; for known age add only local monotonic time since receipt. Cached `current=False` can never become true by extrapolation. A new successful response with the same stale producer report must remain stale.
- [ ] Change mobile payload receipt and one-second freshness tick together. Use this calculation in both `qualifyRetainedHealth` and the header, rather than retaining two independent formulas:

```typescript
const payloadAgeMs = lastPayloadMonotonicMs === null
  ? null
  : monotonicNowMs() - lastPayloadMonotonicMs;
const retained = phase !== "live" || payloadAgeMs === null ||
  payloadAgeMs < 0 || payloadAgeMs > LIVE_STALE_AFTER_MS;
```

Keep `LIVE_STALE_AFTER_MS=30000` and real-payload gating for alert transitions. Do not trigger an alert for the initial fixture. Browser/phone clock reset or app resume invalidates local receipt and requires a new socket payload; Expo AppState transition to background makes retained immediately, transition to active does not promote cached data.

- [ ] Add `test_health_poll_rollbacks_do_not_freeze_rows`: regular monotonic polling continues across wall rollback; a stopped health source ages out at 15 seconds even if temperature fetches keep succeeding. Assert Qt's invalid-label/qualifier update, not just fetch call counts.
- [ ] Add mobile `receipt_freshness_ignores_phone_wall_steps`: freeze the socket in live phase, advance the receipt clock past 30 seconds with Date.now ±3600 seconds, and assert retained health/header. Add `resume_requires_new_payload` and `fixture_does_not_emit_alert` assertions. Preserve null-age UI rendering and existing presence gating.

**Verification at implementation:**

```bash
uv run pytest tests/ui/test_qtbackend.py tests/ui/test_qtquick_probe_health.py tests/ui/test_qtapp_power.py -q -n 0
bun run --cwd mobile test --runInBand tests/useLive.test.tsx tests/AppHealthLayout.test.tsx tests/DashboardProbeRow.test.tsx
bun run --cwd mobile typecheck
```

Launch the existing non-hardware web/Qt preview and Expo test app after tests: acquire, halt acquisition without dropping the socket, and observe current→retained on each real surface. Jump only injected wall clocks, never the workstation clock. Record whether each visual surface was exercised; screenshots of a fresh page alone do not prove aging.

### Task 5: Preserve last-reading provenance without misleading age

**Files:** modify `common/current_schema.py:LastReading/build_current/_carry_last_readings`, current store writers in `controller/runtime/store.py`, `common/persistence/runtime.py` snapshot readers, `display/staleness.py`, flex/Qt consumers and socket probe projection; update current contract fixtures and generated schema consumers.

**Interfaces:** retain `LAST[label].ts` as epoch milliseconds and numeric `temp`; add nullable `clock_stamp: ClockStamp` to the last-reading record. A new valid reading gets a new stamp; a missing reading carries both value and original stamp unchanged. Server `lastReadingAge` remains seconds-or-null; null means unknown provenance, with the stale qualifier still visible. No remote raw-monotonic comparison is introduced.

- [ ] Extend the production-owned current schema/default/serializer, current snapshot conversion, and all writer signatures together; source the stamp from the same acquisition pass, not the time a web server serializes the snapshot. `CurrentSchema` is deliberately an unversioned regenerable cache: retain that convention, add nullable stamp metadata to `LastReading`, and preserve literal old `LAST={temp,ts}` inputs with unknown age. Do not invent a second current-cache version registry. Malformed current blobs keep the existing discard-and-refill behavior; do not weaken numeric-reading validation.
- [ ] Migrate `common/persistence/transforms.py:current_snapshot`, `common/persistence/runtime.py:write_current`, `controller/runtime/store.py:InMemoryStore.write_current/SqliteStore.write_current`, and their persistence protocol/fake signatures with the same acquisition-stamp argument. The production and in-memory paths must carry the same stamp; one must not independently sample wall/monotonic at serialization. Extend `tests/unit/common/test_current_schema.py` and `tests/unit/datastore/test_sqlite_store_parity.py` to assert a missing reading retains its original age/provenance through both stores.
- [ ] Change `resolve_reading` and socket age projection to consume a validated elapsed age or stamp context, not `wall_now_ms - ts`. Retain the key existing rule: missing numeric reading selects stale/last-known output; age alone never resurrects it as a current measurement.

```python
# The missing-reading branch still owns this decision.
if value is None and last is not None:
    age_s = validated_last_age_s  # float | None, computed in the local clock domain
    label = "Last known" if age_s is None else f"Last known {int(age_s)}s ago"
    return last["temp"], False, label
```

Use the existing formatting/localization conventions when integrating this branch; preserve actual displayed units and stale styles.

- [ ] Add `test_last_reading_age_survives_wall_jump`, `test_last_reading_previous_boot_has_unknown_age`, and `test_missing_reading_never_becomes_live_after_rollback`. Assert retained temperature, `has_temp=False`, unchanged wall `ts`, elapsed age advancing only on the trusted domain, and unknown age across restart generation changes. Do not assert a warning's exact wording merely to pin implementation text.

**Verification at implementation:**

```bash
uv run pytest tests/ui/test_probe_staleness.py tests/web/test_socket_probe_staleness.py tests/unit/common/test_current_contract_fixtures.py -q -n 0
bun run --cwd packages/pifire-core test tests/deriveView.test.ts tests/probeStatus.test.ts tests/probeHealth.test.ts
```

## Deployment, restart, rollback, and approval gate

- Ship the producer model, raw dictionary schema, both Python projections, generated TS contracts, web bundle, Qt backend and supported mobile app together. The plan deliberately supplies no ambiguous `observed_at` alias. If independently deployed mobile versions cannot understand null freshness, the release is gated on their upgrade/support policy; do not declare them current by a legacy fallback.
- Stop active control safely before deploying the shared-mode/probe contract. Start a new runtime generation; persisted legacy/current-generation-mismatched health stays retained/unknown until genuine acquisition. Cloud caches start empty. New heartbeat alone does not rejuvenate old probe stamps.
- Proposed suspend/resume design, subject to separate threshold/platform approval: the peripheral helper detects boottime-minus-monotonic discontinuity even before a writer's next tick; consumers invalidate old freshness. Controller safe restart clears observation/recovery history and reacquires before reenabling control. Do not add suspended seconds to heat exposure or clean-recovery intervals. This policy is not part of Main's approved timestamp-producer correction.
- Rollback means stop control, restore the matching complete code/UI contract and pre-migration operational snapshot, then start a new generation. Never reinterpret new monotonic fields as old epochs. Keep durable diagnostic/evidence records intact; no rollback deletes cook, trace, or model evidence. No active timer or heat output is automatically restored by this rollback procedure.
- User review decisions: whether to require a minimum mobile app version at coordinated rollout; accept retained confirmed-fault UI with unknown age instead of silently omitting it; approve null/unknown last-reading age across restart rather than guessed wall age; accept the proposed Linux suspend detector, 0.25s offset tolerance and unsupported-platform fail-closed behavior. These are explicit proposals, not preexisting product policy or authorization for the current coupled learning implementation.
- After proof, update the existing relevant API/probe documentation and changelog, remove throwaway smoke scripts, and retain only the listed boundary regressions. Preserve generated-contract checks and exact-revision evidence rules.

## Final verification and publication (executor only)

Run focused commands above after their task lands; final project-wide validation is centralized by the integration owner after all dependent plans land. Commit-time checks are explicit (`prek run --all-files`), since `jj new`/`jj describe` do not run hooks. Read the jujutsu skill before any VCS operation; no raw Git or direct `jj git push`.

The focused contract preflight remains prerequisite to the five authoritative release commands, not a sixth command:

```bash
uv run pytest tests/unit/test_no_cross_test_imports.py tests/unit/mpc/test_mutation_score.py tests/unit/common/test_current_contract_fixtures.py -q
uv run python scripts/exact_revision_gate.py verify-bookmark --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
```

The gate owns command ordering and before/after exact-revision checks. Preserve schema-v2 `.artifacts/exact-revision/<full-revision>/evidence.json`, separate preflight stdout/stderr/SHA-256 evidence and all five command logs; schema-v1 evidence is historical only. Failed/interrupted/missing/reordered/drifted commands fail closed. Push only when separately authorized, using:

```bash
uv run python scripts/exact_revision_gate.py push --bookmark massive-reworks-and-new-ui --artifact-root .artifacts/exact-revision
```

The push wrapper revalidates the remote revision and local evidence; never hand-edit, reuse, rename, or delete an earlier attempt's evidence. Plan creation does not constitute any of this validation.
