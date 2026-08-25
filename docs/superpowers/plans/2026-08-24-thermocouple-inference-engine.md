# Thermocouple Inference Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect open/collapsed/stuck thermocouples on compatible MCP9600, MCP9601, and MAX31856 amplifiers using two independent software evidence channels, with default notify-only observation and explicit enforce authority.

**Architecture:** Drivers expose raw Celsius hot/cold pairs without inference. A pure fixed-memory engine owns per-physical-port sampling and evidence; `ProbesMain` owns stable engine identity, peer selection, hardware/inference fusion, logical-label projection, and invalidation. `ControlMode` supplies actual delivered-heat intervals and policy authority before every numeric guard or actuation.

**Tech Stack:** Python 3.14, frozen/slotted dataclasses, `deque`, Pydantic v2 settings, Adafruit CircuitPython MCP9600/MAX31856, SQLite current status, pytest/pytest-cov, Jujutsu (`jj`).

**Spec:** `docs/superpowers/specs/2026-08-23-mcp9601-thermocouple-health-design.md`

## Global Constraints

- Use Jujutsu only; never raw Git. Each task ends with `jj desc -m`, `jj new`, and `jj st`, leaving empty `@`.
- Strict TDD: observe the correct RED before production code, then focused GREEN tests. No test-after implementation.
- Do not use text grep. Use LSP for symbols/references, AST search for structure, targeted reads, and context-mode for broad/test output.
- No Qt/QML, React web, mobile, wizard-form, or generated frontend-contract changes in this plan. UI surfacing and policy controls remain in the separately approved UI design.
- Compatible amplifiers must expose both hot and cold junctions. Non-thermocouple sensors remain outside this engine.
- Fixed policy modes: `off | observe | enforce`; normalized default is `observe`. Old settings backfill through normal schema defaults; do not bump `SETTINGS_SCHEMA_VERSION` or add a bespoke migration.
- `off` allocates no inference engine/history and removes inferred reports; enabled hardware detection remains authoritative.
- A confirmed hardware report is invalid under every policy. A confirmed inferred secondary is invalid under observe/enforce. A confirmed inferred primary is valid and numeric under observe, invalid and Error-authoritative under enforce.
- Hardware health wins fusion when confirmed. Hardware and enforced inference retain status-before-numeric-guard/manual/controller/actuation safety ordering.
- Notify only on semantic transition to confirmed. Suspected, repeated reports, mode re-entry, metric-only changes, and recovery do not notify.
- No last-known-good or cold-junction substitution for invalid current data.
- Fixed engine constants are code policy, not settings: ring 301; admission period 1 s; minimum coverage 240 s; max adjacent gap 30 s; collapse absolute delta/fraction/span 1°C/95%/1°C; stuck hot span 1°C; fast prior delta 15°C; fall 20°C within 10 s plus five subsequent collapsed samples; start deficit 15°C; delivered heat 30 s; peer/cold witness rise 10°C/3°C; peer hot rise `<3°C`; cold-witness delta growth `<2°C`; secondary recovery 60 s; policy version 1.
- Peer witnesses: other physical Primary/Aux thermocouples only, pre-pass fused healthy+valid, never Food/self/suspected/confirmed/invalid. Prefer greatest qualifying rise with stable device/port tie-break; prefer peer over cold fallback.
- Active-cook eligibility: Startup, Reignite, Smoke, Hold, Manual, and active recipe submodes. Exclude Prime, Monitor, Shutdown, Stop, Error.
- Clock regression, process restart, and probe-map rebuild reset inference. `observe↔enforce` preserves history; entering `off` drops it; leaving `off` starts empty. Unit changes preserve Celsius history.
- Current structured diagnostics remain in `probe_device_info`; write one deduplicated structured event-log record per semantic transition. Do not persist rings or per-second samples.
- New/substantially rewritten modules require greater than 90% branch coverage.
- Run formatters/linters only after behavior is green and only on changed files.

---

### Task 1: Pure inference engine and policy-aware fusion

**Files:**
- Create: `probes/thermocouple_inference.py`
- Modify: `probes/thermocouple_health.py`
- Create: `tests/unit/probes/test_thermocouple_inference.py`
- Modify: `tests/unit/probes/test_thermocouple_health.py`

**Interfaces:**
- Produces `ThermocoupleInferencePolicy(StrEnum)`: `OFF`, `OBSERVE`, `ENFORCE`
- Produces `ThermocoupleJunctionSample(hot_c: float, cold_c: float)`
- Produces `ThermocoupleWitnessSample(source: tuple[str, str], temperature_c: float)`
- Produces `ThermocoupleExcitationContext(active_cook, primary_setpoint_c, delivered_heat_on_s, witnesses)`
- Produces `ThermocoupleInferenceEngine.observe(sample, excitation, is_primary, now) -> ThermocoupleHealthReport`
- Produces `ThermocoupleInferenceEngine.current_report()` and `.reset()`
- Produces `fuse_thermocouple_health(hardware, inferred, policy, is_primary) -> ThermocoupleHealthReport`
- Relaxes the report invariant only for confirmed inferred primary observe reports with `temperature_valid=True`

- [ ] **Step 1: Use LSP before evolving the report invariant**

Run LSP references for `ThermocoupleHealthReport`, `HardwareFaultLatch`, `.confirmed`, and `temperature_valid`. Record every constructor/test/consumer. Preserve hardware constructor behavior and JSON shape.

- [ ] **Step 2: Write failing policy/fusion contracts**

Add tests first:

```python
def test_observed_inferred_primary_is_confirmed_but_remains_numeric():
    inferred = _confirmed_inference(now=10.0)
    fused = fuse_thermocouple_health(
        hardware=None,
        inferred=inferred,
        policy=ThermocoupleInferencePolicy.OBSERVE,
        is_primary=True,
    )
    assert fused.state is ThermocoupleHealthState.CONFIRMED
    assert fused.temperature_valid is True
    assert fused.detail["policy"] == "observe"
    assert fused.detail["authority"] == "notify_only"


def test_enforced_inferred_primary_is_invalid():
    fused = fuse_thermocouple_health(
        hardware=None,
        inferred=_confirmed_inference(now=10.0),
        policy=ThermocoupleInferencePolicy.ENFORCE,
        is_primary=True,
    )
    assert fused.confirmed
    assert fused.temperature_valid is False
    assert fused.detail["authority"] == "stop"


def test_confirmed_hardware_wins_even_when_inference_is_off():
    hardware = ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.OPEN,), now=10.0, status=0x10)
    fused = fuse_thermocouple_health(
        hardware=hardware,
        inferred=None,
        policy=ThermocoupleInferencePolicy.OFF,
        is_primary=True,
    )
    assert fused is hardware
```

Cover observe/enforce secondary invalidity, suspected validity, clean hardware plus inferred result, inferred malfunction fault/evidence ordering, off without hardware returning unmonitored, and hardware status/detail preservation.

Run through context-mode. Expected RED: missing inference module and the current confirmed+valid invariant rejects observe-primary fusion.

- [ ] **Step 3: Implement immutable types and pure fusion**

Create frozen/slotted input dataclasses and validate all temperatures/times are finite. Keep the engine module independent from controller/settings modules. In `ThermocoupleHealthReport.__post_init__`, permit confirmed+valid only when evidence is non-hardware and owned detail contains `policy="observe"`, `authority="notify_only"`, and `is_primary=True`; all other confirmed reports remain invalid.

Fusion rules, in priority order:

```python
if hardware is not None and hardware.confirmed:
    return hardware
if policy is OFF or inferred is None:
    return hardware or ThermocoupleHealthReport.unmonitored(observed_at)
if not inferred.confirmed:
    return inferred
# confirmed inferred
return inferred.with_policy_authority(
    policy=policy,
    is_primary=is_primary,
    temperature_valid=(policy is OBSERVE and is_primary),
)
```

Do not mutate input reports/detail mappings.

- [ ] **Step 4: Write sampling, boundary, and reset RED tests**

Use injected numeric `now`; never sleep. Cover:

- admission calls at 0.0/0.999/1.0 and accumulated heat across rejected sub-second calls;
- ring eviction at 300/301/302 accepted samples;
- coverage 239.999/240/240.001;
- adjacent gap 29.999/30/30.001;
- clock regression resetting ring/fast arm/report;
- reset returning unmonitored and empty history;
- no synthetic samples.

Expected RED: engine methods absent.

- [ ] **Step 5: Implement fixed sampling and diagnostics**

Use `deque(maxlen=301)`. Every accepted entry owns `now`, hot/cold/delta, cumulative delivered heat since prior admitted sample, active flag, setpoint, and immutable witness snapshot. Calls before one second accumulate heat but do not append. A clock regression calls `reset()` before admitting the new sample.

Every report detail is JSON-safe and includes policy version, sample count, coverage, max gap, hot/cold/delta spans, collapse fraction, heat-on seconds, witness source/rise, and asserted channels when available. Metrics-only changes retain state/fault identity.

- [ ] **Step 6: Write and implement slow-channel RED/GREEN sequences**

Tests must cover exact boundaries:

```text
collapse fraction: below / exactly / above 0.95
abs(delta): below / exactly / above 1°C
delta span: below / exactly / above 1°C
hot span: below / exactly / above 1°C
setpoint deficit: 14.999 / 15 / 15.001°C
heat: 29.999 / 30 / 30.001s
peer rise: 9.999 / 10 / 10.001°C
cold rise: 2.999 / 3 / 3.001°C
candidate peer response: below 3 confirms; exactly 3 does not
cold delta growth: below 2 confirms; exactly 2 does not
```

A valid ramp stays healthy. Diagnostic collapse at rest/setpoint cannot change state. One eligible channel yields suspected; both channels in the same eligible window yield confirmed malfunction. Commanded heat without a warming witness remains healthy/insufficient evidence. Peer wins over cold when both qualify.

Implement channel evaluation as pure helpers in the engine module; no controller branches.

- [ ] **Step 7: Write and implement fast-path RED/GREEN sequences**

Cover prior separation 14.999/15, fall 19.999/20, event interval 9.999/10/10.001, and exactly five strictly subsequent collapsed samples. Assert inactive cook, steady maintenance, and lid-open drop without collapse never arm/confirm. Fast confirmation detail contains both `implausible-step` and `junction-collapse` evidence.

- [ ] **Step 8: Write and implement inference recovery/latching contracts**

- Primary confirmed remains confirmed until reset.
- Slow suspected clears only on a later eligible clean slow window.
- Slow secondary confirmed recovery advances only through eligible non-anomalous observations and resets on anomaly/ineligible gap.
- Fast secondary confirmed recovery accepts ordinary contiguous clean observations and clears at exact 60 seconds.
- No elapsed-time-only clearing.

- [ ] **Step 9: Run Task 1 focused tests and branch gate**

```bash
uv run pytest -q -n 0 \
  tests/unit/probes/test_thermocouple_inference.py \
  tests/unit/probes/test_thermocouple_health.py \
  --cov=probes.thermocouple_inference \
  --cov=probes.thermocouple_health \
  --cov-branch --cov-report=term-missing
```

Expected: all pass; both modules individually exceed 90% branch coverage.

- [ ] **Step 10: Commit Task 1**

```bash
jj desc -m "Add thermocouple inference engine"
jj new
jj st
```

---

### Task 2: Celsius junction samples from compatible drivers

**Files:**
- Modify: `probes/base.py`
- Modify: `probes/_mcp960x_adafruit.py`
- Modify: `probes/max31856_adafruit.py`
- Modify: `tests/unit/probes/test_mcp9600_probe.py`
- Modify: `tests/unit/probes/test_mcp9601_probe.py`
- Modify: `tests/unit/probes/test_max31856_probe.py`

**Interfaces:**
- Produces `ProbeInterface.get_thermocouple_samples() -> Mapping[str, ThermocoupleJunctionSample]`, default empty
- MCP960x and MAX31856 implementations return physical-port-keyed Celsius samples from the current successful read
- Preserves all existing user-unit temperature dictionaries and hardware status-before-temperature behavior

- [ ] **Step 1: Run LSP references for driver read contracts**

Use LSP references on `ProbeInterface`, each device temperature property, and each `ReadProbes.read_all_ports`. Use AST search for overrides. No text grep.

- [ ] **Step 2: Add failing default and MCP960x sample tests**

Assert non-thermocouple base returns `{}`. For MCP9600/MCP9601, assert one successful read captures exact raw Celsius hot/cold under `KTT0` regardless of F/C UI units. Detection-off still reads hot+cold once; direct hardware fault returns no new sample and preserves invalid health without reading hot/cold. A hot or cold read exception publishes no partial sample and preserves prior health/recovery guarantees.

- [ ] **Step 3: Add failing MAX31856 cold-junction tests**

Expose `TCDevice.reference_temperature` from Adafruit's property. Assert successful `TC0` sample stores hot/reference Celsius, user output conversion remains unchanged, and exceptions do not publish partial samples.

- [ ] **Step 4: Implement default and driver side channels**

`read_all_ports()` owns a `_thermocouple_samples` mapping replaced atomically only after both hot and cold reads succeed. Use the same hot value for user output and sample; do not reread. Keep MCP9601 status first. For clean secondary recovery, commit latch progress only after both junction reads succeed; either read exception cancels the clean window and reraises unchanged.

- [ ] **Step 5: Run focused drivers and coverage**

```bash
uv run pytest -q -n 0 \
  tests/unit/probes/test_mcp9600_probe.py \
  tests/unit/probes/test_mcp9601_probe.py \
  tests/unit/probes/test_max31856_probe.py \
  tests/unit/probes/test_base.py
```

Expected: all pass; MCP9600 behavior and hardware safety ordering remain unchanged.

- [ ] **Step 6: Commit Task 2**

```bash
jj desc -m "Expose thermocouple junction samples"
jj new
jj st
```

---

### Task 3: ProbesMain engine ownership, witnesses, fusion, and invalidation

**Files:**
- Modify: `probes/main.py`
- Modify: `tests/unit/probes/test_probe_health_aggregation.py`
- Create: `tests/unit/probes/test_thermocouple_orchestration.py`

**Interfaces:**
- `ProbesMain.__init__(probe_map, units, disable=False, inference_policy=OBSERVE)`
- `set_thermocouple_inference_policy(policy) -> None`
- `read_probes(*, excitation=None, now=None) -> dict`, retaining no-argument compatibility
- Engines keyed by `(configured device name, physical port)`; reports projected by logical label

- [ ] **Step 1: Write failing ownership/reset/policy tests**

Cover engine allocation only for compatible samples when policy is not off, stable physical identity, no label-based aliasing, rebuild/process-construction reset, off drop/no allocation, off→observe empty start, and observe↔enforce history preservation. Invalid policy raises before changing state.

- [ ] **Step 2: Implement engine registry and policy lifecycle**

Build identity from configured device+port, never display label. Policy setter performs the exact reset/preserve semantics. Existing hardware reports remain available under off. Default/no-argument reads use `time.monotonic()` and inactive/zero excitation for non-controller callers.

- [ ] **Step 3: Write failing two-pass witness selection tests**

Construct mixed Primary/Food/Aux devices. Prove self/Food/unhealthy/suspected/confirmed/invalid exclusion, Primary/Aux inclusion, maximum rise selection, deterministic identity tie-break, peer priority over cold fallback, and same-pass circularity prevention using pre-pass fused health.

- [ ] **Step 4: Implement two-phase observation/fusion**

Phase A: read/filter devices and collect current raw samples+hardware reports. Phase B: select witness candidates from prior/pre-pass fused health, observe each engine, fuse hardware+inference, project labels, and replace current health. Only then compare `(state,faults)` for transitions.

- [ ] **Step 5: Write failing output invalidation and device-info consistency tests**

- observed inferred primary confirmed: numeric primary remains;
- enforced inferred primary confirmed: primary becomes `None` on the confirming read;
- inferred secondary confirmed under observe/enforce: affected output becomes `None`, primary stays numeric;
- suspected stays numeric;
- hardware confirmation remains invalid under off/observe/enforce;
- `get_device_info()` serialized report is exactly the fused report seen by safety, not driver-only hardware state;
- metadata-only detail changes do not enqueue transitions.

- [ ] **Step 6: Implement fused projection/invalidation**

Replace per-device `thermocouple_health` status entries with matching fused reports before returning device info. Apply invalidation to existing output group/label after fusion; never substitute values. Preserve central Kalman `None` behavior.

- [ ] **Step 7: Run Task 3 focused tests and coverage**

```bash
uv run pytest -q -n 0 \
  tests/unit/probes/test_probe_health_aggregation.py \
  tests/unit/probes/test_thermocouple_orchestration.py \
  tests/unit/probes/test_thermocouple_inference.py \
  --cov=probes.main --cov=probes.thermocouple_inference \
  --cov-branch --cov-report=term-missing
```

- [ ] **Step 8: Commit Task 3**

```bash
jj desc -m "Orchestrate inferred thermocouple health"
jj new
jj st
```

---

### Task 4: Settings default and runtime construction

**Files:**
- Modify: `common/defaults.py`
- Modify: `common/settings_schema.py`
- Modify: `controller/runtime/devices.py`
- Modify: `tests/unit/common/test_settings_schema.py`
- Modify: `tests/unit/datastore/test_settings_shape_migration.py`
- Modify: `tests/unit/runtime/test_devices.py`

**Interfaces:**
- Adds root settings shape `thermocouple_health: { inference_policy: Literal['off','observe','enforce'] = 'observe' }`
- Constructs `ProbesMain` with normalized policy
- No settings schema-version bump or custom migration

- [ ] **Step 1: Use LSP on settings authority and ProbesMain construction**

Find all `default_settings`, root settings model, and `ProbesMain(...)` references. Migrate every constructor/fake.

- [ ] **Step 2: Add failing schema/default/migration tests**

Assert default parity, round-trip, sparse old stored shape normalizing to observe, all three accepted values, arbitrary string rejected, and schema version unchanged. Assert runtime device construction passes the normalized enum/string.

- [ ] **Step 3: Implement settings model/default and construction**

Define one nested Pydantic model; reject booleans/unknown keys through existing strict rules. Do not duplicate defaults outside `common/defaults.py` and Pydantic default.

- [ ] **Step 4: Run focused settings/device tests**

```bash
uv run pytest -q -n 0 \
  tests/unit/common/test_settings_schema.py \
  tests/unit/datastore/test_settings_shape_migration.py \
  tests/unit/runtime/test_devices.py
```

- [ ] **Step 5: Commit Task 4**

```bash
jj desc -m "Add thermocouple inference policy setting"
jj new
jj st
```

---

### Task 5: Actual heat excitation and policy-aware controller authority

**Files:**
- Modify: `controller/runtime/modes/base.py`
- Modify: `controller/runtime/controller.py`
- Modify: `tests/fakes/probes.py`
- Modify: `tests/unit/runtime/test_control_mode_base.py`
- Modify: `tests/unit/runtime/test_hold_calibration.py`
- Modify: `tests/unit/runtime/test_mode_settings_reload.py`
- Modify: `tests/characterization/test_modes_golden.py` only for intentional extra inference context/read signatures

**Interfaces:**
- ControlMode computes delivered auger-or-igniter seconds from actual output state and monotonic tick intervals
- Passes `ThermocoupleExcitationContext`, Celsius setpoint, and injected `now` to each read
- `_process_thermocouple_health()` stops primary only for hardware or `authority='stop'`
- Policy settings reload reaches the existing `ProbesMain` without reconstruction for observe↔enforce

- [ ] **Step 1: Write failing excitation accumulator tests**

Use `ManualClock`. Prove exact union time for auger/igniter booleans (both on does not double count), zero on first read, no negative/regressed time, and actual output—not requested duty—drives accumulation. Verify active mode set and Fahrenheit setpoint conversion to Celsius.

- [ ] **Step 2: Implement controller-neutral excitation construction**

Capture output status before the probe read but perform no actuation. Attribute the elapsed interval to the output state that was actually applied. Pass delta to `ProbesMain`; the engine accumulates until one-second admission. Preserve the fresh health fence before manual/controller actuation.

- [ ] **Step 3: Write failing observe/enforce/hardware safety tests**

Cover preflight, post-setup, and in-loop:

- inferred primary observe confirmed: numeric, notification transition once, no Error, on_tick allowed;
- inferred primary enforce confirmed: `None`, Error before numeric guards/manual/on_tick;
- hardware primary confirmed: Error under off/observe/enforce;
- inferred secondary confirmed: `None`, secondary notification once, control continues;
- suspected: numeric, no notification/stop;
- observe→enforce already-confirmed primary stops immediately without duplicate notification;
- mode re-entry/repeated report does not notify again;
- no fallback notification when transition queue is empty.

- [ ] **Step 4: Implement policy-aware health processing**

Stop by report authority/effective validity, not state alone. Iterate transitions for notifications/logging; remove current fallback notification. A confirmed report still blocks if hardware or enforce authority even when its transition was consumed, but does not notify again.

- [ ] **Step 5: Write and implement settings reload behavior**

Active and stopped settings update paths call `set_thermocouple_inference_policy()`. `observe↔enforce` retains engines; entering off drops them. Unit changes do not rebuild engines or convert stored Celsius samples.

- [ ] **Step 6: Run Task 5 safety/characterization tests**

```bash
uv run pytest -q -n 0 \
  tests/unit/runtime/test_control_mode_base.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_mode_settings_reload.py \
  tests/unit/runtime/test_guard_engine.py \
  tests/characterization/test_modes_golden.py \
  tests/characterization/test_mode_transitions.py
```

- [ ] **Step 7: Commit Task 5**

```bash
jj desc -m "Apply thermocouple inference authority"
jj new
jj st
```

---

### Task 6: Accurate notifications and structured transition diagnostics

**Files:**
- Modify: `notify/notifications.py`
- Modify: `controller/runtime/modes/base.py`
- Modify: `tests/unit/notify/test_notifications_events.py`
- Modify: `tests/unit/runtime/test_control_mode_base.py`

**Interfaces:**
- Adds exact event `Thermocouple_Fault_Primary_Observed`
- Retains shutdown primary event for hardware/enforced faults and existing secondary event
- Emits one structured event-log line per semantic transition

- [ ] **Step 1: Add failing notification-copy tests**

Pin exact observed copy that states a control-probe fault was detected and observe mode did not stop heating. Retain existing shutdown copy only for actual stop authority. Suspected/recovery/repeat emits nothing.

- [ ] **Step 2: Implement event selection and transition logs**

Select event from transition current report role/authority. Structured log payload includes label, role, state, faults, evidence, policy, authority, policy version, window/coverage, spans, collapse fraction, heat seconds, witness identity/rise, and hardware status when present. Serialize deterministically; no ring/sample dump.

- [ ] **Step 3: Add end-to-end transition tests**

Assert one observed notification/log, one enforced stop notification/log, one secondary notification/log, no repeated/mode-reentry notification, recovery current-state clearing without notification, and exact `probe_device_info` report equality.

- [ ] **Step 4: Run notification/runtime tests**

```bash
uv run pytest -q -n 0 \
  tests/unit/notify/test_notifications_events.py \
  tests/unit/runtime/test_control_mode_base.py \
  tests/unit/probes/test_thermocouple_orchestration.py
```

- [ ] **Step 5: Commit Task 6**

```bash
jj desc -m "Report inferred thermocouple faults"
jj new
jj st
```

---

## Final verification

- [ ] Run LSP diagnostics on all changed Python files and LSP references for every evolved exported symbol.
- [ ] Run changed-file `ruff format`, then changed-file `ruff check`; rerun affected tests if formatting changes files.
- [ ] Run branch coverage for `probes/thermocouple_inference.py`, `probes/thermocouple_health.py`, and substantially changed orchestration modules; each new/substantially rewritten module must exceed 90% branch coverage.
- [ ] Run focused backend gate:

```bash
uv run pytest -q -n 0 \
  tests/unit/probes \
  tests/unit/common/test_settings_schema.py \
  tests/unit/datastore/test_settings_shape_migration.py \
  tests/unit/runtime/test_devices.py \
  tests/unit/runtime/test_control_mode_base.py \
  tests/unit/runtime/test_hold_calibration.py \
  tests/unit/runtime/test_mode_settings_reload.py \
  tests/unit/runtime/test_guard_engine.py \
  tests/unit/notify/test_notifications_events.py \
  tests/characterization/test_modes_golden.py \
  tests/characterization/test_mode_transitions.py
```

- [ ] Run the full Python suite with the existing native artifacts linked into the isolated workspace. Any manifest/catalog count change must update the producing-end API contract in the same task.
- [ ] Confirm no Qt/QML, React web, mobile, generated frontend-contract, or wizard-form files changed.
- [ ] Verify `jj st` shows empty undescribed `@` above the last task commit. Keep the workspace until UI design/implementation integration is decided.
