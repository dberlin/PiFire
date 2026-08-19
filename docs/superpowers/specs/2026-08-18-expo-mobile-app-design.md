# Expo Mobile App Design

## Scope

A native iOS and Android **cook companion** app built with Expo, added to this
repository as a third client of PiFire's existing API. It covers watching and
running a cook from a phone: the dashboard gauge, probes, mode and setpoint
control, timers, history, and event alerts.

It deliberately does **not** cover settings forms, the wizard, the tuner, admin,
pellet-database editing, or recipe authoring. Those stay on the web UI, which is
already the right surface for them.

The backend is not modified. The app binds to the same contract the web UI uses:
SocketIO `socket_dash_data` for live reads (`blueprints/mobile/socket_io.py`) and
the REST command grammar `POST /api/set|cmd/…` for writes
(`blueprints/api/routes.py` → `common/api_commands.py`). No new endpoints, no
schema changes, no auth changes.

Logic the two clients must agree on is extracted into a shared package rather
than duplicated. That extraction is part of this work, and it changes
`web-react`'s imports and the repository's package layout.

## Architecture

### Repository layout

The repository becomes a bun workspace:

```
package.json                  # workspaces: ["web-react", "mobile", "packages/*"]
bun.lock                      # moves here from web-react/
packages/pifire-core/         # @pifire/core — shared, platform-free
web-react/                    # existing web client, imports @pifire/core
mobile/                       # new Expo app, imports @pifire/core
```

`pifire_build_web_ui()` in `auto-install/pifire-install-common.sh` is **not
changed**. Its `cd web-react && bun install --frozen-lockfile && bun run build`
keeps working as-is: bun finds the root lockfile, and the isolated linker still
gives `web-react/node_modules` only web-react's dependencies.

The one consequence, verified by measurement and confirmed by bun's
documentation ("`bun install` installs dependencies for all workspaces in the
monorepo"), is that the grill *downloads* the React Native tree it never uses.
Adding `--filter web-react` to that line scopes the install and works with
`--frozen-lockfile`, but it is a deliberate non-goal here: it is a one-word
change available at any time if grill update duration proves annoying, and it is
not worth touching a production update path preemptively.

### The shared package: `@pifire/core`

Platform-free TypeScript. No DOM, no React Native, no React Query. It holds
exactly what both clients must agree on:

- **`src/contracts/`** — the generated contract types. These are emitted by
  **Python**, not by a bun script: `common/web_contracts/export.py` writes them
  from the Pydantic registry. Its `TYPESCRIPT_DIRECTORY` constant moves from
  `web-react/src/helpers/contracts` to `packages/pifire-core/src/contracts`.
  `SCHEMA_DIRECTORY` deliberately stays at `web-react/schema/contracts`: the
  clients consume generated types, never raw schemas, so moving the JSON as well
  would churn the exporter's tests for no consumer's benefit. `gen:types:check`
  runs from the root and still fails loudly on drift.
  `settingsDefaults.gen.ts` stays in `web-react` — settings are out of the
  mobile scope, and `gen-types.ts` keeps emitting it there.
- **`command.ts`** — the `POST /api/set|cmd/…` command grammar, moved from
  `web-react/src/helpers/command.ts`. One implementation of every write.
- **`liveState.ts`** — the `socket_dash_data` subscription with its reconnect
  and staleness semantics. `socket.io-client` is this package's one runtime
  dependency; it works under React Native.
- **`deriveView.ts`, `gaugeMath.ts`, `buttonsForMode.ts`, unit conversion** —
  pure payload-to-display and mode-to-controls logic.
- **`demoData.ts`** — the live cook simulator, which lets either client run with
  no PiFire on the network.

React Query hooks stay per-client. A phone's cache and refetch policy differs
enough from a browser tab's that sharing them would be forced rather than
shared.

Tests move with the code they cover and keep running under rstest.

### Runtime host selection

`useLiveState.ts` currently reads its target from
`import.meta.env.PUBLIC_PIFIRE_URL`, a build-time value. A phone chooses its
host at runtime, so the shared `liveState.ts` **takes the URL as an argument**.
`web-react` passes its existing env value at its entry point; `mobile` passes
the host stored on the device. This changes `web-react`'s wiring and is in
scope.

### The app

Navigation is `expo-router` with native tabs.

| Screen | Reuses from `@pifire/core` | New |
|---|---|---|
| **Connect** | — | Host entry, mDNS discovery of `pifire.local`, remembered hosts, unreachable state |
| **Dashboard** | `gaugeMath`, `buttonsForMode`, `deriveView`, `command` | `GrillGauge` in `react-native-svg`; the 250 ms OutCubic value-arc ease and glow pulse in Reanimated; probe cards; control row; setpoint entry as a native modal |
| **History** | `historyAdapter.ts` | A `react-native-svg` line chart — uPlot is DOM-only. Full pan/zoom parity is deferred |
| **Events** | events API types | Native event list |
| **App prefs** | — | Host, theme accent, unit display, alert toggles. *App* preferences only — grill settings stay on web |

Visual identity follows PiFire: the `theme.css` accent tokens (Ember / Ice /
Crimson) port to a TypeScript theme object, and the 270° gauge is the dashboard's
center. Navigation, gestures, modals, haptics, and pull-to-refresh are native.

### Connection lifecycle

iOS suspends sockets when the app backgrounds. The app reconnects on foreground
and shows an explicit reconnecting state with the age of the last reading
("last data 47s ago"), rather than presenting stale temperatures as current. The
staleness semantics `deriveView` already implements carry over unchanged.

When no host is reachable the app says so and keeps retrying. It never
fabricates data — demo mode is a separate, explicitly labeled mode.

### Alerts

An `expo-notifications` local notification fires on probe-target, timer-expiry,
and grill-error events observed in the socket stream, deduplicated by event id
so a reconnect does not re-alert.

This works only while the app is running. Server-side apprise / pushover /
pushbullet / IFTTT (`notify/notifications.py`) remains the reliable path, and the
preferences screen states this plainly. No push infrastructure, device registry,
or FCM/APNs credentials are added.

### Connectivity and trust

LAN only. The API has no authentication today and this work adds none: the trust
model is unchanged from the web UI's. Reaching a grill from outside the LAN is
the user's own VPN's job. No cloud relay, no shared secret, no exposed port.

## Build and distribution

`eas.json` defines two profiles:

- **`development`** — `expo-dev-client`, for day-to-day iteration.
- **`preview`** — an installable artifact: an APK for Android sideloading, an
  ad-hoc build for iOS.

No public App Store or Play Store submission. Store review would additionally
require a reviewer to reach a grill they cannot see.

**Prerequisite:** iOS installs beyond 7-day free provisioning require an Apple
Developer account ($99/yr). Android has no equivalent gate. Without the account,
this design still yields a working Android app and an iOS app that runs in the
simulator and on a personally provisioned device.

## Testing

- **`packages/pifire-core`** — rstest, with the existing suites moved intact.
  The majority of behavior is tested here, once, for both clients.
- **`mobile`** — jest with the `jest-expo` preset and
  `@testing-library/react-native`. rstest has no React Native environment, so
  this is a deliberate second runner, scoped to mobile components only.
- **`web-react`** — its existing rstest and Playwright suites must stay green
  through the extraction. They are the regression proof that moving code changed
  no behavior.
- **Python** — `tests/unit/updater/test_web_ui_build.py` covers the staleness
  change and `tests/unit/common/web_contracts/test_export.py` the contract
  emission target, both described below.

Per-task verification: `bun run typecheck`, `bun run lint`, `bun run test` in
each affected package, plus the Python test above where it applies.

## Backend-adjacent changes

Two Python changes, both small and both already covered by existing tests.

**Contract emission target.** `common/web_contracts/export.py` writes generated
TypeScript to `TYPESCRIPT_DIRECTORY`, currently `web-react/src/helpers/contracts`.
It moves to `packages/pifire-core/src/contracts`, with
`tests/unit/common/web_contracts/test_export.py` updated to match.

**Bundle staleness.** `newest_source_mtime()` (`common/web_ui_build.py:63`) walks only `web-react/` to
decide whether the served bundle is stale. Once shared sources live in
`packages/pifire-core/`, an edit there would not trigger a rebuild, and a grill
would serve a stale bundle after an update. The function must also walk the
shared package, honoring the same `SKIP_DIRS`.

## Risks

- **Apple Developer account.** Blocks durable iOS installs. Android is
  unaffected; the plan sequences Android first so progress does not depend on it.
- **iOS background socket suspension.** Mitigated with explicit reconnect and
  staleness UI rather than by fighting the OS. A backgrounded app is not a
  reliable alerting channel, and the app says so.
- **Lockfile relocation.** Moving `bun.lock` to the root is a one-time
  disruption: every existing checkout, including any running grill, must
  `bun install` again. It lands in its own commit, early, so it is easy to
  identify and revert.
- **Chart parity.** The history chart is a reimplementation, not a port. v1
  targets a readable chart, not uPlot's full interaction model.
- **Two test runners.** rstest for shared and web, jest for mobile. Accepted
  because `jest-expo` is the supported React Native path; contained by keeping
  pure logic in the shared package.

## Out of scope

Settings forms, wizard, tuner, admin, pellet-database editing, recipe authoring,
store submission, push notification infrastructure, remote access beyond the
LAN, and any authentication work on the backend.
