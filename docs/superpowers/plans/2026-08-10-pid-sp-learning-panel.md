# PID-SP Learning Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Use `superpowers:test-driven-development` for every behavior change and `superpowers:verification-before-completion` before delivery.

**Goal:** Replace the MPC-only dashboard learning pill/panel with one shared learning disclosure that preserves all MPC behavior and shows an informational PID-SP learning report whenever PID-SP is selected.

**Architecture:** Share only the behavior both controllers genuinely have: controller selection, pill, status label/tone, modal portal, focus management, loading/error/retry behavior, request invalidation, and responsive shell. Keep the rich reports controller-specific. MPC continues to use its exact evidence/calibration/activation/rollback contract. PID-SP gets a read-only, versioned report derived from its live identifier/Smith Predictor status plus its durable `ControllerModelStore` checkpoint. A small discriminated frontend adapter maps either report to a common display summary; controller-specific views render the details.

**Tech stack:** Python 3.14, Flask, frozen/Pydantic dataclasses, SQLite-backed controller model store, React 19, TypeScript, TanStack Query, Vitest/Testing Library, Playwright, Ruff, Bun/Rspack.

---

## Scope and invariants

### In scope

- Show a learning pill when the selected controller is `mpc` or `pid_sp`.
- Preserve the current MPC report, command APIs, status meanings, confirmation gates, and exact activation/rollback behavior.
- Add an informational PID-SP report and dialog. PID-SP remains passive and automatically adopts a model only through its existing learner.
- Share the modal/pill mechanics rather than cloning them.
- Keep the pill and dialog usable at both the 800×480 panel layout and the ≥1280 px reference layout.
- Refetch immediately when the socket invalidation token changes and retain the existing five-second fallback poll.

### Out of scope

- No PID-SP calibration, accept, reset, activation, or rollback commands.
- No change to FOPDT/IPDT identification mathematics, thresholds, model adoption, persistence, or Smith Predictor safety behavior.
- No attempt to force MPC evidence and PID-SP RLS diagnostics into one nullable backend schema.
- No changes to controller selection or settings UI.
- No API aliases or deprecated component exports after the frontend cutover.

### Required behavior

1. `pid` and unknown controllers render no learning pill and issue no learning-report request.
2. `mpc` renders the same pill semantics and every existing modal section/action.
3. `pid_sp` renders `PID-SP learning: <status>` and an informational dialog with no mutation controls.
4. PID-SP thresholds come from Python learner constants. TypeScript must not duplicate `MIN_ACCEPTED`, `MIN_ACCEPTED_SECONDS`, `MIN_DUTY_STD`, `MIN_TEMP_SPAN_F`, `CONFIRM_WINDOW`, or related gates.
5. A PID-SP report remains useful outside Hold: show the durable checkpoint even when live identifier/predictor state is absent.
6. Live status is accepted only when it identifies itself as PID-SP; stale MPC or unmarked status must not be projected as PID-SP data.
7. Every JSON float is finite. Malformed live/checkpoint data becomes a structured report failure or a 422 response; never emit `NaN`/`Infinity`.
8. Controller/API-base changes cancel or supersede older requests. A late MPC response must never paint a PID-SP dialog, and vice versa.
9. Closing by button, scrim, or Escape returns focus to the trigger. Tab and Shift+Tab stay trapped inside an open dialog.

---

## Data contracts

### Backend PID-SP live learning projection

`controller.pid_sp.Controller.get_status()` keeps its existing top-level controller diagnostics and adds this JSON-safe member:

```python
{
    "learning": {
        "schema_version": 1,
        "controller": "pid_sp",
        "status": "collecting" | "insufficient-excitation" | "evaluating" | "active" | "fallback",
        "identifier": { ...existing FOPDTIdentifier.status() fields... },
        "predictor": { ...existing SmithPredictor.status() fields... },
        "gates": [
            {
                "name": str,
                "passed": bool,
                "observed": int | float | bool,
                "required": int | float | bool,
                "unit": str | None,
            },
            ...
        ],
    }
}
```

The five excitation gates are accepted samples, accepted duration, duty standard deviation, observed duty transition, and temperature span. Confirmation and candidate counts are diagnostics, not duplicated excitation gates.

State precedence for a live PID-SP projection:

1. `predictor.disabled` → `fallback`.
2. `predictor.active` with a trusted model → `active`.
3. all five excitation gates pass → `evaluating` (candidate count/confirmation then explains whether a usable fit exists).
4. minimum accepted samples and duration have been reached but any remaining excitation gate fails → `insufficient-excitation`.
5. otherwise → `collecting`.

### Read-only PID-SP report

`GET /api/pid-sp-learning/report` returns schema version 1:

```typescript
type PidSpLearningStatus =
  | "idle"
  | "collecting"
  | "insufficient-excitation"
  | "evaluating"
  | "active"
  | "fallback"
  | "error";

interface PidSpLearningReport {
  schema_version: 1;
  controller: "pid_sp";
  status: PidSpLearningStatus;
  live: boolean;
  revision: string; // canonical SHA-256 of every report field except revision
  gates: PidSpLearningGate[];
  identifier: PidSpIdentifierReport | null;
  predictor: PidSpPredictorReport | null;
  checkpoint: PidSpModel | null;
  failure: { code: string; detail: string; terminal: boolean } | null;
}
```

`PidSpModel` is a discriminated union:

- `form: "fopdt"`: `K`, `tau`, `theta`, `revision`, optional `identified_at_f`/`setpoint_f`.
- `form: "ipdt"`: `K_i`, `c0`, `theta`, `revision`, optional `identified_at_f`/`setpoint_f`.
- Legacy snapshots without `form` normalize to `fopdt`, matching `FOPDTIdentifier.restore()`.

Report composition:

- Valid marked live state supplies `status`, `gates`, `identifier`, and `predictor`; `live=true`.
- No live state supplies `status="idle"`, empty gates, null identifier/predictor, and `live=false`.
- A valid `ControllerModelStore().load("pid_sp")` snapshot supplies `checkpoint` in either case.
- Malformed marked live state produces `status="error"` and a structured failure without hiding a separately valid checkpoint.
- An absent checkpoint is normal. A malformed checkpoint is not silently represented as valid model data.

### Shared frontend summary

Do not expose controller-specific detail through the shared shell. Adapt each report to:

```typescript
interface LearningDisplaySummary {
  controller: "mpc" | "pid_sp";
  status: string;
  label: string;
  tone: "neutral" | "ok" | "warn" | "danger";
  busy: boolean;
}
```

The adapter owns display wording and tone. Controller report types remain discriminated and exact.

---

## Task 1: Specify the PID-SP live learning projection

**Files:**
- Create: `controller/pid_sp_learning.py`
- Modify: `controller/pid_sp.py`
- Modify: `tests/unit/controller/test_pid_sp.py`
- Create: `tests/unit/controller/test_pid_sp_learning.py`

**Step 1: Write failing contract tests**

In `tests/unit/controller/test_pid_sp_learning.py`, construct identifier/predictor status mappings directly and pin:

- all five gate names, observed values, required values, units, and booleans;
- each state-precedence branch (`collecting`, `insufficient-excitation`, `evaluating`, `active`, `fallback`);
- `fallback` winning over a trusted/active-looking model;
- `active` requiring both a trusted identifier model and an active predictor;
- finite-number rejection, boolean-not-number handling, and defensive copying.

In `tests/unit/controller/test_pid_sp.py`, add a behavioral assertion that `Controller.get_status()["learning"]` is schema 1, marked `controller="pid_sp"`, and is built from the same identifier/predictor snapshot returned at the top level. Do not assert implementation source text.

Run and observe RED:

```bash
uv run pytest -q tests/unit/controller/test_pid_sp_learning.py tests/unit/controller/test_pid_sp.py
```

**Step 2: Implement one backend-owned projection builder**

In `controller/pid_sp_learning.py`:

- define immutable validated report/gate structures following existing Pydantic-dataclass conventions;
- import excitation and confirmation constants from `controller.fopdt_identifier` rather than restating numeric values;
- implement `build_pid_sp_live_learning(identifier, predictor)`;
- copy nested mappings so callers cannot mutate controller state through a status result;
- reject non-finite values and booleans in numeric fields;
- encode the precedence above in one function.

In `controller/pid_sp.py`:

- call `identifier.status()` and `predictor.status()` once each inside `get_status()`;
- preserve the existing `identifier` and `predictor` top-level members;
- add the normalized `learning` member from `build_pid_sp_live_learning()`.

**Step 3: Run focused tests and static checks**

```bash
uv run pytest -q tests/unit/controller/test_pid_sp_learning.py tests/unit/controller/test_pid_sp.py
uv run ruff check controller/pid_sp_learning.py controller/pid_sp.py tests/unit/controller/test_pid_sp_learning.py tests/unit/controller/test_pid_sp.py
uv run ruff format --check controller/pid_sp_learning.py controller/pid_sp.py tests/unit/controller/test_pid_sp_learning.py tests/unit/controller/test_pid_sp.py
```

**Step 4: Commit the focused change**

```bash
jj commit -m "feat(pid-sp): publish normalized learning status"
```

---

## Task 2: Build the durable PID-SP report and API

**Files:**
- Modify: `controller/pid_sp_learning.py`
- Modify: `blueprints/api/routes.py`
- Modify: `tests/unit/controller/test_pid_sp_learning.py`
- Create: `tests/web/test_api_pid_sp_learning.py`

**Step 1: Write failing report-composition tests**

Cover:

- empty live state + no checkpoint → 200-ready `idle` report;
- empty live state + valid FOPDT checkpoint → idle report with checkpoint;
- empty live state + valid IPDT checkpoint → idle report with checkpoint;
- legacy no-`form` checkpoint → normalized `fopdt`;
- marked live PID-SP status → live fields and live status preserved;
- stale MPC `learning` mapping or unmarked mapping → ignored as live PID-SP state;
- malformed marked PID-SP live state → structured `error` while preserving a valid checkpoint;
- malformed/non-finite checkpoint → explicit failure or 422 according to the existing route error convention;
- canonical revision is stable across mapping insertion order and changes when any visible field changes;
- returned mappings do not alias datastore/live input objects.

Inject readers/build inputs in unit tests; do not mutate global datastore paths.

Run and observe RED:

```bash
uv run pytest -q tests/unit/controller/test_pid_sp_learning.py
```

**Step 2: Implement the report builder**

Add to `controller/pid_sp_learning.py`:

- immutable `PidSpLearningReport` and discriminated FOPDT/IPDT checkpoint contracts;
- `current_pid_sp_learning_report(*, status, checkpoint)` as a pure projection;
- `backend_pid_sp_learning_report()` that reads `read_status()` once and `ControllerModelStore().load("pid_sp")` once;
- canonical strict-JSON serialization and SHA-256 revision generation, excluding the revision field itself;
- explicit normalization matching the existing restore physics/schema contract without mutating persisted data.

Keep datastore imports local if needed to avoid controller/common import cycles.

**Step 3: Add the read-only route**

In `blueprints/api/routes.py`, add:

```text
GET /api/pid-sp-learning/report
```

Behavior:

- `200` with the exact schema for empty, idle, collecting, evaluating, active, and fallback states;
- `422` only when the report itself cannot be represented under its published contract;
- no POST route and no command dispatch entry.

In `tests/web/test_api_pid_sp_learning.py`, monkeypatch the report sources and verify status codes plus exact serialized fields. Include a test proving there is no PID-SP action endpoint.

**Step 4: Run focused tests and static checks**

```bash
uv run pytest -q tests/unit/controller/test_pid_sp_learning.py tests/web/test_api_pid_sp_learning.py
uv run ruff check controller/pid_sp_learning.py blueprints/api/routes.py tests/unit/controller/test_pid_sp_learning.py tests/web/test_api_pid_sp_learning.py
uv run ruff format --check controller/pid_sp_learning.py blueprints/api/routes.py tests/unit/controller/test_pid_sp_learning.py tests/web/test_api_pid_sp_learning.py
```

**Step 5: Commit the focused change**

```bash
jj commit -m "feat(api): expose PID-SP learning report"
```

---

## Task 3: Generalize the socket invalidation token

**Files:**
- Create: `controller/learning_report.py`
- Modify: `blueprints/mobile/socket_io.py`
- Modify: `web-react/src/helpers/types.ts`
- Modify: `tests/web/test_socket_dash_payload_fields.py`
- Create or modify: `tests/unit/controller/test_learning_report.py`
- Modify fixtures that construct `LiveState` only if the field is required there.

**Step 1: Write failing dispatcher tests**

Specify `controller_learning_report_revision(controller_name)`:

- `mpc` delegates to the existing `controller.model_learning.report.learning_report_revision()`;
- `pid_sp` delegates to `backend_pid_sp_learning_report().revision`;
- unsupported/unknown controllers return `None` without touching either backend;
- one provider failure does not accidentally invoke the other provider.

Add a socket payload test proving `_get_dash_data()` dispatches from `settings["controller"]["selected"]` and emits `modelLearningRevision` for both supported controllers.

Run and observe RED:

```bash
uv run pytest -q tests/unit/controller/test_learning_report.py tests/web/test_socket_dash_payload_fields.py
```

**Step 2: Implement the narrow dispatcher**

Create `controller/learning_report.py` with lazy imports to keep MPC and PID-SP report machinery isolated. Replace the direct MPC revision import/call in `blueprints/mobile/socket_io.py` with the dispatcher.

Keep the published wire key `modelLearningRevision`; its meaning is already generic and retaining it avoids needless protocol churn. The socket payload is handed directly to `LiveState`, so rename the incorrect frontend-only `learningReportRevision` member/props/tests to `modelLearningRevision` and type it as `string | null | undefined` (the current backend already emits a string digest).

**Step 3: Run focused tests and static checks**

```bash
uv run pytest -q tests/unit/controller/test_learning_report.py tests/web/test_socket_dash_payload_fields.py
uv run ruff check controller/learning_report.py blueprints/mobile/socket_io.py tests/unit/controller/test_learning_report.py tests/web/test_socket_dash_payload_fields.py
uv run ruff format --check controller/learning_report.py blueprints/mobile/socket_io.py tests/unit/controller/test_learning_report.py tests/web/test_socket_dash_payload_fields.py
```

**Step 4: Commit the focused change**

```bash
jj commit -m "refactor(learning): dispatch report invalidation by controller"
```

---

## Task 4: Extract the shared learning disclosure shell

**Files:**
- Create: `web-react/src/components/dashboard/learning/LearningDialog.tsx`
- Create: `web-react/src/components/dashboard/learning/learningDisplay.ts`
- Create: `web-react/tests/unit/components/dashboard/LearningDialog.test.tsx`
- Modify: `web-react/src/components/dashboard/dashboard.css`

**Step 1: Write failing shell behavior tests**

Test the shared shell independently with dummy content:

- pill renders supplied controller label and normalized status;
- opening moves focus to Close;
- Escape closes and returns focus to the trigger;
- scrim click closes, content click does not;
- Tab and Shift+Tab wrap inside the dialog;
- loading sets `aria-busy` without removing prior content;
- an error renders as an alert with Retry and Retry calls the supplied callback;
- title/close accessible names come from props, not hard-coded MPC strings.

Run and observe RED:

```bash
cd web-react && bunx vitest run tests/unit/components/dashboard/LearningDialog.test.tsx
```

**Step 2: Implement the shell**

Move only generic mechanics from `MpcLearningPanel.tsx`:

- trigger/open state;
- trigger and close refs;
- fixed portal/scrim;
- focus restoration and focus trap;
- loading/error/retry rendering;
- shared section class and status display utilities.

Keep the shell controlled by exact props; do not introduce a context/provider framework.

Rename CSS selector `.pf-dash-mpc-learning` to `.pf-dash-learning` and update the shell. Preserve every declaration byte-for-byte except the selector/comment wording.

**Step 3: Run focused frontend checks**

```bash
cd web-react
bunx vitest run tests/unit/components/dashboard/LearningDialog.test.tsx
bunx eslint src/components/dashboard/learning/LearningDialog.tsx src/components/dashboard/learning/learningDisplay.ts tests/unit/components/dashboard/LearningDialog.test.tsx
bunx prettier --check src/components/dashboard/learning/LearningDialog.tsx src/components/dashboard/learning/learningDisplay.ts tests/unit/components/dashboard/LearningDialog.test.tsx src/components/dashboard/dashboard.css
```

**Step 4: Commit the focused change**

```bash
jj commit -m "refactor(ui): extract shared learning disclosure"
```

---

## Task 5: Move MPC into the shared shell without behavior changes

**Files:**
- Rename: `web-react/src/components/dashboard/MpcLearningPanel.tsx` → `web-react/src/components/dashboard/learning/MpcLearningView.tsx`
- Modify: `web-react/src/components/dashboard/learning/MpcLearningView.tsx`
- Rename/modify: `web-react/tests/unit/components/dashboard/MpcLearningPanel.test.tsx` → `web-react/tests/unit/components/dashboard/MpcLearningView.test.tsx`
- Modify: `web-react/src/components/dashboard/Dashboard.tsx` only in Task 7; use a temporary direct test harness here.

**Step 1: Preserve the existing MPC suite as the regression oracle**

Before changing production code, rename the test and update only its import/render helper to target `MpcLearningView`. Keep all existing assertions covering:

- exact report request and five-second polling;
- socket-revision invalidation and stale response rejection;
- calibration confirmations/actions;
- exact candidate digest + decision activation gating;
- rollback reason and action;
- report sections, backend failures, loading/error/retry;
- controller deactivation/unmount cancellation behavior.

Add a test that MPC supplies `MPC learning`/`MPC model learning` to the shared shell and that every MPC-only action remains present.

Run against the not-yet-refactored target and observe RED due to the missing component.

```bash
cd web-react && bunx vitest run tests/unit/components/dashboard/MpcLearningView.test.tsx
```

**Step 2: Refactor MPC to use `LearningDialog`**

- Keep the exact TanStack Query key, request-generation fencing, refresh interval, report DTO, and API calls.
- Remove duplicated portal/focus/loading/error shell code now owned by `LearningDialog`.
- Adapt `ModelEvidenceReport.status` through `learningDisplay.ts`; preserve current user-facing labels and tones.
- Render the existing MPC sections/actions as children of the shell without changing their backend authority checks.
- Do not leave an `MpcLearningPanel` alias or re-export.

**Step 3: Run the MPC and shared-shell suites**

```bash
cd web-react
bunx vitest run tests/unit/components/dashboard/LearningDialog.test.tsx tests/unit/components/dashboard/MpcLearningView.test.tsx
bunx eslint src/components/dashboard/learning/LearningDialog.tsx src/components/dashboard/learning/learningDisplay.ts src/components/dashboard/learning/MpcLearningView.tsx tests/unit/components/dashboard/LearningDialog.test.tsx tests/unit/components/dashboard/MpcLearningView.test.tsx
bunx prettier --check src/components/dashboard/learning/LearningDialog.tsx src/components/dashboard/learning/learningDisplay.ts src/components/dashboard/learning/MpcLearningView.tsx tests/unit/components/dashboard/LearningDialog.test.tsx tests/unit/components/dashboard/MpcLearningView.test.tsx
```

**Step 4: Commit the focused change**

```bash
jj commit -m "refactor(ui): move MPC learning into shared shell"
```

---

## Task 6: Add the PID-SP frontend report adapter and informational view

**Files:**
- Create: `web-react/src/helpers/pidSpLearning/types.ts`
- Create: `web-react/src/helpers/pidSpLearning/pidSpLearningApi.ts`
- Create: `web-react/src/components/dashboard/learning/PidSpLearningView.tsx`
- Create: `web-react/tests/unit/helpers/pidSpLearningApi.test.ts`
- Create: `web-react/tests/unit/components/dashboard/PidSpLearningView.test.tsx`

**Step 1: Write failing API adapter tests**

Pin:

- exact GET path `/api/pid-sp-learning/report` and same-origin/base URL behavior;
- abort-signal forwarding;
- valid discriminated FOPDT and IPDT decoding;
- empty idle report;
- non-2xx, invalid JSON, and schema mismatch mapped to the existing `{ok,status,message,data}` result style;
- booleans rejected from numeric fields and non-finite values rejected;
- response ownership/no input aliasing where applicable.

Run and observe RED:

```bash
cd web-react && bunx vitest run tests/unit/helpers/pidSpLearningApi.test.ts
```

**Step 2: Implement exact TypeScript contracts and fetch adapter**

Mirror schema version 1 exactly. Do not reuse `ModelEvidenceReport` for PID-SP details. Reuse only generic result/error helpers when they already fit without changing MPC behavior.

**Step 3: Write failing PID-SP view tests**

Cover:

- pill copy and each normalized status/tone;
- no calibration, activation, rollback, reset, or mutation request controls;
- idle/no-checkpoint empty state;
- durable FOPDT and IPDT model tables;
- live excitation gates showing observed vs required values;
- accepted samples/seconds, duty variation, temperature span, transition, duty segments;
- candidate count, confirmation progress, best/runner-up residuals, distrust ratio/count;
- predictor active/disabled, residual streak, truncation count, `x0`, and `xd`;
- structured failure alert while a valid checkpoint remains visible;
- loading, retry, five-second polling, socket-token invalidation, and late-response suppression;
- unmount/API-base change cancels or supersedes the old request.

Run and observe RED:

```bash
cd web-react && bunx vitest run tests/unit/components/dashboard/PidSpLearningView.test.tsx
```

**Step 4: Implement the informational view**

- Use `LearningDialog` for all pill/modal behavior.
- Use a query key containing both API base and controller kind.
- Match the MPC request-generation fence and invalidation behavior.
- Keep the previous successful report visible during refresh failures.
- Render parameter names exactly (`K`, `tau`, `K_i`, `c0`, `theta`) with units/short explanations in presentation code.
- Do not derive gate pass/fail in TypeScript; display backend booleans and thresholds.
- Do not render buttons other than Close and Retry.

**Step 5: Run focused frontend checks**

```bash
cd web-react
bunx vitest run tests/unit/helpers/pidSpLearningApi.test.ts tests/unit/components/dashboard/LearningDialog.test.tsx tests/unit/components/dashboard/PidSpLearningView.test.tsx
bunx eslint src/helpers/pidSpLearning/types.ts src/helpers/pidSpLearning/pidSpLearningApi.ts src/components/dashboard/learning/PidSpLearningView.tsx tests/unit/helpers/pidSpLearningApi.test.ts tests/unit/components/dashboard/PidSpLearningView.test.tsx
bunx prettier --check src/helpers/pidSpLearning/types.ts src/helpers/pidSpLearning/pidSpLearningApi.ts src/components/dashboard/learning/PidSpLearningView.tsx tests/unit/helpers/pidSpLearningApi.test.ts tests/unit/components/dashboard/PidSpLearningView.test.tsx
```

**Step 6: Commit the focused change**

```bash
jj commit -m "feat(ui): show PID-SP learning diagnostics"
```

---

## Task 7: Cut the dashboard over to the generic `LearningPanel`

**Files:**
- Create: `web-react/src/components/dashboard/LearningPanel.tsx`
- Modify: `web-react/src/components/dashboard/Dashboard.tsx`
- Modify: `web-react/tests/unit/components/dashboard/Dashboard.test.tsx`
- Create: `web-react/tests/unit/components/dashboard/LearningPanel.test.tsx`
- Modify demo/fixture data only if the corrected revision type requires it.

**Step 1: Write failing selection/race tests**

`LearningPanel.test.tsx` must prove:

- `pid` and unknown controller → null and zero report requests;
- `mpc` → only the MPC view/report request;
- `pid_sp` → only the PID-SP view/report request;
- `mpc → pid_sp` and `pid_sp → mpc` transitions discard late old-controller responses;
- remount/API-base changes do not resurrect prior successful reports;
- the socket revision token invalidates only the active controller query.

Update `Dashboard.test.tsx` to prove settings-selected `pid_sp` produces the PID-SP pill and selected `mpc` preserves the MPC pill.
Also pin the wire-to-component handoff: `Dashboard` passes `dash.modelLearningRevision` unchanged. Remove the nonexistent `learningReportRevision` frontend field rather than adding a second name for the same token.

Run and observe RED:

```bash
cd web-react && bunx vitest run tests/unit/components/dashboard/LearningPanel.test.tsx tests/unit/components/dashboard/Dashboard.test.tsx
```

**Step 2: Implement the coordinator and clean cutover**

`LearningPanel.tsx` is a narrow switch:

- exact `mpc` → `MpcLearningView`;
- exact `pid_sp` → `PidSpLearningView`;
- everything else → `null`.

Pass only the props each provider needs. Update `Dashboard.tsx` to import/render `LearningPanel`. Remove every `MpcLearningPanel` path/name and the old CSS selector. Do not leave an alias or compatibility export.

Use the language server file rename/reference tooling for moved exports and inspect all references before deleting the old path.

**Step 3: Run focused integration checks**

```bash
cd web-react
bunx vitest run tests/unit/components/dashboard/LearningDialog.test.tsx tests/unit/components/dashboard/MpcLearningView.test.tsx tests/unit/components/dashboard/PidSpLearningView.test.tsx tests/unit/components/dashboard/LearningPanel.test.tsx tests/unit/components/dashboard/Dashboard.test.tsx
bunx eslint src/components/dashboard/LearningPanel.tsx src/components/dashboard/Dashboard.tsx src/components/dashboard/learning/*.tsx src/components/dashboard/learning/*.ts tests/unit/components/dashboard/LearningPanel.test.tsx tests/unit/components/dashboard/Dashboard.test.tsx
bunx prettier --check src/components/dashboard/LearningPanel.tsx src/components/dashboard/Dashboard.tsx src/components/dashboard/learning/*.tsx src/components/dashboard/learning/*.ts tests/unit/components/dashboard/LearningPanel.test.tsx tests/unit/components/dashboard/Dashboard.test.tsx
```

**Step 4: Commit the focused change**

```bash
jj commit -m "feat(dashboard): select learning panel by controller"
```

---

## Task 8: Exercise the responsive dialogs in a real browser

**Files:**
- Modify: `web-react/tests/e2e/dashboard-panel.spec.ts`
- Modify test API fixtures/helpers used by that spec.

**Step 1: Add the PID-SP browser scenario**

Using the existing controlled API route pattern:

- serve settings with `controller.selected="pid_sp"`;
- serve a PID-SP report containing live gates, an IPDT or FOPDT trusted model, and predictor diagnostics;
- verify the pill is visible and opens the PID-SP dialog;
- verify representative details near the top and bottom are reachable;
- verify no MPC action label is present;
- close by Escape and confirm focus returns to the PID-SP trigger.

Retain the current MPC scenario as a regression check.

**Step 2: Assert both breakpoints**

At 800×480:

- the pill remains within the dashboard right column;
- the fixed dialog stays on screen;
- the dialog body scrolls while title/Close remain reachable;
- the last PID-SP section can be scrolled into view.

At 1280×720 or the project’s existing reference viewport:

- the dialog does not overflow horizontally;
- the parameter/gate sections use the intended multi-column layout;
- the dashboard underneath remains unchanged after close.

Run and observe RED before final fixture/component wiring if the scenario was added first.

**Step 3: Run the real-browser suite**

```bash
cd web-react && bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts
```

If the repository uses a different existing project name for this file, use the configured project that already runs `dashboard-panel.spec.ts`; do not create a duplicate Playwright project.

**Step 4: Commit the browser contract**

```bash
jj commit -m "test(ui): cover PID-SP learning dialog"
```

---

## Task 9: Aggregate verification and cleanup

**Files:**
- All changed files only; no opportunistic refactors.

**Step 1: Search for obsolete frontend names**

Confirm zero production/test references to:

```text
MpcLearningPanel
pf-dash-mpc-learning
learningReportRevision
```

Also confirm no PID-SP mutation API/action names were introduced.

**Step 2: Run focused backend contracts together**

```bash
uv run pytest -q \
  tests/unit/controller/test_pid_sp.py \
  tests/unit/controller/test_pid_sp_learning.py \
  tests/unit/controller/test_learning_report.py \
  tests/web/test_api_pid_sp_learning.py \
  tests/web/test_api_model_evidence.py \
  tests/web/test_socket_dash_payload_fields.py \
  tests/unit/runtime/test_hold_control_trace.py
```

The existing MPC API and Hold live-learning tests are mandatory regression coverage, not optional collateral suites.

**Step 3: Run focused frontend contracts together**

```bash
cd web-react
bunx vitest run \
  tests/unit/helpers/pidSpLearningApi.test.ts \
  tests/unit/components/dashboard/LearningDialog.test.tsx \
  tests/unit/components/dashboard/MpcLearningView.test.tsx \
  tests/unit/components/dashboard/PidSpLearningView.test.tsx \
  tests/unit/components/dashboard/LearningPanel.test.tsx \
  tests/unit/components/dashboard/Dashboard.test.tsx
bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts
```

**Step 4: Run repository quality gates**

From the repository root:

```bash
uv run ruff check .
uv run pytest -q
```

From `web-react/`:

```bash
bun run typecheck
bun run test
bun run build
bun run lint
```

If a full-repository gate fails, establish whether the failure reproduces on the plan’s parent revision before attributing it to this work. Do not suppress or reformat unrelated files.

**Step 5: Smoke-test the actual dashboard**

Launch the normal backend/frontend development stack through the project’s existing commands, then use Chromium to exercise:

1. PID selected → no learning pill.
2. PID-SP selected → PID-SP pill, live/idle report, scrollable informational dialog, no mutation controls.
3. MPC selected → MPC pill and existing calibration/activation/rollback UI.
4. Switch PID-SP ↔ MPC while a report request is delayed → no stale controller content.
5. Repeat at 800×480 and ≥1280 px.

The smoke test, not component tests alone, is the completion proof for this UI change.

**Step 6: Request final code review**

Review for:

- accidental changes to MPC authority/action semantics;
- frontend duplication that belongs in `LearningDialog`;
- generic abstractions containing controller-specific nullable fields;
- frontend copies of backend learner thresholds;
- stale-response/controller-switch races;
- non-finite JSON and malformed checkpoint handling;
- accessibility/focus regressions;
- mobile-panel overflow.

Resolve every Critical or Important finding and rerun the finding’s focused contract plus Steps 2–5.

**Step 7: Confirm clean delivery state**

Use Jujutsu-safe status inspection. The working copy should contain no uncommitted changes, no obsolete component aliases, and no generated evidence artifacts. Do not move bookmarks or push unless explicitly requested.
