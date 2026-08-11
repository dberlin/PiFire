# Task 9 aggregate verification report

Status: **DONE**

## Cleanup searches

- `MpcLearningPanel`: 0 production/test references.
- `pf-dash-mpc-learning`: 0 production/test references.
- `learningReportRevision`: 0 production/test references.
- PID-SP learning mutation API/actions: 0 production implementations. The only route is `GET /api/pid-sp-learning/report`; focused backend coverage confirms POSTing to the report or action paths returns 404.
- PID-SP browser dialogs exposed 0 mutation controls at both target widths.

## Focused gates

- Backend bundle from the brief: **195 passed** in 5.89s.
- The brief's historical `bunx vitest run ...` invocation was executed: **6 suites failed before test collection / 0 tests ran** because these tests intentionally import native `@rstest/core` APIs. The reviewed Tasks 1–8 ledger already records the native Rstest substitution.
- Native focused frontend bundle, `bunx rstest run ...`: **6 files / 165 tests passed** in 3.52s.
- Panel Playwright gate: **14 passed** in 12.1s.

## Aggregate gates

- `uv run ruff check .`: **green**, all checks passed.
- `uv run pytest -q`: three no-display runs exposed the existing random-order/xdist Pygame environment failure, each with a different failing subset (`2 failed / 6804 passed`, `1 failed / 6805 passed`, and `4 failed / 6802 passed`; every failure was `pygame.error: No available video device`). The first two failing tests immediately passed focused (**2 passed**). With the repository's established headless display environment (`SDL_VIDEODRIVER=dummy QT_QPA_PLATFORM=offscreen`), the full aggregate command was **6806 passed / 5 skipped / 18 warnings** in 61.07s. No PID-SP/task-caused defect was identified or suppressed.
- `bun run typecheck`: **green**.
- `bun run test`: **204 files / 2071 tests passed** in 23.6s.
- `bun run build`: **green**, Rsbuild completed in 0.71s.
- `bun run lint`: **green**, Biome checked 542 files and ESLint exited 0.

## Real-browser smoke observations

The normal stack was launched with `uv run python control.py`, production-style Gunicorn on port 5000, and `bun run dev` on port 5173. Chromium exercised the production Dashboard bundle.

Live backend observations:

- Original controller `pid`: no learning pill.
- Switched through the normal settings endpoint to `pid_sp`: REST returned an idle, non-live PID-SP report; the PID-SP idle pill and informational dialog rendered at 800x480 and 1280x720, with only the Close control.
- Switched to `mpc`: the existing MPC pill rendered from the backend-owned schema-invalidated report.
- The original `pid` selection was restored and confirmed through `GET /api/settings` before the stack was stopped.

Controlled production-bundle observations used exact reviewed DTOs to exercise states unavailable from an idle prototype:

| Width | PID | PID-SP live dialog | MPC ready-for-review | MPC active | Delayed response races |
| --- | --- | --- | --- | --- | --- |
| 800x480 | 0 learning pills | active; informational; 0 mutation controls; contained; 478px client / 1406px scroll height; wheel-equivalent scroll reached 928px | calibration and exact activation controls present | calibration and explicit-owner rollback present | late PID-SP response delivered after MPC switch and late MPC response delivered after PID-SP switch; final pill and dialog stayed with the new controller |
| 1280x720 | 0 learning pills | active; informational; 0 mutation controls; contained; 718px client / 1406px scroll height; scroll reached 688px | calibration and exact activation controls present | calibration and explicit-owner rollback present | both late responses delivered; final pill and dialog stayed with the new controller |

The PID-SP dialog rendered backend-owned trusted-model revision, confirmation progress, excitation gates, durable checkpoint, and predictor diagnostics. It stayed within the viewport/document width at both sizes. Escape closed the dialog and focus behavior is covered by the focused shell and panel suites.

## Final review

Fresh current-tree review: **CLEAN** (confidence 0.93), with no Critical or Important findings. It specifically confirmed:

- PID-SP remains informational/read-only while MPC retains mutation authority;
- controller/report changes are fenced;
- strict non-finite and malformed report/checkpoint handling remains intact;
- SmithPredictor's revision-free live predictor projection is intentional while identifier/checkpoint data retain revision authority;
- `ControllerModelStore.load_strict()` re-reads persisted data and rejects malformed snapshots even after a warm shared cache.

## Delivery and concerns

- No production/test correction was required on the post-contract-migration tree.
- The historical Vitest command is no longer the project runner; native Rstest is the authoritative focused gate.
- Bare full pytest is sensitive to the host's missing SDL video device under random-order/xdist. The established headless display environment produced the green aggregate result; the failures were unrelated to PID-SP and were not modified.
- Playwright's demo web server logged expected proxy errors while no backend was attached; all 14 panel scenarios passed.
- Verification left no tracked generated evidence artifact. Delivery change: `wzwmzzpo`.
