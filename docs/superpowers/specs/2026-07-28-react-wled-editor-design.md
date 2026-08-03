# React WLED preset/profile editor — Design

**Date:** 2026-07-28
**Backlog:** item 10 finding #17 (`docs/superpowers/backlogs/react-migration-backlog.md`;
`audits/2026-07-26-deferred-inventory-plans.md`).

## Goal

Bring the React Settings → Notifications → WLED card to parity with Flask's WLED
card. Today `NotificationsTab.tsx` round-trips the whole `notify_services` subtree
on Save but renders only three WLED scalars (`enabled`, `device_address`,
`notify_duration`). This adds the two mode toggles, the suggested-config block,
the 12-row profile-number grid, and the three network action buttons (Discover,
Push Profiles, Test Profile) — all of which Flask already exposes and whose
backend endpoints already exist.

## Non-goals / parity boundary

- **`mode_presets` and `event_presets` are NOT rendered.** They exist in
  `common/settings_schema.py` (`WledModePresets`, `WledEventPresets`) and
  `common/defaults.py`, and `notify/wled_handler.py` reads them, but they have
  **zero** references in Flask's `settings/index.html` — never user-editable.
  Porting, not inventing: the React card does not render them either. They must
  survive Save byte-identical (see Data model).
- No new backend endpoints, schema changes, or notification-handler changes.
- No "Send Test Notification" (that is a separate finding; only the three WLED
  buttons are in scope).
- Credential masking (#19) is a separate accepted enhancement, out of scope here.

## Global constraints

- **Port, don't invent.** Match Flask's WLED card behavior; do not add UX Flask
  lacks. Where a behavior is a judgment call, Flask's behavior governs.
- **web-react tooling is `bun`**, not npm. Task gates must include
  `bun run lint` (Biome) in addition to typecheck + unit + e2e.
- Field numeric bounds mirror the schema, but `write_settings()`'s strict gate on
  the merged tree is the sole authority (Layer 2).
- New `pf-*` classes require both a CSS rule (`cssCoverage.test.ts`) and a
  consumer, and must not violate `styleCoverage.test.ts`'s `UNSTYLED` exact-list.
- Chromium e2e specs skip in agent worktrees; touched specs are re-run in the
  main checkout before merge.

## Architecture (approach A — extracted card)

```
NotificationsTab.tsx
  └─ <WledCard wled={wled} onChange={next => setV(s => ({ns:{...s.ns, wled: next}}))} />
        ├─ reads typed views off the wled bag
        ├─ edits: builds next wled immutably (spreads untouched keys), calls onChange
        └─ actions: helpers/notify/wledApi.ts (discover / push / test)
```

- **`web-react/src/components/settings/tabs/notifications/WledCard.tsx`** (new) —
  owns the whole WLED card UI and its local action/status state.
- **`web-react/src/helpers/notify/wledApi.ts`** (new) — typed client for the three
  endpoints, mirroring the shape of `helpers/files/cookfileApi.ts`.
- **`web-react/src/components/settings/tabs/NotificationsTab.tsx`** (modify) —
  replace the inline WLED `<Section>` (currently lines ~315–331) with `<WledCard>`.
  No other change; Save is untouched.

### Component interface

```ts
// WledCard props
type NotifyService = Record<string, unknown>; // same loosely-typed bag the tab uses
interface WledCardProps {
  wled: NotifyService;
  onChange: (next: NotifyService) => void;
}
```

`WledCard` never mutates `wled`; every edit produces a new object by spreading the
current bag and overwriting one key (or one nested object built the same way), so
keys the card does not render (`mode_presets`, `event_presets`) are carried
through unchanged.

## Data model, edits & Save

Editable fields and their schema homes (`WledService` in
`common/settings_schema.py`):

| Field | Type / bounds | Control |
|---|---|---|
| `enabled` | bool | Toggle |
| `device_address` | str | TextField + Discover button |
| `use_suggested_presets` | bool | Toggle (reveals suggested block) |
| `use_profiles` | bool | Toggle (reveals profile block) |
| `notify_duration` | int ≥ 0 | NumberField (min 0) |
| `suggested_config.cooking_color` | "blue" \| "green" | select |
| `suggested_config.idle_brightness` | int 1–100 | NumberField |
| `suggested_config.led_count` | int 1–1000 | NumberField |
| `suggested_config.night_mode` | bool | Toggle |
| `profile_numbers.<state>` × 12 | int 1–250 | NumberField per row |

`profile_numbers` states, in defaults order: `idle, booting, preheat, cooking,
cooldown, target_reached, overshoot_alarm, probe_alarm, low_pellets, timer_done,
error_fault, night_mode`.

Nested edits (e.g. one profile row) rebuild the nested object immutably:
`onChange({ ...wled, profile_numbers: { ...pn, [state]: value } })`.

**Save is unchanged.** The card's edits land in the tab's draft `v.ns.wled`; the
existing `onSave` posts `save({ notify_services: v.ns }, ["settings_update"])` —
a whole-subtree replace. `PartialSettingsSchema` (Layer 1) and `write_settings()`
(Layer 2) validate the merged tree.

## Action buttons & client

All three act on **live draft state** (no Save required — matches Flask, which
reads current form values).

`wledApi.ts`:

```ts
interface WledDevice { ip: string; led_count: number; name: string }
interface WledDiscoverResult { result: "success" | "error"; message: string; devices: WledDevice[] }
interface WledActionResult { result: "success" | "error"; message: string; profiles_pushed?: number }

discoverWled(timeoutSec?: number): Promise<WledDiscoverResult>          // GET /api/wled_discover?timeout=15
pushWledProfiles(deviceAddress: string,
                 profileNumbers: Record<string, number>): Promise<WledActionResult>  // POST /api/wled_push_profiles
testWledProfile(deviceAddress: string, profileNumber: number): Promise<WledActionResult> // POST /api/wled_test_profile
```

Button behavior:
- **Discover** (default `timeout=15`): on success, render a results list; each row
  shows `name`, `ip`, `led_count`, and a **Use** button that sets
  `device_address` (and, when the suggested block is active, `led_count`) via
  `onChange`. Zero devices → an inline "none found" hint.
- **Push Profiles**: reads `device_address` + the live `profile_numbers` from the
  draft; on success shows `profiles_pushed`.
- **Test Profile**: reads `device_address` + `profile_numbers.cooking`.
- **Empty `device_address` guard**: Push/Test show an inline error and make no
  network call (Flask parity).
- Each button has its own loading/disabled state while its call is in flight.

## Error & status handling

The card holds a single status value `{ kind: "info" | "success" | "error"; text: string } | null`
rendered in one status region. Success/error envelopes from the endpoints set it;
a rejected fetch sets an error status. No global toast dependency — the card is
self-contained. This mirrors Flask's inline `showProfileStatus` / `showWLED*`.

## Testing

- **`wledApi.test.ts`** — mock `fetch`; assert URL, method, and JSON body for each
  of the three calls, and that both `success` and `error` envelopes are returned
  as typed results (no throw on `result: "error"`).
- **`WledCard.test.tsx`** —
  - grid renders exactly the 12 `profile_numbers` rows in defaults order;
  - editing one profile row calls `onChange` with that row changed **and
    `mode_presets` still present** (parity-boundary preservation);
  - `use_profiles` / `use_suggested_presets` toggles reveal/hide their blocks;
  - empty `device_address` blocks Push and Test (guard, no call);
  - Discover populates the results list and **Use** writes `device_address`.
- **`wled-editor.spec.ts`** (Playwright) — route-mock the three endpoints; drive
  the card: toggle profiles on, edit a profile number, Push (assert success
  status), Test, Discover + Use. Re-run in the main checkout (Chromium skips in
  agent worktrees).
- **Gates:** `bun run typecheck`, `bun run lint` (Biome), unit suite, e2e; any new
  `pf-*` class has a CSS rule and a consumer and does not break the `UNSTYLED`
  exact-list assertion.

## Risks / open points

- The card is the first place NotificationsTab makes live network calls; confining
  that to `WledCard` + `wledApi.ts` keeps the tab's other services pure-form.
- Discover is real network I/O in production but fully route-mocked in tests.
- If `write_settings()` rejects an out-of-bounds profile number the whole Save
  fails (existing behavior for every settings tab); field `min`/`max` make that a
  corner case, not the normal path.
