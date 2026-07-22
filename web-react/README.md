# PiFire React UI — POC spike

A React/TypeScript reimplementation of PiFire's on-device **QT Quick** controller
UI, delivered as a **remote browser app**. Plan:
`docs/superpowers/plans/2026-07-21-react-qtquick-ui.md`.

This spike is the highest-signal slice — it de-risks the two hard problems:

1. **The data/command contract** — it binds to PiFire's *existing* API (no backend
   change) with a read/write split: `listen_app_data` → `socket_dash_data` (SocketIO)
   for live data, and the REST command grammar `POST /api/set|cmd/…` → `process_command`
   → `write_control` for commands. See `src/useDashData.ts`, `src/command.ts`,
   `blueprints/mobile/socket_io.py`, and `common/api_commands.py`.
2. **Animation fidelity** — the signature **270° gauge** (`src/components/Gauge.tsx`)
   with value-arc easing (250 ms OutCubic), pulsing glow, and setpoint marker,
   plus the accent-theme token system (`src/theme.css`, ported from
   `display/qml/Theme.qml`; Ember/Ice/Crimson switchable live).

Scope of the spike: DashScreen **center column only** (gauge + control panel +
one food-probe line). Deferred: food-probe column, right column, menus, keypads,
splash/sleep, responsive breakpoints — see the plan.

## Run

Uses [bun](https://bun.sh) as the package manager / runner.

```bash
cd web-react
bun install
bun run demo       # http://localhost:5173  — LIVE test data, no Pi needed
bun run dev        # connects to a real PiFire (see PUBLIC_PIFIRE_URL)
bun run test       # unit tests (vitest)
bun run build      # type-check + production build
```

**Try it with no hardware:** `bun run demo` runs a live simulator
(`src/demoData.ts`) — a "Hold at 225°F" cook where the primary eases up to
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

`src/fixture.ts`'s `FIXTURE_DASH` is a real `socket_dash_data` payload
captured from this prototype backend via a one-shot python-socketio client
(see git history of that file for the exact capture snippet).
