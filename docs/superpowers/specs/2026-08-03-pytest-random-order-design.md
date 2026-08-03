# Randomized pytest Ordering Design

**Date:** 2026-08-03

## Goal

Run the Python test suite in a randomized order by default so tests that depend on state left by earlier tests fail visibly. Preserve a reported seed so any failing order can be reproduced.

## Dependency and configuration

Add `pytest-random-order>=1.2.0` to the existing `dev` dependency group in `pyproject.toml` and regenerate `uv.lock` with uv.

Add a `[tool.pytest.ini_options]` section with `addopts = ["--random-order"]`. The plugin does not randomize unless this option is enabled, so project configuration is required in addition to installing it.

Use the plugin's default `module` bucket. Pytest will shuffle module order and test order within each module without interleaving tests from different modules. This catches order coupling while avoiding the fixture churn and runtime cost of global shuffling.

## Runtime behavior

Every normal pytest invocation that loads project configuration will enable random ordering. Pytest will print the selected bucket and generated seed. A failure can be reproduced by rerunning with `--random-order-seed=<seed>`.

Do not add disabled markers or other exceptions. An order-dependent failure is a defect to repair rather than suppress.

## Files

- `pyproject.toml`: add the dev dependency and pytest option.
- `uv.lock`: record the resolved package and dependency-group membership.
- `tests/web/conftest.py`: confine sync Playwright's event loop to each web test module.
- `tests/web/test_socketio_app_data.py`: isolate handler tests from the process-wide events log.
- `tests/web/test_webapp_sqlite.py`: restore the seeded datastore baseline before every test.
- `tests/unit/datastore/test_read_path_validation.py`: make the captured logger level explicit.

No application source files change. Test-harness changes are limited to order dependencies exposed by the randomized suite.

## Verification

1. Regenerate and sync the uv environment from the updated lockfile.
2. Run pytest and confirm its session output reports the `module` bucket and a random-order seed.
3. Run the full Python test suite with the project configuration enabled; any newly exposed order dependency must be fixed before completion.
