# Wizard Critical Fixes (C4 / C5+I12 / C6 / C7) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the React first-run wizard *completable* on a fresh install, and stop it destroying data or lying to the user. Four Critical defects: empty hardware pickers (C5/I12), 404 vendor photos (C6), unconfirmed cascading deletes (C7), and no way out plus a UI sentence that claims there is one (C4).

**Architecture:** Three of the four are pure client fixes against contracts the backend already ships. Only C4 adds a backend route, and it is a near-verbatim port of the Flask handler that already exists. No new discovery endpoint — `POST /api/wizard/scan` is already there and already wired.

**Tech Stack:** React 19 + react-router 8, TS7, rsbuild, Biome, @rstest/core, Playwright, bun. Backend: Flask blueprint `blueprints/api_wizard/`, Python 3.14+, ruff.

## Why this exists

This is the only surface in the React app where a user gets **stuck** rather than inconvenienced. A fresh install lands on `/wizard` (forced — see the redirect trap below), and from there:

- the I2C-bus and USB-serial fields are `<select>` elements with **zero** `<option>` children, so the value a real board needs cannot be entered at all;
- every board photo is a broken image, which removes the wizard's entire component-identification mechanism (these photos exist so a user can match the PCB in their hand to the entry in the list);
- a stray click on Delete silently removes a probe device **and every probe attached to it**, with no confirmation and no undo;
- and there is no exit — while the Welcome step tells the user there is one.

---

## Global Constraints

- Test runner is **@rstest/core** (`rs.fn`, `rs.mock`) — **`vi` does NOT exist**. `.test.tsx` → jsdom, `.test.ts` → node.
- **bun**, never npm. Commit `bun.lock` if it moves.
- **No suppressions**: no `biome-ignore`, no `@ts-expect-error`, no `eslint-disable`. If a rule fires, fix the code.
- **No `setState` in `useEffect` for derived state** (React Compiler). Use render-phase adjustment — see `web-react/src/components/settings/tabs/SafetyTab.tsx`, `web-react/src/components/dashboard/SetpointEntry.tsx:24-28` pattern as implemented in `settings/tabs/UnitsTab.tsx:24-28`.
- `react-refresh/only-export-components`: **non-components go in their own module** (`helpers/`). A `.tsx` file exports exactly one component and nothing else.
- `helpers/` must **never** import from `components/` — enforced by `src/structure.test.ts`.
- Reuse the existing `pf-*` class vocabulary (`pf-field`, `pf-input`, `pf-field-column`, `pf-field-hint`, `pf-btn`, `pf-modal*`). Do not introduce a second visual language.
- Gate: `bun run typecheck && bun run lint && bun run test && bun run build && bun run gen:types:check` (from `web-react/`).
- Python side is **3.14+**: `except A, B` **without** parens is ruff-canonical here (see `blueprints/api_wizard/routes.py:57`). Do not "fix" it. Run `uvx ruff format` on every changed `.py` before committing.
- Python tests: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py` from the repo root.

---

## Verified facts (checked against live code on 2026-07-25 — do not re-derive, do not guess)

### The discovery endpoint already exists — do NOT build one

`POST /api/wizard/scan` (`blueprints/api_wizard/routes.py:184-260`) accepts `{kind, vid?, pid?}` and returns `{groups: [{title, items: [{value, label}]}], error}`. Supported kinds: `extended`, `mcp2221`, `ft232h`, `usb_serial`. It is registered at `app.py:105` under `/api/wizard`.

The client half is complete too:
- `helpers/wizard/wizardApi.ts:29-39` — `scan(baseUrl, {kind, vid?, pid?})`.
- `components/wizard/DiscoveryPanel.tsx` — renders the groups as pick-buttons, errors as `role="alert"`.
- Both pickers already call it: `fields/I2cBusPicker.tsx:43-45`, `fields/UsbSerialPicker.tsx:36-38`.
- Both call sites already pass the right kind: `ModuleCard.tsx:60,72` and `probes/DeviceConfigField.tsx:110,128`.

**Discover works today. The only thing broken is the input next to it.** C5 is a one-control change in two files, not an integration.

### C5 + I12 — the pickers render an empty `<select>`

`I2cBusPicker.tsx:25` and `UsbSerialPicker.tsx:19` do `Object.entries(dep.options ?? {})` and hand the result to `SelectField` (`I2cBusPicker.tsx:41`, `UsbSerialPicker.tsx:35`). `SelectField.tsx:19-25` maps `options` to `<option>` — an empty array yields a `<select>` with no children.

Counted from `wizard/wizard_manifest.json` (script over the live file, not read by eye):

| section | dep type | total | **without `options`** |
|---|---|---|---|
| grillplatform | `i2c_bus_num` | 8 | **8** |
| distance | `usb_serial_device` | 1 | 0 |
| probes (`device_specific.config`) | `i2c_bus_num` | 5 | **5** |

The 8 grillplatform deps are `custom`, `pcb_2.00a`, `pcb_3.01a`, `pcb_pwm`, `pcb_4.x.x`, `x86_numato` (×2 — it has both `i2c_bus_num` and `device_distance_i2c_bus_num`), `ft232h_relay`. All 8 have `"type": "i2c_bus_num"`, `"default": "CP2112"`, and **no `options` key**.

The 5 probe fields are `ads1115_adafruit`, `ads1015_adafruit`, `mcp9600_adafruit`, `prototype`, `ads1115`.

Worse, on the probe side it is unconditional: `probes/DeviceConfigField.tsx:46-50` constructs `dep` as `{friendly_name, description, settings: []}` — **`options` is never set at all**, so probe device forms would render an empty select even if the manifest grew one.

The one dep that *does* have options (`distance.sen0628.sen0628_device`, 4 `/dev/tty*` paths) still can't accept `/dev/ttyACM2`.

**Flask never used a select for these types.** `blueprints/wizard/templates/wizard/_macro_wizard_card.html:36-46` branches on the dep type: `i2c_bus_num` → `render_input_i2c_bus_num`, `usb_serial_device` → `render_input_usb_serial_device`, **else** → the generic `<select>` over `options`. Both macros (`blueprints/probeconfig/templates/probeconfig/_macro_probes_config.html:542-552` and `:587-597`) render `<input type="text">` + a Discover button. `options` is ignored by Flask for these two types.

The values these fields accept are free-form by design — the manifest description says so: `'CP2112'` / `'MCP2221'` (adapter-name auto-discovery), `'serial:<ISERIAL>'`, a bare bus number, a pyftdi URL, or an MCP2221 serial. No fixed option list can cover that.

**The dep `description` is currently never rendered by either picker.** Flask shows it in the card table's Description column (`_macro_wizard_card.html:48`). It is the only place the `CP2112` / `serial:` syntax is explained — mandatory once the control is free text.

### C6 — vendor photos 404

Three `<img>` sites use the manifest's **bare filename** as `src`:
- `components/wizard/ModuleCard.tsx:116` — `src={selected.image}`
- `components/wizard/probes/DeviceForm.tsx:23` — `src={moduleData.image}`
- `components/wizard/probes/DevicesCard.tsx:118` — `src={modules[d.module].image}`

These are the **only** `<img>` tags in the app that load a real asset (grep over `src/`; the other hits are XSS-fixture strings in history tests). There is no `public/` directory.

Manifest values are bare filenames — `"pcb_4.x.x.png"`, `"ads1115.png"`, `"dsi_touch.png"` — 35 distinct names across 62 modules, **all 35 present** on disk under `static/img/wizard/`.

**The correct URL prefix is `/static/img/wizard/`, confirmed three ways:**
1. `app.py:49` is a bare `app = Flask(__name__)` — Flask's default `static_folder="static"`, `static_url_path="/static"`, and the repo root has `static/img/wizard/`.
2. Flask's own card template builds exactly that: `_macro_wizard_card.html:7` — `url_for('static', filename='img/wizard/' + moduleData['image'])`.
3. **Live HTTP check against the running backend:** `curl -o /dev/null -w "%{http_code}" http://localhost:5000/static/img/wizard/custom.png` → **`200`**.

**The dev-server trap.** `rsbuild.config.ts:16-19` proxies **only** `/socket.io` and `/api`. A relative `/static/img/wizard/x.png` from `:5173` never reaches Flask.

**The bigger trap — do NOT proxy bare `/static`.** rsbuild emits the app's *own* bundles under `/static/`: `web-react/dist/static/js/*.js`, `web-react/dist/static/css/*.css`. A blanket `/static` proxy would send every script and stylesheet to Flask and break the app outright. rsbuild's default asset directories (extracted from `node_modules/@rsbuild/core/dist/711.js`) are `static/js`, `static/css`, `static/font`, `static/wasm`, `static/image`, `static/media` — **`static/img` is not among them**, so `/static/img` is provably collision-free.

`BASE_URL` is `import.meta.env.PUBLIC_PIFIRE_URL || ""` (`helpers/wizard/wizardRoutes.ts:4`). Prefixing with it covers both configurations: absolute cross-origin when pointed at a remote Pi (`<img>` display needs no CORS), relative-through-the-proxy in the default `bun run dev` setup. Both halves are required.

### C7 — destructive deletes with no confirmation

- `probes/DevicesCard.tsx:127` — `onClick={() => onChange(deleteDevice(probeMap, d.device))}`, fires immediately.
- `probes/PortsCard.tsx:99` — `onClick={() => del(p.label)}`, fires immediately.

**The device delete cascades.** `helpers/wizard/probeReducer.ts:113-129`: `deleteDevice` computes `doomed` = every probe whose `device` matches, drops them from `probe_info` (`:127`), and scrubs those labels out of any virtual device's `probes_list` (`:118-125`). Deleting one device can silently take several probes with it.

**Flask confirms both.** `_macro_probes_config.html:70-89` is `delProbeDeviceModal`, titled *"Delete Probe Device?"*, body:

> Are you sure you want to delete this probe device?
> *__Note:__ All probes associated with this device will also be deleted.*

and `:354-360` is the port equivalent, *"Delete Probe?"* / *"Are you sure you want to delete this probe?"*.

**`ConfirmAction` is NOT reusable as-is.** `components/dashboard/ConfirmAction.tsx:1-6` takes `{open, title, onConfirm, onCancel}` — **title only, no body slot**. Jamming the cascade warning into `title` would render it in `.pf-modal-title` (`dashboard.css:183-187`: `font: 700 20px "Barlow"; text-align: center`) — a two-sentence bold centred headline. It needs an **optional `message` prop**, which is backward-compatible with its one existing consumer, `settings/tabs/UnitsTab.tsx:62-67`.

Scrim caveat: `.pf-modal-scrim` is `position: absolute; inset: 0` (`dashboard.css:165-172`), **not** `fixed`, so it fills the nearest positioned ancestor. There is **no `pf-wizard-*` CSS anywhere** (`grep pf-wizard src --include='*.css'` → zero hits; the wizard is entirely unstyled today), so the wizard tree has no positioned ancestor. See Task 3 Step 5.

### C4 — no exit, and the UI lies about it

`WizardShell.tsx:157-161` renders:

> This wizard walks through configuring PiFire's hardware modules — grill platform, probes, display, and distance/hopper sensing. **You can leave at any point;** your progress is saved as a draft.

There is no exit control anywhere in `WizardShell.tsx` — the footer (`:207-218`) has Back and Next only, and there is **no cancel route in `blueprints/api_wizard/routes.py`** (routes present: `/state`, `/draft`, `/scan`, `/module-values`, `/finish`, `/installstatus`, `/scan/bluetooth`, `/probes/validate-bus-kinds`, `/scan/thermoworks`).

Flask has one. `blueprints/wizard/routes.py:71-74`:

```python
def _wizard_cancel(settings, control, wizardData, python_exec):
    settings["globals"]["first_time_setup"] = False
    write_settings(settings)
    return redirect("/")
```

dispatched as `("POST", "cancel")` at `:265`. Note it uses plain `write_settings` — **no** control update-flag, **not** `save_settings_and_flag_update` (`common/app.py:401-413`). Nothing about the running hardware changed, so mirror that exactly.

**THE TRAP — a cancel that doesn't clear the flag is an infinite loop.** `components/DashboardRoute.tsx:23-35` fetches settings after mount and does `if (s.globals?.first_time_setup) navigate("/wizard")`. Leaving the wizard to `/` without clearing `first_time_setup` bounces the user straight back in, forever. Clearing the flag is not incidental to this fix; it *is* the fix.

The draft must **survive** the exit. `POST /api/wizard/draft` persists `{selections, settings_dep_values, display_config, probe_map, probes_units}` under the `react_draft` marker (`routes.py:174-181`), and `/state` resumes it (`routes.py:74-82`, `has_draft`). Legacy `_wizard_cancel` does not touch the draft blob either. Save the draft, then cancel — that is what makes the Welcome sentence true.

`write_settings` (`common/datastore_accessors.py:297-315`) strict-validates the whole settings tree before persisting; a rejected write leaves the store untouched.

### Test-harness facts

- `web-react/tests/e2e/wizard.spec.ts:72,76` clicks `Delete` directly in the probes step. **Task 3 breaks these** and must fix them in the same commit. (This file already has uncommitted local modifications — rebase onto whatever is there, do not clobber it.)
- `src/components/wizard/steps/ProbesStep.test.tsx:72` also clicks a bare `Delete`.
- `WizardShell.test.tsx:59-71` builds a `createMemoryRouter` with **only** a `/wizard` route. Task 5 adds `navigate("/")`, so the harness needs a `/` route or navigation has nowhere to land.
- `ConfirmAction.test.tsx` — 5 tests, all title/behaviour only; an optional `message` prop leaves them green.
- `test_api_wizard.py:10-14` — the `client` fixture pattern (`ds` fixture + `flask_app.test_client()`).
- `bun run lint` is `biome check . && eslint .`; `biome.jsonc` sets `lineWidth: 100`, double quotes, trailing commas, `useButtonType: off`.

### Out of scope — flagged, not fixed

- `wizardApi.ts:29-39` accepts `vid`/`pid`, and `/scan` forwards them to `discover_usb_serial_devices` (`routes.py:246`), but **no client call site passes them** (`ModuleCard.tsx:72`, `DeviceConfigField.tsx:128` both hardcode `{kind: "usb_serial"}`), and neither `SettingsDependency` (`wizardTypes.ts:4-11`) nor `ProbeConfigField` (`probeTypes.ts:38-50`) has a `vid`/`pid` field. Currently a **no-op**: the only `usb_serial_device` dep in the manifest has `vid: null, pid: null`. Note also that Flask parses them as hex (`routes.py:233-234`) while `/api/wizard/scan` does not — if this is ever wired up, that mismatch is a real bug.
- `InstallProgress.tsx:105` (via `WizardShell.tsx:105`) does `window.location.href = "/admin/restart"` — a same-origin path that hits the React dev server, not Flask. Same class of bug as C6, different surface.
- The wizard has **no CSS at all**. Not this slice's job, but it is why Task 3 Step 5 exists.

---

## File Structure

**Create**
- `web-react/src/helpers/wizard/wizardAssets.ts` + `.test.ts` — pure URL builder for the board photos.

**Modify — client**
- `web-react/rsbuild.config.ts` — add a `/static/img` proxy entry.
- `web-react/src/components/wizard/ModuleCard.tsx` — image URL.
- `web-react/src/components/wizard/probes/DeviceForm.tsx` — image URL.
- `web-react/src/components/wizard/probes/DevicesCard.tsx` — image URL + delete confirmation.
- `web-react/src/components/wizard/fields/I2cBusPicker.tsx` (+ `.test.tsx`) — free-text input.
- `web-react/src/components/wizard/fields/UsbSerialPicker.tsx` (+ `.test.tsx`) — free-text input.
- `web-react/src/components/wizard/probes/DeviceConfigField.tsx` (+ `.test.tsx`) — pass `default`/`options` into the picker `dep`.
- `web-react/src/components/dashboard/ConfirmAction.tsx` (+ `.test.tsx`) — optional `message`.
- `web-react/src/components/wizard/probes/PortsCard.tsx` (+ `.test.tsx`) — delete confirmation.
- `web-react/src/components/wizard/WizardShell.tsx` (+ `.test.tsx`) — Exit Setup + honest copy.
- `web-react/src/helpers/wizard/wizardApi.ts` (+ `.test.ts`) — `cancelWizard`.
- `web-react/src/components/settings/settings.css` — containing block for the wizard confirm scrim.
- `web-react/tests/e2e/wizard.spec.ts` — confirm-dialog steps + an exit spec.

**Modify — backend**
- `blueprints/api_wizard/routes.py` — `POST /cancel`.
- `tests/web/test_api_wizard.py` — cancel tests.

---

### Task 1: C6 — board photos resolve against the PiFire origin

**Files:** Create `web-react/src/helpers/wizard/wizardAssets.ts` + `wizardAssets.test.ts`; modify `rsbuild.config.ts`, `components/wizard/ModuleCard.tsx`, `components/wizard/probes/DeviceForm.tsx`, `components/wizard/probes/DevicesCard.tsx`.

**Interfaces:** Produces `moduleImageUrl(baseUrl: string, image: string | undefined): string | undefined`.

- [ ] **Step 1: Write the failing pure test** — `web-react/src/helpers/wizard/wizardAssets.test.ts` (`.ts`, node project):

```ts
import { describe, expect, it } from "@rstest/core";
import { moduleImageUrl } from "./wizardAssets";

describe("moduleImageUrl", () => {
  it("prefixes a bare manifest filename with PiFire's static wizard path", () => {
    expect(moduleImageUrl("", "pcb_4.x.x.png")).toBe("/static/img/wizard/pcb_4.x.x.png");
  });

  it("keeps the configured PiFire origin when one is set", () => {
    expect(moduleImageUrl("http://pifire.local:5000", "ads1115.png")).toBe(
      "http://pifire.local:5000/static/img/wizard/ads1115.png",
    );
  });

  it("returns undefined for a module with no image so no <img> is rendered", () => {
    expect(moduleImageUrl("", undefined)).toBeUndefined();
    expect(moduleImageUrl("", "")).toBeUndefined();
  });
});
```

- [ ] **Step 2: Run, confirm it fails** — `bun run test src/helpers/wizard/wizardAssets.test.ts`.

- [ ] **Step 3: Create `web-react/src/helpers/wizard/wizardAssets.ts`.** Non-component logic, so it lives in `helpers/` (the `react-refresh/only-export-components` rule) and must not import from `components/`:

```ts
// PiFire serves the wizard's vendor board photos from Flask's DEFAULT static
// folder -- `app = Flask(__name__)` (app.py:49) over the repo-root `static/`
// directory -- and the legacy card template builds exactly this URL:
// `url_for('static', filename='img/wizard/' + moduleData['image'])`
// (blueprints/wizard/templates/wizard/_macro_wizard_card.html:7).
// Verified live: GET http://localhost:5000/static/img/wizard/custom.png -> 200.
//
// wizard_manifest.json stores a BARE FILENAME ("pcb_4.x.x.png"), so using it
// directly as an <img src> resolves against the React app's own origin and
// 404s. These photos are how a user identifies which board they physically
// have, so a broken image is a loss of function, not a cosmetic defect.
//
// `baseUrl` is the same PUBLIC_PIFIRE_URL-derived value the wizard API client
// uses (helpers/wizard/wizardRoutes.ts:4): an absolute origin when the app is
// pointed at a remote PiFire (plain <img> loads need no CORS), or "" in the
// default dev setup, where rsbuild proxies /static/img through to Flask.
const WIZARD_IMAGE_PATH = "/static/img/wizard";

export function moduleImageUrl(baseUrl: string, image: string | undefined): string | undefined {
  if (!image) return undefined;
  return `${baseUrl}${WIZARD_IMAGE_PATH}/${image}`;
}
```

- [ ] **Step 4: Add the dev-server proxy** in `web-react/rsbuild.config.ts`, inside `server.proxy` alongside the existing two entries:

```ts
    proxy: {
      "/socket.io": { target, ws: true, changeOrigin: true },
      "/api": { target, changeOrigin: true },
      // PiFire's own static assets -- currently just the wizard's board photos
      // under /static/img/wizard/. Scoped to /static/img and NOT bare /static:
      // rsbuild emits THIS app's bundles under /static/js and /static/css (see
      // web-react/dist), so a blanket /static proxy would hand every script and
      // stylesheet to Flask. rsbuild's default asset dirs are js/css/font/wasm/
      // image/media -- "img" is not one of them, so this prefix cannot collide.
      "/static/img": { target, changeOrigin: true },
    },
```

- [ ] **Step 5: Update the three `<img>` call sites.** Each already has a `baseUrl` in scope — `ModuleCard.tsx:24` (prop), `DeviceForm.tsx:19` (destructured prop), `DevicesCard.tsx:36` (prop). Compute the URL first and render on that, so a module without an image still renders no `<img>`:

`ModuleCard.tsx` — replace the `selected.image` block at `:115-117`:

```tsx
          {moduleImageUrl(baseUrl, selected.image) && (
            <img
              className="pf-module-image"
              src={moduleImageUrl(baseUrl, selected.image)}
              alt={selected.friendly_name}
            />
          )}
```

`DeviceForm.tsx` — replace `:22-24`:

```tsx
      {moduleImageUrl(baseUrl, moduleData.image) && (
        <img
          className="pf-module-image"
          src={moduleImageUrl(baseUrl, moduleData.image)}
          alt={moduleData.friendly_name}
        />
      )}
```

`DevicesCard.tsx` — replace `:117-119`:

```tsx
                {moduleImageUrl(baseUrl, modules[d.module]?.image) && (
                  <img
                    src={moduleImageUrl(baseUrl, modules[d.module]?.image)}
                    alt=""
                    width={48}
                    height={48}
                  />
                )}
```

Add `import { moduleImageUrl } from "../../helpers/wizard/wizardAssets";` (two `../` from `components/wizard/`, three from `components/wizard/probes/`).

- [ ] **Step 6: Add a component-level assertion** in `ModuleCard.test.tsx` — render with a module carrying `image: "pcb_4.x.x.png"` and `baseUrl=""`, then assert `screen.getByRole("img", { name: <friendly_name> })` has `src` `"/static/img/wizard/pcb_4.x.x.png"`. This is the pin that catches a future regression back to the bare filename.

- [ ] **Step 7: Run the gate.** `bun run typecheck && bun run lint && bun run test`.

- [ ] **Step 8: Verify against the real backend, not just the unit tests.** With Flask on `:5000` and `bun run dev` on `:5173`: `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/static/img/wizard/custom.png` must print `200`, and `curl -s -o /dev/null -w "%{http_code}\n" http://localhost:5173/` must still print `200` with the app's own JS loading (open it and check the console is clean). **The second half is the one that catches a botched proxy scope.** If the backend is not reachable, say so rather than marking this step done.

- [ ] **Step 9: Commit.**

### Task 2: C5 + I12 — free-text hardware fields with Discover

**Files:** Modify `components/wizard/fields/I2cBusPicker.tsx` + `.test.tsx`, `components/wizard/fields/UsbSerialPicker.tsx` + `.test.tsx`, `components/wizard/probes/DeviceConfigField.tsx` + `.test.tsx`.

**Interfaces:** Both pickers keep their prop shapes. `SettingsDependency` gains an optional `default?: string` (already present in the manifest for all 8 + 1 deps, just never typed).

- [ ] **Step 1: Write failing tests** in `I2cBusPicker.test.tsx`. The existing fixture `dep` (`:13-18`) carries `options` — **add a second fixture with no `options`**, mirroring the real manifest shape:

```ts
const optionlessDep: SettingsDependency = {
  friendly_name: "Distance Sensor Extended I2C Bus",
  description: "'CP2112' or 'MCP2221' auto-discovers the bridge by adapter name.",
  type: "i2c_bus_num",
  default: "CP2112",
  settings: ["platform", "devices", "distance", "i2c_bus_num"],
};
```

New cases:
1. *"renders a text input, not a select, so a manifest dep with no options is still fillable"* — render with `optionlessDep` and `value="CP2112"`; assert `screen.getByLabelText("Distance Sensor Extended I2C Bus")` has `tagName === "INPUT"` and `value === "CP2112"`, and that `container.querySelector("select")` is `null`. **This is the exact defect: all 8 grillplatform + 5 probe deps ship without `options`.**
2. *"typing an arbitrary bus value calls onChange"* — `fireEvent.change(input, { target: { value: "serial:0123ABC" } })`; assert `onChange` called with `"serial:0123ABC"`.
3. *"renders the dep description, which is the only place the CP2112 / serial: syntax is documented"* — assert the description text is in the document.
4. *"still offers manifest options as suggestions when present"* — render with the original `dep` (which has `options`) and assert both `Bus 1` and `Bus 2` appear as `<option>` elements inside a `<datalist>` linked by the input's `list` attribute.

The three existing Discover tests (`:48-90`) must still pass unchanged — Discover is not being touched.

- [ ] **Step 2: Mirror the same four cases** into `UsbSerialPicker.test.tsx` using the real `distance.sen0628.sen0628_device` shape (`type: "usb_serial_device"`, `default: "/dev/ttyACM0"`, `options` with four `/dev/tty*` paths) and assert that typing `/dev/ttyACM2` — a path **not** in `options` — reaches `onChange`.

- [ ] **Step 3: Run, confirm they fail** — `bun run test src/components/wizard/fields`.

- [ ] **Step 4: Add `default` to `SettingsDependency`** in `helpers/wizard/wizardTypes.ts`:

```ts
export interface SettingsDependency {
  friendly_name: string;
  description?: string;
  type?: "i2c_bus_num" | "usb_serial_device";
  options?: Record<string, string>;
  /** Manifest fallback (e.g. "CP2112"). Present on every i2c_bus_num dep. */
  default?: string;
  hidden?: boolean;
  settings: string[];
}
```

- [ ] **Step 5: Rewrite `I2cBusPicker`'s control.** Drop the `SelectField` import and the `options` → `SelectField` mapping; keep `useState`, `DiscoveryPanel`, `handleDiscover` and the `kindValue` hint exactly as they are:

```tsx
  // Flask never used a <select> for this type: _macro_wizard_card.html:36-37
  // dispatches i2c_bus_num to render_input_i2c_bus_num, which is a free-text
  // <input> plus a Discover button (_macro_probes_config.html:542-552). It has
  // to be free text -- the accepted values are an adapter NAME ("CP2112",
  // "MCP2221"), a "serial:<ISERIAL>" match, a bare /dev/i2c-N number, a pyftdi
  // URL, or an MCP2221 serial. No enumerable option list covers that, which is
  // why all 8 grillplatform and all 5 probe i2c_bus_num deps in
  // wizard_manifest.json ship with no `options` key at all. Rendering a select
  // over that empty map produced a control with nothing to choose, and a fresh
  // install could not be completed.
  //
  // `options`, when a dep does carry it, becomes non-binding <datalist>
  // suggestions rather than the only permitted values.
  const listId = `${dep.friendly_name}-suggestions`;
  const suggestions = Object.keys(dep.options ?? {});

  return (
    <div className="pf-field-column">
      <label className="pf-field">
        <span className="pf-field-label">{dep.friendly_name}</span>
        <input
          className="pf-input"
          type="text"
          value={value}
          placeholder={dep.default ?? ""}
          list={suggestions.length > 0 ? listId : undefined}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      {suggestions.length > 0 && (
        <datalist id={listId}>
          {suggestions.map((optValue) => (
            <option key={optValue} value={optValue}>
              {dep.options?.[optValue]}
            </option>
          ))}
        </datalist>
      )}
      {dep.description && <span className="pf-field-hint">{dep.description}</span>}
      <span className="pf-field-hint">Detected kind: {kindValue}</span>
      <button type="button" onClick={handleDiscover} disabled={loading}>
        {loading ? "Scanning…" : "Discover"}
      </button>
      {result && <DiscoveryPanel result={result} onPick={onChange} />}
    </div>
  );
```

- [ ] **Step 6: Apply the identical control to `UsbSerialPicker`** (same block minus the `Detected kind` line, which that component does not have). Cite `_macro_wizard_card.html:38-39` and `_macro_probes_config.html:587-597` in its comment.

- [ ] **Step 7: Fix the probe-side `dep` construction.** `probes/DeviceConfigField.tsx:46-50` builds `dep` with no `options` and no `default`, so probe device forms hit the empty control unconditionally. Replace with:

```tsx
  // The pickers take a SettingsDependency; a probe device's ProbeConfigField is
  // the same information under different key names. `default` must be carried
  // across or the picker loses its placeholder ("CP2112") -- 5 probe modules
  // (ads1115_adafruit, ads1015_adafruit, mcp9600_adafruit, prototype, ads1115)
  // ship an i2c_bus_num field and NONE of them carries an option list.
  const dep: SettingsDependency = {
    friendly_name: field.friendly_name,
    description: field.description,
    default: typeof field.default === "string" ? field.default : undefined,
    settings: [],
  };
```

with `import type { SettingsDependency } from "../../../helpers/wizard/wizardTypes";`. Note the `unknown`-typed `field.default` (`probeTypes.ts:43`) — narrow it, do not cast.

- [ ] **Step 8: Add a `DeviceConfigField.test.tsx` case** — render an `i2c_bus_num` field with `default: "CP2112"` and `value: undefined`; assert a text input is rendered with placeholder `CP2112` and no `<select>`.

- [ ] **Step 9: Run the gate.** `bun run typecheck && bun run lint && bun run test`. **Check `SelectField` is still imported where it is still used** (`ModuleCard.tsx:77-89` generic deps, `DeviceConfigField.tsx:69-82` the `list` case) — dropping both picker imports must not leave an unused import behind (`biome check` will catch it; do not silence it).

- [ ] **Step 10: Commit.**

### Task 3: C7 — confirm before a cascading delete

**Files:** Modify `components/dashboard/ConfirmAction.tsx` + `.test.tsx`, `components/wizard/probes/DevicesCard.tsx` + `.test.tsx`, `components/wizard/probes/PortsCard.tsx` + `.test.tsx`, `components/wizard/steps/ProbesStep.test.tsx`, `components/settings/settings.css`, `tests/e2e/wizard.spec.ts`.

**Interfaces:** `ConfirmAction` gains `message?: string`. Existing consumer `UnitsTab.tsx:62-67` is unchanged.

- [ ] **Step 1: Write the failing `ConfirmAction` test** — add to `ConfirmAction.test.tsx`:

```tsx
  it("renders an optional message body below the title", () => {
    render(
      <ConfirmAction
        open
        title="Delete Probe Device?"
        message="All probes associated with this device will also be deleted."
        onConfirm={rs.fn()}
        onCancel={rs.fn()}
      />,
    );
    expect(screen.getByText("Delete Probe Device?")).toBeInTheDocument();
    expect(
      screen.getByText("All probes associated with this device will also be deleted."),
    ).toBeInTheDocument();
  });
```

- [ ] **Step 2: Write failing `DevicesCard` tests.** With one device that has attached probes:
  1. clicking `Delete` does **not** call `onChange` and shows the dialog;
  2. the dialog names the cascade — assert the text `All probes associated with this device will also be deleted.` is present;
  3. `Cancel` closes it with `onChange` still uncalled;
  4. `Confirm` calls `onChange` **once** with a map whose `probe_devices` no longer contains the device *and* whose `probe_info` no longer contains its probes (assert both — the cascade is the reason this task exists).

- [ ] **Step 3: Write failing `PortsCard` tests** — same shape, title `Delete Probe?`, no cascade message. Include one case where the confirmed delete is **rejected** by the Primary-probe invariant (`probeReducer.ts:309-319`): confirm, then assert the `role="alert"` guard error renders and `onChange` was not called. The dialog must close either way; the error surfaces in the existing `{!form && error && <p role="alert">}` slot (`PortsCard.tsx:82`).

- [ ] **Step 4: Run, confirm all fail** — `bun run test src/components/wizard/probes src/components/dashboard/ConfirmAction.test.tsx`.

- [ ] **Step 5: Implement `ConfirmAction`'s message slot:**

```tsx
interface Props {
  open: boolean;
  title: string;
  /** Optional body copy under the title — for consequences the title can't
      carry, e.g. a cascading delete. `.pf-modal-title` is a bold, centred
      20px headline, so a second sentence does not belong up there. */
  message?: string;
  onConfirm(): void;
  onCancel(): void;
}

export function ConfirmAction({ open, title, message, onConfirm, onCancel }: Props) {
  if (!open) return null;
  return (
    <div className="pf-modal-scrim" onClick={onCancel}>
      <div className="pf-modal" onClick={(e) => e.stopPropagation()}>
        <div className="pf-modal-title">{title}</div>
        {message && <div className="pf-modal-message">{message}</div>}
        <div className="pf-modal-actions">
          <button className="pf-modal-btn" onClick={onCancel}>
            Cancel
          </button>
          <button className="pf-modal-btn danger" onClick={onConfirm}>
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}
```

Add `.pf-modal-message` to `dashboard.css` next to `.pf-modal-title` (`:183-187`) — normal weight, centred, muted (`#c9bdab`), `font: 400 15px "Barlow"`.

- [ ] **Step 6: Wire `DevicesCard`.** Add `const [pendingDelete, setPendingDelete] = useState<string | null>(null);` — plain state, **no effect**; there is nothing derived here. Change `:127`:

```tsx
                <button type="button" onClick={() => setPendingDelete(d.device)}>
                  Delete
                </button>
```

and render at the end of the `<section>`, mirroring `UnitsTab.tsx:62-67`:

```tsx
      {/* Deleting a device CASCADES: probeReducer.deleteDevice (probeReducer.ts:113-129)
          drops every probe_info row whose `device` matches and scrubs those labels out
          of any virtual device's probes_list. Legacy warned about exactly this before
          acting -- _macro_probes_config.html:70-89 ("delProbeDeviceModal"). */}
      <ConfirmAction
        open={pendingDelete !== null}
        title="Delete Probe Device?"
        message="All probes associated with this device will also be deleted."
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete !== null) onChange(deleteDevice(probeMap, pendingDelete));
          setPendingDelete(null);
        }}
      />
```

- [ ] **Step 7: Wire `PortsCard`** the same way — `pendingDelete: string | null` holding the probe `label`, `:99` becomes `onClick={() => setPendingDelete(p.label)}`, and:

```tsx
      <ConfirmAction
        open={pendingDelete !== null}
        title="Delete Probe?"
        message="This probe will be removed from the configuration."
        onCancel={() => setPendingDelete(null)}
        onConfirm={() => {
          if (pendingDelete !== null) del(pendingDelete);
          setPendingDelete(null);
        }}
      />
```

`del` (`:45-53`) already handles the guard-rejection path by setting `error`; leave it alone.

- [ ] **Step 8: Give the scrim a containing block.** `.pf-modal-scrim` is `position: absolute` (`dashboard.css:166`), and the wizard has **no** positioned ancestor anywhere (there is no `pf-wizard-*` CSS at all), so the overlay would escape the card. Add to `settings.css`:

```css
/* The wizard's probe cards host ConfirmAction, whose .pf-modal-scrim is
   position:absolute (dashboard.css:165-172) rather than fixed. Without a
   positioned ancestor it resolves against the initial containing block and
   floats free of the card it belongs to. */
.pf-probes-card {
  position: relative;
}
```

**Then look at it in a browser** — this is a visual property that unit tests cannot confirm. If the confirm dialog does not land over its card, say so and fix it rather than marking the step done.

- [ ] **Step 9: Update the tests the confirmation breaks.**
  - `steps/ProbesStep.test.tsx:72` — insert a `fireEvent.click(screen.getByRole("button", { name: "Confirm" }));` after the Delete click.
  - `tests/e2e/wizard.spec.ts:72,76` — same, after each `Delete` click. This file has uncommitted local changes; **read it first and rebase onto what is there.**

- [ ] **Step 10: Run the gate**, then the wizard e2e (`bun run test:e2e tests/e2e/wizard.spec.ts`) with the backend up. The e2e suite mutates one shared live PiFire instance and runs `workers: 1` (`playwright.config.ts:22`) — do not parallelize it.

- [ ] **Step 11: Commit.**

### Task 4: C4a — `POST /api/wizard/cancel`

**Files:** Modify `blueprints/api_wizard/routes.py`, `tests/web/test_api_wizard.py`.

**Interfaces:** Produces `POST /api/wizard/cancel` → `200 {"result": "success"}`; clears `settings["globals"]["first_time_setup"]`; leaves the wizard draft blob untouched.

- [ ] **Step 1: Write failing tests** in `tests/web/test_api_wizard.py`, using the existing `ds` + `client` fixtures (`:10-14`):

```python
def test_cancel_clears_first_time_setup(ds, client):
    settings = read_settings()
    settings["globals"]["first_time_setup"] = True
    write_settings_store(settings)

    resp = client.post("/api/wizard/cancel")
    assert resp.status_code == 200
    assert resp.get_json()["result"] == "success"
    # The React dashboard bounces back to /wizard while this flag is set
    # (web-react/src/components/DashboardRoute.tsx:23-35), so an exit that
    # leaves it True is an inescapable loop, not a partial fix.
    assert read_settings()["globals"]["first_time_setup"] is False
    assert client.get("/api/wizard/state").get_json()["first_time_setup"] is False


def test_cancel_preserves_the_draft(ds, client):
    draft = {
        "selections": {"display": "ili9341b"},
        "settings_dep_values": {"display": {}},
        "display_config": {"ili9341b": {"rotation": 90}},
    }
    client.post("/api/wizard/draft", data=json.dumps(draft), content_type="application/json")

    assert client.post("/api/wizard/cancel").status_code == 200

    # Legacy _wizard_cancel (blueprints/wizard/routes.py:71-74) does not touch
    # the install blob either, and the welcome step promises the draft is kept.
    body = client.get("/api/wizard/state").get_json()
    assert body["has_draft"] is True
    assert body["selections"]["display"] == "ili9341b"


def test_cancel_is_idempotent_when_not_a_fresh_install(ds, client):
    settings = read_settings()
    settings["globals"]["first_time_setup"] = False
    write_settings_store(settings)

    assert client.post("/api/wizard/cancel").status_code == 200
    assert read_settings()["globals"]["first_time_setup"] is False
```

- [ ] **Step 2: Run, confirm they fail** (404, no such route) — `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py -k cancel`.

- [ ] **Step 3: Add `write_settings`** to the `common.datastore_accessors` import block at `blueprints/api_wizard/routes.py:15-22` (keep it alphabetical: after `store_wizard_install_info`).

- [ ] **Step 4: Add the route.** Place it after `wizard_draft` (`:181`), before `wizard_scan`:

```python
@api_wizard_bp.route("/cancel", methods=["POST"])
def wizard_cancel():
    """Leave the wizard without installing anything -- the React counterpart of
    legacy `_wizard_cancel` (blueprints/wizard/routes.py:71-74, dispatched as
    ("POST", "cancel") at :265). Returns JSON instead of legacy's redirect("/");
    the client navigates itself.

    Clearing `first_time_setup` is the whole point, not a side effect: the React
    dashboard re-checks that flag after mount and navigates straight back to
    /wizard while it is True (web-react/src/components/DashboardRoute.tsx:23-35),
    so an exit that left it set would be an inescapable loop.

    Deliberately does NOT touch the wizard draft blob. The client POSTs /draft
    before calling this and /state resumes it on the next visit, which is what
    makes the welcome step's "your progress is saved as a draft" promise true.
    Legacy does not clear it either.

    Uses plain write_settings(), matching legacy -- NOT
    save_settings_and_flag_update() (common/app.py:401-413). No control
    update-flag is set because nothing about the running hardware changed;
    no install was started and no module configuration was applied.
    """
    settings = read_settings()
    settings["globals"]["first_time_setup"] = False
    write_settings(settings)
    return jsonify({"result": "success"}), 200
```

- [ ] **Step 5: Run the tests, confirm they pass.** Then run the whole file — `uv run pytest tests/web/test_api_wizard.py` — to confirm the new route did not disturb `/state` or `/draft`.

- [ ] **Step 6: `uvx ruff format blueprints/api_wizard/routes.py tests/web/test_api_wizard.py` and `uvx ruff check` them.** Standing repo rule, every commit. Do not "fix" the parenthesis-free `except TypeError, ValueError` at `:57` — that is ruff-canonical on 3.14+.

- [ ] **Step 7: Commit.**

### Task 5: C4b — Exit Setup, and the sentence that must stop lying

**Files:** Modify `helpers/wizard/wizardApi.ts` + `wizardApi.test.ts`, `components/wizard/WizardShell.tsx` + `WizardShell.test.tsx`.

**Interfaces:** Produces `cancelWizard(baseUrl: string): Promise<boolean>`. Depends on Task 4's route.

**The plan's hard requirement:** this task must leave `WizardShell.tsx`'s Welcome copy true. Either the exit works and the sentence stands, or the sentence goes. Do not land one half.

- [ ] **Step 1: Write the failing api test** in `wizardApi.test.ts`, following the existing fetch-mock style in that file: `cancelWizard` POSTs to `${baseUrl}/api/wizard/cancel` and returns `r.ok`.

- [ ] **Step 2: Write failing `WizardShell` tests.** First **extend the harness** — `renderShell` (`:59-71`) registers only `/wizard`, so `navigate("/")` has nowhere to land:

```tsx
function renderShell(state: WizardState) {
  const router = createMemoryRouter(
    [
      { path: "/wizard", element: <WizardShell />, loader: () => state },
      // Exit Setup navigates here; without a matching route react-router has
      // nothing to render and the assertion below can't distinguish success
      // from a silently swallowed navigation.
      { path: "/", element: <div>dashboard</div> },
    ],
    { initialEntries: ["/wizard"] },
  );
  return render(<RouterProvider router={router} />);
}
```

Add `cancelWizard: (...args: unknown[]) => cancelWizardMock(...args)` to the `rs.mock` block at `:13-20` and reset it in `afterEach`. Cases:
  1. an `Exit Setup` control is present on the Welcome step;
  2. clicking it calls `saveDraft` **and then** `cancelWizard` — assert the order (the draft is what makes the copy true, and it must be saved before the flag is cleared);
  3. on success the router lands on `/` — assert `screen.getByText("dashboard")`;
  4. on failure (`cancelWizardMock.mockResolvedValue(false)`) an error is shown and the router stays on `/wizard`;
  5. **the copy test** — assert the Welcome text names the control that exists: `expect(screen.getByText(/Exit Setup/)).toBeInTheDocument()` within the welcome step, and that no unqualified "You can leave at any point" claim remains without it;
  6. Exit is **not** rendered once an install is running (`finishState.ok === true`).

- [ ] **Step 3: Run, confirm they fail.**

- [ ] **Step 4: Add `cancelWizard`** to `helpers/wizard/wizardApi.ts`, next to `saveDraft`:

```ts
export async function cancelWizard(baseUrl: string): Promise<boolean> {
  const r = await fetch(url(baseUrl, "cancel"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  return r.ok;
}
```

- [ ] **Step 5: Implement the exit in `WizardShell.tsx`.** Import `useNavigate` from `"react-router"` and `cancelWizard` from the api module; add two pieces of state (plain `useState`, no effect):

```tsx
  const navigate = useNavigate();
  const [exiting, setExiting] = useState(false);
  const [exitError, setExitError] = useState<string | null>(null);

  async function handleExit() {
    setExiting(true);
    // Save first, cancel second. The draft is what makes the welcome step's
    // promise true, and it must be persisted before the flag that brought the
    // user here is cleared.
    try {
      await saveDraft(BASE_URL, working);
    } catch (err) {
      console.warn("Wizard: failed to save draft on exit", err);
    }
    const ok = await cancelWizard(BASE_URL);
    setExiting(false);
    if (!ok) {
      setExitError("Couldn't leave setup — please try again.");
      return;
    }
    setExitError(null);
    // /api/wizard/cancel has cleared globals.first_time_setup, so
    // DashboardRoute's post-mount check (DashboardRoute.tsx:23-35) will NOT
    // bounce us straight back here. Navigating without that clear would loop.
    navigate("/");
  }
```

Render it in the header (`:196-205`), so it is reachable from every step rather than only where the footer shows — but suppress it while an install is running, using the existing `hideFooter` signal:

```tsx
      <header className="pf-wizard-header">
        <div className="pf-wizard-title">Setup Wizard</div>
        <div className="pf-wizard-steps">
          {STEPS.map((s, i) => (
            <span key={s} className={`pf-wizard-step-indicator ${i === step ? "active" : ""}`}>
              {STEP_LABELS[s]}
            </span>
          ))}
        </div>
        {!hideFooter && (
          <button
            type="button"
            className="pf-btn pf-wizard-exit"
            disabled={exiting}
            onClick={() => void handleExit()}
          >
            {exiting ? "Leaving…" : "Exit Setup"}
          </button>
        )}
      </header>
      {exitError && <p className="pf-wizard-finish-error">{exitError}</p>}
```

`hideFooter` is declared at `:192`, after `renderStepBody`; **move its declaration above the `return`** so the header can read it, or the build fails on use-before-declaration.

- [ ] **Step 6: Make the Welcome copy true.** Replace `WizardShell.tsx:157-161` with:

```tsx
            <p>
              This wizard walks through configuring PiFire's hardware modules — grill platform,
              probes, display, and distance/hopper sensing. Use <strong>Exit Setup</strong> to
              leave at any point; your progress is saved as a draft and picked up next time.
            </p>
```

**This is not optional polish.** The old sentence asserted a capability the shipped UI did not have; it stays wrong until it names the control that now exists.

- [ ] **Step 7: Run the gate.**

- [ ] **Step 8: Commit** — the route (Task 4), the client call, the control, and the copy are one user-visible change; do not split the copy fix into a follow-up.

### Task 6: e2e proof and the full gate

**Files:** Modify `web-react/tests/e2e/wizard.spec.ts`.

- [ ] **Step 1: Add an exit spec.** It touches real global settings, so it must restore them:

```ts
test("Exit Setup leaves the wizard and does not bounce back", async ({ page }) => {
  // first_time_setup is real global state on the shared instance. Capture it
  // and put it back, pass or fail -- the next spec's /wizard load depends on it.
  const before = await (await page.request.get("/api/settings")).json();
  try {
    await page.goto("/wizard");
    await expect(page.getByRole("heading", { name: "Welcome" })).toBeVisible();
    await page.getByRole("button", { name: "Exit Setup" }).click();

    await expect(page).toHaveURL(/\/$/);
    const s = await (await page.request.get("/api/wizard/state")).json();
    expect(s.first_time_setup).toBe(false);
    // The draft survived -- that is what the welcome copy promises.
    expect(s.has_draft).toBe(true);

    // The redirect trap: DashboardRoute re-checks the flag after mount, so a
    // reload must STAY on the dashboard rather than bouncing to /wizard.
    await page.reload();
    await page.waitForTimeout(1000);
    await expect(page).toHaveURL(/\/$/);
  } finally {
    await page.request.post("/api/wizard/draft", { data: { clear: true } });
    // restore first_time_setup to whatever it was
  }
});
```

**Confirm the restore path before writing it** — check whether `/api/settings` accepts a write of `globals.first_time_setup` (`blueprints/api/` + `common/api_commands.py`). If it does not, do not fake it: state that plainly and restore via a direct datastore call in a `scripts/`-style helper, or narrow the spec to assert only what can be safely reverted.

- [ ] **Step 2: Add an image assertion** to an existing wizard spec — on the Grill Platform step, `expect(page.locator("img.pf-module-image")).toHaveJSProperty("naturalWidth", <non-zero>)`, i.e. the browser actually decoded the file. A `src` check alone would have passed before this slice too.

- [ ] **Step 3: Add a picker assertion** — on the Grill Platform step with a board selected, `expect(page.getByLabel(/Extended I2C Bus/)).toHaveAttribute("type", "text")` and fill it with `serial:TESTVALUE`, then Next, then assert the value round-trips through `GET /api/wizard/state`'s `settings_dep_values.grillplatform`. This is the end-to-end proof that C5 unblocks a fresh install. Restore the draft afterwards.

- [ ] **Step 4: Full gate.** From `web-react/`: `bun run typecheck && bun run lint && bun run test && bun run build && bun run gen:types:check`. From the repo root: `QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/web/test_api_wizard.py tests/web/test_page_wizard.py` and `uvx ruff check blueprints/ tests/`.

- [ ] **Step 5: Re-run the Playwright suite in the MAIN checkout, not a worktree.** Agent worktrees without chromium silently SKIP browser tests; a green run there proves nothing. `bun run test:e2e` with control.py + gunicorn up.

- [ ] **Step 6: Repo-hygiene check** — no `os_info.json` / `settings.json` / `pelletdb.json` left at the repo root by the test run.

- [ ] **Step 7: Commit.**

---

## Parallelization

Concurrency requires **isolated jj workspaces**; disjoint file lists alone are not enough in this repo.

- **Wave 1 — Task 1 ∥ Task 2 ∥ Task 4.** Fully disjoint. Task 1 touches `wizardAssets.ts`, `rsbuild.config.ts`, `ModuleCard.tsx`, `DeviceForm.tsx`, and only the `<img>` block of `DevicesCard.tsx`. Task 2 touches `fields/*`, `DeviceConfigField.tsx`, `wizardTypes.ts`. Task 4 is Python only.
  - ⚠️ **Task 1 and Task 3 both edit `DevicesCard.tsx`.** They are in different waves for that reason. If you must overlap them, Task 1 goes first and Task 3 rebases.
- **Wave 2 — Task 3 ∥ Task 5.** Task 3 needs Task 1 merged (shared `DevicesCard.tsx`). Task 5 needs Task 4's route. They are disjoint from each other: Task 3 is `ConfirmAction`/`probes/*`/CSS, Task 5 is `WizardShell`/`wizardApi`.
  - ⚠️ **Both touch `tests/e2e/wizard.spec.ts`** (Task 3 Step 9, Task 5 has none but Task 6 does). Assign the e2e edits to Task 3 only in this wave; Task 6 rebases.
- **Wave 3 — Task 6 alone.** Needs everything, and drives one shared stateful backend at `workers: 1`.

---

## Self-Review

**Spec coverage:** C6 → T1 (+T6 S2 proof); C5/I12 → T2 (+T6 S3 proof); C7 → T3; C4 → T4 (route) + T5 (control **and** the copy, one commit). Suggested ordering C6 → C5/I12 → C7 → C4 is preserved; C4 is split across two tasks only because the backend half is independently testable and unblocks Wave-1 parallelism.

**Placeholder scan:** none. Every step names its file, its line range, and the verified contract it is coding against. Two steps deliberately stop short of asserting a result I could not verify: T1 S8 (live dev-server proxy check) and T3 S8 (scrim rendering) both instruct the implementer to report rather than tick if the check cannot be run. T6 S1 requires confirming the settings-write restore path before writing it.

**Type consistency:** `moduleImageUrl` defined T1, consumed T1 (3 sites). `SettingsDependency.default` added T2, consumed by both pickers and `DeviceConfigField`. `ConfirmAction.message` added T3, consumed T3 (2 sites), leaves `UnitsTab.tsx:62-67` untouched. `cancelWizard` defined T5 S4 against the route defined T4 S4 — both agree on `POST /api/wizard/cancel` returning `200 {"result": "success"}`, which is the shape every other route in that blueprint uses.

**Could not verify:** (a) that a blanket-vs-scoped `/static` proxy behaves as reasoned at runtime — the React dev server was not running during research, only Flask on `:5000` (the rsbuild asset-directory defaults *were* read out of `node_modules`, so the collision claim is evidence-based, but the proxy itself is untested — T1 S8 exists for this); (b) `.pf-modal-scrim` placement inside the wizard, since the wizard has no CSS and this is a rendered-geometry question (T3 S8); (c) whether `/api/settings` can write `globals.first_time_setup` for the e2e restore (T6 S1).
