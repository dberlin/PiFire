# Parallel Refactor Program Design

## Scope

This program implements cleanup opportunities 1, 2, 3, 5, 6, 7, 8, 9, and 10 from the 2026-08-12 repository review:

1. Remove the legacy whole-partial control-write merge path.
2. Extract the duplicated controller-runner observation outcome buffer.
3. Make the shared frontend settings query the sole settings authority.
5. Consolidate the Adafruit ADS1015/ADS1115 adapters behind one implementation.
6. Replace synchronized settings-tab strings with a typed manifest.
7. Remove the dashboard-only clock interval.
8. Decompose `HoldMode` into pulse, trace, lifecycle, and thin mode orchestration units.
9. Separate numerical MPC control from activation, learning, calibration, and construction.
10. Split persistence by durable domain and remove duplicated SQLite/in-memory transformations.

Behavior is preserved. This is a clean cutover: migrate every caller, remove obsolete branches and compatibility aliases, and do not keep old facades merely to reduce the migration diff.

## Architecture

### Foundation seams

`controller/runtime/observation_buffer.py` becomes the only owner of generation-bound observation envelopes, terminal drops, bounded eviction accounting, context binding, and drain semantics. Sync and threaded runners keep their execution/locking differences but delegate storage and transformation to this object.

Control writes become two explicit operations: authoritative snapshot replacement and validated intent-delta enqueue. The persisted queue accepts only versioned deltas after a one-time startup policy rejects or migrates legacy rows. `WriteKind.MERGE`, null-stripping merge behavior, and `process_command(kind=...)` are removed.

### Frontend ownership

React Query keys include the normalized API base. `useSettings(baseUrl)` owns settings retrieval for Dashboard, route loaders, preferences, history, and tuner. Dashboard derives MPC learning inputs from this shared result and no longer mirrors request identity in local state.

A handwritten settings-tab manifest owns tab IDs, labels, order, editability, and PWM visibility. Route components/loaders remain in `appRoutes.tsx` but are exhaustively keyed by the manifest ID union. Draft keys accept only editable tab IDs.

The existing `useNow` external store is the only frontend wall clock. The dashboard-specific interval is deleted.

### Hardware adapter ownership

`probes/_ads1x15_adafruit.py` owns bus acquisition, channel reading, voltage conversion, error handling, and shared probe initialization. The existing `ads1015_adafruit` and `ads1115_adafruit` modules remain stable convention-loaded adapters selecting their chip class and log name. Persisted wizard module names do not change.

### Persistence domains

Persistence is split into `common/persistence/` modules:

- `control.py`: authoritative control snapshot and delta FIFO.
- `runtime.py`: settings, pellets, current, status, warnings/errors, connected users, and generic runtime blobs.
- `history.py`: metrics, history, and autotune data.
- `control_trace.py`: control-trace append/read/prune operations.
- `model_evidence.py`: evidence rows and atomic activation/rollback persistence.
- `install_state.py`: wizard/updater/OS installation state.
- `protocols.py`: narrow runtime-facing protocols and composed store types.
- `transforms.py`: pure status/current/metrics/control transformations shared by SQLite and memory adapters.

Controller-specific activation migration policy moves to `controller/model_learning/migration.py`; persistence modules expose transactions and validated storage records, not controller policy. `controller/runtime/store.py` adapters compose the domain stores and share the pure transformations. `ControllerContext.store` receives a typed protocol. Once all imports migrate, `common/datastore_accessors.py` is removed rather than retained as a re-export facade.

### MPC ownership

The plugin entry module `controller/mpc.py` remains because dynamic controller loading requires `controller.<name>.Controller`, but it becomes a small composition root rather than the implementation home.

- `controller/mpc_config.py`: defaults, finite/optional conversion, configuration normalization, and model-identification metadata.
- `controller/mpc_core.py`: estimator/solver pair, target, numerical update, applied-output feedback, diagnostics, resource close.
- `controller/mpc_factory.py`: construction, descriptor restore, candidate dry solve, and ownership of active/candidate pairs.
- `controller/mpc_calibration.py`: immutable calibration command/state transitions and probe allocation.
- `controller/model_learning/grey_runtime.py`: observation, forecast, fitting, evaluation, snapshots, and cook refit.
- `controller/model_learning/activation_runtime.py`: prepared/active/rollback ownership, durable authorization, compensation, and restore.
- `controller/runtime/model_lifecycle.py`: the exact typed activation/refit/teardown protocol shared by runners and Hold.
- `controller/mpc.py`: compose these units and implement only the public `ControllerBase` plugin contract.

Flask model activation routes call a controller-layer activation service using the shared pair factory. Routes validate requests and map typed outcomes to HTTP responses; they no longer construct native controllers or own resource cleanup.

### Hold ownership

`HoldMode` remains the mode registry entry but delegates to:

- `controller/runtime/framed_pulse.py`: frame latching, transitions, reset, applied-output feedback, and completed observation creation.
- `controller/runtime/control_trace_session.py`: trace session identity, records, model-event buffering, and close.
- `controller/runtime/modes/hold_learning.py`: observation reconciliation, evidence persistence, activation restore/events, checkpoint/refit, and calibration evidence.

`HoldMode` retains setup, per-tick ordering, safety/manual hooks, hardware commands, and teardown ordering. Dependencies are passed explicitly; moved methods are deleted from `HoldMode`, not wrapped.

## Parallel Execution

The implementation uses dependency-aware waves:

- **Wave 1:** control-write cleanup (#1), runner observation buffer (#2), base-aware settings query (#3), ADS consolidation (#5), settings-tab manifest (#6), and branch-coverage gate tooling start in parallel. Shared-clock cleanup (#7) follows the settings-query Dashboard edit while the tab-manifest branch remains independent.
- **Wave 2:** persistence pure transformations/protocols, then control/history/runtime/install/trace destination modules and their direct tests can be built in parallel without editing shared callers or the accessor monolith. Model-evidence extraction and runtime-store composition follow; one sequential cutover, governed by the checked importer matrix, migrates all callers and removes the monolith.
- **Wave 3, parallel:** numerical MPC core/factory, MPC calibration runtime, and Hold framed-pulse/trace-session extraction. Each depends only on Waves 1–2 contracts and touches separate implementation files.
- **Wave 4:** MPC activation first lands the fixed runner lifecycle protocol; grey-learning/plugin composition and Hold observation reconciliation then proceed in parallel. Hold activation/refit lifecycle composition follows the final plugin surface.
- **Wave 5:** remove old persistence/MPC/Hold implementations and run aggregate safety, integration, frontend, and coverage gates.

Within a wave, each task is a separate Jujutsu change and may run in an isolated workspace. Cross-wave interfaces are fixed in the plans; workers do not invent compatibility shims.

## Coverage Contract

For opportunities #8, #9, and #10, branch coverage is a shipment gate, not an advisory:

- Every new or substantially rewritten module in the Hold, MPC, and persistence slices must report **strictly greater than 90.0% branch coverage**.
- The final thin `controller/runtime/modes/hold.py` and `controller/mpc.py` must individually exceed 90.0% branch coverage.
- `common/datastore_accessors.py` cannot satisfy the end-state gate by remaining as uncovered compatibility code; it is removed.
- Coverage is calculated per file as `covered_branches / num_branches * 100`; a file with zero branches is 100% for this gate.
- A checked-in `scripts/check_branch_coverage.py` reads coverage.py JSON and fails if any named file is at or below 90.0% or absent from the report.
- Focused branch suites run after each extraction. The aggregate #8–#10 coverage run measures all named final modules together to catch integration-only branches and import omissions.

Tests must defend observable contracts: FIFO ordering, generation fencing, bounded eviction, pulse transition ordering, applied-output accounting, activation durability/rollback, snapshot compatibility, transaction atomicity, and exact API behavior. Source-text assertions do not count toward the coverage target.

## Safety and Compatibility

- Preserve dynamic module names `controller.mpc`, `probes.ads1015_adafruit`, and `probes.ads1115_adafruit`.
- Preserve controller output, status, snapshot, trace, and calibration wire shapes.
- Preserve control-delta version and FIFO transaction behavior.
- Preserve model activation CAS behavior, durable receipt ordering, fallback authorization, and close-on-every-path resource ownership.
- Preserve framed-pulse latching, safety/manual reset behavior, observation ordering, and actuator-off-before-archive teardown ordering.
- Preserve settings route order, probes loader, hidden-but-addressable PWM route, query invalidation behavior, and API-base changes.
- No native generated code, stored evidence bytes, public wizard keys, or unrelated UI behavior changes.

## Verification

Each slice runs focused tests and LSP diagnostics. The final program runs:

1. focused control/runtime/MPC/Hold/persistence/probe Python suites;
2. focused frontend settings/dashboard/clock tests and production build;
3. aggregate branch coverage gate for #8–#10;
4. the repository Python suite;
5. Ruff formatting/checks and language-server diagnostics;
6. the existing framed Hold smoke scenario using the real controller runner and generated Acados runtime.
