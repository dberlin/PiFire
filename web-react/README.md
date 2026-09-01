# PiFire React UI — POC spike

A React/TypeScript reimplementation of PiFire's on-device **QT Quick** controller
UI, delivered as a **remote browser app**. Plan:
`docs/superpowers/plans/2026-07-21-react-qtquick-ui.md`.

This spike is the highest-signal slice — it de-risks the two hard problems:

1. **The data/command contract** — it binds to PiFire's _existing_ API (no backend
   change) with a read/write split: `listen_app_data` → `socket_dash_data` (SocketIO)
   for live data, and the REST command grammar `POST /api/set|cmd/…` → `process_command`
   → validated control deltas for commands. The connection and command-building
   logic that implements this now live in `@pifire/core` (`liveConnection.ts`,
   `command.ts`) — a platform-free package shared with `mobile/`, the native
   Expo app — not in `web-react/src/helpers/` directly. See
   `packages/pifire-core/src/liveConnection.ts`,
   `packages/pifire-core/src/command.ts`, `blueprints/mobile/socket_io.py`,
   and `common/api_commands.py`.
2. **Animation fidelity** — the signature **270° gauge** (`src/components/dashboard/GrillGauge.tsx`)
   with value-arc easing (250 ms OutCubic), pulsing glow, and setpoint marker,
   plus the accent-theme token system (`src/theme.css`, ported from
   `display/qml/Theme.qml`; Ember/Ice/Crimson switchable live). The gauge's
   geometry math is likewise shared via `@pifire/core/gaugeMath`, reused by
   `mobile/`'s own gauge component.

Scope of the spike: DashScreen **center column only** (gauge + control panel +
one food-probe line). Deferred: food-probe column, right column, menus, keypads,
splash/sleep, responsive breakpoints — see the plan.

## Module naming convention

- **Two trees, one direction.** `src/components/` holds React component
  modules (`.tsx`); `src/helpers/` holds non-component logic (pure functions,
  API clients, hooks). Feature grouping (`dashboard/`, `settings/`, …) is
  preserved inside each tree, e.g. `components/dashboard/GrillGauge.tsx` next
  to `helpers/dashboard/deriveView.ts`. **`helpers/` must never import from
  `components/`** — `components/` → `helpers/` is the only allowed direction.
  Root-level infra (`main.tsx` and friends) may import from either tree.
  Tests stay co-located with what they test, in whichever tree that is.
- **`PascalCase.tsx`** — React components only, one exported component per file,
  named export matching the filename (`ControlButtons.tsx` → `ControlButtons`).
- **`camelCase.ts`** — non-component logic (pure functions, API clients, hooks
  in `useX.ts`).
- **A module must NOT share a case-folded name with any sibling module** — e.g.
  `controlButtons.ts` next to `ControlButtons.tsx` is forbidden. On
  case-insensitive filesystems (macOS/Windows) both match `./ControlButtons`,
  and TypeScript's extension priority (`.ts` before `.tsx`) silently resolves
  the import to the _logic_ file — so the code builds on Linux and breaks on a
  Mac. When a component's logic wants its own module, name it after what it
  exports (`buttonsForMode.ts`), never a case-variant of the component.
- Tests: `*.test.tsx` = component tests (jsdom project), `*.test.ts` = pure
  tests (node project) — the rstest env split keys off exactly this.
- Enforced by `tests/unit/structure.test.ts`, which fails on (a) any case-folded
  module collision — the only tripwire that fires on Linux, where the
  filesystem never surfaces the problem — and (b) any `helpers/` module that
  imports from `components/`, enforcing the one-way layering rule above.

## Run

This is part of a bun workspace (`workspaces: ["web-react", "packages/*",
"mobile"]` in the repo-root `package.json`) that also contains `mobile/`
(the native Expo app) and `packages/pifire-core` (the shared, platform-free
package both UIs are built on — contract types, the REST command grammar,
the SocketIO live connection, gauge geometry, and dashboard-derivation
logic). `bun install` therefore runs from the **repository root**, not from
`web-react/` — there is exactly one lockfile, at the root:

```bash
cd /path/to/PiFire   # repo root
bun install
```

Then, from `web-react/`:

```bash
cd web-react
bun run demo       # http://localhost:5173  — LIVE test data, no Pi needed
bun run dev        # connects to a real PiFire (see PUBLIC_PIFIRE_URL)
bun run test       # unit tests (rstest)
bun run build      # type-check + production build
```

**Try it with no hardware:** `bun run demo` runs a live simulator
(`src/helpers/demoData.ts`) — a "Hold at 225°F" cook where the primary eases up to
setpoint and wobbles, the food probe climbs toward its target, and the auger
pulses. You'll see the gauge sweep, glow pulse, and header dot pulse. The status
badge reads `DEMO`. Commands are logged to the console instead of sent.

**Against a real PiFire:** `PUBLIC_PIFIRE_URL=http://<pi-host>:5000 bun run dev`
(defaults to `http://localhost:5000`). The dev server proxies `/socket.io` there;
the badge reads `LIVE` once connected. If PiFire is **not reachable**, `dev` shows
an explicit "PiFire not reachable — tried `<URL>`" screen and keeps retrying — it
does **not** fake data. (For test data with no Pi, use `bun run demo`.)

## Running against the real backend (prototype)

The dev server proxies both `/socket.io` (reads) and `/api` (REST command
writes) to `PUBLIC_PIFIRE_URL` (default `http://localhost:5000`), so the app can
talk to a running PiFire instance without CORS.

From the repo root (`/home/dannyb/sources/PiFire`), in two terminals, start the
prototype backend:

```bash
uv run python control.py                                              # control loop, prototype grill platform → datastore
uv run gunicorn -k gthread --threads 25 -b 0.0.0.0:5000 -w 1 app:app  # web app (Flask+SocketIO)
```

`gunicorn` is how production runs the web app (`auto-install/supervisor/webapp.conf`);
`python app.py` trips Werkzeug's production guard, so don't use it here.

Verify the backend is up:

```bash
curl -s http://localhost:5000/api/current | head -c 200
```

This should return JSON with a `current` object. Leave both processes running,
then from `web-react/`:

```bash
bun run dev   # http://localhost:5173, proxied to :5000
```

With the backend running, `http://localhost:5173/api/current` (and
`/socket.io`) resolve through the rsbuild proxy to the same JSON/events the
backend serves directly. `bun run demo` remains the fully offline path (no
backend required) for UI-only iteration.

`src/helpers/fixture.ts`'s `FIXTURE_DASH` is a real `socket_dash_data` payload
captured from this prototype backend via a one-shot python-socketio client
(see git history of that file for the exact capture snippet).
