# React WLED preset/profile editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the React Settings → Notifications → WLED card to parity with Flask's WLED card — the two mode toggles, the suggested-config block, the 12-row profile-number grid, and the three network action buttons (Discover / Push / Test).

**Architecture:** A new self-contained `WledCard` component owns the whole WLED card UI and its local action/status state; a new typed client `wledApi.ts` speaks the three existing `/api/wled_*` endpoints. `NotificationsTab` swaps its inline 3-field WLED `<Section>` for `<WledCard>` and is otherwise unchanged — Save still posts the whole `notify_services` subtree with the `["settings_update"]` flag.

**Tech Stack:** React + TypeScript, `@rstest/core` (unit, `rs.fn`/`rs.mock`/`rs.stubGlobal`), `@testing-library/react`, Playwright (e2e), Biome (lint/format), bun (package manager + scripts).

**Spec:** `docs/superpowers/specs/2026-07-28-react-wled-editor-design.md`

## Global Constraints

- **Port, don't invent.** Match Flask's WLED card; where a behavior is a judgment call, Flask's behavior governs. `mode_presets`/`event_presets` are NOT rendered (zero refs in Flask's template) but MUST survive Save byte-identical.
- **web-react tooling is `bun`.** Every task's final gate runs, from `web-react/`: `bun run typecheck`, `bun run lint` (Biome), `bun run test` (unit). e2e via `bunx playwright test` where noted.
- **Chromium e2e specs skip in agent worktrees.** The e2e task's Playwright run must be re-run in the main checkout before merge; note it, don't treat a skip as a pass.
- **Buttons act on live, unsaved draft state** (current `device_address` + current `profile_numbers`) — no Save required, matching Flask.
- **Field numeric bounds** mirror the schema (`profile_numbers` 1–250, `idle_brightness` 1–100, `led_count` 1–1000, `notify_duration` ≥0); `write_settings()`'s strict gate on the merged tree stays the sole authority.
- **CSS gates:** every new `pf-*` class used gets a matching rule in `src/components/settings/settings.css` in the SAME task (`cssCoverage.test.ts`), every rule has a consumer, and NONE of the new classes go on the `UNSTYLED` allowlist in `styleCoverage.test.ts`.
- Commit with the repo's VCS (jj colocated). Each task ends with one commit.

## File Structure

- **Create** `web-react/src/helpers/notify/wledApi.ts` — typed client for `/api/wled_discover`, `/api/wled_push_profiles`, `/api/wled_test_profile`. Returns typed result envelopes; never throws on `result: "error"`; synthesizes an error result on network/parse failure. (Task 1)
- **Create** `web-react/src/helpers/notify/wledApi.test.ts` — unit test for the client. (Task 1)
- **Create** `web-react/src/components/settings/tabs/notifications/WledCard.tsx` — the WLED card. Editor fields land in Task 2; buttons/status/discovery in Task 3.
- **Create** `web-react/src/components/settings/tabs/notifications/WledCard.test.tsx` — card unit tests (fields in Task 2, buttons in Task 3).
- **Modify** `web-react/src/components/settings/tabs/NotificationsTab.tsx` — replace the inline WLED `<Section>` (currently ~lines 315–331) with `<WledCard>`. (Task 2)
- **Modify** `web-react/src/components/settings/settings.css` — new classes: `pf-wled-grid`, `pf-wled-subhead` (Task 2); `pf-wled-results`, `pf-wled-result-row`, `pf-wled-status` (Task 3).
- **Create** `web-react/tests/e2e/wled-editor.spec.ts` — route-mocked end-to-end. (Task 4)

## Existing primitives (reuse — do not reinvent)

From `web-react/src/components/settings/fields/`:
- `Section` — `{ title: string; children: ReactNode }`
- `Toggle` — `{ label; checked: boolean; onChange: (v: boolean) => void; disabled? }`
- `NumberField` — `{ label; value: number; onChange: (v: number) => void; min?; max?; step?; suffix?; hint?; disabled? }` (clamps to min/max on blur)
- `TextField` — `{ label; value: string; onChange: (v: string) => void; ... }`
- `Select` — `{ label; value: string; options: {value: string; label: string}[]; onChange: (v: string) => void }`

Button class: reuse `pf-modal-btn` (and `pf-modal-btn accent` for the primary), as `ProbesTab.tsx` does. No new button class.

---

### Task 1: `wledApi.ts` typed client

**Files:**
- Create: `web-react/src/helpers/notify/wledApi.ts`
- Test: `web-react/src/helpers/notify/wledApi.test.ts`

**Interfaces:**
- Consumes: nothing (leaf module). Uses `import.meta.env.PUBLIC_PIFIRE_URL` like `helpers/files/cookfileApi.ts`.
- Produces (Task 3 consumes these exact names/types):
  - `interface WledDevice { ip: string; led_count: number; name: string }`
  - `interface WledDiscoverResult { result: "success" | "error"; message: string; devices: WledDevice[] }`
  - `interface WledActionResult { result: "success" | "error"; message: string; profiles_pushed?: number }`
  - `discoverWled(timeoutSec?: number): Promise<WledDiscoverResult>`
  - `pushWledProfiles(deviceAddress: string, profileNumbers: Record<string, number>): Promise<WledActionResult>`
  - `testWledProfile(deviceAddress: string, profileNumber: number): Promise<WledActionResult>`

- [ ] **Step 1: Write the failing test**

Create `web-react/src/helpers/notify/wledApi.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { discoverWled, pushWledProfiles, testWledProfile } from "./wledApi";

describe("wledApi", () => {
  let fetchMock: ReturnType<typeof rs.fn>;
  const jsonRes = (body: unknown, ok = true, status = 200) => ({
    ok,
    status,
    json: async () => body,
  });

  beforeEach(() => {
    fetchMock = rs.fn();
    rs.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => rs.unstubAllGlobals());

  it("discoverWled GETs /api/wled_discover with the timeout and returns devices", async () => {
    fetchMock.mockResolvedValue(
      jsonRes({ result: "success", message: "Found 1", devices: [{ ip: "10.0.0.5", led_count: 30, name: "WLED-A" }] }),
    );
    const res = await discoverWled(15);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/wled_discover?timeout=15");
    expect(res.result).toBe("success");
    expect(res.devices).toEqual([{ ip: "10.0.0.5", led_count: 30, name: "WLED-A" }]);
  });

  it("pushWledProfiles POSTs device_address + profile_numbers and returns profiles_pushed", async () => {
    fetchMock.mockResolvedValue(jsonRes({ result: "success", message: "ok", profiles_pushed: 12 }));
    const res = await pushWledProfiles("wled.local", { idle: 200, cooking: 203 });
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/wled_push_profiles");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({
      device_address: "wled.local",
      profile_numbers: { idle: 200, cooking: 203 },
    });
    expect(res.profiles_pushed).toBe(12);
  });

  it("testWledProfile POSTs device_address + profile_number", async () => {
    fetchMock.mockResolvedValue(jsonRes({ result: "success", message: "ok" }));
    await testWledProfile("wled.local", 203);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/wled_test_profile");
    expect(JSON.parse(opts.body)).toEqual({ device_address: "wled.local", profile_number: 203 });
  });

  it("returns the error envelope without throwing on result:error", async () => {
    fetchMock.mockResolvedValue(jsonRes({ result: "error", message: "device unreachable" }, false, 500));
    const res = await pushWledProfiles("bad", { idle: 200 });
    expect(res.result).toBe("error");
    expect(res.message).toBe("device unreachable");
  });

  it("synthesizes an error result when fetch rejects", async () => {
    fetchMock.mockRejectedValue(new Error("network down"));
    const res = await discoverWled();
    expect(res.result).toBe("error");
    expect(res.devices).toEqual([]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `web-react/`): `bun run test wledApi`
Expected: FAIL — `./wledApi` has no such exports.

- [ ] **Step 3: Write minimal implementation**

Create `web-react/src/helpers/notify/wledApi.ts`:

```ts
// Typed client for the /api/wled_* action surface (discover / push / test).
//
// Unlike the file endpoints (helpers/files/apiEnvelope.ts) these return a bare
// {result: "success"|"error", message, ...} envelope — result:"success" on a
// 200, and result:"error" either in a 200 body or alongside a 500 (see
// blueprints/api/routes.py:_api_get_wled_discover / _api_post_wled_push_profiles
// / _api_post_wled_test_profile). We therefore branch on the `result` field,
// not res.ok, and never throw on a result:"error": the card renders the message.
// A network/parse failure becomes a synthesized error result so the caller has
// one uniform shape to display.

const BASE_URL = import.meta.env.PUBLIC_PIFIRE_URL || "";

export interface WledDevice {
  ip: string;
  led_count: number;
  name: string;
}

export interface WledDiscoverResult {
  result: "success" | "error";
  message: string;
  devices: WledDevice[];
}

export interface WledActionResult {
  result: "success" | "error";
  message: string;
  profiles_pushed?: number;
}

export async function discoverWled(timeoutSec = 15): Promise<WledDiscoverResult> {
  try {
    const res = await fetch(`${BASE_URL}/api/wled_discover?timeout=${timeoutSec}`);
    const body = (await res.json().catch(() => ({}))) as Partial<WledDiscoverResult>;
    return {
      result: body.result === "success" ? "success" : "error",
      message: body.message ?? `HTTP ${res.status}`,
      devices: body.devices ?? [],
    };
  } catch {
    return { result: "error", message: "Could not reach PiFire to discover WLED devices.", devices: [] };
  }
}

export async function pushWledProfiles(
  deviceAddress: string,
  profileNumbers: Record<string, number>,
): Promise<WledActionResult> {
  try {
    const res = await fetch(`${BASE_URL}/api/wled_push_profiles`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_address: deviceAddress, profile_numbers: profileNumbers }),
    });
    const body = (await res.json().catch(() => ({}))) as Partial<WledActionResult>;
    return {
      result: body.result === "success" ? "success" : "error",
      message: body.message ?? `HTTP ${res.status}`,
      profiles_pushed: body.profiles_pushed,
    };
  } catch {
    return { result: "error", message: "Could not reach PiFire to push profiles." };
  }
}

export async function testWledProfile(deviceAddress: string, profileNumber: number): Promise<WledActionResult> {
  try {
    const res = await fetch(`${BASE_URL}/api/wled_test_profile`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_address: deviceAddress, profile_number: profileNumber }),
    });
    const body = (await res.json().catch(() => ({}))) as Partial<WledActionResult>;
    return {
      result: body.result === "success" ? "success" : "error",
      message: body.message ?? `HTTP ${res.status}`,
    };
  } catch {
    return { result: "error", message: "Could not reach PiFire to test the profile." };
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run (from `web-react/`): `bun run test wledApi`
Expected: PASS (5 tests).

- [ ] **Step 5: Typecheck, lint, commit**

Run: `bun run typecheck && bun run lint`
Expected: clean. Then commit `wledApi.ts` + `wledApi.test.ts` with message `feat(web-react): typed client for the /api/wled_* action surface`.

---

### Task 2: `WledCard` editor fields, wired into NotificationsTab

**Files:**
- Create: `web-react/src/components/settings/tabs/notifications/WledCard.tsx`
- Create: `web-react/src/components/settings/tabs/notifications/WledCard.test.tsx`
- Modify: `web-react/src/components/settings/tabs/NotificationsTab.tsx` (replace inline WLED `<Section>`)
- Modify: `web-react/src/components/settings/settings.css` (add `pf-wled-grid`, `pf-wled-subhead`)

**Interfaces:**
- Consumes: nothing from Task 1 yet (buttons come in Task 3).
- Produces: `WledCard` component with props `{ wled: Record<string, unknown>; onChange: (next: Record<string, unknown>) => void }`. NotificationsTab passes the `wled` service bag and stores the returned bag under `ns.wled`.

This task renders ONLY the editor fields (toggles, suggested-config, profile grid). No network buttons, no status region, no discovery list — those are Task 3.

- [ ] **Step 1: Write the failing test**

Create `web-react/src/components/settings/tabs/notifications/WledCard.test.tsx`:

```tsx
import { afterEach, describe, expect, it, rs } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WledCard } from "./WledCard";

afterEach(cleanup);

// A full wled bag, including the legacy mode_presets/event_presets keys the
// card must NOT render but MUST preserve on edit.
const wledFixture = () => ({
  enabled: true,
  device_address: "wled.local",
  use_profiles: true,
  use_suggested_presets: false,
  notify_duration: 120,
  profile_numbers: {
    idle: 200, booting: 201, preheat: 202, cooking: 203, cooldown: 204,
    target_reached: 205, overshoot_alarm: 206, probe_alarm: 207, low_pellets: 208,
    timer_done: 209, error_fault: 210, night_mode: 211,
  },
  mode_presets: { Stop: 1, Startup: 1, Reignite: 1, Smoke: 1, Hold: 1, Shutdown: 1, Prime: 1 },
  event_presets: { Temp_Achieved: 1, Recipe_Next: 1, Grill_Error: 1, Pellet_Level_Low: 1, Timer_Expired: 1 },
  suggested_config: { cooking_color: "blue", idle_brightness: 20, night_mode: false, led_count: 6 },
});

describe("WledCard editor fields", () => {
  it("renders all 12 profile-number rows when use_profiles is on", () => {
    render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    for (const label of [
      "idle", "booting", "preheat", "cooking", "cooldown", "target_reached",
      "overshoot_alarm", "probe_alarm", "low_pellets", "timer_done", "error_fault", "night_mode",
    ]) {
      expect(screen.getByLabelText(new RegExp(label, "i"))).toBeInTheDocument();
    }
  });

  it("does NOT render mode_presets/event_presets keys", () => {
    render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    expect(screen.queryByLabelText(/Reignite/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Temp_Achieved/i)).not.toBeInTheDocument();
  });

  it("editing a profile number calls onChange with that row changed AND mode_presets preserved", () => {
    const onChange = rs.fn();
    render(<WledCard wled={wledFixture()} onChange={onChange} />);
    const cooking = screen.getByLabelText(/cooking/i) as HTMLInputElement;
    fireEvent.change(cooking, { target: { value: "222" } });
    const next = onChange.mock.calls.at(-1)[0];
    expect(next.profile_numbers.cooking).toBe(222);
    expect(next.profile_numbers.idle).toBe(200); // sibling intact
    expect(next.mode_presets).toEqual(wledFixture().mode_presets); // parity boundary preserved
  });

  it("hides the profile grid when use_profiles is off", () => {
    render(<WledCard wled={{ ...wledFixture(), use_profiles: false }} onChange={rs.fn()} />);
    expect(screen.queryByLabelText(/cooking/i)).not.toBeInTheDocument();
  });

  it("shows suggested-config fields only when use_suggested_presets is on", () => {
    const { rerender } = render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    expect(screen.queryByLabelText(/idle brightness/i)).not.toBeInTheDocument();
    rerender(<WledCard wled={{ ...wledFixture(), use_suggested_presets: true }} onChange={rs.fn()} />);
    expect(screen.getByLabelText(/idle brightness/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `web-react/`): `bun run test WledCard`
Expected: FAIL — `./WledCard` does not exist.

- [ ] **Step 3: Write minimal implementation (fields only)**

Create `web-react/src/components/settings/tabs/notifications/WledCard.tsx`:

```tsx
import { NumberField } from "../../fields/NumberField";
import { Section } from "../../fields/Section";
import { Select } from "../../fields/Select";
import { TextField } from "../../fields/TextField";
import { Toggle } from "../../fields/Toggle";

// The wled service is the same loosely-typed bag NotificationsTab holds. This
// card renders every WLED field Flask's settings/index.html exposes; the legacy
// mode_presets/event_presets subtrees (schema-carried, zero Flask UI) are NEVER
// rendered but ARE preserved because every edit spreads the existing bag.
type WledBag = Record<string, unknown>;

interface WledCardProps {
  wled: WledBag;
  onChange: (next: WledBag) => void;
}

// Ordered exactly as common/defaults.py default_notify_services()["wled"]
// ["profile_numbers"], with each key's default as a UI fallback for a bag that
// is missing the value (real settings always carry all twelve).
const PROFILE_STATES: [string, number][] = [
  ["idle", 200], ["booting", 201], ["preheat", 202], ["cooking", 203],
  ["cooldown", 204], ["target_reached", 205], ["overshoot_alarm", 206],
  ["probe_alarm", 207], ["low_pellets", 208], ["timer_done", 209],
  ["error_fault", 210], ["night_mode", 211],
];

const asBool = (v: unknown): boolean => !!v;
const asStr = (v: unknown, fallback = ""): string => (typeof v === "string" ? v : fallback);
const asNum = (v: unknown, fallback: number): number => (typeof v === "number" ? v : fallback);

export function WledCard({ wled, onChange }: WledCardProps) {
  const useProfiles = asBool(wled.use_profiles);
  const useSuggested = asBool(wled.use_suggested_presets);
  const profileNumbers = (wled.profile_numbers as Record<string, unknown> | undefined) ?? {};
  const suggested = (wled.suggested_config as Record<string, unknown> | undefined) ?? {};

  const setKey = (key: string, val: unknown) => onChange({ ...wled, [key]: val });
  const setProfile = (state: string, val: number) =>
    onChange({ ...wled, profile_numbers: { ...profileNumbers, [state]: val } });
  const setSuggested = (key: string, val: unknown) =>
    onChange({ ...wled, suggested_config: { ...suggested, [key]: val } });

  return (
    <Section title="WLED">
      <Toggle label="WLED Enabled" checked={asBool(wled.enabled)} onChange={(b) => setKey("enabled", b)} />
      <TextField
        label="WLED Device Address"
        value={asStr(wled.device_address)}
        onChange={(val) => setKey("device_address", val)}
      />
      <NumberField
        label="WLED Notify Duration"
        value={asNum(wled.notify_duration, 120)}
        onChange={(n) => setKey("notify_duration", n)}
        min={0}
        suffix="sec"
      />

      <Toggle
        label="Use PiFire Suggested LED Behaviors"
        checked={useSuggested}
        onChange={(b) => setKey("use_suggested_presets", b)}
      />
      {useSuggested && (
        <>
          <h3 className="pf-wled-subhead">Suggested Preset Configuration</h3>
          <Select
            label="Cooking Color"
            value={asStr(suggested.cooking_color, "blue")}
            options={[
              { value: "blue", label: "Blue" },
              { value: "green", label: "Green" },
            ]}
            onChange={(v) => setSuggested("cooking_color", v)}
          />
          <NumberField
            label="Idle Brightness"
            value={asNum(suggested.idle_brightness, 20)}
            onChange={(n) => setSuggested("idle_brightness", n)}
            min={1}
            max={100}
            suffix="%"
          />
          <NumberField
            label="LED Count"
            value={asNum(suggested.led_count, 6)}
            onChange={(n) => setSuggested("led_count", n)}
            min={1}
            max={1000}
          />
          <Toggle
            label="Night Mode (dim amber glow)"
            checked={asBool(suggested.night_mode)}
            onChange={(b) => setSuggested("night_mode", b)}
          />
        </>
      )}

      <Toggle
        label="Use Profile-Based WLED Control"
        checked={useProfiles}
        onChange={(b) => setKey("use_profiles", b)}
      />
      {useProfiles && (
        <>
          <h3 className="pf-wled-subhead">Profile Numbers</h3>
          <div className="pf-wled-grid">
            {PROFILE_STATES.map(([state, def]) => (
              <NumberField
                key={state}
                label={state}
                value={asNum(profileNumbers[state], def)}
                onChange={(n) => setProfile(state, n)}
                min={1}
                max={250}
              />
            ))}
          </div>
        </>
      )}
    </Section>
  );
}
```

- [ ] **Step 4: Add the two new CSS classes**

In `web-react/src/components/settings/settings.css`, add (match the file's existing token/spacing conventions — read a nearby rule such as `.pf-section-body` first):

```css
.pf-wled-subhead {
  margin: 0.75rem 0 0.25rem;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--pf-muted, #9aa0a6);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.pf-wled-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.25rem 1rem;
}
```

(Do NOT add either class to the `UNSTYLED` allowlist in `styleCoverage.test.ts`.)

- [ ] **Step 5: Wire WledCard into NotificationsTab**

In `web-react/src/components/settings/tabs/NotificationsTab.tsx`:
1. Add import: `import { WledCard } from "./notifications/WledCard";`
2. Replace the entire inline WLED `<Section title="WLED"> … </Section>` block (the one rendering `wled` enabled/device_address/notify_duration) with:

```tsx
<WledCard
  wled={wled}
  onChange={(next) => setV((s) => ({ ns: { ...s.ns, wled: next } }))}
/>
```

The existing `const wled = svc("wled");` line stays. Remove now-unused local references only if they become unused (do not remove `svc`/`setField` — other services use them).

- [ ] **Step 6: Run tests to verify they pass**

Run (from `web-react/`): `bun run test WledCard` then `bun run test NotificationsTab`
Expected: WledCard PASS (5 tests); NotificationsTab PASS (unchanged — WledCard renders the same "WLED Enabled"/"WLED Device Address" labels it asserted on). If NotificationsTab.test asserted the OLD inline structure and now fails, update those assertions to match the card (labels are identical, so this is unlikely).

- [ ] **Step 7: Run the CSS gates**

Run: `bun run test cssCoverage && bun run test styleCoverage`
Expected: PASS — both new classes have rules and consumers, and neither is on the `UNSTYLED` list.

- [ ] **Step 8: Typecheck, lint, commit**

Run: `bun run typecheck && bun run lint`
Expected: clean. Commit the new component + test + CSS + NotificationsTab change with message `feat(web-react): WledCard editor fields (toggles, suggested config, profile grid)`.

---

### Task 3: Action buttons — Discover pick-list, Push, Test

**Files:**
- Modify: `web-react/src/components/settings/tabs/notifications/WledCard.tsx` (add buttons, status, discovery results)
- Modify: `web-react/src/components/settings/tabs/notifications/WledCard.test.tsx` (add button tests)
- Modify: `web-react/src/components/settings/settings.css` (add `pf-wled-results`, `pf-wled-result-row`, `pf-wled-status`)

**Interfaces:**
- Consumes from Task 1: `discoverWled`, `pushWledProfiles`, `testWledProfile`, and the `WledDevice`/`WledDiscoverResult`/`WledActionResult` types from `helpers/notify/wledApi`.
- Produces: no new exports; extends `WledCard`'s behavior.

- [ ] **Step 1: Write the failing tests (append to WledCard.test.tsx)**

Add a new `describe` block. It mocks the `wledApi` module so no real fetch happens:

```tsx
import { discoverWled, pushWledProfiles, testWledProfile } from "../../../../helpers/notify/wledApi";

rs.mock("../../../../helpers/notify/wledApi", () => ({
  discoverWled: rs.fn(),
  pushWledProfiles: rs.fn(),
  testWledProfile: rs.fn(),
}));

describe("WledCard actions", () => {
  it("Push and Test are blocked with an inline error when the address is empty", async () => {
    render(<WledCard wled={{ ...wledFixture(), device_address: "" }} onChange={rs.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /push profiles/i }));
    expect(await screen.findByText(/enter a wled device address/i)).toBeInTheDocument();
    expect(pushWledProfiles).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /test profile/i }));
    expect(testWledProfile).not.toHaveBeenCalled();
  });

  it("Push sends the live device_address and profile_numbers, then shows success", async () => {
    (pushWledProfiles as ReturnType<typeof rs.fn>).mockResolvedValue({
      result: "success", message: "done", profiles_pushed: 12,
    });
    render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /push profiles/i }));
    expect(pushWledProfiles).toHaveBeenCalledWith(
      "wled.local",
      expect.objectContaining({ cooking: 203, idle: 200 }),
    );
    expect(await screen.findByText(/12/)).toBeInTheDocument();
  });

  it("Test sends the cooking profile number", async () => {
    (testWledProfile as ReturnType<typeof rs.fn>).mockResolvedValue({ result: "success", message: "ok" });
    render(<WledCard wled={wledFixture()} onChange={rs.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /test profile/i }));
    expect(testWledProfile).toHaveBeenCalledWith("wled.local", 203);
  });

  it("Discover renders a pick-list and Use fills the device address", async () => {
    (discoverWled as ReturnType<typeof rs.fn>).mockResolvedValue({
      result: "success", message: "Found 1",
      devices: [{ ip: "10.0.0.9", led_count: 30, name: "WLED-Kitchen" }],
    });
    const onChange = rs.fn();
    render(<WledCard wled={{ ...wledFixture(), use_suggested_presets: true }} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /find wled devices/i }));
    const use = await screen.findByRole("button", { name: /use/i });
    fireEvent.click(use);
    const next = onChange.mock.calls.at(-1)[0];
    expect(next.device_address).toBe("10.0.0.9");
    expect(next.suggested_config.led_count).toBe(30);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `bun run test WledCard`
Expected: FAIL — no Push/Test/Find buttons exist yet.

- [ ] **Step 3: Add buttons, status, and discovery to WledCard**

Extend `WledCard.tsx`:

1. Add imports at top:
```tsx
import { useState } from "react";
import {
  type WledDevice,
  discoverWled,
  pushWledProfiles,
  testWledProfile,
} from "../../../../helpers/notify/wledApi";
```

2. Inside the component, before `return`:
```tsx
  const [busy, setBusy] = useState<null | "discover" | "push" | "test">(null);
  const [status, setStatus] = useState<{ kind: "info" | "success" | "error"; text: string } | null>(null);
  const [devices, setDevices] = useState<WledDevice[] | null>(null);

  const address = asStr(wled.device_address).trim();
  const profileNums = Object.fromEntries(
    PROFILE_STATES.map(([state, def]) => [state, asNum(profileNumbers[state], def)]),
  );

  const requireAddress = (): boolean => {
    if (address) return true;
    setStatus({ kind: "error", text: "Enter a WLED device address first." });
    return false;
  };

  const onDiscover = async () => {
    setBusy("discover");
    setStatus({ kind: "info", text: "Searching for WLED devices…" });
    const res = await discoverWled();
    setDevices(res.devices);
    setStatus({ kind: res.result === "success" ? "success" : "error", text: res.message });
    setBusy(null);
  };

  const onPush = async () => {
    if (!requireAddress()) return;
    setBusy("push");
    setStatus({ kind: "info", text: "Pushing profiles to WLED device…" });
    const res = await pushWledProfiles(address, profileNums);
    setStatus({
      kind: res.result === "success" ? "success" : "error",
      text: res.result === "success" ? `Pushed ${res.profiles_pushed ?? 0} profiles to WLED.` : res.message,
    });
    setBusy(null);
  };

  const onTest = async () => {
    if (!requireAddress()) return;
    setBusy("test");
    const res = await testWledProfile(address, profileNums.cooking);
    setStatus({ kind: res.result === "success" ? "success" : "error", text: res.message });
    setBusy(null);
  };

  const useDevice = (dev: WledDevice) =>
    onChange({
      ...wled,
      device_address: dev.ip,
      suggested_config: { ...suggested, led_count: dev.led_count },
    });
```

3. In the device-address area (right after the `WLED Device Address` `TextField`), add the Discover button and results list:
```tsx
      <div className="pf-settings-actions">
        <button type="button" className="pf-modal-btn" disabled={busy !== null} onClick={() => void onDiscover()}>
          {busy === "discover" ? "Searching…" : "Find WLED Devices"}
        </button>
      </div>
      {devices && devices.length > 0 && (
        <ul className="pf-wled-results">
          {devices.map((dev) => (
            <li key={dev.ip} className="pf-wled-result-row">
              <span>{dev.name} — {dev.ip} ({dev.led_count} LEDs)</span>
              <button type="button" className="pf-modal-btn" onClick={() => useDevice(dev)}>
                Use
              </button>
            </li>
          ))}
        </ul>
      )}
      {devices && devices.length === 0 && (
        <p className="pf-wled-subhead">No WLED devices found on your network.</p>
      )}
```

4. Inside the `{useProfiles && ( … )}` block, ABOVE the `pf-wled-grid`, add the two action buttons:
```tsx
          <div className="pf-settings-actions">
            <button type="button" className="pf-modal-btn accent" disabled={busy !== null} onClick={() => void onPush()}>
              {busy === "push" ? "Pushing…" : "Push Profiles to WLED"}
            </button>
            <button type="button" className="pf-modal-btn" disabled={busy !== null} onClick={() => void onTest()}>
              {busy === "test" ? "Testing…" : "Test Profile"}
            </button>
          </div>
```

5. At the very end of the `<Section>` (after the profile block), add the status region:
```tsx
      {status && <div className={`pf-wled-status ${status.kind}`}>{status.text}</div>}
```

(`pf-settings-actions` already exists in settings.css — reuse it. Only `pf-wled-results`, `pf-wled-result-row`, `pf-wled-status` are new.)

- [ ] **Step 4: Add the three new CSS classes**

In `web-react/src/components/settings/settings.css` (again match nearby conventions):

```css
.pf-wled-results {
  list-style: none;
  margin: 0.25rem 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.pf-wled-result-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.4rem 0.6rem;
  border: 1px solid var(--pf-border, #33373b);
  border-radius: 6px;
}

.pf-wled-status {
  margin-top: 0.5rem;
  padding: 0.4rem 0.6rem;
  border-radius: 6px;
  font-size: 0.85rem;
}
.pf-wled-status.info { color: var(--pf-muted, #9aa0a6); }
.pf-wled-status.success { color: var(--pf-ok, #4caf50); }
.pf-wled-status.error { color: var(--pf-danger, #e5534b); }
```

(Do NOT add any of these to the `UNSTYLED` allowlist. If `styleCoverage` treats the `.info`/`.success`/`.error` compound selectors as separate rules needing consumers, the three status kinds are each rendered by the template literal `pf-wled-status ${status.kind}`, so all three have consumers — verify against the gate output.)

- [ ] **Step 5: Run to verify tests pass**

Run: `bun run test WledCard`
Expected: PASS (9 tests total — 5 field + 4 action).

- [ ] **Step 6: Run the CSS gates**

Run: `bun run test cssCoverage && bun run test styleCoverage`
Expected: PASS. If `styleCoverage` flags an unused `.pf-wled-status.info/.success/.error`, confirm each `status.kind` value is reachable (info on start, success/error on result) — they are; the gate matches on the base class token. Fix any genuine unused rule by removing it, not by allowlisting.

- [ ] **Step 7: Typecheck, lint, commit**

Run: `bun run typecheck && bun run lint`
Expected: clean. Commit with message `feat(web-react): WLED Discover/Push/Test action buttons on WledCard`.

---

### Task 4: End-to-end spec (route-mocked)

**Files:**
- Create: `web-react/tests/e2e/wled-editor.spec.ts`

**Interfaces:**
- Consumes: the running React app + the WledCard UI (Tasks 2–3). Mocks the three `/api/wled_*` endpoints with `page.route` (pattern from `tests/e2e/metrics.spec.ts`).

- [ ] **Step 1: Write the spec**

Create `web-react/tests/e2e/wled-editor.spec.ts`. Follow `metrics.spec.ts` for the `page.route` idiom and the project's settings-navigation helper (open `/settings`, click the Notifications tab — copy how `settings.spec.ts`/`notify.spec.ts` reach the tab). Core body:

```ts
import { expect, test } from "@playwright/test";

test.describe("WLED editor", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/wled_discover*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          result: "success",
          message: "Found 1 WLED devices",
          devices: [{ ip: "10.0.0.9", led_count: 30, name: "WLED-Kitchen" }],
        }),
      }),
    );
    await page.route("**/api/wled_push_profiles", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ result: "success", message: "ok", profiles_pushed: 12 }),
      }),
    );
    await page.route("**/api/wled_test_profile", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ result: "success", message: "Activated profile 203" }),
      }),
    );
  });

  test("discover → use → push → test", async ({ page }) => {
    // Navigate to Settings → Notifications (copy the exact steps settings.spec.ts uses).
    await page.goto("/settings");
    await page.getByRole("tab", { name: /notifications/i }).click();

    // Discover and pick a device.
    await page.getByRole("button", { name: /find wled devices/i }).click();
    await page.getByRole("button", { name: /^use$/i }).click();
    await expect(page.getByLabel(/wled device address/i)).toHaveValue("10.0.0.9");

    // Ensure profile control is on, then push + test.
    const profileToggle = page.getByRole("button", { name: /use profile-based wled control/i });
    if ((await profileToggle.getAttribute("aria-pressed")) === "false") await profileToggle.click();

    await page.getByRole("button", { name: /push profiles to wled/i }).click();
    await expect(page.getByText(/pushed 12 profiles/i)).toBeVisible();

    await page.getByRole("button", { name: /test profile/i }).click();
    await expect(page.getByText(/activated profile 203/i)).toBeVisible();
  });
});
```

If `settings.spec.ts` uses a different navigation (e.g. a helper or a direct `/settings` deep link with a tab query), match it exactly rather than the `getByRole("tab")` guess above.

- [ ] **Step 2: Run it in the main checkout**

Run (from `web-react/`, in the MAIN checkout — not an agent worktree, where Chromium is absent): `bun run test:e2e wled-editor`
Expected: PASS. If run in a worktree without Chromium, the spec SKIPS — that is not a pass; re-run in the main checkout before merge.

- [ ] **Step 3: Typecheck (e2e), lint, commit**

Run: `bun run typecheck:e2e && bun run lint`
Expected: clean. Commit with message `test(web-react): e2e for the WLED editor (route-mocked discover/push/test)`.

---

## Self-Review

**Spec coverage:**
- Files & components (spec §1) → Tasks 1 (client), 2 (card fields + wiring), 3 (buttons). ✓
- Data model, edits, Save (spec §2) → Task 2 (immutable edits, mode_presets preservation test, Save unchanged — NotificationsTab wiring keeps `save({notify_services: v.ns}, ["settings_update"])`). ✓
- Action buttons & client (spec §3) → Task 1 (client contracts) + Task 3 (live-state buttons, empty-address guard, Discover pick-list with Use). ✓
- Error/status (spec §4) → Task 3 (single `pf-wled-status` region, synthesized error results from the client in Task 1). ✓
- Testing (spec §5) → Task 1 (`wledApi.test.ts`), Tasks 2–3 (`WledCard.test.tsx`), Task 4 (e2e), CSS gates in Tasks 2–3, `bun run lint` every task. ✓
- Parity boundary (mode_presets/event_presets not rendered, preserved) → Task 2 test asserts both. ✓

**Placeholder scan:** No TBD/TODO; every code step has concrete code. The one deliberate "match the existing file" instruction (settings.css conventions, settings.spec navigation) points at a named reference, not a blank. ✓

**Type consistency:** `discoverWled`/`pushWledProfiles`/`testWledProfile` and `WledDevice`/`WledDiscoverResult`/`WledActionResult` are defined in Task 1 and consumed with identical names/signatures in Task 3. `WledCard` props `{ wled, onChange }` are identical in Tasks 2 and 3 and at the NotificationsTab call site. `profileNums.cooking` exists because `PROFILE_STATES` includes `cooking`. ✓

## Parallelization

- **Task 1** (client) and **Task 2** (card fields) touch disjoint files and can run in parallel in separate workspaces — Task 2's field-only card does not import the client. Their only shared file is none (Task 2 touches settings.css + NotificationsTab + WledCard; Task 1 touches helpers/notify/*).
- **Task 3** depends on BOTH Task 1 (imports the client) and Task 2 (extends WledCard) — must follow both.
- **Task 4** (e2e) depends on Task 3.
- Concurrent work needs isolated jj workspaces (copy `.lsp.json` + `bun install`, both gitignored). Disjoint files alone are not enough per repo convention.
