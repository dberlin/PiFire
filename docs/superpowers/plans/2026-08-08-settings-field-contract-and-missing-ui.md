# Settings Field Contract and Missing UI Chrome — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every settings field a description and a save-error slot that cannot drift from what is on screen, port four pieces of missing chrome, and retire two cycle settings the controller stopped reading.

**Architecture:** One `Field` component owns the label/hint/error markup and ARIA wiring; a `SettingsFieldErrors` context lets each field claim its own settings path on mount, so the set of "paths with a slot on screen" is computed rather than declared. The `ui_hash` the backend already publishes rides the existing 1 Hz socket frame and drives a settings-cache invalidation instead of Flask's page reload. The two retirements delete a setting with no reader at all (`u_min`) and one whose only reader is `pid_sp`'s timing guard (`HoldCycleTime`).

**Tech Stack:** React 19 + TypeScript, TanStack Query, rstest, Biome + ESLint, rsbuild; Flask + Flask-SocketIO, Pydantic settings schema, pytest, ruff.

**Source spec:** `docs/superpowers/specs/2026-08-08-settings-field-contract-and-missing-ui-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Package manager is `bun`, never bare `npm`.** Run everything from `web-react/`. Commit `bun.lock` if it changes.
- **The web test runner is rstest, not vitest.** Import from `@rstest/core` (`import { describe, expect, it } from "@rstest/core"`). Run with `bun run test` — **never** `bun test`, which is Bun's own runner and will fail with import errors.
- **Web gate for every task that touches `web-react/`:** `bun run typecheck && bun run test && bun run lint`. Lint is Biome format + ESLint; a task is not done until lint is clean.
- **Python gate:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <paths>`. A bare `python` gives false failures — the venv holds PySide6.
- **Format Python with `.venv/bin/ruff format <changed files>` before every commit.** Never `uvx ruff`: the repo pins ruff <0.16 and uvx resolves newer.
- **Commit with `jj`, not `git`.** This is a colocated repo, so `git commit` silently works and puts the change in the wrong place.
  - **Your very first action in a task, before any Write, is `jj new -m "<the task's commit subject>"`.** In jj the working copy IS the commit: edits land in `@` as you make them. Running `jj new` at the *end* would leave your work in the previous change and start an empty one — backwards.
  - Finish with `jj describe --stdin` (there is no `-F` flag) only if the message needs refining.
  - **Never `jj squash` after editing.** Your edits are already in `@`; squashing moves them into the parent, which may be a pushed commit.
  - Other sessions commit to this branch concurrently. If `jj st` lists files your task does not name, split them out rather than describing them as yours.
- **No backticks inside double-quoted shell arguments** — zsh eats them. Use a quoted heredoc or a file.
- **Python is 3.14+.** `except A, B` without parens is ruff-canonical here; do not "fix" it.
- **SQLite (`pifire.db`) is the authoritative store.** `settings.json` is only ever an export/backup/first-boot import.

---

## File Structure

**Created**

| File | Responsibility |
|---|---|
| `web-react/src/components/settings/fields/Field.tsx` | The one label/hint/error shell: markup plus `aria-describedby` / `aria-invalid` wiring |
| `web-react/src/helpers/settings/fieldErrorContext.tsx` | `SettingsFieldErrorsProvider` + `useSettingsFieldErrors`: holds the save's errors and the claimed-path registry |
| `web-react/public/manifest.webmanifest` | PWA manifest, values copied from the retired Flask route |
| `web-react/tests/unit/components/settings/fields/Field.test.tsx` | Field markup + ARIA coverage |
| `web-react/tests/unit/helpers/settings/fieldErrorContext.test.tsx` | Claim/unclaim and unmatched-error derivation |
| `tests/web/test_spa_manifest.py` | The declared manifest href is served, and so is every icon it names |
| `tests/web/test_socket_ui_hash.py` | The socket frame carries `uiHash` |

**Modified**

| File | Change |
|---|---|
| `web-react/src/components/settings/fields/{NumberField,Toggle,Select,TextField,ColorField,StringListField,SecretField}.tsx` | Compose onto `Field` |
| `web-react/src/components/settings/SaveBar.tsx` | Read unmatched errors from context; drop `errors`/`paths` props |
| `web-react/src/components/settings/RangeProfileTable.tsx` | Gain an error slot and claim its path |
| `web-react/src/components/settings/tabs/*.tsx` (9 with a SaveBar) | Wrap in the provider; `StartupTab` loses `CLAIMED_PATHS` |
| `web-react/src/components/settings/tabs/ControllerTab.tsx` | Pass `option_description` as each option's hint |
| `web-react/src/components/wizard/ConfigOptionField.tsx` | Compose onto `Field`; render `option_description` |
| `web-react/src/components/wizard/DiscoveryPanel.tsx` | Refresh + Close controls |
| `web-react/src/components/settings/tabs/WorkModeTab.tsx` | `u_max` recommendation button; lose the `u_min` and `HoldCycleTime` controls |
| `web-react/src/helpers/types.ts` | `LiveState.uiHash` |
| `web-react/src/components/shell/AppShell.tsx` | Invalidate the settings query when `uiHash` changes |
| `web-react/index.html` | `<link rel="manifest">` |
| `common/app.py` | `create_ui_hash(settings=None)` |
| `blueprints/mobile/socket_io.py` | `uiHash` in `_get_dash_data` |
| `blueprints/spa/routes.py` | Serve the manifest |
| `common/settings_schema.py`, `common/defaults.py` | Drop `u_min` and `HoldCycleTime` |
| `controller/pid_sp.py` | `cycle_time` from `AUGER_TIMING.frame_s` |
| `controller/applied_output.py`, `controller/runtime/modes/hold.py` | Correct the two comments that claim a `u_min` clamp |
| `controller/controllers.json` | Drop `cycle_time` and `cycle_ratio_min` from `recommendations.cycle` |

**Deleted**

- `controller/runtime/logic/fan.py::fan_assist_times` and `tests/unit/runtime/test_logic_fan.py`

---

## Phase 1 — The field contract (spec section A)

### Task 1: The `Field` shell

**Files:**
- Create: `web-react/src/components/settings/fields/Field.tsx`
- Test: `web-react/tests/unit/components/settings/fields/Field.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces: `Field`, `FieldProps` — `{ label: string; hint?: string; error?: string | null; path?: string; children: (ids: { describedBy: string | undefined; invalid: true | undefined }) => ReactNode }`. The render-prop hands the control the ARIA attributes it must carry, because those ids belong to elements `Field` renders and the control does not.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { Field } from "../../../../src/components/settings/fields/Field";

describe("Field", () => {
  it("describes the control with the hint id when only a hint renders", () => {
    render(
      <Field label="Duration" hint="Seconds to run">
        {({ describedBy, invalid }) => (
          <input aria-label="Duration" aria-describedby={describedBy} aria-invalid={invalid} />
        )}
      </Field>,
    );
    const input = screen.getByLabelText("Duration");
    const hint = screen.getByText("Seconds to run");
    expect(input.getAttribute("aria-describedby")).toBe(hint.id);
    expect(input.getAttribute("aria-invalid")).toBeNull();
  });

  it("describes the control with both ids and marks it invalid when both render", () => {
    render(
      <Field label="Duration" hint="Seconds to run" error="Must be an integer">
        {({ describedBy, invalid }) => (
          <input aria-label="Duration" aria-describedby={describedBy} aria-invalid={invalid} />
        )}
      </Field>,
    );
    const input = screen.getByLabelText("Duration");
    const ids = (input.getAttribute("aria-describedby") ?? "").split(" ");
    expect(ids).toContain(screen.getByText("Seconds to run").id);
    expect(ids).toContain(screen.getByText("Must be an integer").id);
    expect(input.getAttribute("aria-invalid")).toBe("true");
  });

  it("references no id at all when neither renders", () => {
    render(
      <Field label="Duration">
        {({ describedBy }) => <input aria-label="Duration" aria-describedby={describedBy} />}
      </Field>,
    );
    expect(screen.getByLabelText("Duration").getAttribute("aria-describedby")).toBeNull();
  });

  it("puts the error in an alert region", () => {
    render(
      <Field label="Duration" error="Must be an integer">
        {() => <input aria-label="Duration" />}
      </Field>,
    );
    expect(screen.getByRole("alert").textContent).toBe("Must be an integer");
  });
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web-react && bun run test tests/unit/components/settings/Field.test.tsx`
Expected: FAIL — cannot resolve the `Field` import.

Note the depth: the test sits at `tests/unit/components/settings/fields/`, which is five levels below `web-react/`, so the import above needs `../../../../../src/...`. Fix it before running rather than after — a wrong relative path fails with the same "cannot resolve" message as a missing file, and that is how a test gets declared red for the wrong reason.

- [ ] **Step 3: Write the implementation**

The hint and error sit *outside* the `<label>` deliberately: a `<label>` wrapping a control folds all of its text content into that control's accessible name, so text left inside would be read as part of the name. `NumberField` already documents this; `Field` inherits the reasoning.

```tsx
import { type ReactNode, useId } from "react";

export interface FieldProps {
  label: string;
  /** The setting's description. Rendered beneath the control. */
  hint?: string;
  /** The backend's reason for refusing this field on the last save. */
  error?: string | null;
  /** Dotted settings path, e.g. "startup.duration". Present on settings
   *  fields, absent on wizard fields that write no settings path. Task 2
   *  gives this meaning; here it is accepted and ignored. */
  path?: string;
  children: (aria: {
    describedBy: string | undefined;
    invalid: true | undefined;
  }) => ReactNode;
}

export function Field({ label, hint, error = null, children }: FieldProps) {
  const hintId = useId();
  const errorId = useId();
  // aria-describedby takes a space-separated id list; only reference ids for
  // parts that actually render, or the attribute points at nothing.
  const describedBy =
    [hint ? hintId : null, error ? errorId : null].filter(Boolean).join(" ") || undefined;
  return (
    <>
      <label className="pf-field">
        <span className="pf-field-label">{label}</span>
        {children({ describedBy, invalid: error ? true : undefined })}
      </label>
      {hint && (
        <span className="pf-field-hint" id={hintId}>
          {hint}
        </span>
      )}
      {error && (
        <span className="pf-field-error" id={errorId} role="alert">
          {error}
        </span>
      )}
    </>
  );
}
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `cd web-react && bun run test tests/unit/components/settings/Field.test.tsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Prove the ARIA test can fail**

Temporarily change `describedBy` to always return `[hintId, errorId].join(" ")`. Re-run.
Expected: the "references no id at all" case FAILS. Revert the change and re-run to green. A test that cannot fail is not coverage.

- [ ] **Step 6: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
feat(web-react): Field shell owning label, hint, error and ARIA wiring
MSGEOF
```

`jj st` should list only the two files this task names. If it lists more, another session's edits have landed in your change — split them out rather than describing them as yours.

---

### Task 2: The claimed-path context

**Files:**
- Create: `web-react/src/helpers/settings/fieldErrorContext.tsx`
- Test: `web-react/tests/unit/helpers/settings/fieldErrorContext.test.tsx`
- Modify: `web-react/src/components/settings/fields/Field.tsx`

**Interfaces:**
- Consumes: `Field` (Task 1); `SaveFieldError` from `src/helpers/settings/settingsApi.ts` — `{ path: string; message: string }`.
- Produces:
  - `SettingsFieldErrorsProvider({ errors: SaveFieldError[]; children: ReactNode })`
  - `useSettingsFieldErrors(): { errors: SaveFieldError[]; unmatched: SaveFieldError[]; claim: (path: string) => () => void } | null`
  - `Field` now resolves `error` from context when given a `path` and no explicit `error`, and claims that path for its lifetime.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { useState } from "react";
import { Field } from "../../../../src/components/settings/fields/Field";
import { SettingsFieldErrorsProvider } from "../../../../src/helpers/settings/fieldErrorContext";
import { SaveBar } from "../../../../src/components/settings/SaveBar";

const ERRORS = [
  { path: "startup.duration", message: "Input should be a valid integer" },
  { path: "startup.hidden_one", message: "Input should be greater than 0" },
];

function Harness({ showHidden }: { showHidden: boolean }) {
  return (
    <SettingsFieldErrorsProvider errors={ERRORS}>
      <Field label="Duration" path="startup.duration">
        {({ describedBy }) => <input aria-label="Duration" aria-describedby={describedBy} />}
      </Field>
      {showHidden && (
        <Field label="Hidden" path="startup.hidden_one">
          {({ describedBy }) => <input aria-label="Hidden" aria-describedby={describedBy} />}
        </Field>
      )}
      <SaveBar onSave={() => {}} saving={false} status={{ kind: "idle" }} />
    </SettingsFieldErrorsProvider>
  );
}

describe("SettingsFieldErrorsProvider", () => {
  it("renders a mounted field's error inline, not in the save bar", () => {
    render(<Harness showHidden={true} />);
    // Both fields are on screen, so both errors are claimed and the bar adds none.
    expect(screen.getAllByRole("alert").map((n) => n.textContent)).toEqual([
      "Input should be a valid integer",
      "Input should be greater than 0",
    ]);
  });

  it("surfaces an unmounted field's error in the save bar instead", () => {
    render(<Harness showHidden={false} />);
    const alerts = screen.getAllByRole("alert").map((n) => n.textContent);
    expect(alerts).toContain("Input should be a valid integer");
    // Nothing on screen can display the hidden path, so the bar names it.
    expect(alerts).toContain("startup.hidden_one: Input should be greater than 0");
  });

  it("releases a claim when the field unmounts", () => {
    function Toggler() {
      const [show, setShow] = useState(true);
      return (
        <>
          <button type="button" onClick={() => setShow(false)}>
            hide
          </button>
          <Harness showHidden={show} />
        </>
      );
    }
    render(<Toggler />);
    expect(
      screen.getAllByRole("alert").some((n) => n.textContent?.startsWith("startup.hidden_one:")),
    ).toBe(false);
    screen.getByText("hide").click();
    expect(
      screen.getAllByRole("alert").some((n) => n.textContent?.startsWith("startup.hidden_one:")),
    ).toBe(true);
  });
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web-react && bun run test tests/unit/helpers/settings/fieldErrorContext.test.tsx`
Expected: FAIL — cannot resolve `fieldErrorContext`.

- [ ] **Step 3: Write the context**

```tsx
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import type { SaveFieldError } from "./settingsApi";

interface SettingsFieldErrorsValue {
  errors: SaveFieldError[];
  /** The errors no field currently on screen can display. */
  unmatched: SaveFieldError[];
  /** Register a path as displayable. Returns the release function; a field
   *  that unmounts must release, or a hidden field would keep absorbing an
   *  error nobody can see. */
  claim: (path: string) => () => void;
}

const Ctx = createContext<SettingsFieldErrorsValue | null>(null);

export function useSettingsFieldErrors(): SettingsFieldErrorsValue | null {
  return useContext(Ctx);
}

export function SettingsFieldErrorsProvider({
  errors,
  children,
}: {
  errors: SaveFieldError[];
  children: ReactNode;
}) {
  // Counted rather than a Set: two controls may legitimately claim one path
  // (a value and its unit), and the first to unmount must not release both.
  const [claimed, setClaimed] = useState<ReadonlyMap<string, number>>(() => new Map());

  const claim = useCallback((path: string) => {
    setClaimed((prev) => {
      const next = new Map(prev);
      next.set(path, (next.get(path) ?? 0) + 1);
      return next;
    });
    return () => {
      setClaimed((prev) => {
        const next = new Map(prev);
        const remaining = (next.get(path) ?? 1) - 1;
        if (remaining <= 0) next.delete(path);
        else next.set(path, remaining);
        return next;
      });
    };
  }, []);

  const value = useMemo<SettingsFieldErrorsValue>(
    () => ({
      errors,
      claim,
      unmatched: errors.filter((e) => !claimed.has(e.path)),
    }),
    [errors, claim, claimed],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
```

- [ ] **Step 4: Teach `Field` to claim and to read its error**

Add to `Field.tsx`:

```tsx
import { useEffect } from "react";
import { useSettingsFieldErrors } from "../../../helpers/settings/fieldErrorContext";
```

and inside `Field`, before the `useId` calls:

```tsx
  const ctx = useSettingsFieldErrors();
  // Claim on MOUNT, not when an error exists: the claimed set has to mean
  // "paths with a slot on screen", which is a fact about rendering, not about
  // this save's outcome. Deriving it from the errors would let a hidden
  // field's error vanish.
  useEffect(() => {
    if (!path || !ctx) return;
    return ctx.claim(path);
  }, [path, ctx]);
  const resolvedError =
    error ?? (path && ctx ? (ctx.errors.find((e) => e.path === path)?.message ?? null) : null);
```

Then use `resolvedError` everywhere the body currently reads `error`.

- [ ] **Step 5: Point `SaveBar` at the context**

In `SaveBar.tsx`, delete the `errors` and `paths` props and the `unmatchedErrors` import, and replace the trailing map with:

```tsx
      {(useSettingsFieldErrors()?.unmatched ?? []).map((e) => (
        <p key={e.path} className="pf-settings-error-text" role="alert">
          {e.path}: {e.message}
        </p>
      ))}
```

Hooks may not be called inside JSX — lift it to the top of the component body as `const fieldErrors = useSettingsFieldErrors();` and map `fieldErrors?.unmatched ?? []`.

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `cd web-react && bun run test tests/unit/helpers/settings/fieldErrorContext.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 7: Prove the claim-release test can fail**

Make `claim` return a no-op (`return () => {};`). Re-run.
Expected: "releases a claim when the field unmounts" FAILS. Revert and re-run green.

- [ ] **Step 8: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
```

Two existing consumers break on the prop removal and must be fixed in this commit, not left for Task 4:

- `StartupTab` passes `errors`/`paths` to `SaveBar`. Delete those two props here; leave `CLAIMED_PATHS` in place for Task 4 to remove.
- `web-react/tests/unit/components/settings/SaveBar.test.tsx` exercises the old props directly. Rewrite its unmatched-error cases to wrap `SaveBar` in `SettingsFieldErrorsProvider` instead. Do not delete the assertions — the behaviour they check still exists, only its wiring changed.

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
feat(web-react): fields claim their own settings path for error placement
MSGEOF
```

---

### Task 3: Compose the seven settings fields onto `Field`

**Files:**
- Modify: `web-react/src/components/settings/fields/{NumberField,Toggle,Select,TextField,ColorField,StringListField,SecretField}.tsx`
- Test: existing field tests under `web-react/tests/unit/components/settings/`

**Interfaces:**
- Consumes: `Field` (Tasks 1-2).
- Produces: every field accepts `hint?: string`, `error?: string | null` and `path?: string`. `NumberField` keeps `min`/`max`/`step`/`suffix`/`integer`/`disabled` exactly as they are.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { Select } from "../../../../src/components/settings/fields/Select";
import { Toggle } from "../../../../src/components/settings/fields/Toggle";
import { TextField } from "../../../../src/components/settings/fields/TextField";

describe("every settings field carries a description and an error", () => {
  it("Select renders both and links them", () => {
    render(
      <Select
        label="Mode"
        value="a"
        options={[{ value: "a", label: "A" }]}
        onChange={() => {}}
        hint="Which mode to start in"
        error="Not a valid mode"
      />,
    );
    const control = screen.getByLabelText("Mode");
    const ids = (control.getAttribute("aria-describedby") ?? "").split(" ");
    expect(ids).toContain(screen.getByText("Which mode to start in").id);
    expect(ids).toContain(screen.getByText("Not a valid mode").id);
    expect(control.getAttribute("aria-invalid")).toBe("true");
  });

  it("Toggle renders an error", () => {
    render(<Toggle label="On" checked={false} onChange={() => {}} error="Refused" />);
    expect(screen.getByRole("alert").textContent).toBe("Refused");
  });

  it("TextField renders a description", () => {
    render(<TextField label="Name" value="" onChange={() => {}} hint="Shown on the dashboard" />);
    const control = screen.getByLabelText("Name");
    expect(control.getAttribute("aria-describedby")).toBe(
      screen.getByText("Shown on the dashboard").id,
    );
  });
});
```

Adjust the prop names in this test to each component's actual signature before running — read each file first. `Toggle` uses `checked`/`onChange`; confirm the others.

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web-react && bun run test tests/unit/components/settings/`
Expected: FAIL — `hint`/`error` are not props of `Select`, `TextField`.

- [ ] **Step 3: Rewrite each field over `Field`**

`Select` becomes the pattern for the rest:

```tsx
import { Field } from "./Field";

export function Select({
  label,
  value,
  options,
  onChange,
  hint,
  error = null,
  path,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  hint?: string;
  error?: string | null;
  path?: string;
}) {
  return (
    <Field label={label} hint={hint} error={error} path={path}>
      {({ describedBy, invalid }) => (
        <select
          className="pf-input"
          value={value}
          aria-describedby={describedBy}
          aria-invalid={invalid}
          onChange={(e) => onChange(e.target.value)}
        >
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}
```

Do the same for `TextField`, `ColorField`, `StringListField`, `SecretField`, `Toggle` and `NumberField`. For `NumberField`, keep the existing `onBlur` bounds clamp and its comment verbatim — that logic is load-bearing (there is no `<form>` in the settings tree, so the browser never runs constraint validation) and is not what this task is changing. `SecretField` keeps its reveal control inside the render prop.

- [ ] **Step 4: Run the whole web suite**

Run: `cd web-react && bun run test`
Expected: PASS. Existing field tests should not need edits; if one breaks on markup order, the fix is in the component, not the test — the hint and error must stay outside the `<label>`.

- [ ] **Step 5: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
refactor(web-react): compose every settings field onto Field
MSGEOF
```

---

### Task 4: Wire the tabs and delete `CLAIMED_PATHS`

**Files:**
- Modify: `web-react/src/components/settings/tabs/{ControllerTab,StartupTab,PwmTab,NotificationsTab,GeneralTab,WorkModeTab,HistoryTab,PelletsTab,SafetyTab}.tsx`
- Modify: `web-react/src/components/settings/RangeProfileTable.tsx`
- Delete: `StartupTab`'s `CLAIMED_PATHS` and the guard test asserting it

**Interfaces:**
- Consumes: `SettingsFieldErrorsProvider` (Task 2), the field `path` prop (Task 3).
- Produces: nine tabs whose fields carry `path`, no declared path lists anywhere.

- [ ] **Step 1: Write the failing test**

Add to the existing `web-react/tests/unit/components/settings/tabs/WorkModeTab.test.tsx`, reusing that file's `useSaveSettings` mock (module scope, returning `{ save, saving, baseUrl }`) and its settings fixture:

```tsx
it("places a rejected field's reason beside that field, not in the save bar", async () => {
  save.mockResolvedValue({
    kind: "error",
    message: "Some settings were refused",
    errors: [{ path: "cycle_data.PMode", message: "Input should be less than or equal to 9" }],
  });
  renderWorkModeTab();
  await user.click(screen.getByRole("button", { name: "Save" }));

  // Inline, next to the control that owns the path.
  const alerts = screen.getAllByRole("alert").map((n) => n.textContent);
  expect(alerts).toContain("Input should be less than or equal to 9");
  // The save bar prefixes unplaced errors with their path. This one is placed,
  // so that prefixed form must NOT appear.
  expect(alerts).not.toContain("cycle_data.PMode: Input should be less than or equal to 9");
});

it("falls back to the save bar for a path this tab does not render", async () => {
  // A cross-section rule can refuse a path that lives on another tab.
  save.mockResolvedValue({
    kind: "error",
    message: "Some settings were refused",
    errors: [{ path: "safety.maxtemp", message: "Input should be greater than 0" }],
  });
  renderWorkModeTab();
  await user.click(screen.getByRole("button", { name: "Save" }));
  expect(screen.getAllByRole("alert").map((n) => n.textContent)).toContain(
    "safety.maxtemp: Input should be greater than 0",
  );
});
```

Confirm the `SaveStatus` shape against `src/helpers/settings/useSaveSettings.ts` before writing the mock's return value — it returns a `SaveStatus` object, not a boolean, and the mock must match the real one or the test proves nothing about the real path.

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web-react && bun run test tests/unit/components/settings/tabs/WorkModeTab.test.tsx`
Expected: FAIL — the message appears in the save bar, because no field claims the path yet.

- [ ] **Step 3: Wrap each tab and add `path` to its fields**

Per tab: wrap the returned tree in `<SettingsFieldErrorsProvider errors={errors}>`, where `errors` is what the tab already gets back from its save, and add `path="<dotted.path>"` to each field. `StartupTab` additionally drops `CLAIMED_PATHS`, its `errorFor` import and every `error={errorFor(errors, ...)}` — the context supplies those now.

`RangeProfileTable` is a table, not a `Field`. Give it the same two behaviours by hand:

```tsx
const ctx = useSettingsFieldErrors();
useEffect(() => {
  if (!path || !ctx) return;
  return ctx.claim(path);
}, [path, ctx]);
const error = path && ctx ? (ctx.errors.find((e) => e.path === path)?.message ?? null) : null;
```

and render `error` in a `<span className="pf-field-error" role="alert">` beneath the table.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd web-react && bun run test`
Expected: PASS. The old `CLAIMED_PATHS` guard test must be deleted, not adapted — it asserts a mechanism that no longer exists.

- [ ] **Step 5: Prove one tab's wiring is real**

Pick `PelletsTab`. Remove the `path` prop from one field, re-run its test.
Expected: that field's error moves to the save bar and the test FAILS. Restore and re-run green.

- [ ] **Step 6: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
feat(web-react): every settings tab places its own save errors
MSGEOF
```

---

### Task 5: Per-setting descriptions

**Files:**
- Modify: `web-react/src/components/settings/tabs/ControllerTab.tsx`
- Modify: `web-react/src/components/wizard/ConfigOptionField.tsx`
- Test: `web-react/tests/unit/components/wizard/ConfigOptionField.test.tsx`

**Interfaces:**
- Consumes: `Field` (Task 1), the `hint` prop (Task 3).
- Produces: `ConfigOptionField` renders `option.option_description`; `ControllerTab` passes it as each generated field's `hint`.

- [ ] **Step 1: Write the failing test**

```tsx
import { describe, expect, it } from "@rstest/core";
import { render, screen } from "@testing-library/react";
import { ConfigOptionField } from "../../../../src/components/wizard/ConfigOptionField";

const OPTION = {
  option_name: "PB",
  option_friendly_name: "Proportional Band(PB)",
  option_description: "The temperature band centered around the set point.",
  option_type: "float" as const,
  option_default: 60.0,
  hidden: false,
};

describe("ConfigOptionField", () => {
  it("renders the manifest's description for the option", () => {
    render(<ConfigOptionField option={OPTION} value={60} onChange={() => {}} />);
    const control = screen.getByLabelText("Proportional Band(PB)");
    expect(control.getAttribute("aria-describedby")).toBe(
      screen.getByText("The temperature band centered around the set point.").id,
    );
  });

  it("renders nothing for a hidden option", () => {
    const { container } = render(
      <ConfigOptionField option={{ ...OPTION, hidden: true }} value={60} onChange={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
  });
});
```

Match `ConfigOption`'s real shape in `src/helpers/wizard/wizardTypes.ts` — read it before writing the fixture, and take the field values from a real `controllers.json` entry rather than inventing them.

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web-react && bun run test tests/unit/components/wizard/ConfigOptionField.test.tsx`
Expected: FAIL — no `aria-describedby`, description not in the document.

- [ ] **Step 3: Rewrite `ConfigOptionField` over `Field`**

Keep both existing behaviours: the `option.hidden` early return, and the `value === undefined ? option.default : value` fallback with its comment explaining why (an unconfigured module must show what the driver will actually use). Pass `hint={option.option_description}` and move the `<select>` / `<input>` into the render prop, wiring `describedBy` and `invalid` onto it.

- [ ] **Step 4: Pass descriptions in `ControllerTab`**

Each generated field gains `hint={opt.option_description}`. The option loop already walks `meta.metadata[selected].config`, so the text is in hand.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd web-react && bun run test`
Expected: PASS.

- [ ] **Step 6: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
feat(web-react): render per-setting descriptions from the module manifests
MSGEOF
```

---

## Phase 2 — `ui_hash` becomes a settings refetch (spec section B)

### Task 6: Publish `uiHash` on the socket frame

**Files:**
- Modify: `common/app.py:59`
- Modify: `blueprints/mobile/socket_io.py` (`_get_dash_data`, around :297)
- Test: `tests/web/test_socket_ui_hash.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `create_ui_hash(settings=None)` — reads settings itself when not given one. `_get_dash_data` emits `"uiHash": <int>`.

- [ ] **Step 1: Write the failing test**

```python
def test_dash_frame_carries_the_ui_hash(...):
    """The probe-map hash rides the frame the client already receives, so a
    probe reconfiguration needs no extra request to notice."""
    frame = _get_dash_data(settings, pelletdb)
    assert frame["uiHash"] == create_ui_hash(settings)
```

Follow `tests/web/test_socket_dash_payload_fields.py` for how it builds `settings`/`pelletdb` and calls `_get_dash_data`; reuse those fixtures rather than inventing a settings tree.

Add a second test proving the argument is honoured, not decorative:

```python
def test_create_ui_hash_uses_the_settings_it_is_given(...):
    """Passing settings must avoid the read, or the 1 Hz frame pays for a
    datastore round trip it already has the answer to."""
    one = dict(settings)
    other = copy.deepcopy(settings)
    other["probe_settings"]["probe_map"]["probe_info"] = []
    assert create_ui_hash(one) != create_ui_hash(other)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socket_ui_hash.py -v`
Expected: FAIL — `KeyError: 'uiHash'`, and `create_ui_hash()` takes no arguments.

- [ ] **Step 3: Implement**

```python
def create_ui_hash(settings=None):
    if settings is None:
        settings = read_settings()
    return hash(json.dumps(settings["probe_settings"]["probe_map"]["probe_info"]))
```

In `_get_dash_data`, add to the `dash_data` dict, next to `"status"`:

```python
        # The probe-map hash. A client compares it across frames and refetches
        # the settings blob when it moves: set_probe_map() rebuilds hidden_cards,
        # notify_data and history_page.probe_config off probe labels, none of
        # which the socket payload carries. Computed from the settings already
        # in hand, so the frame costs no extra read.
        "uiHash": create_ui_hash(settings),
```

with `create_ui_hash` added to the existing `from common.app import ...`.

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socket_ui_hash.py tests/web/test_socket_dash_payload_fields.py -v`
Expected: PASS.

- [ ] **Step 5: Check the characterization golden**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/characterization/test_process_command_golden.py -v`
Expected: PASS. That file already strips `ui_hash` as non-deterministic (Python salts `hash()` for strings) — if it fails, the strip needs to cover the new key too, and the fix goes there rather than in the payload.

- [ ] **Step 6: Format, gate and commit**

```bash
.venv/bin/ruff format common/app.py blueprints/mobile/socket_io.py tests/web/test_socket_ui_hash.py
QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/ -q
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
feat(api): publish the probe-map ui hash on the dash socket frame
MSGEOF
```

---

### Task 7: Invalidate the settings query when `uiHash` moves

**Files:**
- Modify: `web-react/src/helpers/types.ts`
- Modify: `web-react/src/components/shell/AppShell.tsx`
- Test: `web-react/tests/unit/components/shell/AppShell.test.tsx`

**Interfaces:**
- Consumes: `uiHash` on the frame (Task 6); `queryKeys.settings` from `src/helpers/query/keys.ts`.
- Produces: no exported API — a side effect in `AppShell`.

- [ ] **Step 1: Write the failing test**

```tsx
it("refetches settings when the probe map changes under it", async () => {
  // A changed uiHash means set_probe_map() ran somewhere else: hidden_cards,
  // notify_data and probe_config are all stale in cache even though the
  // readings on screen are live.
  const client = new QueryClient();
  const spy = rs.spyOn(client, "invalidateQueries");
  rerenderWithFrame({ ...FRAME, uiHash: 111 });
  rerenderWithFrame({ ...FRAME, uiHash: 222 });
  expect(spy).toHaveBeenCalledWith({ queryKey: queryKeys.settings });
});

it("does not refetch when the hash is unchanged", async () => {
  const client = new QueryClient();
  const spy = rs.spyOn(client, "invalidateQueries");
  rerenderWithFrame({ ...FRAME, uiHash: 111 });
  rerenderWithFrame({ ...FRAME, uiHash: 111 });
  expect(spy).not.toHaveBeenCalled();
});
```

Follow the existing `AppShell` test for how it stubs `useLiveState`. Note the first-frame case: the initial hash must seed without invalidating, exactly as Flask's `lastCookMode == 'PageLoad'` branch did.

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web-react && bun run test tests/unit/components/shell/AppShell.test.tsx`
Expected: FAIL — `uiHash` is not on `LiveState`, and nothing invalidates.

- [ ] **Step 3: Add the field and the effect**

In `types.ts`, on `LiveState`:

```ts
  /** Hash of the probe map (common/app.py::create_ui_hash). It moves when a
   *  probe is reconfigured anywhere, including from another client. Note it
   *  also moves on a server restart, because Python salts hash() for strings
   *  -- a spurious settings refetch, which is cheap and the reason this drives
   *  an invalidation rather than Flask's full page reload. */
  uiHash: number;
```

In `AppShell`:

```tsx
  const queryClient = useQueryClient();
  const seenUiHash = useRef<number | null>(null);
  useEffect(() => {
    const next = liveState.live.uiHash;
    if (next === undefined) return;
    if (seenUiHash.current === null) {
      seenUiHash.current = next; // first frame seeds; it is not a change
      return;
    }
    if (seenUiHash.current === next) return;
    seenUiHash.current = next;
    queryClient.invalidateQueries({ queryKey: queryKeys.settings });
  }, [liveState.live.uiHash, queryClient]);
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd web-react && bun run test`
Expected: PASS.

- [ ] **Step 5: Prove the seed branch matters**

Delete the `seenUiHash.current === null` branch. Re-run.
Expected: the "does not refetch when the hash is unchanged" case FAILS on the very first frame. Restore and re-run green.

- [ ] **Step 6: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
feat(web-react): refetch settings when the probe map changes, no reload prompt
MSGEOF
```

---

## Phase 3 — PWA manifest (spec section C)

### Task 8: Ship and serve the manifest

**Files:**
- Create: `web-react/public/manifest.webmanifest`
- Modify: `web-react/index.html`
- Modify: `blueprints/spa/routes.py`
- Test: `tests/web/test_spa_manifest.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `GET /manifest.webmanifest` served from the build output.

- [ ] **Step 1: Write the failing test**

```python
def test_declared_manifest_is_served_and_its_icons_resolve(client):
    """Mirrors the favicon test: read the href out of the shipped shell and
    fetch exactly that, so a link the shell declares can never 404."""
    shell = client.get("/").get_data(as_text=True)
    href = re.search(r'rel="manifest"\s+href="([^"]+)"', shell).group(1)
    res = client.get(href)
    assert res.status_code == 200
    manifest = json.loads(res.get_data(as_text=True))
    for icon in manifest["icons"]:
        assert client.get(icon["src"]).status_code == 200
```

Follow `tests/web/test_spa.py::test_favicon_is_declared_and_the_declared_path_is_served` for the client fixture and how it handles a missing `dist/`.

- [ ] **Step 2: Run it and confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_spa_manifest.py -v`
Expected: FAIL — no `rel="manifest"` in the shell.

- [ ] **Step 3: Write the manifest**

`web-react/public/manifest.webmanifest`, values copied from the retired Flask route:

```json
{
  "short_name": "PiFire",
  "name": "PiFire - Pellet Smoker Controller",
  "icons": [
    { "src": "/static/img/launcher-icon-2x.png", "sizes": "96x96", "type": "image/png" },
    { "src": "/static/img/launcher-icon-3x.png", "sizes": "144x144", "type": "image/png" },
    { "src": "/static/img/launcher-icon-4x.png", "sizes": "192x192", "type": "image/png" }
  ],
  "start_url": "/",
  "background_color": "#FFFFFF",
  "theme_color": "#3b3b3b",
  "display": "standalone",
  "orientation": "portrait"
}
```

The icons are referenced, not bundled: `/static/img` is a kept tree Flask's default static handler serves and the spa blueprint deliberately does not shadow, so one href resolves in production and through the dev proxy alike — the same reasoning the favicon already documents.

- [ ] **Step 4: Link it and serve it**

In `index.html`, beside the existing favicon link:

```html
    <link rel="manifest" href="/manifest.webmanifest" />
```

In `blueprints/spa/routes.py`, ahead of the catch-all — without its own route the request reaches the catch-all and is served `index.html`:

```python
@spa_bp.route("/manifest.webmanifest")
def spa_manifest():
    return _cached(send_from_directory(_DIST, "manifest.webmanifest"), _SHELL_CACHE)
```

Confirm rsbuild copies `public/` into `dist/`. If this project has no `public/` convention configured, put the file beside `index.html` in the source root and add it to the build's copy list instead — check `rsbuild.config.ts` before assuming.

- [ ] **Step 5: Build and run the tests**

Run: `cd web-react && bun run build` then
`QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_spa_manifest.py tests/web/test_spa.py -v`
Expected: PASS.

- [ ] **Step 6: Format, gate and commit**

```bash
.venv/bin/ruff format blueprints/spa/routes.py tests/web/test_spa_manifest.py
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
feat(spa): ship and serve a PWA manifest
MSGEOF
```

---

## Phase 4 — The recommended-value button (spec section D)

### Task 9: `u_max` recommendation on WorkModeTab

**Files:**
- Modify: `web-react/src/components/settings/tabs/WorkModeTab.tsx:165-170`
- Test: `web-react/tests/unit/components/settings/tabs/WorkModeTab.test.tsx`

**Interfaces:**
- Consumes: `getControllerMetadata` from `src/helpers/settings/settingsApi.ts`, `queryKeys.controllerMetadata`.
- Produces: no exported API.

Flask's original (`_macro_settings.html:136`) labelled the button with the value itself, titled it "Click to Use Recommended Value.", and set the input without saving. Match that. Its other two buttons are not ported — the settings behind them are retired in Phase 6.

- [ ] **Step 1: Write the failing test**

```tsx
it("stages the selected controller's recommended u_max without saving", async () => {
  renderWorkModeTab({ selected: "pid", recommendations: { cycle: { cycle_ratio_max: 0.9 } } });
  await user.click(screen.getByTitle("Click to Use Recommended Value."));
  expect((screen.getByLabelText("U Max") as HTMLInputElement).value).toBe("0.9");
  expect(saveMock).not.toHaveBeenCalled();
});

it("renders no button when controller metadata is unavailable", () => {
  // getControllerMetadata fails OPEN with null by design, so the tab must
  // still render -- without inventing a recommendation.
  renderWorkModeTab({ metadata: null });
  expect(screen.queryByTitle("Click to Use Recommended Value.")).toBeNull();
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web-react && bun run test tests/unit/components/settings/tabs/WorkModeTab.test.tsx`
Expected: FAIL — no such button.

- [ ] **Step 3: Implement**

Read the selected controller's metadata, then beside the `U Max` `NumberField`:

```tsx
{recommendedUMax !== undefined && (
  <button
    type="button"
    className="pf-recommend-btn"
    title="Click to Use Recommended Value."
    onClick={() => setCycleData("u_max", recommendedUMax)}
  >
    ← {recommendedUMax}
  </button>
)}
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `cd web-react && bun run test tests/unit/components/settings/tabs/WorkModeTab.test.tsx`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
feat(web-react): use-recommended-value button for u_max
MSGEOF
```

---

## Phase 5 — Discovery controls (spec section E)

### Task 10: Refresh and Close on `DiscoveryPanel`

**Files:**
- Modify: `web-react/src/components/wizard/DiscoveryPanel.tsx`
- Test: `web-react/tests/unit/components/wizard/DiscoveryPanel.test.tsx`

**Interfaces:**
- Consumes: nothing.
- Produces: `DiscoveryPanelProps` gains `onRefresh: () => void` and `onClose: () => void`. Every call site must pass them — find them with the LSP's findReferences on `DiscoveryPanel`, not grep.

- [ ] **Step 1: Write the failing test**

```tsx
it("re-runs the scan from Refresh and dismisses from Close", async () => {
  const onRefresh = rs.fn();
  const onClose = rs.fn();
  render(<DiscoveryPanel result={RESULT} onPick={() => {}} onRefresh={onRefresh} onClose={onClose} />);
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  expect(onRefresh).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Close" }));
  expect(onClose).toHaveBeenCalledTimes(1);
});

it("offers Refresh even when the scan found nothing", async () => {
  // The empty case is exactly when a re-scan is what the user wants.
  const onRefresh = rs.fn();
  render(
    <DiscoveryPanel result={{ groups: [] }} onPick={() => {}} onRefresh={onRefresh} onClose={() => {}} />,
  );
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  expect(onRefresh).toHaveBeenCalledTimes(1);
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd web-react && bun run test tests/unit/components/wizard/DiscoveryPanel.test.tsx`
Expected: FAIL — no Refresh or Close button.

- [ ] **Step 3: Implement**

Add the two props, and render an actions row that shows on **all three** return paths — the error branch, the empty branch, and the populated one. The empty and error cases are where a re-scan matters most, so they must not be the branches that lack the button.

- [ ] **Step 4: Update the call sites**

Use the LSP (`findReferences` on `DiscoveryPanel`) to find every consumer. Grep under-reports here; a previous pass on this codebase found 16 of 41 real references that way.

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd web-react && bun run test`
Expected: PASS.

- [ ] **Step 6: Gate and commit**

```bash
cd web-react && bun run typecheck && bun run test && bun run lint
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
feat(web-react): Refresh and Close on the wizard discovery panel
MSGEOF
```

---

## Phase 6 — Retire the two cycle settings (spec section F)

### Task 11: Retire `cycle_data.u_min`

**Files:**
- Modify: `common/settings_schema.py:97`, `common/defaults.py:128`
- Modify: `controller/runtime/logic/fan.py` (delete `fan_assist_times`)
- Delete: `tests/unit/runtime/test_logic_fan.py`
- Modify: `controller/applied_output.py:11`, `controller/runtime/modes/hold.py:102`
- Modify: `web-react/src/components/settings/tabs/WorkModeTab.tsx:159-164` and its `cycle_data` type at :19

**Interfaces:**
- Consumes: nothing.
- Produces: `cycle_data` no longer has a `u_min` member, in the schema or the TypeScript type.

`u_min` has no production reader. The duty floor is `AUGER_TIMING.pulse_s / AUGER_TIMING.frame_s` = 2/20 = 0.1 — the same number, arrived at from the pulse geometry. `fan_assist_times` is the only function that takes it and has no callers outside its own test.

- [ ] **Step 1: Prove the premise before deleting anything**

```bash
cd /home/dannyb/sources/PiFire
grep -rn "u_min" --include='*.py' . | grep -v '/tests/\|/experiments/\|node_modules\|\.venv\|/docs/'
```

Expected: exactly three hits — `fan.py:41`, `fan.py:42`, and the comment at `applied_output.py:11`. **If anything else appears, stop and report it**; the premise has changed and the rest of this task is unsafe.

- [ ] **Step 2: Write the failing test**

```python
def test_cycle_data_has_no_u_min(...):
    """u_min had no reader: the duty floor is pulse_s/frame_s, from the pulse
    geometry, not from a configured ratio."""
    assert "u_min" not in DEFAULT_SETTINGS["cycle_data"]
    assert "u_min" not in CycleData.model_fields
```

Add it to `tests/unit/common/test_settings_schema.py`.

- [ ] **Step 3: Run it and confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/common/test_settings_schema.py -k u_min -v`
Expected: FAIL — the key is present.

- [ ] **Step 4: Delete it everywhere**

Remove the schema field, the default, `fan_assist_times` and its test file, and the `u_min` control and type member from `WorkModeTab`. Correct the two comments so they describe the clamp that exists — a `u_max` ceiling and a lid-open auger pin, with no `u_min` floor. State what the code does; do not narrate the removal.

- [ ] **Step 5: Fix the fixtures that carry the key**

Many test fixtures spell `{"u_min": 0.1, "u_max": 0.9, "HoldCycleTime": 25}`. Those that feed controllers keep working — `mpc.py` reads only `u_max` — but a schema-validating fixture will now fail. Run the full suite and fix what breaks:

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`

- [ ] **Step 6: Confirm the settings-shedding claim rather than assuming it**

An existing tree sheds the key through `validate_settings_tree()`'s repair pass on its next validated write, which is why no migration is written. Prove it: build a settings tree containing `cycle_data.u_min`, run one validated write, and assert the key is gone and nothing else changed. If it does not shed, a migration IS needed and this task grows.

- [ ] **Step 7: Format, gate and commit**

```bash
.venv/bin/ruff format common/settings_schema.py common/defaults.py controller/runtime/logic/fan.py controller/applied_output.py controller/runtime/modes/hold.py
cd web-react && bun run typecheck && bun run test && bun run lint
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
refactor: retire cycle_data.u_min, which no code read
MSGEOF
```

---

### Task 12: Retire `cycle_data.HoldCycleTime`

**Files:**
- Modify: `common/settings_schema.py`, `common/defaults.py`
- Modify: `controller/pid_sp.py:99`
- Modify: `controller/controllers.json` (three `recommendations.cycle` blocks)
- Modify: `web-react/src/components/settings/tabs/WorkModeTab.tsx:123-130` and its type at :15
- Test: `tests/unit/controller/test_pid_sp.py`

**Interfaces:**
- Consumes: `AUGER_TIMING` from `grillplat.actuator_capabilities` — `AugerTiming(pulse_s=2, frame_s=20)`.
- Produces: `PIDSP.cycle_time` is `AUGER_TIMING.frame_s`; `cycle_data` has no `HoldCycleTime`.

`HoldCycleTime` does not set the hold cycle — `hold.py:145` takes its frame from `scheduler.timing.frame_s`, and `PulseScheduler` has no settings dependency. Its one production reader is `pid_sp.py:99`, used at `:282` and `:314` as `cycle_time * 3`.

- [ ] **Step 1: Prove the premise**

```bash
grep -rn "HoldCycleTime" --include='*.py' . | grep -v '/tests/\|node_modules\|\.venv\|/docs/'
```

Expected: exactly two hits — `pid_sp.py:99` and the comment in `smith_predictor.py:39`. **Anything else, stop and report.**

- [ ] **Step 2: Write the failing test**

```python
def test_pid_sp_paces_its_guards_off_the_real_auger_frame():
    """The 3-cycle windows are meant to be three control cycles. The control
    cycle is the pulse frame, which is what actually paces the auger."""
    c = PIDSP(config=CONFIG, units="F", cycle_data={})
    assert c.cycle_time == AUGER_TIMING.frame_s
```

Note `cycle_data={}` — after this change `pid_sp` must not require the key at all.

- [ ] **Step 3: Run it and confirm it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/controller/test_pid_sp.py -k auger_frame -v`
Expected: FAIL with `KeyError: 'HoldCycleTime'`.

- [ ] **Step 4: Implement**

```python
from grillplat.actuator_capabilities import AUGER_TIMING
...
        # Three control cycles is what the guards below mean. The control cycle
        # is the auger's pulse frame: Hold paces the auger from
        # PulseScheduler's timing, which takes no setting.
        self.cycle_time = AUGER_TIMING.frame_s
```

`controller/runtime/logic/pulse.py` already imports from `grillplat.actuator_capabilities`, so this direction is established.

Then delete the schema field, the default, and the `WorkModeTab` control and type member. In `controllers.json`, drop `cycle_time` and `cycle_ratio_min` from all three `recommendations.cycle` blocks, leaving `cycle_ratio_max` — the one key with live code behind it.

- [ ] **Step 5: Run the suites**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/ -q`
Expected: PASS. Fixtures spelling `HoldCycleTime` in a plain dict are harmless; schema-validating ones need the key removed.

- [ ] **Step 6: Confirm shedding, as in Task 11 Step 6**

- [ ] **Step 7: Format, gate and commit**

```bash
.venv/bin/ruff format common/settings_schema.py common/defaults.py controller/pid_sp.py
cd web-react && bun run typecheck && bun run test && bun run lint
```

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
refactor: pace pid_sp off the auger frame and retire HoldCycleTime
MSGEOF
```

---

### Task 13: Validate the `pid_sp` behaviour change on GrillSim

**Files:**
- Create: a scratch experiment script (not committed to `controller/`)
- Modify: `docs/superpowers/backlogs/react-migration-backlog.md` — record the result

**Interfaces:**
- Consumes: `controller/grill_sim.py` — GrillSim is the plant; do not reach for `HiFiGrill`.
- Produces: a recorded before/after measurement.

Task 12 changes `pid_sp`'s two guard windows from 75 s (25 s default × 3) to 60 s (20 s frame × 3). That is a real behaviour change on anyone running `pid_sp`.

- [ ] **Step 1: State what the number decides, before running anything**

Write it down first: **the change ships if setpoint-change overshoot and settling time do not regress against the 25 s baseline.** A sweep with no stated decision rule is how a measurement turns into an open-ended CPU burn.

- [ ] **Step 2: Build the negative control first**

Run the harness with `cycle_time` pinned to 25 (the old value) and confirm it reproduces the pre-change behaviour. If it does not, the harness is wrong — fix the harness before drawing any conclusion about the change. A bad result means the setup is wrong before it means the threshold is.

- [ ] **Step 3: Run both arms**

`pid_sp` on GrillSim, identical seeds, one arm at `cycle_time = 25`, the other at `AUGER_TIMING.frame_s = 20`. Drive a setpoint change, since that is the only thing either guard affects. If the script needs numba: `uv run --with numba <script>` — numba is deliberately not a repo dependency, because pinning it would pin numpy.

- [ ] **Step 4: Report overshoot and settling for both arms**

Absolute numbers, not just a verdict. If the 20 s arm regresses, **stop and report** — the retirement then needs a different landing (keep `pid_sp`'s window at a named constant of 25 rather than the frame), and that is the user's call, not the implementer's.

- [ ] **Step 5: Record the result in the backlog and commit**

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
docs: record the pid_sp guard-window measurement
MSGEOF
```

---

## Phase 7 — Fidelity

### Task 14: Human checkpoint, then recapture

**Files:**
- Modify: the committed baselines under `web-react/tests/e2e/`

Descriptions change page height, so baselines will break. **The order is the point.** Recapturing first bakes in whatever the reviewer was about to object to, and a green gate then becomes the evidence that it was fine.

- [ ] **Step 1: Confirm a backend is running before trusting a red baseline**

A missing backend adds a banner that shifts every landmark by 50/82px and reads as a layout regression. Broken assets also hide sizing bugs — a failed `<img>` honours width/height attributes while a loaded one obeys CSS. Check the backend is up before diagnosing anything.

- [ ] **Step 2: Human checkpoint**

Show the affected settings and wizard pages. Get sign-off that the descriptions read correctly and the layout is right. Do not proceed on your own judgement.

- [ ] **Step 3: Recapture, then verify determinism**

Recapture, then capture a second time and confirm the files are byte-identical. The `history` spec already stubs `/api/files/cookfiles` for exactly this reason; a baseline that differs between two captures is not a baseline.

- [ ] **Step 4: Re-run the touched web tests in the main checkout**

Agent worktrees have no Chromium and silently SKIP `[chromium]` tests. Re-run `tests/web/*.py` touched by this plan in the main checkout before merging, or the gate proved nothing.

- [ ] **Step 5: Commit**

```bash
# Your edits are ALREADY in @ -- you began this task with `jj new -m`.
# Verify the change holds only this task's files, then finalise its message.
jj st
jj describe --stdin <<'MSGEOF'
test: recapture fidelity baselines after the description slots
MSGEOF
```

---

## Parallelization

Concurrency needs **isolated jj workspaces**, not merely disjoint files — two sessions in one workspace will interleave commits.

| Group | Tasks | Notes |
|---|---|---|
| **Serial spine** | 1 → 2 → 3 → 4 → 5 | Each builds directly on the last. One worker. |
| **Independent A** | 6 → 7 | Backend then frontend. Own workspace. |
| **Independent B** | 8 | Manifest. Own workspace. Touches `blueprints/spa/`, `index.html`. |
| **Independent C** | 10 | Discovery. Own workspace. |
| **Blocked** | 9 | Touches `WorkModeTab`; must follow Tasks 11-12, which delete two of its controls. |
| **Serial tail** | 11 → 12 → 13 | Retirements then validation. Must follow the spine (Task 4 touches `WorkModeTab` too). |
| **Last** | 14 | Needs 5 and 12 landed. |

Groups A, B and C can run alongside the spine. Tasks 9, 11, 12 and 14 all touch `WorkModeTab.tsx`, so they are strictly ordered.

Reviews must be scoped to **specific commit SHAs**, not the branch diff — several sessions commit to this branch concurrently.
