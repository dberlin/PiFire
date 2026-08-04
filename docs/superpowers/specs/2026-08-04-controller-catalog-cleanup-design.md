# Controller Catalog Cleanup Design

**Date:** 2026-08-04  
**Status:** Approved for planning

## 1. Goal

PiFire supports exactly three temperature controllers:

- `pid` — standard PID;
- `pid_sp` — PID with Smith predictor;
- `mpc` — model-predictive control.

Remove the six alternative or experimental controllers rather than retaining unmaintained choices, compatibility aliases, or optional dependency paths.

## 2. Scope

Retire these controller IDs:

- `pid_clamping`;
- `pid_clamping_percent_pb`;
- `pid_ac`;
- `pid_parallel`;
- `fuzzy`;
- `ml`.

The cutover includes controller implementations, manifest entries, generated frontend types, settings fixtures, tests that exist only for retired controllers, fuzzy/ML generators and persisted artifacts, dedicated documentation/assets, package dependencies, and updater declarations.

Historical design and plan documents remain historical records and are not rewritten merely because they mention a retired controller.

This design does not change PID, PID-SP, or MPC control behavior. The MPC actuator scheduler is a separate design and implementation plan.

## 3. Catalog authority

`controller/controllers.json` remains the sole catalog authority and contains exactly `pid`, `pid_sp`, and `mpc` under `metadata`.

The React controller configuration types continue to be generated from that file. No frontend compatibility union, hidden retired option, or hand-written alias is added.

The runtime continues importing the selected controller by its manifest ID. There are no retired shim modules.

## 4. Settings migration

Add one schema-versioned shape migration for stored settings.

For each of the six explicitly retired IDs:

1. If `controller.selected` is the retired ID, set it to `pid`.
2. Preserve the existing `controller.config.pid` mapping exactly when present.
3. If `controller.config.pid` is absent or malformed, leave normal settings repair/defaulting to restore the standard PID defaults.
4. Delete every retired controller key from `controller.config`, regardless of which controller is selected.

The migration is idempotent. It does not change `pid`, `pid_sp`, or `mpc` selections. It does not treat an unknown controller ID as retired: unknown or corrupt selections remain subject to the runtime's existing build failure and PID fallback behavior.

The settings schema version advances by one and `_SHAPE_MIGRATIONS` records the new step. Release versions do not gate this migration.

## 5. Removed artifacts

Delete:

- the six retired Python controller modules;
- fuzzy and ML model generation utilities;
- `fuzzy.pickle`, `ml_model.joblib`, and `ml_dataset.csv`;
- fuzzy-specific documentation and image assets;
- tests whose only contract is a retired controller;
- retired entries in characterization and construction matrices.

The retained PID/PID-SP characterization coverage remains. Files named for all PID variants are renamed when their contents become retained-controller-only.

## 6. Dependencies and installation

Remove `scikit-fuzzy` and `scikit-learn` from `pyproject.toml`. Regenerate `uv.lock`; do not hand-edit the lockfile.

Remove their obsolete declarations from `updater/updater_manifest.json` and update installer comments that claim those packages are installed. Retain direct `numpy` and `scipy` dependencies because active control and identification code imports them directly.

No retained runtime module may import `skfuzzy` or `sklearn` after the cutover.

## 7. UI and API behavior

The settings API returns only the three retained manifest entries. The React controller selector displays exactly PID, PID-SP, and MPC.

Generated `ControllerConfigs` contains exactly `pid`, `pid_sp`, and `mpc`. Tests for empty-config controllers are removed because no retained controller has that shape; generator behavior itself may remain generic if it costs no controller-specific code.

An upgraded installation that had selected a retired controller opens settings with standard PID selected after migration. No warning or manual-selection blocker is required because PID is the existing safe runtime fallback and default.

## 8. Verification

Required contracts:

1. A parameterized migration test covers all six retired selected values.
2. Migration preserves an existing standard-PID config byte-for-byte.
3. Migration deletes all retired config blocks even when a retained controller is selected.
4. Migration is idempotent and leaves each retained selection unchanged.
5. Backend catalog tests assert the exact three controller IDs.
6. Controller construction smoke tests build the retained controllers only.
7. PID and PID-SP golden update traces remain unchanged.
8. React settings tests assert the exact three visible choices and successfully edit/save retained controller configs.
9. Generated types and fixtures match the reduced manifest.
10. The lockfile contains neither retired direct dependency, except if a retained dependency independently requires it; such a transitive requirement must be investigated rather than suppressed.
11. Focused Python and React suites pass, followed by the normal full suites and static checks.

## 9. Rollout

The migration runs before ordinary settings use, so a removed selection never reaches normal Hold construction after upgrade. The release note names all retired controllers and states that affected installations move to standard PID while retaining their existing standard-PID configuration.
