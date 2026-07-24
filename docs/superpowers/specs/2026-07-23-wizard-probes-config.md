# Wizard Probes Config (React) — Design

**Status:** Approved (2026-07-23). Next: implementation plan via superpowers:writing-plans.

Ports PiFire's probe-configuration surface (the Wizard "Probe Input" pill tab,
`blueprints/probeconfig/*`) into the React wizard module-config surface, extending the
shared spine (`ModuleCard` widgets / `DiscoveryPanel` / `/api/wizard/*`) built for the
display slice. Ground truth for every legacy behavior below is
`.superpowers/sdd/probeconfig-inventory.md` (cited as §N), which is itself cited to
live `file:line`. Read that inventory alongside this spec; this document records the
**decisions**, not a re-derivation of the legacy surface.

Companion to the display-first spec `2026-07-23-wizard-module-config-display-first.md`
(same wizard shell, same client-held + one-POST-at-Finish data flow, same draft
persistence). Where this spec says "as display", it means that established contract.

---

## Why probes is not a ModuleCard

Display was one `<select>` → one module → a flat config dict, which mapped onto
`ModuleCard`. Probes is a **two-list relational editor** on `wizardInstallInfo["probe_map"]`
(§1):

- `probe_devices[]` — physical/virtual hardware (ADS1115, MAX31865, `virtual_average`,
  `thermoworks_cloud`, …), each with manifest-driven device-specific config and discovery
  affordances (I2C / Bluetooth / USB-serial / ThermoWorks scans).
- `probe_info[]` — logical probes (Grill / Probe1 / …), each pinned to a `device:port`, a
  `type` (exactly one Primary), an `enabled` flag, and a **value-copied** thermistor
  profile.

The two lists are coupled by cascade-delete, virtual-device `probes_list` membership, and
a **list-order-load-bearing reposition algorithm** (§3). The shared spine is reused at the
*widget* level (discovery panels, `ConfigOptionField`); the *screen* is a new multi-device
CRUD surface, not a `ModuleCard`.

---

## Global Constraints

Every task inherits these verbatim.

- **Scope:** one combined probes spec (devices + ports + reposition + profile selection +
  all five discovery flows). Not split into Devices-first / Ports-next.
- **Logic home:** a **pure client-held TS reducer** owns the `probe_map` working object.
  All CRUD, the reposition algorithm, cascade-delete, and the exactly-one-Primary invariant
  run in TS. No live per-operation server writes. The reposition algorithm is reproduced
  **exactly** (§3) — it encodes a runtime invariant the value-averaging pass depends on.
  Legacy Python `blueprints/probeconfig/*` stays until the Jinja UI is deleted; that
  duplication is the accepted transition cost.
- **Data flow (as display):** client holds `probe_map`; it joins `display_config` in the
  draft blob (datastore key `wizard:install` draft) and in the single `/api/wizard/finish`
  payload. `/api/wizard/state` resumes `probe_map` from the draft if present, else
  `_build_state` seeds it. No selection is `null`, never `""`. List fields are always
  lists.
- **Coverage / gate:** the web-react gate must stay green — **≥75% lines per file**
  (rstest, `thread` concurrency for accurate measurement), `bun run typecheck` clean, Biome
  lint clean, `bun run build` succeeds. bun, not npm; commit `bun.lock`.
- **Python:** full suite green under
  `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest`. `uvx ruff format` on
  changed Python before every commit. PEP 758 bare-tuple `except A, B` is ruff-canonical
  here — do not "fix" it.
- **Installer safety:** `os.system` in `/finish` must be monkeypatch-neutralized in every
  test that reaches it; no test may fire the real installer.
- **jj:** per-task commit, manual format, verify diff before commit. Never run jj-write
  mutations while an implementer is committing in the same workspace.

---

## ① Data flow & seeding

`probe_map` = `{ probe_devices: Device[], probe_info: Probe[] }`, held in React working
state, mutated only through the reducer (§③).

- **Seeding.** `_build_state` (`blueprints/api_wizard/routes.py`) seeds `probe_map` from
  live `settings["probe_settings"]["probe_map"]` (the re-run case,
  `blueprints/wizard/wizard.py:89`). **Assumption / cross-step dependency:** genuine
  first-time-setup seeds `probe_map` from the *selected PCB board's* default manifest
  (`wizardInstallInfoDefaults`, `blueprints/wizard/wizard.py:70-72`), which requires the
  grillplatform step's board selection. Grillplatform is still a placeholder step, so
  board-default seeding is **out of scope here** and noted as a follow-up. On the dev
  system (already installed) live-settings seeding is correct and testable.
- **Resume.** `/state` returns `probe_map` from the draft blob if `has_draft`, else the
  seeded value — identical resume semantics to `display_config`.
- **Reseed footgun retired (§9).** Legacy blows away in-progress edits on every bare
  `/wizard` GET (`blueprints/wizard/routes.py:294-300`). The React port does **not** reseed
  on mount when a draft exists; draft-first resume is the single source of truth.
- **Persist.** `probe_map` is flushed into the draft on step transitions (as
  `display_config`) and sent once in the `/finish` payload. `/finish` already reads
  `probe_map` from the `wizard:install` blob and runs the full cross-subsystem bus-kind
  check → the **422 `bus_conflict`** path becomes live for probes (already implemented and
  tested in the display slice's Task 4).

### `/finish` payload (extended)

```jsonc
{
  "selections": { "...": "..." },
  "settings_dep_values": { "...": "..." },
  "display_config": { "...": "..." },
  "probe_map": {                       // NEW
    "probe_devices": [ /* Device */ ],
    "probe_info":    [ /* Probe  */ ]
  },
  "probes_units": "F"                  // NEW — "F" | "C"; see §②
}
```

`_wizard_install_info_from_payload()` writes `probe_map` straight through to
`wizardInstallInfo["probe_map"]` and `probes_units` into
`wizardInstallInfo["modules"]["probes"]["settings"]["units"]` (§7).

---

## ② UI structure

One wizard step ("Probes"), two stacked cards mirroring the legacy columns (§6), plus a
units selector. Bootstrap modals are replaced by React dialogs/inline panels.

**Devices card** (`render_probe_devices` legacy analog, §6):
- Table rows: thumbnail (`img/wizard/<module.image>`) / device name / module (friendly
  name) / Edit · Delete. Trailing "add device" affordance.
- Add/Edit opens a **manifest-driven device-settings form** built from the module's
  `device_specific.config[]`. Widget dispatch by field type (§6), reusing the shared
  `ConfigOptionField` and discovery widgets:
  - `float`/`int` → number input with manifest `min`/`max`/`step`.
  - `list` → single `<select>` from parallel `list_values`/`list_labels`.
  - `string` → text input.
  - `probes_list` → multi-select of every existing probe label (`available_probes`,
    derived live from `probe_info`).
  - `i2c_bus_num` → text + Discover button (I2C scan); `kind` read from the paired
    `i2c_bus_kind` sibling field's current value (the display slice's `depValues[kindKey]`
    fix — never send the literal `"i2c_bus_num"`).
  - `bt_address` → text + Scan button (Bluetooth scan); description column replaced with
    the fixed "Turn on your bluetooth device…" copy (§6).
  - `device_serial` → readonly text + Test-Connection button (ThermoWorks). **Special
    case:** this field is manifest-`hidden: true` yet its row stays visible because it hosts
    the Test-Connection button (§6/§9) — reproduce this one exception, not a blanket
    hidden rule.
  - `usb_serial_device` → text + Discover button (USB-serial). Not used by any probe module
    today, but included in the shared widget set for forward-compat (§6).
- **Edit-mode config backfill (§2 edit_config):** when opening Edit, any manifest config
  setting absent from the saved device config is filled with the manifest default before
  render — otherwise a newly-added dropdown option silently mis-renders to its first
  `<option>`. Reproduce.

**Ports card** (`render_probe_ports` legacy analog, §6):
- Table columns: display name / Enabled (icon) / Type / Device / Port / Profile (name if
  port contains `ADC`, else "NA") / Edit · Delete. Trailing "add probe" affordance.
- Add/Edit opens the **fixed 5-field port form** (`probe_config_options` order, §6):
  `name` (free text), `device:port` (`<select>` of every device's ports, derived live),
  `type` (Primary / Food / Aux), `profile` (`<select>` from live `probe_profiles`),
  `enabled`.
- **Conditional visibility (`probe_showHideFields`, §6), fired on load and on change:**
  - Profile row shown iff the selected `device:port` value contains `"ADC"`.
  - Enabled row hidden iff the selected type contains `"Aux"`.

**Units.** `probes_units` (`"F"` | `"C"`) selector lives on this step (§7). It is *not*
part of `probe_map`; it flows to `modules.probes.settings.units` at finish. Seeded from live
`settings` units on `/state`.

**Retired legacy quirks** (§9):
- Nested-Bootstrap-modal workarounds and the hardcoded **500ms submit delay** — dropped;
  React dialogs meet the real requirement (a Scan sub-dialog must not close its parent) with
  no magic number.
- The `refresh_probes` **pseudo-action** — dropped; client-held state re-renders the ports
  table automatically after a cascade delete.

---

## ③ The reducer (pure TS)

A single reducer over `probe_map`. Every mutation is a pure `(probe_map, action) →
probe_map` (plus a validation-error channel). This is the coverage-critical unit.

**Device actions:**
- `addDevice(name, module, config)` — validate: (a) uniqueness of the **alnum-stripped**
  device name; (b) name non-blank; (c) **empty-sanitized-name** (FIX, §9 — reject when the
  alnum-strip yields `""`, e.g. `"---"`); (d) per-device bus-kind coexistence over the
  in-progress device set only (see bus-kind note). Append device with `ports` copied from
  the module manifest, `config` overlaid from the form.
- `editDevice(originalName, newName, config)` — module/ports/`module_filename` are immutable
  (copied from the original device by `originalName`); only name + device-specific config
  change. **Rename cascade (FIX, §9):** update every `probe_info[].device` equal to
  `originalName`, and every virtual device's own `device` key / dependency reference, to
  `newName`. Legacy leaves these dangling.
- `deleteDevice(name)` — remove the device; cascade-delete every probe whose
  `device == name`. **Scrub (FIX, §9):** remove the deleted probes' labels from every
  virtual device's `config.probes_list` (legacy leaves dangling strings).

**Probe actions:**
- `addProbe` / `editProbe` — build the probe (`label` = alnum-strip of name; `device`/`port`
  split from `device:port` on `:`; `enabled` string→bool; profile **value-copied** as a
  whole `{A,B,C,id,name}` object from live `probe_profiles`, §⑤). Validation:
  - name non-empty;
  - **exactly-one-Primary:** if the new/edited probe is `Primary`, no *other* probe may be
    Primary (skip the one being edited by original label).
  - **Reposition (§3), reproduced exactly:** the pre-step in-place `probes_list` rename; then
    the three-way branch — (a) `"VIRT" in port` → backward scan, relocate the virtual entry to
    immediately after its last input probe; (b) probe feeds a virtual device → forward scan,
    insert immediately before the consuming virtual entry; (c) ordinary probe → in-place
    replace. A genuine new add is **appended at the end** regardless; the invariant is only
    enforced retroactively on the next edit (do not "fix" this).
- `deleteProbe(label)` — before removing, scrub `label` from every virtual device's
  `probes_list` (legacy already does this for `delete_probe`). Then remove from `probe_info`.
- **Zero-Primary guard (FIX, §9):** a delete or type-change that would leave **zero Primary
  probes while ≥1 probe remains** is rejected with a validation error. Zero probes → zero
  Primaries is allowed (the empty state is valid).

**Derived selectors (not stored):** `available_probes` (labels for the `probes_list`
multi-select) and the `device:port` option list are computed live over the one state — the
legacy Devices↔Ports round-trip coupling (§8) disappears.

**Bus-kind validation.** Per-device add/edit validates only the **in-progress probe device
set against itself** (legacy passes `settings=None`, §7 — deliberately excludes stale
saved fan/distance kinds to avoid false positives). This lightweight check is exposed as a
server call `POST /api/wizard/probes/validate-bus-kinds` (reusing Python
`configured_bus_kinds`/`validate_bus_kinds`, `common/i2c_bus.py`) so the single tested
implementation is shared; the client surfaces its error inline. The **full** cross-subsystem
check (probes + distance + fan, using in-progress selections) still runs once at `/finish`
(422 `bus_conflict`).

---

## ④ Discovery — all five flows

Every flow's empty result renders as a **friendly error banner**, not an empty table (§4/§9).

| kind | endpoint | inputs | backend | row shape |
|---|---|---|---|---|
| I2C extended/mcp2221/ft232h | `POST /api/wizard/scan` (existing) | `kind` | `discover_extended_i2c_buses` / `discover_mcp2221_devices` / `discover_ft232h_devices` | `{groups:[{title,items:[{value,label}]}], error}` |
| USB-serial | `POST /api/wizard/scan` (`kind=usb_serial`) | `vid?`, `pid?` | `discover_usb_serial_devices` (`common/usb_serial.py`) | same groups shape |
| Bluetooth | `POST /api/wizard/scan/bluetooth` (sibling) | — | `process_command(action="sys", arglist=["scan_bluetooth"])` + `get_system_command_output(timeout=6)`; fast-fail "No support for bluetooth scan command." if unsupported | `{rows:[{name,hw_id,info}], error}` |
| ThermoWorks | `POST /api/wizard/scan/thermoworks` (sibling) | `email`, `password` | `asyncio.run(discover(email, password))`; distinguish `AuthenticationError` (bad-creds message) from generic | `{rows:[{label,type,serial,num_channels}], error}` |

I2C + USB-serial share the existing `/api/wizard/scan` (`kind` discriminator, groups shape).
Bluetooth and ThermoWorks get **sibling endpoints** because their inputs and row shapes
differ. Bluetooth is hardware/daemon-mediated (6s hard timeout); ThermoWorks is a blocking
network auth call — both wrapped so they never 500 (never-raise contract, §4).

**Bluetooth in-use warning (§7):** `parse_bt_device_info` checks each scanned peripheral's
`hw_id` against **live** `settings[...]["probe_devices"][].config["hardware_id"]` and appends
an inline warning to that row's `info` rather than filtering — reproduce.

---

## ⑤ Profiles

Value-copy semantics (§5/§9), reproduced exactly:

- `/state` ships the live `settings["probe_settings"]["probe_profiles"]` list for the port
  form's profile picker.
- On assignment, the client **copies the whole `{A,B,C,id,name}` object** into
  `probe_info[].profile` — not a foreign-key `profile_id`. Probes carry a profile snapshot;
  they do not "follow" later profile edits (that propagation is a Settings-page-only path,
  §5, and never touches in-progress `wizardInstallInfo`).
- Profile CRUD (add/edit/delete) stays on the Settings page
  (`blueprints/settings/routes.py:226-319`) — **out of scope** for this spec.
- Display-only: the ports table shows the profile name only when the port contains `ADC`,
  else "NA" (§5) — a display distinction, not a data constraint.

---

## ⑥ Testing

- **Reducer (unit, primary coverage target):** exhaustive tests over every device/probe
  action. Dedicated tests for each reposition branch (§3 a/b/c, new-append, retroactive
  enforcement) asserting **list order**, not just membership. Each of the four fixes gets a
  **red-before** test (rename cascade; zero-Primary-with-probes-remaining; empty-sanitized
  name; virtual `probes_list` scrub on device delete). ≥75% lines per file; aim near 100%
  for the reducer.
- **Backend (pytest):** one test per new/extended scan endpoint (USB-serial groups; BT
  friendly-error + in-use warning; ThermoWorks auth-vs-generic), each with the underlying
  `discover_*` / command channel monkeypatched. `probe_map` round-trip through
  `/state` → `/draft` → `/finish` (installer `os.system` neutralized), including the 422
  `bus_conflict` path with `probe_map` present. `validate-bus-kinds` endpoint: conflict and
  clean cases.
- **e2e (Playwright, live backend):** add a device → add a probe → assign a profile →
  Finish (installer neutralized); assert the staged `probe_map` reached the blob. Re-run in
  the main checkout (chromium tests skip in agent worktrees).

---

## Out of scope / follow-ups

- First-time-setup board-default `probe_map` seeding (depends on the grillplatform step;
  placeholder today).
- Probe-profile CRUD (Settings page).
- Deleting the legacy Jinja `blueprints/probeconfig/*` surface (transition cleanup, after
  React parity is proven live).
