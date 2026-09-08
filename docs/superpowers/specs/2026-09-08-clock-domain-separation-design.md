# Clock-domain separation design

## Authorization and scope

On 2026-09-08 the user requested a learning-clock fix and separate implementation plans for PID, PID-SP, pulse scheduling, shared-mode timers, probe contracts, and peripheral timing. After the physical-frame dependency was explained, the user selected **Implement coupled learning boundary**: implement learning capture, replay/evidence clock semantics, and the necessary pulse/PID-SP timestamp producers together now. The remaining subsystem changes are plans, not authorized implementations. The previously verified warmup-admission fix remains included.

## Time semantics

- Wall time records when an event was observed or published. It may move backward or forward. Never use it to establish physical duration, causal order, freshness, or model authority.
- Monotonic time supplies same-boot physical interval coordinates, controller elapsed time, forecast horizons, local deadlines, and observation freshness.
- Durable insertion order and explicit generation/revision/sequence establish causal order. Wall timestamp sorting cannot decide which authority supersedes another.
- Monotonic coordinates are local to a boot/runtime trajectory identity. Restart closes old segments, invalidates live state, and obtains a new seed. Never subtract coordinates from different boots or extrapolate unobserved actuation.
- Calendar scheduling is a wall-time requirement. Relative-duration timers that survive restart require explicit saved-remaining and downtime policies; do not reinterpret old epoch deadlines as monotonic.

## Coupled learning implementation

The trace schema migrates from 9 to 10: current physical frame, applied-output, observation, and forecast interval coordinates are monotonic milliseconds. Trace envelope `ts_ms` stays wall publication time. Model observation and pulse frame payloads carry separately captured `wall_start_ms` and `wall_end_ms`. Historical trace schemas remain historical contracts; unknown or ambiguous historical mappings are not current exact evidence.

`FrameObservation.frame_start_s` and `frame_end_s` have one meaning: monotonic seconds. Add required `wall_start_ms: int` and `wall_end_ms: int` provenance fields. Wall endpoints may regress; nonnegative integer provenance is required, but wall elapsed time is not a physical admission condition. All production builders, replay adapters, experiments and current-contract fixtures migrate together. No numeric-magnitude clock guessing and no default wall/monotonic substitution.

Trajectory observation schema 4 admits physically continuous monotonic intervals with independent wall metadata. Preserve all exact-delivery, sample-monotonic-age, cadence, identity, sequence, generation, fit-corpus and seed requirements. Preserve historical schema-2/3 wall mapping validation at their historical boundary; do not reinterpret or rewrite existing evidence to claim newly available precision. Captured wall sample age/skew remain truthful signed metadata, not authority. Store/replay/migration support and canonical digests must follow the versioned contract.

Pulse scheduling and feedback receive actual monotonic readings, including initialization, advance, reset and teardown. Sample wall readings separately at frame boundaries; do not synthesize an epoch by extrapolating one old offset. PID-SP update/target/reset and Smith predictor input-history times use the same monotonic source as completed frames. Preserve the raw signed `Controller.update()` return and existing controller numerical policy. PID-wide derivative redesign, predictor coverage redesign and arbitrary loop-gap thresholds are outside this clock correction.

Learning evidence envelope timestamps come from the existing injected wall clock, not forecast completion monotonic values. Latest evidence and supersession use preserved durable order; exact authority, digest, generation and transaction fences remain unchanged. Replay joins use immutable identities and insertion order, with explicit monotonic time checks, not wall publication equality with solve/frame time.

Current evidence schema 6 adds exact monotonic frame bounds to completed calibration summaries. Database migration 13 admits trajectory observation schema 4 while preserving historical canonical rows. Current frame, trace and calibration endpoints use nearest-millisecond rounding consistently; truncating a floating-point round trip can invent a one-millisecond gap. A real Smoke-to-Hold setup gap splits the durable trajectory and discards its live seed suffix rather than fabricating delivery or disabling subsequent Hold capture.

The implemented callable clock sources propagate through Hold, initial and fallback runner construction, synchronous/threaded wrapping, and reconfiguration. Paired trajectory sampling uses the same context clock. Remaining mode timers and classical PID still follow the separate proposed migrations.

## Planned shared runtime clock API

The shared-mode plan owns the complete removal of ambiguous `Clock.now()` after every caller is classified. The destination API is:

```python
class Clock:
    def wall_time(self) -> float: ...  # epoch seconds; provenance/calendar use
    def monotonic(self) -> float: ...  # steady seconds in this boot
    def sleep(self, seconds: float) -> None: ...
```

`RealClock` delegates to `time.time`, `time.monotonic`, and `time.sleep`. `ManualClock` has independent wall and monotonic axes: normal advance/sleep advances both; `jump_wall(delta)` changes only wall. During the coupled boundary implementation, adding an explicit monotonic method while existing `now()` retains its wall meaning is permitted because untouched shared-mode metadata and timers still use that existing contract. It is not permission to pass wall time into migrated physical intervals. The later shared-mode plan removes `now()` in its complete consumer cutover.

## Plan ownership and dependencies

- PID plan: elapsed/reset timestamps, clock injection and tests; preserves output policy.
- PID-SP plan: remaining timing cleanup and shared-clock integration after the coupled producer correction; preserves predictor trust/history and raw public return.
- Pulse plan: full explicit shared-clock integration and suspension/unknown-delivery policy; distinguishes the implemented learning producer boundary from proposed additional runtime work.
- Shared-mode plan: remaining mode actuator timers, startup/shutdown, lid/manual durations, metrics and exported duration/display fields; owns shared Clock cutover.
- Probe plan: fresh local receipt clocks, wall provenance, persisted boot/runtime identity, cloud cache, and all web/Qt/mobile projections.
- Peripheral plan: watchdog/heartbeat and action-triggering user/recipe timers first; queue/serial/sampling bounds next; display/telemetry/debounce last. Learning causal-order migration is a prerequisite, not duplicated ownership.

PID-SP, pulse and shared-mode changes must never deploy with mixed physical axes. Independently authored plans are not independently deployable when their producer/consumer cutover is shared.

## Explicitly proposed policies, not implicit implementation scope

Suspend invalidates live control/learning history: unknown hardware delivery is not manufactured from elapsed time. A suspend detector may compare Linux boot-time and monotonic clocks, but its threshold and platform behavior require review. An arbitrary one-second loop-gap safe-stop threshold is not approved by this design.

For persisted relative timers, conservative proposed behavior is pause/interruption on unclean restart or suspend, with explicit resume rather than automatic delayed cooking actions. Historical epoch-only timers cannot produce trustworthy remaining duration after an unknown clock correction; require explicit rearm. This product behavior is a review point in the peripheral plan.

## Acceptance and release discipline

Regression scenarios independently move wall time by ±3600 seconds while advancing monotonic time normally. Assert physical pulse delivery, warmup countdown, scored evidence, replay order and latest-authority selection are unchanged by wall jumps. Reject real monotonic gaps, unknown output, stale samples, incompatible identities and missing seeds. Verify actual production runtime paths, not only directly constructed frames with equal clocks.

Keep `MODEL_SCHEMA = 7` and `versions 3 through 6 are migration input only.` unique and unchanged. Schema changes migrate all builders, serializers, replay/import, persisted recovery, smoke tools, mutation anchors, characterization tests and experiments. Shared test helpers remain in `_` modules, `conftest.py` or `tests/fakes/`; no cross-test imports.

Run focused checks and smoke/replay proof, then the repository contract preflight before any release commands. Only the exact-revision gate owns the five release commands and push evidence. No push is authorized by this task; no schema-v1 or stale evidence may authorize a push.
