# Cross-Platform Thermocouple Health UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface one authoritative thermocouple-health projection in React web, Qt/QML, and Expo mobile with truthful policy/outcome copy and native safety hierarchy.

**Architecture:** Stabilize one typed all-probe wire projection and one `@pifire/core` semantic mapper first. Web, Qt, and mobile then implement native layered presentation independently: persistent confirmed summary plus contextual probe state, with transport freshness kept orthogonal.

**Tech Stack:** Python 3.14/Pydantic, Socket.IO, TypeScript 7, React 19, Qt 6/QML, Expo/React Native, pytest, Rstest, Playwright/browser, Jest, Jujutsu.

**Spec:** `docs/superpowers/specs/2026-08-24-thermocouple-health-ui-design.md`

## Global Constraints

- Depends on completed backend inference semantics: confirmed inferred primary under observe is valid/notify-only; hardware/enforce stop; secondary confirmed unavailable.
- Use one typed projection containing stable identity/role, exact report, source, policy, actual outcome, and backend-relative freshness. Clients do not infer outcome from Error or raw evidence.
- Preserve Primary/Food/Aux in the projection. Aux remains summary/details-only; do not create new Aux dashboard cards.
- Health validity overrides last-good stale fallback. Transport stale/unreachable remains a separate axis and qualifies retained health as `Last reported`.
- Suspected is inline amber only, numeric, no banner/notification. Confirmed is non-dismissible while active. Recovery clears current treatment without toast/notification; primary never auto-resumes.
- Web owns `off | observe | enforce` configuration and conditional compatibility warning. Qt/mobile are read-only.
- Hardware/software source appears in details, not the headline. Do not expose raw ring samples.
- Generated contracts are regenerated through existing tooling, never hand-edited.
- No text grep; use LSP, AST search, targeted reads, context-mode. Jujutsu only.
- Strict TDD per task; each changed/new module requires focused behavioral tests. UI completion requires actual rendered/browser/device proof.

---

### Task 1: Producing-end health projection and generated contracts

**Files:**
- Modify: `common/web_contracts/core.py`, `common/web_contracts/registry.py`
- Modify: `blueprints/mobile/socket_io.py`
- Regenerate: `web-react/schema/contracts/core.schema.json`, `packages/pifire-core/src/contracts/core.gen.ts`
- Modify: `packages/pifire-core/src/fixture.ts`
- Test: `tests/web/test_socketio_app_data.py`, `tests/web/test_socket_dash_payload_fields.py`, generated-contract drift tests

**Produces:** typed `ThermocoupleHealthView` list on `DashSocketPayload` for all Primary/Food/Aux reports.

- [ ] Write failing producing-end tests for missing/healthy/suspected/confirmed, hardware/software/mixed, observe/enforce outcome, open+short, Aux, malformed detail, and finite relative age.
- [ ] Implement a pure backend projection from fused report + probe map + actual controller outcome. Do not reuse error strings.
- [ ] Add the list to the socket payload; preserve omission/empty-list compatibility for old clients.
- [ ] Regenerate schema/TypeScript with `bun run gen:types`; run drift check and focused Python tests.
- [ ] Commit: `jj desc -m "Expose thermocouple health to clients" && jj new`.

---

### Task 2: Shared core health semantics and validity precedence

**Files:**
- Create: `packages/pifire-core/src/dashboard/probeHealth.ts`
- Modify: `packages/pifire-core/src/dashboard/deriveView.ts`, exports and fixture users
- Test: `packages/pifire-core/tests/probeHealth.test.ts`, `deriveView.test.ts`, contract/type tests

**Produces:** pure `ProbeHealthView` with severity, availability, headline, impact/cause/source copy, priority, policy/outcome, and freshness qualifier.

- [ ] Write the full state/outcome copy matrix as failing tests, including multiple faults and `Last reported`.
- [ ] Add tests proving confirmed-invalid never falls back to last-good, suspected/observe-primary retain current numeric, and transport freshness is orthogonal.
- [ ] Implement immutable pure projection and `deriveView` integration; no React Native/DOM/QML imports.
- [ ] Run focused core tests and TypeScript diagnostics.
- [ ] Commit: `jj desc -m "Project thermocouple health semantics" && jj new`.

---

### Task 3: React web banner, probe context, settings, and policy

**Files:**
- Modify: `web-react/src/components/shell/AppShell.tsx`, `Banners.tsx`, `shell.css`
- Modify: dashboard `Dashboard.tsx`, `GrillGauge.tsx`, `ProbeCard.tsx`, `dashboard.css`
- Modify: settings `SettingsShell.tsx`, `tabs/ProbesTab.tsx`
- Modify: wizard probes `DeviceForm.tsx`, `probes.css`
- Test: corresponding AppShell/Banners/Dashboard/ProbeCard/ProbesTab/DeviceForm unit tests and chrome fidelity fixture

- [ ] Write failing tests for non-dismissible confirmed summaries, `+N more`, suspected inline-only, observe/stopped copy, invalid em dash, recovery removal, Aux details, and stale transport qualification.
- [ ] Extend Banners structurally; do not translate health to durable string errors/warnings or overload `criticalError`.
- [ ] Add dashboard badges/status and Settings > Probes full health details using the existing single live context.
- [ ] Add web-owned policy selector and type/hardware-value-driven `pf-module-notes` warning. Remove static duplicate MCP9601 wording only in the same atomic change.
- [ ] Run focused Rstest, typecheck, Biome, and browser-drive desktop/mobile widths with accessibility assertions.
- [ ] Commit: `jj desc -m "Surface thermocouple health in web UI" && jj new`.

---

### Task 4: Qt health transport and model roles

**Files:**
- Modify: `display/qtapp.py`, `display/qtbackend.py`
- Test: `tests/ui/test_qtbackend.py`, `test_probe_staleness.py`, `test_qtquick_parity.py`

- [ ] Write failing adapter tests for all states/outcomes, Aux, malformed/missing data, current/last-reported freshness, recovery, and invalid-over-stale precedence.
- [ ] Add separately throttled health projection reads; do not poll/decode full generic device info at 20 Hz.
- [ ] Expose fixed semantic QObject/list-model roles matching shared copy/outcome; never decode detector thresholds in Qt.
- [ ] Run focused offscreen backend/model tests.
- [ ] Commit: `jj desc -m "Add thermocouple health to Qt backend" && jj new`.

---

### Task 5: Qt banner, contextual status, details, and accessibility

**Files:**
- Create: `display/qml/components/ProbeHealthBanner.qml`, `display/qml/screens/ProbeHealthScreen.qml`
- Modify: `DashScreen.qml`, `Gauge.qml`, `ProbeCard.qml`, `Main.qml`, `Theme.qml`
- Modify: Qt preview scenario files
- Test: QML load, compact, rotation, parity/accessibility tests

- [ ] Write failing rendered contracts for suspected, observe-primary, stopped-primary, secondary unavailable, multiple faults, Aux detail, recovery, and stale transport.
- [ ] Reuse Gauge/ProbeCard reserved status line and add one steady, non-pulsing highest-priority banner.
- [ ] Add MenuScreen-style scrollable details; banner remains while active, details can close.
- [ ] Add Accessible metadata and focusable touch/encoder actions; verify 1024×600, 1280×720, 1024×768 and 0/90/180/270 rotations.
- [ ] Capture actual offscreen screenshots with no QML warnings.
- [ ] Commit: `jj desc -m "Surface thermocouple health in Qt" && jj new`.

---

### Task 6: Mobile health transport consumption and layered dashboard

**Files:**
- Modify: `mobile/app/_layout.tsx`, `mobile/app/(tabs)/index.tsx`
- Create or modify native health banner component
- Modify: `mobile/src/components/ProbeCard.tsx`, `GrillGauge.tsx`
- Test: ProbeCard, GrillGauge, dashboard row, live/offline layout tests

- [ ] Write failing tests for transport-only StatusStrip, global primary banner, suspected numeric retention, confirmed invalid em dash, Aux summary, multiple faults, recovery, and `Last reported` offline copy.
- [ ] Consume shared `@pifire/core` semantics; no separate REST poll or fifth tab.
- [ ] Keep primary confirmed banner above all tabs and secondary state in cards/summary; preserve connection freshness hierarchy.
- [ ] Add text/icon/accessibility semantics and dynamic-layout tests.
- [ ] Commit: `jj desc -m "Surface thermocouple health in mobile UI" && jj new`.

---

### Task 7: Mobile confirmed notifications and alert preference

**Files:**
- Modify: `mobile/src/alerts.ts`, `mobile/app/_layout.tsx`, preference integration
- Test: `mobile/tests/alerts.test.ts`, preference/layout notification tests

- [ ] Write failing matrix: no first-frame, suspected, recovery, repeat, reconnect replay; one confirmed transition; reconfirm after genuine recovery; correct primary/secondary outcome copy; alerts-disabled schedules nothing.
- [ ] Make `prefs.alerts=false` suppress permission request and scheduling, fixing the existing ignored-toggle defect.
- [ ] Extend `alertsFor()` only for confirmed transitions; do not create a second notification state machine.
- [ ] Run focused Jest/TypeScript checks.
- [ ] Commit: `jj desc -m "Notify mobile users of thermocouple faults" && jj new`.

---

## Parallel execution contract

Tasks 1–2 are sequential shared foundations. After Task 2 is review-approved:

- Web Task 3 owns only web files.
- Qt Tasks 4→5 are sequential within Qt.
- Mobile Tasks 6→7 are sequential within mobile.
- Web, Qt, and mobile lanes may run concurrently without shared-file edits.

Each lane receives the frozen wire/type/core interfaces verbatim; no lane changes shared semantics independently.

## Final verification

- Backend producing-end contract/schema generation and full relevant Python tests.
- Full `packages/pifire-core`, React, and mobile test/typecheck/build gates.
- Full Qt offscreen backend/QML/compact/rotation suite with warning-free loads.
- Browser-drive React actual surface at desktop/mobile widths.
- Render Qt screenshots for all state/outcome scenarios.
- Run actual iOS/Android simulator/device smoke for dashboard, reconnect, foreground/background notification, permission denied, and alerts disabled; unit tests alone are insufficient.
- Broad final review verifies copy consistency, validity precedence, Aux behavior, one-socket/no-extra-polling architecture, accessibility, and no duplicated threshold logic.
- Verify empty Jujutsu `@`; keep worktree until integration choice.
