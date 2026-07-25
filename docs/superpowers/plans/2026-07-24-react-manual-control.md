# React Manual Output Control Implementation Plan

> **SHIPPED.** Verified against live code 2026-07-25: `buttonsForMode.ts:56` has
> the `Manual` branch returning the four output toggles, `PwmEntry.tsx` exists,
> and `roundtrip.spec.ts`'s "manual mode exposes the output relays and toggles
> one end to end" drives a real relay through the API and passes.
>
> The step checkboxes below were never ticked, so this read as outstanding work
> and the migration backlog listed `manual` as un-migrated for a day longer than
> it should have. The boxes are left as they are rather than back-filled — this
> banner is the accurate record.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate PiFire's manual output control (power / igniter / auger / fan relays + DC-fan PWM duty) into the React dashboard, replacing the Flask `manual` page and its site-wide control-panel buttons.

**Architecture:** Manual outputs become the dashboard's **mode-button row** — `buttonsForMode(dash)` gains a `Manual` branch returning the four output toggles plus Stop, reusing the existing button grid. A button renders `accent` when its output is live (mirroring legacy's `btn-primary` vs `btn-outline-primary`). DC-fan duty gets a `PwmEntry` overlay modeled on the existing `SetpointEntry`. The socketio dash payload gains `outputs.power` and `manualPwm` for full parity.

**Tech Stack:** Flask + Flask-SocketIO (Python 3.14), React 19 + TypeScript (TS7/tsgo), rsbuild, `@rstest/core`, `@testing-library/react` 16, Playwright, Biome. Package manager: **bun**.

## Design decisions (user-approved 2026-07-24)

- **D1 — Manual controls live on the Dashboard, as the button row; there is NO `/manual` route.** The dashboard stage is a **fixed 1280×720 box** scaled to fit (`.pf-stage`, targeting an 800×480 touchscreen), laid out as flex rows summing to exactly 720 — there is no spare vertical space, so a bolted-on panel would break the layout at small sizes. Reusing the existing `gridAutoFlow: column` button row (which already renders 5 buttons in active-cook modes) costs zero layout change. **Consequence the user accepted:** a bookmarked `/manual` URL will not resolve in the React app.
- **D2 — Full parity.** Add `power` to the socketio payload's `outputs` and expose `control["manual"]["pwm"]` as `manualPwm`, so all four legacy buttons work and the duty entry shows its real current value.
- **D3 — Safety gate matches legacy exactly.** Legacy shows the manual buttons **only** when `mode == Manual`, even though `_cmd_set_manual` would also permit toggles when `settings["safety"]["allow_manual_changes"]` is true. Do NOT widen this: `allowManualOutputs` stays unused in the UI. Manual output controls appear only in Manual mode.

## Global Constraints

- **bun, not npm** for all web-react install/run.
- **Testing API is `@rstest/core`** (`rs.fn`/`rs.mock`) — NOT vitest/`vi`. `.test.tsx` runs in jsdom.
- **`bun run lint` must be run and exit 0** in every task (Biome enforces format; `bunx biome check --write <file>` if needed). Two pre-existing `react-refresh` **warnings** (`App.tsx`, `WizardShell.tsx`) are acceptable; **errors** are not.
- **`bun run typecheck`** (TS7, `noUnusedLocals`) must stay clean.
- **Coverage ≥75% lines per changed file.**
- **Python:** `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`; run `uvx ruff format` on changed Python before committing. PEP 758 bare-tuple `except A, B` is canonical — do NOT rewrite.
- **Do not modify the Flask `manual` blueprint or its template/JS.** The legacy page keeps working until it is retired separately.
- **jj boundary protocol:** the controller runs `jj new` before each dispatch; the implementer finalizes with a single `jj desc -m`.

## Parallelization

Dependency graph:

```
T1 (backend payload + React types + fixture)  ─┐
                                               ├─→ T3 (buttonsForMode + PwmEntry + ControlButtons) ─→ T4 (e2e + full gate)
T2 (command client methods)                   ─┘
```

- **Wave 1 — dispatch T1 and T2 CONCURRENTLY.** They are fully independent: T1 touches Python + `types.ts` + `fixture.ts`; T2 touches `command.ts` + its test. No file overlap.
- **Wave 2 — T3** consumes both (it needs `dash.outputs.power`/`dash.manualPwm` from T1 and `manualOutput`/`manualPwm` from T2). Single task, so no contention.
- **Wave 3 — T4** (e2e + full gate) after T3.
- **Reviews parallelize** (read-only): review T1 and T2 concurrently.

Isolated jj workspaces are **mandatory** for wave 1 — two agents sharing one working copy cross-pollute commits regardless of which files they touch:

```bash
jj workspace add --name mc1 -r <plan-commit> ../PiFire-mc1   # T1
jj workspace add --name mc2 -r <plan-commit> ../PiFire-mc2   # T2
# then in each: cd ../PiFire-mcN/web-react && bun install   (node_modules is
# gitignored, ~200MB per workspace — pre-warm before dispatching)
```

**Behavioural-reach caution (bitten twice this session):** file-disjointness is necessary but NOT sufficient. T1 changes the `DashData` shape, which every dashboard test fixture flows through — if `fixture.ts` is not updated in the same task, unrelated suites break. T1 therefore owns `fixture.ts`, and the **merged state must be verified** at integration because no single task tests the combination.

**Integration (controller):** `jj workspace forget mc1 mc2` → linearize with **change ids** → verify merged state (`typecheck && lint && test && build`) → e2e in the main checkout (HUP-reload gunicorn first) → `rm -rf ../PiFire-mc{1,2}` → update ledger + backlog.

---

### Task 1: socketio payload parity + React types

**Files:**
- Modify: `blueprints/mobile/socket_io.py` (the `_get_dash_data` payload)
- Modify: `web-react/src/helpers/types.ts`
- Modify: `web-react/src/helpers/fixture.ts`
- Test: `tests/web/test_socketio_app_data.py`

**Interfaces:**
- Consumes: `status["outpins"]` — a dict with keys `auger`, `fan`, `igniter`, **`power`** (all bool); `control["manual"]` — `{"change": ..., "pwm": int}` (default `pwm: 100`).
- Produces: dash payload gains `outputs.power: bool` and top-level `manualPwm: int`; `DashData.outputs` gains `power: boolean`; `DashData` gains `manualPwm: number`.

- [ ] **Step 1: Write the failing backend test**

Add to `tests/web/test_socketio_app_data.py` (read the file first — it has an `sio` fixture exposing the module as `sio.mod`, and helpers `read_settings()` / `read_pellets_store()` already imported):

```python
def test_dash_data_exposes_manual_power_and_pwm(sio):
    """The React manual controls need the power relay's live state and the
    current DC-fan duty. Legacy's control panel reads both (status['outpins']
    has all four pins; the PWM slider is seeded from control['manual']['pwm']),
    but the socketio dash payload only carried fan/auger/igniter."""
    dash = sio.mod._get_dash_data(read_settings(), read_pellets_store())

    assert set(dash["outputs"]) == {"fan", "auger", "igniter", "power"}
    assert isinstance(dash["outputs"]["power"], bool)
    assert isinstance(dash["manualPwm"], int)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socketio_app_data.py::test_dash_data_exposes_manual_power_and_pwm -v`
Expected: FAIL — `outputs` lacks `power` and there is no `manualPwm` key.

- [ ] **Step 3: Extend the payload**

In `blueprints/mobile/socket_io.py`, the `outputs` block currently reads:

```python
        "outputs": {
            "fan": status["outpins"]["fan"],
            "auger": status["outpins"]["auger"],
            "igniter": status["outpins"]["igniter"],
        },
```

Change it to include the power relay:

```python
        "outputs": {
            "fan": status["outpins"]["fan"],
            "auger": status["outpins"]["auger"],
            "igniter": status["outpins"]["igniter"],
            # The manual control panel toggles a power relay too (platform
            # outputs.power); the dashboard needs its live state to render the
            # button's on/off styling.
            "power": status["outpins"]["power"],
        },
```

and add `manualPwm` next to the existing `pwmControl` entry (which currently reads `"pwmControl": control["pwm_control"],`):

```python
        "pwmControl": control["pwm_control"],
        # Current manual DC-fan duty cycle (0-100), so the duty entry opens on
        # the real value rather than a guess. Distinct from pwmControl, which
        # is the automatic PWM-control enable flag.
        "manualPwm": control["manual"]["pwm"],
```

- [ ] **Step 4: Run the backend test to verify it passes**

Run: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socketio_app_data.py -v`
Expected: PASS (the new test plus every pre-existing test in the file).

- [ ] **Step 5: Update the React types and fixture**

In `web-react/src/helpers/types.ts`, extend the `DashData` interface — the `outputs` line currently reads `outputs: { fan: boolean; auger: boolean; igniter: boolean };`:

```typescript
  allowManualOutputs: boolean;
  manualPwm: number;
  timer: { start: number; paused: number; end: number; keepWarm: boolean; shutdown: boolean };
  outputs: { fan: boolean; auger: boolean; igniter: boolean; power: boolean };
```

In `web-react/src/helpers/fixture.ts`, add matching values so every consumer of the fixture keeps type-checking. Find the `outputs:` entry and the `allowManualOutputs:` entry and make them:

```typescript
  allowManualOutputs: false,
  manualPwm: 100,
```
and add `power: false` to the fixture's `outputs` object (keep its existing fan/auger/igniter values unchanged).

- [ ] **Step 6: Typecheck + find any other full-DashData literals**

Run: `cd /home/dannyb/sources/PiFire-mc1/web-react && bun run typecheck`
Expected: clean. `DashData` gained two REQUIRED fields, so any other object literal typed as a full `DashData` must also be updated. If typecheck reports missing `manualPwm`/`power` anywhere, fix those literals too (grep `grep -rln "allowManualOutputs" src` to find them).

- [ ] **Step 7: Full gate**

Run: `cd /home/dannyb/sources/PiFire-mc1/web-react && bun run typecheck && bun run lint && bun run test`
then: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_socketio_app_data.py -v`
Expected: all PASS.

- [ ] **Step 8: Format and commit**

```bash
uvx ruff format blueprints/mobile/socket_io.py tests/web/test_socketio_app_data.py
git add blueprints/mobile/socket_io.py tests/web/test_socketio_app_data.py web-react/src/helpers/types.ts web-react/src/helpers/fixture.ts
git commit -m "feat(api): expose manual power relay and DC-fan duty in the dash payload"
```

---

### Task 2: command-client methods for manual output control

**Files:**
- Modify: `web-react/src/helpers/command.ts`
- Test: `web-react/src/helpers/command.test.ts`

**Interfaces:**
- Consumes: the existing private `post(baseUrl, segments)` helper and `buildCommandUrl`.
- Produces:
  ```typescript
  export type GrillMode = "startup" | "smoke" | "shutdown" | "stop" | "monitor" | "reignite" | "manual";
  export type ManualOutput = "power" | "igniter" | "auger" | "fan";
  // on CommandClient:
  manualOutput(output: ManualOutput, action?: "toggle" | "true" | "false"): Promise<CommandResult>;
  manualPwm(duty: number): Promise<CommandResult>;
  ```

**Backend grammar being targeted** (`common/api_commands.py` `_cmd_set_manual`):
`/api/set/manual/{power|igniter|auger|fan}/{true|false|toggle}` and `/api/set/manual/pwm/{0-100}`.
Entering/leaving manual mode uses the existing `setMode`: `/api/set/mode/manual` and `/api/set/mode/stop`.

- [ ] **Step 1: Write the failing tests**

Add to `web-react/src/helpers/command.test.ts` (read it first and mirror its existing fetch-mocking style):

```typescript
  test("manualOutput posts the toggle command for an output", async () => {
    globalThis.fetch = rs
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ result: "OK", message: "" }) }) as never;
    const c = createCommand("");
    const r = await c.manualOutput("auger");
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain(
      "/api/set/manual/auger/toggle",
    );
    expect(r.ok).toBe(true);
  });

  test("manualOutput accepts an explicit true/false action", async () => {
    globalThis.fetch = rs
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ result: "OK", message: "" }) }) as never;
    await createCommand("").manualOutput("power", "false");
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain(
      "/api/set/manual/power/false",
    );
  });

  test("manualPwm posts a rounded, clamped duty cycle", async () => {
    globalThis.fetch = rs
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ result: "OK", message: "" }) }) as never;
    const c = createCommand("");
    await c.manualPwm(42.6);
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain(
      "/api/set/manual/pwm/43",
    );
    await c.manualPwm(150);
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[1][0]).toContain(
      "/api/set/manual/pwm/100",
    );
    await c.manualPwm(-5);
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[2][0]).toContain(
      "/api/set/manual/pwm/0",
    );
  });

  test("setMode supports manual", async () => {
    globalThis.fetch = rs
      .fn()
      .mockResolvedValue({ ok: true, json: async () => ({ result: "OK", message: "" }) }) as never;
    await createCommand("").setMode("manual");
    expect((globalThis.fetch as ReturnType<typeof rs.fn>).mock.calls[0][0]).toContain(
      "/api/set/mode/manual",
    );
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/dannyb/sources/PiFire-mc2/web-react && bun run test src/helpers/command.test.ts`
Expected: FAIL — `manualOutput`/`manualPwm` do not exist; `setMode("manual")` is a type error.

- [ ] **Step 3: Implement**

In `web-react/src/helpers/command.ts`, extend the mode union and add the output type:

```typescript
export type GrillMode =
  | "startup"
  | "smoke"
  | "shutdown"
  | "stop"
  | "monitor"
  | "reignite"
  | "manual";
export type SystemCmd = "reboot" | "shutdown" | "restart";
export type ManualOutput = "power" | "igniter" | "auger" | "fan";
```

Add to the `CommandClient` interface (after `setUnits`):

```typescript
  manualOutput(output: ManualOutput, action?: "toggle" | "true" | "false"): Promise<CommandResult>;
  manualPwm(duty: number): Promise<CommandResult>;
```

and to the object returned by `createCommand` (after `setUnits`):

```typescript
    manualOutput: (output, action = "toggle") => post(baseUrl, ["set", "manual", output, action]),
    // The backend takes an integer 0-100; clamp here so a slider/entry can't
    // send an out-of-range duty that the API would reject.
    manualPwm: (duty) =>
      post(baseUrl, ["set", "manual", "pwm", Math.min(100, Math.max(0, Math.round(duty)))]),
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd /home/dannyb/sources/PiFire-mc2/web-react && bun run test src/helpers/command.test.ts`
Expected: PASS (new tests plus all pre-existing ones).

- [ ] **Step 5: Full gate**

Run: `cd /home/dannyb/sources/PiFire-mc2/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add web-react/src/helpers/command.ts web-react/src/helpers/command.test.ts
git commit -m "feat(web-react): add manual output + PWM commands to the command client"
```

---

### Task 3: Manual button row + PWM entry on the dashboard

**Files:**
- Modify: `web-react/src/helpers/dashboard/buttonsForMode.ts`
- Test: `web-react/src/helpers/dashboard/buttonsForMode.test.ts`
- Create: `web-react/src/components/dashboard/PwmEntry.tsx`
- Create: `web-react/src/components/dashboard/PwmEntry.test.tsx`
- Modify: `web-react/src/components/dashboard/ControlButtons.tsx`
- Test: `web-react/src/components/dashboard/ControlButtons.test.tsx`

**Interfaces:**
- Consumes (T1): `dash.outputs.power`, `dash.manualPwm`, `dash.hasDcFan`, `dash.currentMode`. (T2): `command.manualOutput(output, action?)`, `command.manualPwm(duty)`, `setMode("manual")`.
- Produces: `ButtonAction` gains `{ type: "pwm" }`; `buttonsForMode` handles `"Manual"`; `PwmEntry({ open, initial, onSubmit, onCancel })`.

- [ ] **Step 1: Write the failing `buttonsForMode` tests**

Add to `web-react/src/helpers/dashboard/buttonsForMode.test.ts` (mirror its existing fixture usage — it builds a `DashData` from `FIXTURE_DASH`):

```typescript
  it("offers a Manual entry button when idle", () => {
    const buttons = buttonsForMode({ ...FIXTURE_DASH, currentMode: "Stop" });
    expect(buttons.map((b) => b.label)).toContain("Manual");
  });

  it("in Manual mode shows the four output toggles and Stop", () => {
    const buttons = buttonsForMode({ ...FIXTURE_DASH, currentMode: "Manual", hasDcFan: false });
    expect(buttons.map((b) => b.label)).toEqual([
      "Power",
      "Igniter",
      "Auger",
      "Fan",
      "Stop",
    ]);
  });

  it("marks an output button accent while that output is live", () => {
    const buttons = buttonsForMode({
      ...FIXTURE_DASH,
      currentMode: "Manual",
      hasDcFan: false,
      outputs: { fan: false, auger: true, igniter: false, power: false },
    });
    const byLabel = Object.fromEntries(buttons.map((b) => [b.label, b]));
    expect(byLabel.Auger.variant).toBe("accent");
    expect(byLabel.Fan.variant).toBeUndefined();
  });

  it("adds a Fan % button only when the platform has a DC fan", () => {
    const withFan = buttonsForMode({ ...FIXTURE_DASH, currentMode: "Manual", hasDcFan: true });
    expect(withFan.map((b) => b.label)).toContain("Fan %");
    const withoutFan = buttonsForMode({ ...FIXTURE_DASH, currentMode: "Manual", hasDcFan: false });
    expect(withoutFan.map((b) => b.label)).not.toContain("Fan %");
  });

  it("does not show manual outputs outside Manual mode even if manual changes are allowed", () => {
    // Legacy hides these unless mode == Manual, despite _cmd_set_manual also
    // permitting toggles when safety.allow_manual_changes is true. Match that.
    const buttons = buttonsForMode({
      ...FIXTURE_DASH,
      currentMode: "Smoke",
      allowManualOutputs: true,
    });
    expect(buttons.map((b) => b.label)).not.toContain("Auger");
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/helpers/dashboard/buttonsForMode.test.ts`
Expected: FAIL — no Manual handling; `"Manual"` mode currently falls through to the active-cook branch.

- [ ] **Step 3: Implement the Manual branch**

In `web-react/src/helpers/dashboard/buttonsForMode.ts`, add `"pwm"` to the action union:

```typescript
export type ButtonAction =
  | { type: "command"; run(c: CommandClient): Promise<CommandResult> }
  | { type: "setpoint" }
  | { type: "pwm" }
  | { type: "confirm"; title: string; run(c: CommandClient): Promise<CommandResult> };
```

Add a Manual branch to `buttonsForMode`, immediately BEFORE the final active-cook `return` (so Manual no longer falls through):

```typescript
  // Manual mode: the button row becomes the output control panel, mirroring
  // legacy's control-panel buttons (accent == relay energised, matching its
  // btn-primary vs btn-outline-primary styling). Shown ONLY in Manual mode --
  // legacy hides these outside it even when safety.allow_manual_changes is set.
  if (mode === "Manual") {
    const toggle = (label: string, output: ManualOutput, live: boolean): ControlButton => ({
      label,
      variant: live ? "accent" : undefined,
      action: cmd((c) => c.manualOutput(output)),
    });
    return [
      toggle("Power", "power", dash.outputs.power),
      toggle("Igniter", "igniter", dash.outputs.igniter),
      toggle("Auger", "auger", dash.outputs.auger),
      toggle("Fan", "fan", dash.outputs.fan),
      ...(dash.hasDcFan ? [{ label: "Fan %", action: { type: "pwm" } as ButtonAction }] : []),
      STOP,
    ];
  }
```

Add `Manual` to the idle branch so the mode is reachable — the `Stop`/`Error`/`""` branch becomes:

```typescript
    return [
      STARTUP,
      { label: "Prime", action: cmd((c) => c.prime(dash.primeAmount || 10, "startup")) },
      { label: "Monitor", action: cmd((c) => c.setMode("monitor")) },
      { label: "Manual", action: cmd((c) => c.setMode("manual")) },
    ];
```

and import the output type at the top:

```typescript
import type { CommandClient, CommandResult, ManualOutput } from "../command";
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/helpers/dashboard/buttonsForMode.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the failing `PwmEntry` test**

Create `web-react/src/components/dashboard/PwmEntry.test.tsx`, mirroring `SetpointEntry.test.tsx`'s style (read it first):

```tsx
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PwmEntry } from "./PwmEntry";

afterEach(cleanup);

describe("PwmEntry", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <PwmEntry open={false} initial={50} onSubmit={rs.fn()} onCancel={rs.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("seeds from the current duty and submits the chosen value", () => {
    const onSubmit = rs.fn();
    render(<PwmEntry open initial={40} onSubmit={onSubmit} onCancel={rs.fn()} />);
    const slider = screen.getByRole("slider", { name: /fan duty/i });
    expect(slider).toHaveValue("40");
    fireEvent.change(slider, { target: { value: "75" } });
    fireEvent.click(screen.getByRole("button", { name: /set/i }));
    expect(onSubmit).toHaveBeenCalledWith(75);
  });

  it("cancels without submitting", () => {
    const onSubmit = rs.fn();
    const onCancel = rs.fn();
    render(<PwmEntry open initial={40} onSubmit={onSubmit} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalled();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 6: Implement `PwmEntry`**

Create `web-react/src/components/dashboard/PwmEntry.tsx`. Read `SetpointEntry.tsx` first and mirror its modal markup/classes (`pf-modal-scrim`, `pf-modal-btn`, the stopPropagation on the inner panel, and its `prevSeedKey` re-seed idiom — house style forbids `setState` in `useEffect`):

```tsx
import { useState } from "react";

interface Props {
  open: boolean;
  initial: number;
  onSubmit(duty: number): void;
  onCancel(): void;
}

const clampDuty = (n: number) => Math.min(100, Math.max(0, Math.round(n)));

// DC-fan duty entry for Manual mode. Modeled on SetpointEntry: the dashboard
// stage is a fixed 1280x720 box with no spare room for an inline slider, so
// duty is edited in an overlay.
export function PwmEntry({ open, initial, onSubmit, onCancel }: Props) {
  const [duty, setDuty] = useState(() => clampDuty(initial));
  // Re-seed when reopened on a new value -- render-phase adjustment, NOT an
  // effect (React Compiler rejects setState-in-effect; no suppressions).
  const seedKey = `${open}:${initial}`;
  const [prevSeedKey, setPrevSeedKey] = useState(seedKey);
  if (seedKey !== prevSeedKey) {
    setPrevSeedKey(seedKey);
    if (open) setDuty(clampDuty(initial));
  }

  if (!open) return null;

  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">Fan Duty Cycle</div>
        <label className="pf-field">
          <span className="pf-field-label">Fan duty</span>
          <input
            type="range"
            min={0}
            max={100}
            step={1}
            value={duty}
            onChange={(e) => setDuty(clampDuty(Number(e.target.value)))}
          />
        </label>
        <div className="pf-modal-value">{duty}%</div>
        <div className="pf-modal-actions">
          <button type="button" className="pf-modal-btn" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="pf-modal-btn accent" onClick={() => onSubmit(duty)}>
            Set
          </button>
        </div>
      </div>
    </div>
  );
}
```

Adjust the wrapper class names to whatever `SetpointEntry.tsx` actually uses (match it exactly — do not invent new CSS classes; if `SetpointEntry` uses different element structure, mirror that). The accessible name for the slider must make `getByRole("slider", { name: /fan duty/i })` resolve.

- [ ] **Step 7: Wire it into `ControlButtons`**

In `web-react/src/components/dashboard/ControlButtons.tsx`: import `PwmEntry`, add state, handle the new action type, and render the overlay next to `SetpointEntry`/`ConfirmAction`:

```tsx
  const [pwmOpen, setPwmOpen] = useState(false);
```

in `onClick`:

```tsx
  const onClick = (action: ButtonAction) => {
    if (action.type === "command") fire(action.run);
    else if (action.type === "setpoint") setSetpointOpen(true);
    else if (action.type === "pwm") setPwmOpen(true);
    else setConfirm({ title: action.title, run: action.run });
  };
```

and alongside the other overlays:

```tsx
      <PwmEntry
        open={pwmOpen}
        initial={dash.manualPwm}
        onCancel={() => setPwmOpen(false)}
        onSubmit={(duty) => {
          setPwmOpen(false);
          fire((c) => c.manualPwm(duty));
        }}
      />
```

- [ ] **Step 8: Add a ControlButtons integration test**

Add to `web-react/src/components/dashboard/ControlButtons.test.tsx` (mirror its existing command-mock style):

```tsx
  it("fires a manual output toggle from the Manual button row", async () => {
    const manualOutput = rs.fn().mockResolvedValue({ ok: true, message: "" });
    render(
      <ControlButtons
        dash={{ ...FIXTURE_DASH, currentMode: "Manual", hasDcFan: false }}
        command={{ ...stubCommand, manualOutput } as never}
        disabled={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Auger" }));
    await waitFor(() => expect(manualOutput).toHaveBeenCalledWith("auger"));
  });

  it("opens the PWM entry and submits a duty cycle", async () => {
    const manualPwm = rs.fn().mockResolvedValue({ ok: true, message: "" });
    render(
      <ControlButtons
        dash={{ ...FIXTURE_DASH, currentMode: "Manual", hasDcFan: true, manualPwm: 40 }}
        command={{ ...stubCommand, manualPwm } as never}
        disabled={false}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Fan %" }));
    fireEvent.change(screen.getByRole("slider", { name: /fan duty/i }), {
      target: { value: "75" },
    });
    fireEvent.click(screen.getByRole("button", { name: /set/i }));
    await waitFor(() => expect(manualPwm).toHaveBeenCalledWith(75));
  });
```

Use whatever stub-command helper the file already defines (read it first — name and shape must match; if it has none, build a minimal object with the methods used).

- [ ] **Step 9: Run the touched suites, then the full gate**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run test src/helpers/dashboard/buttonsForMode.test.ts src/components/dashboard/PwmEntry.test.tsx src/components/dashboard/ControlButtons.test.tsx`
then: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint && bun run test`
Expected: all PASS. The full suite matters — `buttonsForMode` drives the dashboard's button row, so `Dashboard`/`App` tests exercise it.

- [ ] **Step 10: Commit**

```bash
git add web-react/src/helpers/dashboard/buttonsForMode.ts web-react/src/helpers/dashboard/buttonsForMode.test.ts web-react/src/components/dashboard/PwmEntry.tsx web-react/src/components/dashboard/PwmEntry.test.tsx web-react/src/components/dashboard/ControlButtons.tsx web-react/src/components/dashboard/ControlButtons.test.tsx
git commit -m "feat(web-react): manual output control on the dashboard button row"
```

---

### Task 4: e2e + full gate

**Files:**
- Modify: `web-react/tests/e2e/dashboard.spec.ts` (or the closest existing dashboard e2e spec — read `web-react/tests/e2e/` and pick the right file)

- [ ] **Step 1: Read the existing e2e conventions**

Run: `ls web-react/tests/e2e/ && sed -n '1,40p' web-react/tests/e2e/dashboard.spec.ts`
Note the file's leave-no-trace convention (the wizard specs clear their draft; a dashboard spec that changes grill MODE must restore it).

- [ ] **Step 2: Add a manual-mode e2e test**

Append a test that: loads `/`, enters Manual via the Manual button, asserts the four output buttons render, toggles one and asserts it reports back (the socket pushes `outputs`), then **restores state by pressing Stop** so the grill is left as found. Guard it so it only runs when the backend is in `Stop` mode to begin with — do not fight a real cook. Model the API assertions on the wizard specs' `page.request.get(...)` style, e.g. verify via `/api/get/mode` that the mode returned to `Stop`.

**SAFETY:** this test drives real relay outputs on real hardware. It must only run against the dev/prototype backend (`platform.real_hw` false in the dev harness). Add a comment stating that, and keep the toggle brief (toggle on, assert, toggle off).

- [ ] **Step 3: Run the e2e (main checkout)**

Ensure the Flask backend serves current code (HUP-reload gunicorn if it predates Task 1's payload change; confirm `/api/current` or the socket payload now carries `outputs.power`).
Run: `cd /home/dannyb/sources/PiFire/web-react && bunx playwright test --reporter=line`
Expected: PASS (all specs).

- [ ] **Step 4: Full gate**

Run: `cd /home/dannyb/sources/PiFire/web-react && bun run typecheck && bun run lint && bun run test && bun run build`
then: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add web-react/tests/e2e/
git commit -m "test(web-react): e2e for manual output control"
```

---

## Self-Review

**1. Spec coverage:** D1 (dashboard button row, no `/manual` route) → T3 ✅. D2 (power + manualPwm parity) → T1 ✅. D3 (safety matches legacy — manual buttons only in Manual mode) → T3, pinned by an explicit test ✅. Command grammar → T2 ✅. e2e → T4 ✅.

**2. Placeholder scan:** every code step carries complete code. Four steps say "read the file first and mirror its existing helper/style" (T2 Step 1 fetch mocking, T3 Steps 5/6/8 SetpointEntry markup + stub-command helper, T4 Step 2 e2e conventions) — each names the exact file and is accompanied by concrete code to adapt. These are local conventions the implementer must match rather than duplicate, and inventing new CSS classes or a parallel stub helper would be worse than matching.

**3. Type consistency:** `ManualOutput` is defined in `command.ts` (T2) and imported by `buttonsForMode.ts` (T3). `manualOutput(output, action?)` and `manualPwm(duty)` signatures are identical in T2's definition and T3's call sites. `dash.outputs.power` / `dash.manualPwm` are added in T1 and consumed in T3. `ButtonAction` gains `{type:"pwm"}` in T3 and is handled in the same task's `ControlButtons` switch. `GrillMode` gains `"manual"` in T2, used by T3's idle-branch `setMode("manual")`.
