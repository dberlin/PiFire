# Acados Grey-Box MPC Migration

## Problem

PiFire's production MPC currently builds its nonlinear grey-box controller with do-mpc, CasADi, and IPOPT. The same controller also carries a neural approximation policy plus three linear-model learning paths: Scheduled ARX, innovation state-space, and linear MPC. This creates a large runtime dependency and several model, policy, evidence, and persistence paths for one control function.

The sibling `../acados` project demonstrates that PiFire's grey-box MPC can be generated and solved through acados with comparable control behavior and solve speed comparable to the linear policies. Its native wrapper, generated solver, platform selection, parity tests, and error contracts are the starting point for this migration.

The migration must not require an operator to download acados manually. PiFire must not record a Git submodule or vendor upstream acados source. A single command must regenerate the PiFire solver, build acados and the native wrapper, validate the result, and publish it. The updater and fresh installer must build the native component when necessary.

Linear MPC and the neural policy no longer provide a useful runtime speed tradeoff once acados is the production nonlinear solver. They must be removed. Online learning, operator-directed probes, the MPC learning panel, its dashboard pill, reviewed activation, automatic passive activation, and rollback must remain, but every learned candidate must describe the grey-box model that acados controls.

## Decisions

### Solver and estimator

- Acados is the only MPC policy.
- The production state estimator is the EKF by default. The lightweight KF remains available. MHE is removed.
- The grey-box model has eight transport-delay states and a fixed 25-second prediction step.
- `n_horizon` remains configurable from 5 through 24 steps, providing 125 through 600 seconds of prediction.
- The neural `net` policy is removed.
- All Scheduled ARX, innovation state-space, Laguerre DMC, and linear-MPC control paths are removed.

### Online learning

- The learned free parameters remain `C_c`, `K_Q`, and `theta`.
- `h_amb` and `sigma` remain fixed. A chamber-temperature trace identifies their combined loss effect, not their independent split; freeing either has already produced scale runaway and physically unusable fits.
- Passive observations and operator-directed calibration probes feed one grey-box candidate pipeline.
- `enable_online_adaptation=true` authorizes automatic activation of a passive candidate after every evidence, confidence, safety, persistence, and native-solver gate passes.
- Candidates produced by an operator-started calibration remain subject to explicit reviewed activation using the exact candidate digest and confidence decision ID.
- The complete state of passive and manual learning is exposed through one backend report, the existing learning panel, and the existing dashboard pill.

### Source ownership

PiFire owns its integration code and generated PiFire model code. It does not own or store upstream acados source.

Committed PiFire artifacts include:

- the root/native CMake entry points;
- `AcadosPifirePlatform.cmake`, imported unchanged from `../acados`;
- PiFire's native C ABI header and wrapper implementation;
- generated `pifire_grey` C and its provenance manifest;
- the Python native wrapper and contracts;
- rebuild and updater integration;
- focused parity, platform, migration, and runtime tests.

The following from `../acados` do not move into PiFire:

- `vendor/acados`;
- `.gitmodules`;
- the generated linear solver;
- linear C ABI exports and Python wrappers;
- linear code generation and benchmarks.

There is no known vendoring blocker. If a clean FetchContent build cannot obtain a required upstream dependency inside the ignored build directory, implementation stops and reports the exact dependency before any upstream source is copied into PiFire.

## Dependency Acquisition

CMake `FetchContent` obtains upstream acados from its Git repository at the reviewed commit:

```text
503364817c872d474ab5bed219c26760ac267769
```

The FetchContent declaration may initialize acados' required upstream submodules inside CMake's ignored `_deps` checkout. This does not create a PiFire submodule and does not expose Jujutsu to a Gitlink. The source checkout and every recursive dependency remain build-cache material.

The declaration is pinned to the full commit rather than a moving tag. The build manifest records that commit, the resolved required dependency revisions, the platform selection, compiler identity, ABI version, generated-source digest, and wrapper-source digest.

The native build retains the settings proven in `../acados`:

- static dependencies;
- OpenMP enabled;
- examples disabled;
- unit tests disabled for the production build;
- platform-specific BLASFEO and HPIPM selections from `AcadosPifirePlatform.cmake`.

Unsupported processors fail configuration explicitly. They do not silently fall back to a generic target unless the imported platform module already selects that fallback.

## One-Command Rebuild

The repository exposes:

```text
./rebuild-acados.sh
./rebuild-acados.sh --if-needed
```

The command is non-interactive and safe for developer, CI, installer, and updater use.

### Full rebuild

Full mode performs these ordered steps:

1. Acquire a build lock so concurrent updater, installer, and developer builds cannot publish over one another.
2. Configure CMake and populate the pinned acados checkout in the ignored build directory.
3. Create staging directories on the same filesystem as the eventual published outputs.
4. Run the PiFire grey-box generator with the code-generation dependency group and the fetched acados Python templates.
5. Validate generated provenance, generated-tree completeness, and numerical equation parity.
6. Build acados, BLASFEO, HPIPM, the generated solver, and the PiFire native wrapper with the imported platform settings.
7. Validate the native ABI, Python library discovery, horizon 5 and horizon 24 construction, invalid-horizon rejection, one cold solve, and representative perturbed warm solves.
8. Write a complete build manifest into staging.
9. Atomically replace the checked generated solver tree, native library, and installed manifest only after every check succeeds.

A failed command leaves every previously published output intact and returns nonzero.

### Conditional deployed rebuild

`--if-needed` does not install CasADi and does not regenerate C on a deployed grill. It compares the installed manifest against:

- the pinned acados commit and dependency revisions;
- generated-source manifest and digest;
- native wrapper and public header digests;
- ABI version;
- platform and architecture;
- relevant CMake configuration;
- compiler/runtime compatibility fields required by the native loader.

If those inputs match, it exits successfully without building. If they differ, it compiles the committed reviewed generated C into staging, runs the deployed smoke checks, and atomically publishes the result.

The updater calls `--if-needed` after source and Python dependency synchronization and before it declares the update successful or restarts PiFire. A branch change follows the same ordering. A native build or smoke failure is fatal to that update: the updater records a terminal failure, does not publish `Finished`, and does not restart the service. The already-running process continues with the library it already loaded.

Fresh installers call `--if-needed` after installing the compiler and CMake prerequisites. Service startup also runs the conditional check before starting PiFire, so a machine rebooted after an interrupted update must finish a compatible native build or fail closed rather than load new Python against an old ABI. Build output is delimited in the updater log so native failures are independently diagnosable.

## Native ABI and Python Boundary

The public ABI is based on the reviewed wrapper in `../acados` and is bumped for runtime horizon support and removal of linear exports.

The grey configuration includes `horizon_steps`. Valid values are 5 through 24. The prediction step and delay-state count are ABI constants: 25 seconds and eight states.

The native handle:

- creates the generated capsule with `pifire_grey_acados_create_with_discretization()`;
- supplies a 25-second vector for the selected stage count;
- allocates warm-start state, control, multiplier, and diagnostic storage for that stage count;
- updates physical parameters at every stage;
- retains the last successful iterate;
- restores that iterate after a failed solve;
- emits bounded structured diagnostics without returning NaN or infinity across the ABI;
- rejects unsupported dimensions before allocating solver state.

The public solve-output structure retains fixed capacity for the maximum 24 stages and reports the selected sequence length explicitly. The wrapper writes only that many entries and zeroes the unused tail. Runtime selection therefore does not expose allocator ownership or variable-length C storage across the ABI.

The Python wrapper owns native-handle lifetime and finalization. It validates finite configuration and input values, shapes arrays without hidden copies, exposes immutable solve results and diagnostics, and converts every backend failure into the established structured `SolverError` contract.

The native library is discovered only at the repository-controlled published path. The loader verifies the ABI before resolving any remaining symbols and reports a rebuild command in every missing-library or ABI-mismatch error.

## Controller Runtime Cutover

`controller/mpc.py` retains control orchestration, actuator allocation, asynchronous execution, calibration commands, evidence publication, model persistence, and safety behavior. Its do-mpc construction is replaced directly; there is no dual backend or compatibility switch.

The build path produces:

- an EKF or optional KF using the configured grey parameters;
- an `AcadosGreyBoxMPC` handle using the same parameters and selected horizon;
- the existing combustion allocator.

A control update continues to:

1. consume realized applied load and the latest chamber measurement;
2. update the estimator;
3. compute the equilibrium load and residual initial state;
4. submit/consume the asynchronous acados solve;
5. validate solver status, finiteness, constraints, generation, and freshness;
6. allocate the accepted firing-rate command to auger and fan;
7. retain the last safe command on a transient failure;
8. enter the existing fallback behavior after repeated failures.

No fitted parameter changes generated C. Candidate and active parameters are runtime values supplied at every acados stage.

## Runtime Dimensions

Prediction look-ahead is:

```text
n_horizon * 25 seconds
```

The runtime horizon range is therefore:

| Steps | Look-ahead |
| ---: | ---: |
| 5 | 125 s |
| 8 | 200 s |
| 12 | 300 s |
| 18 | 450 s |
| 24 | 600 s |

A Release, single-threaded workstation probe of the existing generated solver with 1,000 perturbed warm solves measured:

| Steps | Median | p95 | p99 | Non-success status |
| ---: | ---: | ---: | ---: | ---: |
| 5 | 0.182 ms | 0.279 ms | 0.547 ms | 0/1000 |
| 8 | 0.290 ms | 0.445 ms | 0.811 ms | 3/1000 |
| 12 | 0.419 ms | 0.752 ms | 1.223 ms | 2/1000 |
| 18 | 0.598 ms | 1.033 ms | 1.630 ms | 2/1000 |
| 24 | 0.834 ms | 1.227 ms | 2.065 ms | 2/1000 |

Runtime creation was below 2 ms in the probe. Horizons above 24 showed unacceptable solve-failure rates with the currently generated solver options and are not supported.

The generated discrete RK4 map hardcodes 25 seconds. Acados' runtime time-step array changes cost scaling and solver metadata but does not change that map, so `t_step` must not remain a user setting under a false claim of physical discretization. Supporting a different step is outside this migration and would require a parameterized transition or continuous-dynamics regeneration plus new parity and stability evidence.

## Estimator Decision

MHE is removed and saved MHE selections migrate to EKF.

In paired calibrated closed-loop runs, EKF had lower RMSE, lower integrated absolute error, a larger fraction of time within 5°F, and lower median update time than MHE across GrillSim steady, step, and lid scenarios. On the long MAK 450°F scenario, tracking was effectively tied while EKF remained faster. MHE provides no observed control-quality benefit that warrants preserving do-mpc/CasADi.

The optional KF remains because it is a lightweight estimator, not a linear MPC policy, and does not retain the removed dependency path.

## Grey-Box Learning Architecture

### Observation sources

One candidate pipeline accepts three labeled evidence origins:

- `passive-online`: completed normal Hold frames while Online Model Adaptation is enabled;
- `operator-calibration`: completed eligible probe frames produced by the learning panel's calibration workflow;
- `cook-refit`: the complete bounded cook record submitted at teardown when Learn This Grill is enabled.

Every observation carries requested load, realized load, chamber temperature, ambient provenance, continuity, inhibition/discard disposition, cook/session identity, frame revision, and model generation. Discarded, unknown-actuation, interrupted, or stale-generation frames cannot enter a fit.

### Background fitting

Grey fitting takes seconds and never runs on the controller worker.

A single background fitting process receives an immutable bounded history snapshot after the minimum-sample, excitation, coverage, and continuity gates pass. BLAS thread counts are fixed to one in the worker. Only one fit may be outstanding. A result carries the source mode, cook/session identity, input-window identity, configuration digest, incumbent digest, and generation. Any mismatch at delivery makes the result stale and records that disposition in learning status.

The fitter reuses the canonical grey-box simulation and existing log-space least-squares implementation. It learns `C_c`, `K_Q`, and `theta`, preserves configured `h_amb`, `T_amb`, `sigma`, and the fixed eight-state structure, and reports convergence, fit quality, identifiability, sample count, temperature band, and solver effort.

### Candidate evaluation

A successful fit becomes a grey-box challenger. It does not immediately control the grill.

The generic evidence, confidence, and activation contracts currently located under `controller.linear_mpc` move to model-neutral modules. Linear model implementations and policy contracts are deleted.

Incumbent and challenger predictions are evaluated prospectively from identical causal origins over completed future observations. The existing horizon, temperature-band, heating/coasting, bias, band-error, bootstrap confidence, consecutive-win, and evidence completeness gates remain. Candidate evaluation additionally requires:

- physical promotion bounds;
- identifiability acceptance;
- current target reachability and safety feasibility;
- a candidate native handle built successfully off-path;
- a successful representative acados dry solve with finite diagnostics;
- target-hardware timing evidence;
- a durable activation record prepared for the exact candidate digest and decision ID.

### Activation policy

A `passive-online` candidate may activate automatically only when `enable_online_adaptation` is true and every automatic gate passes. The activation reason records `passive-auto`.

An `operator-calibration` candidate stops at `ready-for-review`. The existing activation endpoint must receive the exact candidate digest and confidence decision ID. Its activation reason records `operator-reviewed`.

A `cook-refit` candidate retains the existing Learn This Grill authorization: when `enable_identification` is true, an accepted teardown fit is adopted for the next cook and records `cook-refit`. If the setting is false, teardown does not fit or activate. Probe observations may improve that cook record, but they do not independently change this explicit end-of-cook setting.

For a `passive-online` or `operator-calibration` candidate, the runtime builds the candidate estimator and acados handle before committing. After durable persistence succeeds, the controller swaps the estimator/solver pair at a completed-frame boundary. The previous pair remains the rollback owner through the confidence observation window. A failed build, persistence transaction, swap, or post-activation confidence check leaves or restores the incumbent and records the exact reason.

### End-of-cook learning

The full end-of-cook refit remains as the `cook-refit` origin. It runs only after the controller worker stops, uses the complete bounded cook history, applies the same physical and identifiability rules, records its outcome, and publishes a final checkpoint. Its accepted parameters become active for the next cook only when Learn This Grill (`enable_identification`) authorized the refit. It does not revive a linear or neural candidate path or perform a mid-frame activation.

## Calibration Probes and Learning Panel

The operator-facing calibration workflow remains functionally intact:

- Start, pause, resume, stop, and reset-progress commands;
- empty-grill and pellet acknowledgements;
- 225°F, 325°F, and 425°F bands;
- probes around the operator's active Hold rather than autonomous setpoint changes;
- dynamic grill-maximum ceiling;
- requested-versus-realized load accounting;
- eligible/ineligible counts and reasons;
- completed and missing stages.

`GreyBoxPredictionAdapter` moves out of the deleted linear namespace and remains the canonical adapter for probe safety forecasts and evidence predictions. The probe coordinator continues to refuse a probe whose incumbent grey forecast crosses the configured grill maximum.

### Unified learning report

One report covers passive and manual learning. It exposes:

- current mode and candidate origin;
- observation eligibility and rejection reasons;
- probe calibration state, current band/probe, and stage progress;
- fit queue/running/result state and evidence-window identity;
- stale-result and fit-failure status;
- candidate generation, digest, parameter values/deltas, fit quality, and identifiability;
- native candidate build and dry-solve status;
- incumbent/challenger scores and missing gates;
- automatic versus manual activation requirement;
- persistence and pending frame-boundary swap state;
- active, candidate, default, and rollback identities;
- latest activation, rejection, fallback, and rollback reasons;
- ambient provenance and target-hardware timing evidence;
- schema invalidation and other terminal errors.

The existing panel continues polling this report and remains available whenever MPC is selected. It presents calibration controls, complete progress, candidate/readiness details, prediction scores, manual reviewed activation when required, active-model status, history, and rollback controls.

### Dashboard pill

The existing MPC learning pill remains visible whenever MPC is selected and opens the full panel. It is a compact projection of the same report, never a second state machine.

Its top-level states are:

- `collecting`;
- `insufficient excitation`;
- `fitting`;
- `evaluating`;
- `ready for review`;
- `activating`;
- `active`;
- `fallback`;
- `error` or `schema invalidated`.

The pill and open panel refresh immediately after automatic or manual transitions and cannot report different generations.

## Persistence and Settings Migration

A deterministic migration runs before controller construction.

### Settings

- Remove `policy`; saved `net` and `nlp` values both become the sole acados policy.
- Change `estimator="mhe"` to `estimator="ekf"`.
- Preserve `ekf` and `kf` selections.
- Clamp `n_horizon` to 5 through 24.
- Remove `t_step`; runtime uses 25 seconds.
- Remove `n_delay`; runtime uses eight states.
- Preserve `enable_online_adaptation`, `enable_identification`, calibration settings, physical parameters, cost weights, controller period, and actuator settings.
- Remove settings used only by linear models, MHE, or the neural policy.

The controller catalog no longer declares a do-mpc module or MPC Python extra. Native build availability is checked by the installer/updater and by the controller's module gate.

### Model snapshots

The top-level schema remains a grey-model record and is revised for fixed structure and grey-only adaptation.

Migration preserves valid top-level grey physical parameters and fit metadata. It discards nested Scheduled ARX, innovation state-space, linear-policy, and neural artifacts. Online evidence counters that cannot be interpreted without the removed model are not relabeled.

A snapshot fitted with a delay-state count other than eight is rejected with an operator-visible reason. Parameters fitted against a different delay chain are not silently attached to the new structure. The next eligible cook or calibration refits from current evidence.

The new online snapshot stores only:

- active grey parameters and metadata;
- optional grey challenger and its evidence-window identity;
- evidence counters and confidence decision identity;
- candidate origin and activation policy;
- end-of-cook authorization and the latest `cook-refit` outcome;
- active and rollback digests/generations;
- latest lifecycle and failure metadata.

Pending process jobs are not persisted. Restoration starts a fresh worker session and discards any candidate whose recorded generation does not match the restored active model.

## Deletion Boundary

Delete production code, dependencies, tests, and generated artifacts dedicated to:

- do-mpc model/controller construction;
- GreyBoxMHE;
- neural policy loading and export;
- Scheduled ARX;
- innovation state-space;
- Laguerre DMC;
- linear MPC and its certificates/warm-start path;
- linear acados generation, ABI, and Python wrapper;
- runnable linear bake-off experiments and obsolete reports that claim those models remain selectable.

Move, rather than delete, model-neutral evidence, confidence, activation, rollback, calibration-sample, and trace contracts. Their destination names must not retain `linear_mpc` terminology.

Keep:

- canonical grey-box equations and simulation;
- EKF and KF;
- grey parameter fitting and promotion bounds;
- calibration coordinator and probe behavior;
- learning evidence APIs and persistence contracts;
- controller simulation fixtures used to prove grey closed-loop behavior.

The runtime dependency set drops do-mpc, CasADi, and neural-policy-only packages. SciPy remains because grey parameter fitting uses it. Developer-only code-generation dependencies remain isolated from deployed installations.

## Failure Semantics

- A missing native library or ABI mismatch prevents MPC construction and names the rebuild command.
- A build/update failure never replaces the previously working library.
- A solve failure retains the last safe command and increments existing failure diagnostics.
- A fit failure never affects the active controller.
- A stale fit result is recorded and discarded.
- A candidate build or dry-solve failure rejects only that candidate.
- A persistence failure prevents activation.
- A post-activation confidence or runtime failure invokes the existing rollback path.
- Learning report failures change the pill/panel to an explicit error state; they do not disappear as `collecting`.

## Verification and Acceptance

### Build and supply chain

- An empty-cache `./rebuild-acados.sh` fetches the pinned acados checkout, initializes required upstream submodules only in the build directory, regenerates, builds, validates, and publishes.
- The repository contains no acados Gitlink and no vendored upstream source.
- Regeneration check mode is reproducible against the committed generated tree.
- The platform-selection matrix imported from `../acados` passes unchanged.
- `--if-needed` skips a matching build and rebuilds each individually stale input class.
- Concurrent rebuild attempts serialize.
- A forced compile or smoke failure preserves the old library and manifest.
- An interrupted update followed by service startup reruns `--if-needed`; incompatible Python/native ABI combinations fail before PiFire starts.

### Native and runtime contracts

- ABI structure-size, invalid-argument, allocation/backend-failure, invalid-solution, reset, and lifetime tests pass.
- Horizon 5 and 24 cold/warm solves pass; values outside 5 through 24 are rejected.
- Generated equations match the canonical PiFire grey equations within the established parity tolerance.
- Acados and the previous accepted grey controller agree on representative decisions within the established control tolerance before do-mpc is removed.
- The asynchronous runner's stale generation, deadline, hold-last-command, and repeated-failure paths remain covered.

### Learning and migration

- Passive wrong-model runs produce a grey candidate and improve after a fully gated automatic activation.
- Correctly initialized runs do not materially regress or accept a worse candidate.
- Probe calibration still enforces safety ceilings, stage order, realized-load eligibility, pause/resume/stop behavior, and manual activation.
- Learn This Grill still gates end-of-cook fitting and accepted `cook-refit` parameters become active on the next cook.
- Automatic and manual candidates appear in the same report with the correct origin and activation policy.
- The panel exposes every learning phase, candidate, blocker, score, activation, and rollback state.
- The dashboard pill remains present for MPC, opens the panel, and matches the report through automatic/manual transitions and errors.
- Settings migration covers net, NLP, MHE, both horizon bounds, and removed structural settings.
- Snapshot migration preserves compatible grey parameters, removes linear payloads, and rejects incompatible delay structures.

### End to end

- Existing GrillSim and MAK scenario matrices meet established tracking and safety thresholds using acados.
- Fitting work causes no controller deadline miss and no measurable control-worker stall.
- Updater and fresh-install tests prove native build ordering and visible failure reporting.
- The focused MPC/runtime suite passes.
- Repository Python verification passes.
- Frontend generated settings, type, lint, test, and production build checks pass.
- A browser check exercises the learning pill and full panel through passive collection, fitting/evaluation, automatic activation, manual calibration review, error, and rollback states.
- A framed Hold smoke exercises acados solve, actuator feedback, online observation, checkpoint persistence, and clean teardown refit.
