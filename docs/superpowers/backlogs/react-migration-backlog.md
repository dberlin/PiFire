# React UI — Backlog

Actionable backlog for `web-react/` and the API behavior needed by that UI.
Completed migrations, superseded findings, and settled non-goals are removed
rather than retained here; plans and repository history carry that record.

**Last reconciled against live code: 2026-08-13.**

## Data integrity and backend boundaries

### Make probe-map replacement transactional across editors

The React probe editor replaces the whole map through `POST /api/probe_map`.
The mobile Socket.IO path still calls `common/app.py::update_probe_config()` to
edit individual `probe_info` entries. Concurrent edits in Stop mode are
last-write-wins with no revision check.

A related failure boundary remains in probe construction:
`ProbesMain._setup_probe_devices()` degrades on import failure, but construction
failure from `ReadProbes(...)` can escape after partially building the new
list. Define one transaction boundary that preserves the previous live map and
devices unless the replacement is complete.

### Add optimistic concurrency to pellet writes

Pellet actions still read, mutate, and write the entire pellet blob. Concurrent
browser/mobile actions can silently overwrite one another. Add a durable
revision or equivalent compare-and-swap contract shared by both transports.

### Decide the status of the test-only log reader

`common/datastore.py::read_log()` reads a production table but has no production
caller; tests use it as an oracle for retention and event clearing. Either make
that test-only role explicit at an appropriate boundary or replace it with a
product read path. Do not leave an ambiguous production API used only by tests.

### Remove `board-config.py`'s private logger implementation

`board-config.py` still carries its own `create_logger` and writes directly to
`./logs/`, bypassing the shared logger and `PIFIRE_LOG_DIR`. Route it through
the canonical logging implementation.

### Stop tuner math from swallowing every exception

`blueprints/tuner/tuner.py::calc_shh_coefficients()` and `temp_to_tr()` retain
bare catch-all handlers. The API endpoint is now the only production caller,
so replace sentinel ambiguity with typed, endpoint-visible failure while
preserving the documented unreliable-inverse behavior where appropriate.

## Missing user-facing behavior

### Add Send Test Notification

Notifications settings can configure Apprise and OneSignal but expose no action
to send a test notification. Add one shared backend operation and a React action
with explicit pending, success, and failure states.

### Add deliberate not-found behavior and decide `/manual`

`web-react/src/components/appRoutes.tsx` has neither `/manual` nor a wildcard
route. Every unknown URL falls into React Router's default error screen. Add an
intentional not-found surface; separately decide whether `/manual` redirects,
links to maintained documentation, or remains absent.

### Surface collected fan and pellet metrics

The metrics payload collects fan-on time and pellet start/end/brand data, but
the React metric-field manifest leaves them in raw disclosure only. Promote the
values that communicate measured fan duty and pellet consumption without
recomputing server-derived values in the client.

### Restore post-update release notes or retire the protocol

Upgrades still set `settings.globals.updated_message`, and
`updater/post-update-message.html` still ships, but React reads neither. Build
safe app-shell release notes that consume the flag, or remove the writer, schema
field, payload, and migration tests together. Do not keep an orphaned protocol.

### Confirm shutdown and align destructive-action order

React confirms Shutdown but presents Shutdown then Stop. Physical displays
present Stop then Shutdown, and Qt dispatches Shutdown without confirmation.
Treat this as one cross-UI safety change; see `display-backlog.md`.

## History and settings parity

### Finish history-page behavior

Current gaps:

- preserve the History Stream toggle semantics while reducing the live polling
  interval from roughly five seconds if the backend budget permits;
- represent disabled probes deliberately rather than silently dropping them;
- replace the bare history-duration number field with the intended bounded
  control;
- pass mode-change annotations to the live `/history` chart, not only saved
  cook-file charts.

### Delete `clampSetpoint`

`web-react/src/helpers/dashboard/health.ts::clampSetpoint()` has no production
caller; only its dedicated test imports it. Delete the helper and obsolete tests.

### Derive integer coercion from the settings contract

`NumberField` still takes a manually supplied `integer` boolean. Current call
sites match the schema, but nothing prevents a future numeric field from being
silently rounded. Replace the independent flag with a path-aware field contract
or another structural derivation from generated schema metadata.

### Verify display sleep timeout actually drives DPMS

The General tab edits `display.sleep_timeout`; the UI contract does not prove
that the physical display consumes it and blanks on schedule. Trace and test the
runtime behavior at the display boundary.

## Wizard completion and verification

### Finish wizard feedback and navigation

The wizard still lacks a confirmation summary at Finish, an active-system
warning on entry, complete per-step explanatory copy, actionable step
navigation, and clear table headers/module identifiers. Preserve the now-working
install output, structured Finish errors, and discovery Refresh/Close controls.

### Close wizard coverage gaps

Add behaviorally useful coverage for:

- browser-driven Exit Setup / `POST /api/wizard/cancel`;
- the 800×480 panel viewport;
- accent swaps and Barlow-unavailable rendering;
- the reboot-modal flow and one-wire change on real Pi hardware.

### Add an unconsumed-settings contract

No contract detects a schema field that is writable but consumed by no runtime
or UI surface. Design a maintainable manifest or generated ownership check; do
not use a proximity-based source scan.

## Tuner verification

### Cover interactive tuner states

The fidelity baseline covers only the pre-Start manual screen. Add deterministic
coverage for the Auto reference/accumulation state, curve, and save form without
requiring a live grill where a component fixture can prove the contract.

### Exercise the auto-tuner ready path end to end

The live e2e cannot naturally create the required temperature spread, so it
never reaches `ready`. Provide a controlled end-to-end seam that drives the
converge/select/solve path without weakening the real readiness rule.
