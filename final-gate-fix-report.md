# Final Gate Fix Report

## Inference full-suite regressions

- Bumped `SETTINGS_SCHEMA_VERSION` from 10 to 11, registered the explicit no-op thermocouple-health shape migration, and regenerated the authoritative settings shape digest as `c421e1233979c1ea03bbeee5ed22bd64536e4db90694f6462bbe927702b2e8a8`.
- Made `build_devices()` resolve a missing legacy `thermocouple_health.inference_policy` to `observe` once and pass it through both normal and disabled probe construction paths.
- Made the characterization `read_probes` wrapper accept and forward keyword arguments without changing its read-count side effects.

## Verification

- Original three failed tests: `3 passed in 2.67s`.
- Final focused regression contracts, including both device paths and the v11 migration: `5 passed in 2.88s`.
- Settings migrations and device construction suites: `189 passed in 3.14s`.
- Full characterization suite: `335 passed in 3.60s`.
- Schema digest utility and shape gate: `7 passed in 1.17s`.
