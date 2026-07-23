# Notifications Settings Tab (React) — Design Spec

**Date:** 2026-07-23
**Status:** Approved (design), pending user spec review
**Scope:** `web-react/` only — a 10th settings tab replacing the Flask/Jinja
notification-services form. NO backend changes (the generic
`POST /api/settings_update` + the S2 strict gate handle validation).

## Context

The Flask `_settings_notify` handler (`blueprints/settings/routes.py:85-`)
writes `settings["notify_services"]` from one big form covering 8 channels.
Every field is now schema-typed (`common/settings_schema.py` `NotifyServices`
+ per-service models, S1/S2). This tab reproduces that form in React over the
established settings machinery.

## Goals

1. A NotificationsTab (10th settings tab, after History) editing the 8 notify
   services with one Save, via the existing loader/`useSaveSettings` pattern.
2. Two new reusable primitives: a `StringListField` (add/remove text rows,
   for Apprise locations) and a OneSignal devices manager (edit/delete rows;
   no add — devices self-register from the mobile app).
3. Coverage ≥75% (enforced), and the S2 strict gate means the saved delta
   must be correctly typed — the React primitives already emit typed values.

## Architecture

### Fields per service (from the schema models + what `_settings_notify` writes)

The tab writes ONLY the fields the legacy form submits (verified against
`blueprints/settings/routes.py` `_settings_notify` and the Jinja template) —
the rest of a service's schema keys pass through untouched via the
whole-subtree rebuild:

| Service | Fields (widget) |
|---|---|
| **Apprise** | enabled (Toggle), locations (**StringListField**) |
| **IFTTT** | enabled, APIKey (TextField) |
| **Pushbullet** | enabled, APIKey, PublicURL |
| **Pushover** | enabled, APIKey, UserKeys, PublicURL |
| **OneSignal** | enabled, **devices manager** (uuid/app_id are read-only/backend-managed — not edited here) |
| **InfluxDB** | enabled, url, token, org, bucket |
| **MQTT** | enabled, id, broker, port, username, password, homeassistant_autodiscovery_topic, update_sec |
| **WLED** | enabled, device_address, notify_duration (NumberField, ≥0) |

Secrets (tokens/passwords/API keys) are plain text inputs exactly as the
legacy UI treats them — no masking (out of scope; the whole settings surface
transmits these in clear today).

**WLED preset grids are explicitly deferred** (non-goal): `profile_numbers`
(12), `mode_presets` (7), `event_presets` (5), `suggested_config`,
`use_profiles`/`use_suggested_presets` — these were NOT in the scalar Jinja
notify form the way the 8-service toggles are (they belong to WLED's
profile-editor surface). The tab edits WLED's scalar fields only; the preset
subtrees rebuild untouched.

### Components / files

- `components/settings/tabs/NotificationsTab.tsx` (+ `.test.tsx`): house
  pattern (`useOutletContext<{settings, mode}>`, `useSaveSettings`,
  render-phase `prevSettings` sync, Section per service). `readNotify(settings)`
  builder clones `notify_services` (structuredClone); Save rebuilds the whole
  `notify_services` subtree into the delta.
- `components/settings/fields/StringListField.tsx` (+ `.test.tsx`): controlled
  `{label, values: string[], onChange(next: string[])}` — rows of TextField +
  remove button, an "Add" button appending "". Generic (probe-config reuse
  likely).
- OneSignal devices manager: lives inside NotificationsTab (not a shared
  primitive yet — one consumer). Renders `onesignal.devices` (a
  `dict[str, {friendly_name, device_name, app_version}]`) as a table:
  friendly_name editable (TextField), device_name/app_version read-only,
  per-row delete (removes the key). Empty `{}` → hint: "No devices
  registered. Devices register automatically when you sign in on the PiFire
  mobile app." No add control.
- `SettingsShell.tsx` SETTINGS_TABS: insert `{to: "notifications", label:
  "Notifications"}` after History; `App.tsx` adds the route.

### Save semantics

`save(delta, flags)` where `delta.notify_services` is the rebuilt subtree.
**Flags:** match `_settings_notify` exactly — the plan verifies whether it
calls `save_settings_and_flag_update` with a flag or a bare `write_settings`;
notify config is read fresh by the notification dispatch each cycle (like the
grill-name flagless case), so `[]` (bare) is the expected answer — the plan
confirms against the handler and pins it.

## Testing

- RTL per service card: renders loaded values; edits one field; Save →
  assert exact `(delta, flags)` with `notify_services.<svc>.<field>` changed
  and untouched services preserved.
- StringListField: add row → onChange with `[...v, ""]`; edit row → value
  changed; remove → row dropped; covers Apprise save round-trip.
- OneSignal devices: fixture with 2 devices → renders both; edit friendly_name
  → delta carries it; delete → key removed from delta; empty → hint, no crash.
- Coverage ≥75% per-file (enforced); new primitives aim 100%.
- E2e: toggle a service (e.g. IFTTT) + set its APIKey, Save, reload, assert
  persisted; restore original (leave-as-found).

## Non-goals

WLED preset/profile grids (own surface); per-probe notify TARGETS (cook-flow,
not settings); secret masking; any backend change; add-device (app-driven).

## Sequencing

After this: the probe-config page (its own brainstorm→spec→plan).
