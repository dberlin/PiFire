# MPC Learning Status Relocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the MPC learning status from the cook-mode control grid to a compact, reachable status row below Hopper in the dashboard right column.

**Architecture:** `Dashboard` will own `MpcLearningPanel` because it owns the right-column composition and already loads every prop the panel needs. `ControlButtons` will return to mode-command ownership only. A dedicated dashboard class will size the trigger independently from the cook-control grid while preserving the existing modal and MPC-only rendering.

**Tech Stack:** React 19, TypeScript, Rstest/Testing Library, Playwright, CSS/Tailwind utilities, Jujutsu.

## Global Constraints

- Render the trigger directly after the optional `HopperGauge` in `pf-dash-rightcol`.
- Keep the trigger in the right column when no hopper distance sensor is installed.
- Preserve the existing `MPC learning: <status>` copy, report polling, commands, and modal.
- Use a compact full-width touch target; do not reuse the cook-control grid's 82px track.
- TypeScript LSP references are unavailable in this workspace; make explicit interface edits and let TypeScript diagnostics verify callers.
- Do not modify the unrelated `.gitignore` change in ancestor `vmtwtmpo`.

---

### Task 1: Move MPC panel ownership into Dashboard

**Files:**
- Modify: `web-react/tests/unit/components/dashboard/Dashboard.test.tsx:85-120`
- Modify: `web-react/src/components/dashboard/ControlButtons.tsx:1-69,184-231`
- Modify: `web-react/src/components/dashboard/Dashboard.tsx:1-30,369-410`

**Interfaces:**
- Consumes: `MpcLearningPanelProps` values already held by `Dashboard`: `apiBase`, `mpcConfig.selectedController`, `dash.tempUnits`, `dash.safetyMaxTemp`, and `mpcConfig.ambientC`.
- Produces: `ControlButtons` without `selectedController` or `mpcAmbientC` props; `MpcLearningPanel` rendered as the final child of `[data-pf="rightCol"]`.

- [ ] **Step 1: Add failing ownership and sensor-independence tests**

Add a settings fetch helper inside `describe("Dashboard MPC settings authority", ...)` and assert the trigger's DOM parent for both hopper states:

```tsx
async function renderMpcDashboard(hasDistanceSensor: boolean) {
  const pendingReport = new Promise<Response>(() => {});
  rs.stubGlobal(
    "fetch",
    rs.fn((input: RequestInfo | URL) => {
      if (String(input) === "/api/settings") {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: async () => ({
            settings: {
              controller: { selected: "mpc", config: { mpc: { T_amb: 20 } } },
            },
          }),
        } as Response);
      }
      return pendingReport;
    }),
  );
  renderDashboard({ ...FIXTURE_DASH, hasDistanceSensor });
  return screen.findByRole("button", { name: "MPC learning: loading" });
}

it.each([true, false])(
  "keeps MPC learning in the right column when hopper sensor is %s",
  async (hasDistanceSensor) => {
    const trigger = await renderMpcDashboard(hasDistanceSensor);
    expect(trigger.closest('[data-pf="rightCol"]')).not.toBeNull();
    expect(trigger.closest('[data-pf="controls"]')).toBeNull();
  },
);

it("places MPC learning after Hopper when Hopper exists", async () => {
  const trigger = await renderMpcDashboard(true);
  const hopper = screen.getByText("Hopper").closest(".pf-dash-hopper");
  expect(hopper?.nextElementSibling).toBe(trigger);
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```bash
bun run test -- tests/unit/components/dashboard/Dashboard.test.tsx
```

Expected: the new ownership assertions fail because the trigger is still under `[data-pf="controls"]`.

- [ ] **Step 3: Move the component and narrow `ControlButtons`**

In `ControlButtons.tsx`, remove the `MpcLearningPanel` import, the `DEFAULT_MPC_AMBIENT_C` constant, both MPC props and their types, and the `<MpcLearningPanel ... />` child.

In `Dashboard.tsx`, import `MpcLearningPanel`, stop passing MPC props to `ControlButtons`, and render:

```tsx
{dash.hasDistanceSensor && <HopperGauge h={view.hopper} />}
<MpcLearningPanel
  apiBase={apiBase}
  selectedController={mpcConfig.selectedController}
  units={dash.tempUnits}
  safetyMaxTemp={dash.safetyMaxTemp}
  ambientC={mpcConfig.ambientC}
/>
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```bash
bun run test -- tests/unit/components/dashboard/Dashboard.test.tsx
```

Expected: all `Dashboard.test.tsx` tests pass, including both hopper states and sibling order.

- [ ] **Step 5: Snapshot the ownership change**

Run:

```bash
jj st
```

Expected: only the plan/spec plus `Dashboard.tsx`, `ControlButtons.tsx`, and `Dashboard.test.tsx` are changed; the ancestor `.gitignore` change remains untouched.

### Task 2: Make the right-column trigger compact and reachable

**Files:**
- Modify: `web-react/src/components/dashboard/MpcLearningPanel.tsx:295-307`
- Modify: `web-react/src/components/dashboard/dashboard.css:696-705,953-1029,1042-1116`
- Modify: `web-react/tests/e2e/dashboard-panel.spec.ts:138-250,363-490`

**Interfaces:**
- Consumes: the unchanged `.pf-btn` interaction behavior and the right-column flex layout.
- Produces: `.pf-dash-mpc-learning`, a full-width fixed-height right-column status trigger that remains reachable at 1280×720 and 800×480.

- [ ] **Step 1: Add failing browser layout assertions**

In the existing MPC panel test, before opening the modal, assert ownership and viewport reachability:

```ts
const learning = page.getByRole("button", { name: "MPC learning: collecting" });
await expect(learning).toBeVisible();
await expect(page.locator('[data-pf="rightCol"]')).toContainText("MPC learning: collecting");
await expect(page.locator('[data-pf="controls"]')).not.toContainText("MPC learning:");
await learning.scrollIntoViewIfNeeded();
const learningBox = await learning.boundingBox();
expect(learningBox).not.toBeNull();
expect(learningBox!.y + learningBox!.height).toBeLessThanOrEqual(page.viewportSize()!.height);
```

Add a class assertion so the CSS contract is explicit:

```ts
await expect(learning).toHaveClass(/pf-dash-mpc-learning/);
```

- [ ] **Step 2: Run the panel test and verify it fails**

Run:

```bash
bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts --grep "MPC Hold exposes calibration"
```

Expected: class and/or viewport assertions fail before the compact right-column style exists.

- [ ] **Step 3: Add the dedicated trigger class and sizing**

Change the trigger in `MpcLearningPanel.tsx` to:

```tsx
className="pf-btn pf-dash-mpc-learning"
```

Add dashboard CSS outside the cook-control grid rules:

```css
.pf-dash-mpc-learning {
  width: 100%;
  min-height: 56px;
  flex: 0 0 56px;
  padding: 8px 14px;
  border-color: color-mix(in srgb, var(--accent) 55%, transparent);
  background: color-mix(in srgb, var(--accent) 10%, var(--inset));
  color: var(--text);
  font-size: 18px;
  line-height: 1.1;
}
```

Keep Hopper as the flexible right-column child. At `max-width: 1279px`, retain the 56px fixed trigger and the existing 220px Hopper minimum so document flow—not clipping—provides the extra height.

- [ ] **Step 4: Run focused unit and panel tests**

Run:

```bash
bun run test -- tests/unit/components/dashboard/Dashboard.test.tsx tests/unit/components/dashboard/MpcLearningPanel.test.tsx
bunx playwright test --project=panel tests/e2e/dashboard-panel.spec.ts --grep "MPC Hold exposes calibration"
```

Expected: all focused tests pass; the trigger is in `rightCol`, carries the compact class, fits within the 800×480 viewport when its right column is visible, and still opens the modal.

- [ ] **Step 5: Run TypeScript diagnostics**

Run:

```bash
bun run typecheck
```

Expected: exit 0; no stale `ControlButtons` MPC props or imports remain.

- [ ] **Step 6: Browser smoke-test both dashboard sizes**

Start the demo server with fake MPC settings and evidence responses. At 1280×720 and 800×480:

1. Verify the trigger appears after Hopper.
2. Verify all trigger edges are within the rendered dashboard/scroller.
3. Click it and verify the `MPC model learning` dialog opens.
4. Capture the 1280×720 placement screenshot for user review.

Expected: the status is a compact right-column row, not a cook-mode button; the modal behavior is unchanged.

- [ ] **Step 7: Describe the implementation commit**

Run:

```bash
jj desc -m "Move MPC learning status below hopper"
jj st
```

Expected: one focused implementation commit above the approved design/plan commit, ready for the requested rebase.