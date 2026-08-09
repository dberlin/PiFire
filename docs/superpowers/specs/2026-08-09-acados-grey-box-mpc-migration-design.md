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
3. Create staging directories on the same filesystem as the runtime release root.
4. Run the PiFire grey-box generator with the code-generation dependency group and the fetched acados Python templates.
5. Validate generated provenance, generated-tree completeness, and numerical equation parity.
6. Build acados, BLASFEO, HPIPM, the generated solver, and the PiFire native wrapper into a new immutable release directory.
7. Validate the exact native ABI path, Python library discovery, every horizon from 5 through 24, invalid-horizon rejection, cost scaling, cold solves, and representative perturbed warm solves.
8. Write the build manifest beside the library in that same release directory.
9. Replace the checked generated solver tree as a developer source update. This tree is not loaded at runtime.
10. Atomically switch one `controller/_native/current` pointer to the validated release directory.

Every runtime-consumed artifact for one build, including the library and manifest, lives under `controller/_native/releases/<release-id>/`. The loader resolves only `current`. A pointer rename is the single publication commit point, and the previous release is retained for rollback. An interruption before that rename leaves the running release coherent. The next invocation detects and repairs a checked-generated-tree/runtime mismatch.

A failed command returns nonzero. It may leave a validated generated-source change for developer review, but it never partially publishes a runtime release.

### Conditional deployed rebuild

`--if-needed` does not install CasADi and does not regenerate C on a deployed grill. It compares the installed manifest against:

- the pinned acados commit and dependency revisions;
- generated-source manifest and digest;
- native wrapper and public header digests;
- ABI version;
- platform and architecture;
- relevant CMake configuration;
- compiler/runtime compatibility fields required by the native loader.

If those inputs match, it exits successfully without building. If they differ, it compiles the committed reviewed generated C into a new immutable release directory, runs the deployed smoke checks, writes the manifest beside the library, and atomically switches `current`. The previous release remains available.

The updater ensures CMake/compiler prerequisites before moving the live checkout. It records the exact pre-update revision and branch, updates the source, and invokes `--if-needed` immediately—before Python dependency synchronization, version-cursor advancement, or settings migration. Conditional mode therefore uses only the system toolchain and Python standard library. If the native build or smoke check fails, the runtime release pointer is still unchanged; the updater restores the exact prior source revision/branch, records a terminal failure, does not publish `Finished`, and does not restart PiFire. A branch change follows the same transaction.

After native publication succeeds, the updater continues with Python dependencies and migrations. Fresh installers call `--if-needed` after installing compiler and CMake prerequisites. Service startup also runs the conditional check before starting PiFire, so an interrupted update must finish a compatible native build or fail closed rather than load mismatched Python/native ABI generations. Build output is delimited in the updater log so native failures are independently diagnosable.

The first release that introduces this requirement has an explicit bootstrap path because an updater process imports `updater.py` before it moves the checkout and therefore cannot execute newly pulled Python control flow. The new updater manifest installs native build prerequisites, then invokes a standard-library-only bootstrap command from the updated checkout. That command validates the previous revision/branch from the checkout's update metadata before building. On success it leaves the complete native release for the old updater to finish normally. On failure it restores the previous source revision/branch and runtime pointer, writes terminal failure status, and terminates the old updater process so the old `run_update()` cannot overwrite failure with `Finished`. Upgrade tests start from the pre-migration updater to prove this handoff for both update and branch-change flows.

## Native ABI and Python Boundary

The public ABI is based on the reviewed wrapper in `../acados` and is bumped for runtime horizon support and removal of linear exports.

The grey configuration includes `uint32_t horizon_steps`. Valid values are 5 through 24. The prediction step and delay-state count are ABI constants: 25 seconds and eight states.

The native handle:

- creates the generated capsule with `pifire_grey_acados_create_with_discretization()`;
- supplies a 25-second vector for the selected stage count;
- immediately resets external-cost `scaling` to `1.0` at every stage from zero through the terminal stage, preserving the generated solver's reviewed objective while leaving `Ts=25`;
- allocates warm-start state, control, multiplier, and diagnostic storage for that stage count;
- updates physical parameters at every stage;
- retains the last successful iterate;
- restores that iterate after a failed solve;
- emits bounded structured diagnostics without returning NaN or infinity across the ABI;
- rejects unsupported dimensions before allocating solver state.

ABI v2 defines `ACADOS_PIFIRE_GREY_HORIZON_CAPACITY` as 24. The public solve-output structure contains `uint32_t sequence_length` plus fixed-capacity `sequence_q[24]` and `sequence_residual[24]` arrays. A successful solve sets `sequence_length` to the handle's configured horizon, writes exactly those entries, and zeroes the unused tail. Failure initialization also zeroes the full capacity.

The Python FFI defines the same capacity, requires `1 <= sequence_length <= 24` and equality with the handle configuration, and exposes immutable arrays sliced to `sequence_length`. It never interprets the unused tail. Runtime selection therefore exposes neither allocator ownership nor variable-length C storage across the ABI.

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

The non-success statuses above came from an intentionally perturbed synthetic sequence and demonstrate why recovery remains part of the contract; they are not an unlimited production allowance. Target-hardware acceptance runs 1,000 perturbed solves at every integer horizon from 5 through 24. Each horizon must have at most five transient non-success statuses (0.5%), no consecutive non-success statuses, a finite successful recovery on the next call, p99 solve time below 20% of the configured control period, and maximum solve time below one full control period. Any horizon that misses those bounds is unsupported until solver options are retuned.

The generated discrete RK4 map hardcodes 25 seconds. Acados' runtime time-step array changes cost scaling and solver metadata but does not change that map, so `t_step` must not remain a user setting under a false claim of physical discretization. Supporting a different step is outside this migration and would require a parameterized transition or continuous-dynamics regeneration plus new parity and stability evidence.

## Estimator Decision

MHE is removed and saved MHE selections migrate to EKF.

In paired calibrated closed-loop runs, EKF had lower RMSE, lower integrated absolute error, a larger fraction of time within 5°F, and lower median update time than MHE across GrillSim steady, step, and lid scenarios. On the long MAK 450°F scenario, tracking was effectively tied while EKF remained faster. MHE provides no observed control-quality benefit that warrants preserving do-mpc/CasADi.

The optional KF remains because it is a lightweight estimator, not a linear MPC policy, and does not retain the removed dependency path.

## Grey-Box Learning Architecture

### Observation sources

One candidate pipeline accepts three labeled evidence origins:

- `passive-online`: completed normal Hold frames while Online Model Adaptation is enabled;
- `operator-calibration`: any fit window containing an applied operator-directed probe frame;
- `cook-refit`: a complete bounded teardown record containing no operator-directed probe frame and submitted while Learn This Grill is enabled.

Origin authority follows the most restrictive evidence in the complete fit window: `operator-calibration` overrides `cook-refit`, which overrides `passive-online`. A mixed normal/probe cook therefore remains `operator-calibration` and cannot gain automatic authority by being refit at teardown.

Every observation carries requested load, realized load, chamber temperature, ambient provenance, continuity, inhibition/discard disposition, probe provenance, cook/session identity, frame revision, and model generation. Discarded, unknown-actuation, interrupted, or stale-generation frames cannot enter a fit.

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

An `operator-calibration` candidate stops at `ready-for-review`, including when it was fit from a mixed normal/probe teardown record. The existing activation endpoint must receive the exact candidate digest and confidence decision ID. Its activation reason records `operator-reviewed`.

A `cook-refit` candidate retains the existing Learn This Grill authorization: when `enable_identification` is true, an accepted probe-free teardown fit is adopted for the next cook and records `cook-refit`. If the setting is false, teardown does not fit or activate.

Runtime activation uses a two-phase durable record. After building and dry-solving the candidate pair, the activation manager persists `prepared` with exact incumbent, candidate, origin, generation, digest, and decision identities. At a completed-frame boundary it installs the in-memory pair but does not permit a candidate solve or output yet, then compare-and-swaps the durable record from `prepared` to `active`. Only the committed `active` record authorizes the next controller update. Any swap or persistence failure restores the incumbent pair and compare-and-swaps `prepared` to `aborted`; failure to compensate terminates MPC rather than issuing an ambiguously authorized command.

Startup treats `prepared` as uncommitted: it restores the incumbent, records an interrupted activation, and marks the record `aborted`. Startup restores the candidate only from `active`. A process death before the durable `active` transition therefore returns to the incumbent; a death after it restores the candidate. The prior active pair remains the rollback owner through the confidence observation window.

### End-of-cook learning

The full end-of-cook refit remains. It runs only after the controller worker stops, uses the complete bounded cook history, applies the same physical and identifiability rules, records its outcome, and publishes a final checkpoint. A probe-free record is `cook-refit` and may become active for the next cook under Learn This Grill. Any record containing an applied calibration probe is `operator-calibration` and remains staged for reviewed activation. No teardown path revives a linear/neural candidate or performs a mid-frame activation.

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

One report covers passive, operator-calibration, and end-of-cook learning. It exposes:

- current mode and candidate origin, including mixed-window authority;
- `enable_online_adaptation` and Learn This Grill authorization;
- observation eligibility, probe provenance, and rejection reasons;
- probe calibration state, current band/probe, and stage progress;
- fit queue/running/result state and evidence-window identity for passive, calibration, and teardown fits;
- stale-result, fit-failure, and latest `cook-refit` outcome;
- candidate generation, digest, parameter values/deltas, fit quality, and identifiability;
- native candidate build and dry-solve status;
- incumbent/challenger scores and missing gates;
- automatic versus manual activation requirement;
- durable activation phase (`prepared`, `active`, or `aborted`) and pending frame-boundary swap state;
- active, candidate, default, and rollback identities;
- latest activation, rejection, fallback, interrupted-activation, and rollback reasons;
- ambient provenance and target-hardware timing evidence;
- checkpoint publication, schema invalidation, and other terminal errors.

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

### Durable authority migration

Migration runs transactionally before controller construction across both durable authorities:

- `controller_model_state`, including the versioned MPC top-level `params` and fit metadata;
- `model_activation_state`, including active, candidate, prepared, and rollback snapshots;
- confidence/activation pointers into model-evidence rows.

A compatible grey authority must have the current grey schema, finite in-bounds physical parameters, and `n_delay=8`. Migration chooses authority in this order: a compatible active grey snapshot, a compatible grey rollback snapshot, a compatible top-level controller grey snapshot, then shipped defaults. If the active authority is Scheduled ARX, innovation state-space, another linear kind, or malformed, migration invalidates it and installs the highest-priority compatible grey authority with an operator-visible `schema-invalidated` reason.

Historical evidence rows remain immutable audit history, but rows whose model kind or schema was removed are excluded from current confidence and activation decisions. Migration clears their live pointers and appends one invalidation lifecycle record; it never relabels linear evidence as grey evidence. The datastore transaction updates controller state, activation state, and live evidence pointers together or changes none of them.

The activation-state schema includes the durable phase `prepared`, `active`, or `aborted`. Its compare-and-swap key is the incumbent/candidate generation, digest, and decision identity. This same schema enforces the activation crash recovery described above.

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
- A runtime release is visible only after one atomic `current` pointer switch; failed publication retains the previous release.
- A native updater failure restores the prior source revision before dependency or settings migration and never restarts PiFire.
- A solve failure retains the last safe command and increments existing failure diagnostics.
- A fit failure never affects the active controller.
- A stale fit result is recorded and discarded.
- A candidate build or dry-solve failure rejects only that candidate.
- A prepared activation never authorizes output; persistence/swap failure restores incumbent authority or terminates MPC.
- A post-activation confidence or runtime failure invokes the existing rollback path.
- Learning report failures change the pill/panel to an explicit error state; they do not disappear as `collecting`.

## Verification and Acceptance

### Build and supply chain

- An empty-cache `./rebuild-acados.sh` fetches the pinned acados checkout, initializes required upstream submodules only in the build directory, regenerates, builds, validates, and publishes.
- The repository contains no acados Gitlink and no vendored upstream source.
- Regeneration check mode is reproducible against the committed generated tree.
- The platform-selection matrix imported from `../acados` passes unchanged.
- `--if-needed` skips a matching release and rebuilds each individually stale input class.
- Concurrent rebuild attempts serialize.
- Fault injection before and after every publication step proves that `current` always resolves one complete library/manifest release.
- A native updater failure restores the exact prior source revision and branch before Python dependency sync or settings migration.
- An interrupted update followed by service startup reruns `--if-needed`; incompatible Python/native ABI combinations fail before PiFire starts.

### Native and runtime contracts

- ABI structure-size, invalid-argument, allocation/backend-failure, invalid-solution, reset, and lifetime tests pass.
- Every integer horizon from 5 through 24 passes C and Python cold/warm construction, sequence-length/tail-zeroing, cost-scaling, perturbed recovery, and target timing contracts; values outside the range are rejected.
- Generated equations match the canonical PiFire grey equations within the established parity tolerance.
- The exact runtime-horizon ABI path, including post-creation cost scaling of 1.0, matches the reviewed fixed-horizon control objective and decisions within the established tolerance.
- The asynchronous runner's stale generation, deadline, hold-last-command, and repeated-failure paths remain covered.

### Learning and migration

- Passive wrong-model runs produce a grey candidate and improve after a fully gated automatic activation.
- Correctly initialized runs do not materially regress or accept a worse candidate.
- Probe calibration still enforces safety ceilings, stage order, realized-load eligibility, pause/resume/stop behavior, and manual activation.
- A mixed normal/probe teardown fit remains operator-calibration authority and cannot auto-activate through Learn This Grill.
- A probe-free Learn This Grill record still produces a `cook-refit` candidate for the next cook.
- Injected swap failure and process death at each two-phase activation boundary restore the authority prescribed by the durable phase after restart.
- Transactional migration covers active grey, active Scheduled ARX, active state-space, grey rollback, malformed authority, and incompatible delay-state records across both durable stores and evidence pointers.
- Automatic, manual, and cook-refit candidates appear in the same report with the correct origin, authorization, durable phase, and outcome.
- The panel exposes every collection, fit, candidate, blocker, score, activation, teardown, error, and rollback state.
- The dashboard pill remains present for MPC, opens the panel, and matches the report through passive/manual/teardown transitions and errors.
- Settings migration covers net, NLP, MHE, both horizon bounds, and removed structural settings.

### End to end

- Existing GrillSim and MAK scenario matrices meet established tracking and safety thresholds using acados.
- Fitting work causes no controller deadline miss and no measurable control-worker stall.
- Updater and fresh-install tests prove native build ordering and visible failure reporting.
- The focused MPC/runtime suite passes.
- Repository Python verification passes.
- Frontend generated settings, type, lint, test, and production build checks pass.
- A browser check exercises the learning pill and full panel through passive collection, fitting/evaluation, automatic activation, manual calibration review, error, and rollback states.
- A framed Hold smoke exercises acados solve, actuator feedback, online observation, checkpoint persistence, and clean teardown refit.
