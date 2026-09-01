# oxlint Deferral Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the three deferrals from the oxlint migration that turned out to find real defects, and permanently close the door on the rest with accurate reasons.

**Architecture:** Every deferred rule was enabled and its findings read. Only three describe real problems: a toggle whose invalid state is never announced to screen readers, six modals with no keyboard dismissal, and three genuinely dead expressions. The other nine rules produce 150 findings and zero defects; they stay off, with their `.oxlintrc.jsonc` comments rewritten from "deferred, out of scope" (which implies a debt) to what the evidence actually showed.

**Tech Stack:** oxlint 1.80, oxfmt 0.65, React 19, TypeScript 7, rstest, bun, jj.

**Spec:** `docs/superpowers/specs/2026-09-01-oxlint-oxfmt-migration-design.md` (this plan resolves that spec's deferred-rule list)

## Global Constraints

- **Package manager is `bun`.** `bun run test` (rstest, never `bun test`). Commit `bun.lock` if it changes.
- **VCS is jj.** `jj new` before the first write, `jj describe --stdin`. Never `git commit`. Never a reflex `jj squash` — edits are already in `@`.
- **`massive-reworks-and-new-ui` is at `ccc4bc5a` and pushed.** Do not rewrite it; build on top.
- **Gate is `bun run lint && bun run typecheck && bun run test`** in `web-react/`, run from the main checkout (a worktree has no `node_modules`).
- **A rule is only re-enabled after its findings are fixed.** Never enable a rule and add a suppression in the same breath.

---

## The Evidence

Read this before touching anything — it is why the plan is shaped this way, and it is the answer to "why aren't we fixing the other 150?"

### Worth fixing

| Rule / issue | Findings | What the evidence actually showed |
|---|---|---|
| `jsx-a11y/role-supports-aria-props` | 1 | `Toggle.tsx:31` sets `aria-invalid` on a `<button>`. That role does not support the attribute, so **a toggle's validation error is silently dropped for assistive tech.** A sighted user sees the error styling; a screen-reader user gets nothing. Functional bug. |
| Modal keyboard dismissal | 6 sites | Of 8 click-to-dismiss scrims, only `ActionMenu` and `LearningDialog` handle Escape. `ConfirmAction`, `SetpointEntry`, `PwmEntry`, `ProbeNotifyModal`, `CurrentLoadCard` and `CommentList` have none — `SetpointEntry`'s `onKeyDown` handles Enter only, and `ConfirmAction`'s apparent "Escape" is a CSS comment. Not a hard trap (all six have Tab-reachable buttons) but a real, inconsistent gap, worst on `ConfirmAction`, which guards destructive actions. |
| `unicorn/no-useless-spread`, `no-useless-fallback-in-spread` | 3 | `delta.ts:15`, `NotificationsTab.tsx:78` spread an empty fallback that can never contribute; `HistoryChart.tsx:166` allocates a throwaway object **inside a chart render path**. Dead code, and the fixes are provably behavior-preserving. |

### Not worth fixing — each was checked, not assumed

| Rule | Findings | Why it stays off |
|---|---|---|
| `unicorn/no-array-sort` | 39 | Inspected **all 39** call sites. Every one sorts a fresh array — `Object.keys(…)`, `[...x]`, `.filter(…).sort()`, `Object.entries(…)`. Zero mutations of props or state, which is the bug class this rule is worth having for. Pure `.toSorted()` preference. |
| `unicorn/consistent-function-scoping` | 44 | Checked specifically for components defined inside components (the real bug — it remounts the subtree every render). **None.** All 44 are lowercase helper functions. Style only. |
| `jsx-a11y/prefer-tag-over-role` | 24 | Wants `<output>` over `role="status"`, `<img>` over `role="img"`. Screen readers already announce the role correctly; no user-visible difference. |
| `jsx-a11y/control-has-associated-label` | 14 | Sampled the sites: mostly empty spacer cells (`<th className="pf-cf-thumb-col"> </th>` for thumbnail/action columns, which is standard) and controls labelled through the `Field` wrapper's `id`/`describedBy` contract, which the rule cannot see. Largely false positives. |
| `eslint/no-shadow` | 12 | All are short callback params (`v`, `value`, `scan`) shadowing in a tight, immediately-visible scope. Readability nit. |
| `typescript/no-explicit-any` | 6 | All in test files stubbing `global.fetch` and a jsdom-missing browser API. Type-safety loss confined to tests. |
| `react/exhaustive-effect-dependencies` | 1 | `RecipeView.tsx` — the *extra* dependency is deliberate and documented (`activeStep` drives which element the ref points at). Enabling this rule means immediately re-adding a suppression for it. |
| `eslint/no-underscore-dangle` | 1 | Naming style. |
| CSS `noDescendingSpecificity` (lost with Biome) | 1 | Its single hit in `probes.css` was itself suppressed as intentional ("disjoint elements"). Re-acquiring CSS linting to catch one already-accepted case is not worth a second linter. |

**Totals: 10 findings fixed, 150 correctly left alone.**

---

## Task 1: Fix the toggle's dropped invalid state

The highest-value finding: one attribute, and it restores validation feedback for screen-reader users.

**Files:**
- Modify: `web-react/src/components/settings/fields/Toggle.tsx:31`
- Test: `web-react/tests/unit/components/settings/fields/Toggle.test.tsx`

**Interfaces:**
- Consumes: the `Field` render-prop contract `({ id, describedBy, invalid })`.
- Produces: no API change. `Toggle` keeps its current props.

- [ ] **Step 1: Start a fresh commit**

```bash
cd /home/dannyb/sources/PiFire && jj new
```

- [ ] **Step 2: Write the failing test**

`aria-invalid` is invalid on `role="button"`. The correct expression of "this control is in an error state" for a button is `aria-describedby` pointing at the error text (already wired via `describedBy`) plus `aria-pressed` for state. Assert the button is *not* carrying an unsupported attribute while still being associated with its error:

```tsx
it("announces the error without an aria-invalid the button role ignores", () => {
  render(
    <Toggle
      label="Smart start"
      path="startup.smartstart.enabled"
      value={false}
      error="Must be enabled first"
      onChange={() => {}}
    />,
  );
  const button = screen.getByRole("button", { name: /smart start/i });
  expect(button).not.toHaveAttribute("aria-invalid");
  expect(button).toHaveAccessibleDescription(/must be enabled first/i);
});
```

- [ ] **Step 3: Run it and confirm it fails**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test -- Toggle
```

Expected: FAIL on `expect(button).not.toHaveAttribute("aria-invalid")` — the attribute is present today.

- [ ] **Step 4: Remove the unsupported attribute**

In `Toggle.tsx`, delete `aria-invalid={invalid}` from the `<button>`. Keep `aria-describedby={describedBy}` — that is what actually conveys the error. If `invalid` becomes unused in the render prop destructuring, leave the destructuring alone (other `Field` consumers use it) and only stop applying it here.

Add a comment stating the constraint, not the change:

```tsx
// aria-invalid is not supported on role="button" and is ignored by assistive
// tech; the error reaches screen readers through aria-describedby instead.
```

- [ ] **Step 5: Run the test and the rule**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test -- Toggle
./node_modules/.bin/oxlint -D jsx-a11y/role-supports-aria-props
```

Expected: test PASSES, and oxlint reports **0** findings for that rule.

- [ ] **Step 6: Enable the rule permanently**

In `web-react/.oxlintrc.jsonc`, delete the `"jsx-a11y/role-supports-aria-props": "off"` line so the rule runs under `correctness`. Confirm:

```bash
./node_modules/.bin/oxlint --print-config | grep role-supports-aria-props
```

Expected: it appears as `"deny"`.

- [ ] **Step 7: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj describe --stdin <<'MSG'
Announce toggle validation errors to assistive tech

aria-invalid is not supported on role="button", so a toggle's error state
was visible in styling but silently dropped for screen-reader users. The
error already reaches them through aria-describedby.

Enables jsx-a11y/role-supports-aria-props now that it is clean.
MSG
```

---

## Task 2: Give every modal a keyboard dismissal

Six modals can only be dismissed by mouse or by tabbing to a button. Two already do this correctly; extract their shared pattern rather than writing it six more times.

**Files:**
- Create: `web-react/src/helpers/useDismissOnEscape.ts`
- Create: `web-react/tests/unit/helpers/useDismissOnEscape.test.tsx`
- Modify: `web-react/src/components/dashboard/ConfirmAction.tsx`, `SetpointEntry.tsx`, `PwmEntry.tsx`, `ProbeNotifyModal.tsx`, `web-react/src/components/pellets/CurrentLoadCard.tsx`, `web-react/src/components/cookfiles/CommentList.tsx`
- Modify (adopt the hook): `web-react/src/components/dashboard/ActionMenu.tsx`, `web-react/src/components/dashboard/learning/LearningDialog.tsx`

**Interfaces:**
- Produces: `useDismissOnEscape(active: boolean, onDismiss: () => void): void` — registers a `window` keydown listener only while `active`, calls `onDismiss` on `Escape`, and removes the listener on cleanup. Tasks 2's six modals all consume exactly this signature.

- [ ] **Step 1: Write the failing hook test**

```tsx
import { renderHook } from "@testing-library/react";
import { useDismissOnEscape } from "../../../src/helpers/useDismissOnEscape";

function pressEscape() {
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
}

it("calls onDismiss for Escape only while active", () => {
  const onDismiss = rs.fn();
  const { rerender, unmount } = renderHook(
    ({ active }: { active: boolean }) => useDismissOnEscape(active, onDismiss),
    { initialProps: { active: true } },
  );

  pressEscape();
  expect(onDismiss).toHaveBeenCalledTimes(1);

  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
  expect(onDismiss).toHaveBeenCalledTimes(1);

  rerender({ active: false });
  pressEscape();
  expect(onDismiss).toHaveBeenCalledTimes(1);

  rerender({ active: true });
  unmount();
  pressEscape();
  expect(onDismiss).toHaveBeenCalledTimes(1);
});
```

The last two assertions are the ones that matter: an inactive modal must not steal Escape from whatever is underneath it, and an unmounted one must not leak a listener.

- [ ] **Step 2: Run it and confirm it fails**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test -- useDismissOnEscape
```

Expected: FAIL — the module does not exist.

- [ ] **Step 3: Write the hook**

```ts
import { useEffect } from "react";

// Escape-to-dismiss for overlays. This is a side effect on the document, not
// derived state, so an effect is the right tool. Scrim clicks are a mouse
// affordance; this is the keyboard equivalent, and every overlay needs one.
export function useDismissOnEscape(active: boolean, onDismiss: () => void): void {
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, onDismiss]);
}
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test -- useDismissOnEscape
```

Expected: PASS.

- [ ] **Step 5: Write one failing behavior test per modal**

For each of the six, assert Escape dismisses. `ConfirmAction` first — it guards destructive actions, so it is the one that matters most:

```tsx
it("dismisses on Escape", async () => {
  const onCancel = rs.fn();
  render(
    <ConfirmAction
      open
      title="Delete this cook?"
      onConfirm={() => {}}
      onCancel={onCancel}
    />,
  );
  await userEvent.keyboard("{Escape}");
  expect(onCancel).toHaveBeenCalledTimes(1);
});
```

Write the equivalent for `SetpointEntry`, `PwmEntry`, `ProbeNotifyModal`, `CurrentLoadCard` and `CommentList`, using each component's real props and its own cancel/close callback name. Read each component's `Props` interface first — do not guess the callback name.

- [ ] **Step 6: Run them and confirm all six fail**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test -- ConfirmAction SetpointEntry PwmEntry ProbeNotifyModal CurrentLoadCard CommentList
```

Expected: 6 FAIL. If any passes, that modal already had dismissal the audit missed — drop it from this task and say so.

- [ ] **Step 7: Wire the hook into all six**

In each component, import and call the hook with the flag that controls visibility and the existing cancel callback, e.g.:

```tsx
useDismissOnEscape(open, onCancel);
```

For components whose overlay is driven by state rather than a prop (`CurrentLoadCard`, `CommentList` use local state such as `lightbox !== null`), pass that condition as `active` and a closer as `onDismiss`. Ensure the closer is stable (`useCallback`) or defined outside render, or the effect re-subscribes every render.

- [ ] **Step 8: Run the tests**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test -- ConfirmAction SetpointEntry PwmEntry ProbeNotifyModal CurrentLoadCard CommentList
```

Expected: all PASS.

- [ ] **Step 9: Migrate the two that already worked**

Replace the hand-rolled effects in `ActionMenu.tsx` (lines ~23–32) and `LearningDialog.tsx` (lines ~58–87) with the hook. `LearningDialog` handles more than Escape — **keep its other key handling** and move only the Escape branch. Then:

```bash
bun run test -- ActionMenu LearningDialog
```

Expected: PASS, unchanged. This step is only valid if those tests already covered Escape; if they did not, add the assertion before refactoring.

- [ ] **Step 10: Rewrite the scrim rule comment to match reality**

The scrim `<div onClick>` elements still trip `click-events-have-key-events` and `no-static-element-interactions`, and making a scrim a tab stop is *worse* for keyboard users — it inserts a focus stop that does nothing visible. So these rules stay off, but the reason in `.oxlintrc.jsonc` is now different and must say so:

```jsonc
    // Scrims are a mouse affordance. Making the backdrop <div> keyboard-
    // operable would add a focus stop that does nothing visible; the keyboard
    // path is Escape, which every overlay now handles via
    // useDismissOnEscape and which each overlay's tests assert. These stay
    // off because the accessible behavior is covered elsewhere, not because
    // it is missing.
    "jsx-a11y/click-events-have-key-events": "off",
    "jsx-a11y/no-static-element-interactions": "off",
    "jsx-a11y/no-noninteractive-element-interactions": "off",
```

- [ ] **Step 11: Full gate and commit**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run lint && bun run typecheck && bun run test
```

Expected: all PASS.

```bash
cd /home/dannyb/sources/PiFire
jj describe --stdin <<'MSG'
Dismiss every overlay on Escape

Six of eight overlays had no keyboard dismissal: ConfirmAction,
SetpointEntry, PwmEntry, ProbeNotifyModal, CurrentLoadCard and CommentList.
SetpointEntry handled Enter only, and ConfirmAction's apparent Escape was a
CSS comment. ConfirmAction guards destructive actions, so it mattered most.

Extracts useDismissOnEscape from the two overlays that already did this
correctly, and adopts it there too.

The scrim lint rules stay off, but for a new reason: a keyboard-operable
backdrop would add a focus stop that does nothing, and Escape is now the
keyboard path, asserted per overlay.
MSG
```

---

## Task 3: Delete the three dead expressions

**Files:**
- Modify: `web-react/src/helpers/settings/delta.ts:15`, `web-react/src/components/settings/tabs/NotificationsTab.tsx:78`, `web-react/src/components/history/HistoryChart.tsx:166`

**Interfaces:** none — behavior-preserving edits only.

- [ ] **Step 1: Record the current behavior as a baseline**

These are behavior-preserving by claim; prove it. Note the passing count before touching anything:

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test 2>&1 | grep -E 'passedTests|failedTests'
```

Expected: `passedTests` 2013+ (it will have grown from Tasks 1–2), `failedTests` 0. Write the number down.

- [ ] **Step 2: Read each site and remove the dead expression**

```bash
sed -n '13,17p' src/helpers/settings/delta.ts
sed -n '76,80p' src/components/settings/tabs/NotificationsTab.tsx
sed -n '164,168p' src/components/history/HistoryChart.tsx
```

For the two `no-useless-fallback-in-spread` sites, `{...(x ?? {})}` becomes `{...x}` — spreading `undefined` or `null` is already a no-op, so the fallback can never contribute. For `HistoryChart.tsx:166`, remove the spread that allocates a throwaway object; use the value directly.

Do not "improve" anything else in these lines.

- [ ] **Step 3: Verify behavior is unchanged**

```bash
cd /home/dannyb/sources/PiFire/web-react
bun run test 2>&1 | grep -E 'passedTests|failedTests'
```

Expected: **exactly** the number from Step 1, and 0 failures. A changed count means the edit was not behavior-preserving — revert and report rather than accepting it.

- [ ] **Step 4: Enable both rules permanently**

Delete these two lines from `web-react/.oxlintrc.jsonc`:

```jsonc
    "unicorn/no-useless-spread": "off",
    "unicorn/no-useless-fallback-in-spread": "off"
```

Note these are `correctness`-category rules, so removing the `off` is all that is needed. Verify:

```bash
./node_modules/.bin/oxlint
```

Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj describe --stdin <<'MSG'
Remove three dead expressions

Two `?? {}` fallbacks inside spreads that can never contribute (spreading
null or undefined is already a no-op), and a throwaway object allocation in
HistoryChart's render path.

Enables unicorn/no-useless-spread and no-useless-fallback-in-spread now
that they are clean.
MSG
```

---

## Task 4: Record why the other nine rules stay off

Without this, the next person re-derives the whole triage — or worse, enables `no-array-sort` and makes 39 pointless edits.

**Files:**
- Modify: `web-react/.oxlintrc.jsonc`

- [ ] **Step 1: Replace the deferral comments with findings**

The current comments say the rules are deferred and scheduled as follow-up, which implies a debt that no longer exists. Rewrite each to state what was found. For the `suspicious` category comment:

```jsonc
  // `correctness` only. `suspicious` was enabled and its 97 findings were
  // read before this was settled: all 39 unicorn/no-array-sort sites sort a
  // fresh array (Object.keys, [...x], .filter().sort()) with no props or
  // state mutation; none of the 44 consistent-function-scoping findings is a
  // component defined inside a component; the 12 no-shadow findings are
  // short callback params. Zero defects. See
  // docs/superpowers/plans/2026-09-01-oxlint-deferral-triage.md.
```

And for the remaining a11y and `any` deferrals, state the finding rather than the deferral — `prefer-tag-over-role` is cosmetic, `control-has-associated-label` is mostly empty spacer cells and `Field`-wrapper labelling the rule cannot see, `no-explicit-any` is confined to test fetch stubs.

- [ ] **Step 2: Confirm nothing changed functionally**

```bash
cd /home/dannyb/sources/PiFire/web-react
./node_modules/.bin/oxlint --print-config > /tmp/after.json
bun run lint && bun run typecheck
```

Expected: PASS, and the resolved config still lists the three newly-enabled rules from Tasks 1 and 3.

- [ ] **Step 3: Commit**

```bash
cd /home/dannyb/sources/PiFire
jj describe --stdin <<'MSG'
Record why the remaining lint rules stay off

The comments said "deferred, scheduled as a follow-up", which implied a
debt. Each rule was enabled and its findings read: 150 findings, zero
defects. They stay off because they find nothing here, which is a different
claim and should not read as unfinished work.
MSG
```

---

## Parallelization

**Tasks 1, 2 and 3 touch disjoint files and are genuinely independent** — different components, different tests, no shared interface. They can run concurrently.

The one shared file is `web-react/.oxlintrc.jsonc`: Task 1 removes one line, Task 3 removes two, Task 4 rewrites comments. Concurrent edits to it will conflict.

Two workable shapes:

1. **Serial (simplest).** Tasks 1 → 2 → 3 → 4. Total work is small; the coordination saved is not worth much.
2. **Parallel with a deferred config step.** Run Tasks 1, 2, 3 concurrently in **isolated jj workspaces**, each skipping its `.oxlintrc.jsonc` edit (Task 1 Step 6, Task 2 Step 10, Task 3 Step 4). Merge, then do all config edits together as Task 4. Disjoint file sets alone are not sufficient isolation in this repo — a workspace is required, and `jj workspace add` does **not** populate `node_modules`, so run `bun install` in each new workspace before any gate command or it will report command-not-found, which must not be read as a pass.

Task 4 is last either way — it documents decisions the earlier tasks make.

## Out of scope

Nine rules and 150 findings, itemised with evidence in **The Evidence** above. If a future change makes any of them worth revisiting — a `.sort()` on a props array, a component defined inside a component — the rule is one line away in `.oxlintrc.jsonc`.

CSS linting is not reacquired. Its one historical finding was already an accepted suppression, and adding a second linter to catch it is not a trade worth making.
