# Notifications Settings Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A 10th React settings tab replacing the Flask/Jinja notification-services form — 8 channels (Apprise, IFTTT, Pushbullet, Pushover, OneSignal, InfluxDB, MQTT, WLED-scalar) editable with one Save.

**Architecture:** House settings-tab pattern (outlet context, `useSaveSettings`, render-phase `prevSettings` sync). One Save rebuilds the whole `notify_services` subtree into the delta; two new widgets (`StringListField`, an in-tab OneSignal devices manager) handle the dynamic bits. Zero backend change — the generic endpoint + the S2 strict gate validate.

**Tech Stack:** React 19 + rsbuild, rstest + RTL, TS7, Biome+eslint, bun; Playwright e2e vs the live prototype backend.

## Global Constraints

- **bun only.** Full gate every task: `cd web-react && bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build`. Per-file coverage ≥75% lines ENFORCED (new files must clear it; new primitives aim 100%).
- **Flags: `["settings_update"]`** — VERIFIED against `blueprints/settings/routes.py` `_settings_notify` which ends `save_settings_and_flag_update(settings, control, "settings_update", origin="app")`. (This corrects the spec's tentative `[]`.)
- Save rebuilds the ENTIRE `notify_services` subtree from render-phase-synced local state (structuredClone in the `read*` builder); untouched services/keys must survive byte-identical (WLED preset subtrees, onesignal uuid/app_id, etc.).
- Field set per service = ONLY what `_settings_notify` writes (the spec's table); everything else rides through untouched. OneSignal uuid/app_id are NOT edited (backend-managed).
- **S2 strict gate is live**: the saved delta must be correctly TYPED — `notify_duration` is a number (NumberField), `mqtt.port`/`mqtt.update_sec` are STRINGS in the schema (`MqttService.port: str`, `update_sec: str` — verified), so they use TextField, not NumberField. Getting a type wrong → the server rejects the save. Each task states the widget per field explicitly.
- House style: render-phase sync only (NO useEffect+setState); no new eslint-disable/biome-ignore. Trees/layering (structure.test.ts): components under `src/components/`, no helper needed here.
- SETTINGS_TABS entries use key `path` (not `to`) — the shape is `{ path, label }`.
- **Lint-staging rule**: after any `bunx biome check --write`, `git status` → stage every file it fixed → `bun run lint` (check-only) AFTER staging. Post-commit `git show --stat HEAD` verify (prek-contamination history). Stage explicit paths from `web-react/`.

---

### Task 1: StringListField primitive

**Files:**
- Create: `web-react/src/components/settings/fields/StringListField.tsx`, `.../StringListField.test.tsx`

**Interfaces:**
- Produces: `StringListField({ label, values, onChange }: { label: string; values: string[]; onChange: (next: string[]) => void })` — controlled; renders a TextField-style input per value + a remove button per row + an "Add" button appending `""`. Task 3 (Apprise) consumes it.

- [ ] **Step 1: Failing RTL test** (`StringListField.test.tsx`, jsdom). Read a sibling field primitive first (`src/components/settings/fields/NumberField.tsx`) to match label/className house style. Cases:

```tsx
// @vitest-environment jsdom
import { describe, expect, it, rs } from "@rstest/core";
import { fireEvent, render, screen } from "@testing-library/react";
import { StringListField } from "./StringListField";

describe("StringListField", () => {
  it("renders one input per value with the label", () => {
    render(<StringListField label="Locations" values={["a", "b"]} onChange={() => {}} />);
    expect(screen.getByText("Locations")).toBeInTheDocument();
    expect(screen.getAllByRole("textbox")).toHaveLength(2);
  });
  it("editing a row emits the changed array", () => {
    const onChange = rs.fn();
    render(<StringListField label="L" values={["a", "b"]} onChange={onChange} />);
    fireEvent.change(screen.getAllByRole("textbox")[1], { target: { value: "z" } });
    expect(onChange).toHaveBeenCalledWith(["a", "z"]);
  });
  it("Add appends an empty row", () => {
    const onChange = rs.fn();
    render(<StringListField label="L" values={["a"]} onChange={onChange} />);
    fireEvent.click(screen.getByRole("button", { name: /add/i }));
    expect(onChange).toHaveBeenCalledWith(["a", ""]);
  });
  it("remove drops that row", () => {
    const onChange = rs.fn();
    render(<StringListField label="L" values={["a", "b"]} onChange={onChange} />);
    fireEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);
    expect(onChange).toHaveBeenCalledWith(["b"]);
  });
});
```

- [ ] **Step 2: Implement** (fresh arrays always, never mutate props):

```tsx
export function StringListField({
  label, values, onChange,
}: { label: string; values: string[]; onChange: (next: string[]) => void }) {
  return (
    <div className="pf-field pf-field-column">
      <span className="pf-field-label">{label}</span>
      {values.map((v, i) => (
        // biome-ignore-free: index key is acceptable for a controlled ordered list with no reorder
        <div className="pf-stringlist-row" key={i}>
          <input
            className="pf-input"
            type="text"
            value={v}
            onChange={(e) => onChange(values.map((x, j) => (j === i ? e.target.value : x)))}
          />
          <button type="button" aria-label="Remove" onClick={() => onChange(values.filter((_, j) => j !== i))}>
            ✕
          </button>
        </div>
      ))}
      <button type="button" aria-label="Add" onClick={() => onChange([...values, ""])}>
        + Add
      </button>
    </div>
  );
}
```

Note: the `key={i}` is the one place index-as-key is correct (ordered, non-reordered, controlled). If biome's `noArrayIndexKey` flags it, the repo already disables that rule in `biome.jsonc` (verified present from 2b-2) — no new suppression needed; confirm `bun run lint` is clean. Add a `.pf-field-column`/`.pf-stringlist-row` block to `src/components/settings/settings.css` if no existing class fits.

- [ ] **Step 3: Gate + commit** — `bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build`; `git add src/components/settings/fields/StringListField.tsx src/components/settings/fields/StringListField.test.tsx src/components/settings/settings.css`; commit `feat(web-react): StringListField primitive (add/remove text rows)`; `git show --stat HEAD`.

---

### Task 2: NotificationsTab shell + nav/route + the 6 simple services

**Files:**
- Create: `web-react/src/components/settings/tabs/NotificationsTab.tsx`, `.../NotificationsTab.test.tsx`
- Modify: `web-react/src/components/settings/SettingsShell.tsx`, `web-react/src/components/App.tsx`

**Interfaces:**
- Consumes: `StringListField` (Task 1), house primitives (`Section`, `Toggle`, `TextField`, `NumberField`), `useSaveSettings`, `useOutletContext`.
- Produces: a rendered NotificationsTab covering IFTTT, Pushbullet, Pushover, InfluxDB, MQTT, WLED-scalar + Apprise (via StringListField). OneSignal devices manager is Task 3. Save with flags `["settings_update"]`.

- [ ] **Step 1: Nav + route** — `SettingsShell.tsx` SETTINGS_TABS: insert `{ path: "notifications", label: "Notifications" }` immediately after the `history` entry. `App.tsx`: add `{ path: "notifications", element: <NotificationsTab /> }` beside the other settings children (import it).

- [ ] **Step 2: Failing tab test** — read `src/components/settings/tabs/SafetyTab.test.tsx` for the exact `renderRoute` + `rs.mock("../../../helpers/settings/useSaveSettings")` spy arrangement, then write `NotificationsTab.test.tsx` with a `notify_services` fixture (real shape — every service present; abbreviated but each with `enabled` + its fields). Cases (this task): renders each service Section; toggling `ifttt.enabled` + setting `ifttt.APIKey` → Save → spy called with delta `notify_services.ifttt.{enabled:true, APIKey:"..."}` AND `["settings_update"]` AND untouched services (e.g. `mqtt`) preserved; MQTT `port` edit stays a STRING in the delta (assert `typeof` / exact string value); Apprise locations via StringListField add → delta `notify_services.apprise.locations` grew. (OneSignal card may render a placeholder in this task; its manager lands in Task 3 — the test for OneSignal is Task 3.)

- [ ] **Step 3: Implement the tab** — mirror `SafetyTab.tsx`/`HistoryTab.tsx` structure. `readNotify(settings)` → `{ ns: structuredClone(settings.notify_services ?? {}) }` in local state; render-phase `prevSettings` sync; per-field edit mutates the clone and `setState`. Sections + fields per the spec table, using the S2-verified widget per field:
  - **Apprise**: `Toggle enabled`, `StringListField label="Locations" values={ns.apprise.locations ?? []}`.
  - **IFTTT**: `Toggle`, `TextField APIKey`.
  - **Pushbullet**: `Toggle`, `TextField APIKey`, `TextField PublicURL`.
  - **Pushover**: `Toggle`, `TextField APIKey`, `TextField UserKeys`, `TextField PublicURL`.
  - **OneSignal**: `Toggle enabled` + `{/* devices manager — Task 3 */}` placeholder comment (no fields beyond enabled this task).
  - **InfluxDB**: `Toggle`, `TextField url/token/org/bucket`.
  - **MQTT**: `Toggle`, `TextField id/broker/port/username/password/homeassistant_autodiscovery_topic/update_sec` — ALL TextField (schema types port + update_sec as `str`).
  - **WLED**: `Toggle enabled`, `TextField device_address`, `NumberField notify_duration` (min 0 — schema `ge=0`).
  - Save: `setSaved(await save({ notify_services: ns }, ["settings_update"]))` + a Saved ✓ indicator, matching sibling tabs.

- [ ] **Step 4: Gate + commit** — full gate green (NotificationsTab ≥75% lines — will rise further in Task 3); `git add src/components/settings/tabs/NotificationsTab.tsx src/components/settings/tabs/NotificationsTab.test.tsx src/components/settings/SettingsShell.tsx src/components/App.tsx`; commit `feat(web-react): Notifications settings tab (6 services + Apprise list, settings_update flag)`; `git show --stat HEAD`.

---

### Task 3: OneSignal devices manager

**Files:**
- Modify: `web-react/src/components/settings/tabs/NotificationsTab.tsx`, `.../NotificationsTab.test.tsx`

**Interfaces:**
- Consumes: the tab's `ns` state + save path from Task 2.
- Produces: the OneSignal card's devices table (edit friendly_name, delete row, empty-state hint) writing into `notify_services.onesignal.devices`.

- [ ] **Step 1: Failing tests** — extend `NotificationsTab.test.tsx`. Fixture `onesignal.devices`:

```ts
const DEVICES = {
  "player-abc": { friendly_name: "Danny iPhone", device_name: "iPhone15", app_version: "1.2.0" },
  "player-xyz": { friendly_name: "Kitchen Tablet", device_name: "SM-T500", app_version: "1.1.0" },
};
```

Cases: renders a row per device showing friendly_name + device_name + app_version; editing "Danny iPhone" → Save → delta `notify_services.onesignal.devices["player-abc"].friendly_name` changed, the OTHER device untouched, `device_name`/`app_version` unchanged; delete "player-xyz" → Save → that key ABSENT from `notify_services.onesignal.devices`; empty `devices: {}` → renders the hint text "register automatically" and NO row, no crash.

- [ ] **Step 2: Implement** inside the OneSignal Section (replace Task 2's placeholder). Iterate `Object.entries(ns.onesignal?.devices ?? {})`: per row a `TextField` bound to `friendly_name` (edit updates `ns.onesignal.devices[id].friendly_name`), read-only `device_name`/`app_version` spans, a delete button (`delete`s the key from the clone + setState). Below the rows (or when empty): `<p className="pf-settings-hint">No devices registered. Devices register automatically when you sign in on the PiFire mobile app.</p>`. Uuid/app_id NOT rendered. Save path unchanged (whole `ns` already rebuilt).

- [ ] **Step 3: Gate + commit** — full gate; NotificationsTab ≥75%; `git add src/components/settings/tabs/NotificationsTab.tsx src/components/settings/tabs/NotificationsTab.test.tsx`; commit `feat(web-react): OneSignal devices manager in Notifications tab (edit name / delete)`; `git show --stat HEAD`.

---

### Task 4: E2e round-trip + final gate

**Files:**
- Modify: `web-react/tests/e2e/settings.spec.ts`

- [ ] **Step 1: E2e test** — follow the file's existing request-context/read-back style. Open `/settings/notifications`; toggle IFTTT enabled + set its APIKey to a distinct value; Save (assert "Saved ✓"); `page.reload()`; assert the APIKey input kept the value AND the toggle is on; then RESTORE the original (a second save putting IFTTT back to its pre-test enabled/APIKey — read them first, or reset APIKey to "" + original enabled; leave the backend as found). Note: the S2 strict gate is live — this proves the tab writes a strictly-valid `notify_services` delta end-to-end.

- [ ] **Step 2: Restart gunicorn** (standing recipe: kill `gunicorn ... app:app` master, relaunch `uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app` from repo root, `curl :5000/api/current` → 201; the S2 validation code must be loaded). `bun run test:e2e` → all pass (existing + the new one; cold-start-flake rerun rule applies).

- [ ] **Step 3: Final gate** — `bun run typecheck && bun run lint && bun run test && bun run test:coverage && bun run build` green; report final test/file counts + coverage summary line.

- [ ] **Step 4: Commit** — `git add tests/e2e/settings.spec.ts`; commit `test(web-react): e2e round-trip for Notifications tab`; `git show --stat HEAD`.

---

## Verification summary (maps to spec)

Goal 1 (tab, 8 services, one Save)→Tasks 2-3; Goal 2 (StringListField + devices manager)→Tasks 1+3; Goal 3 (coverage + typed delta under S2)→every task's gate + the flag/widget-type constraints. Non-goals (WLED preset grids, notify targets, masking, backend, add-device) absent by construction.
