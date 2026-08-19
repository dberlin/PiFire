# PiFire Mobile

A native Expo/React Native app for PiFire: connect to a grill on your LAN,
watch live dashboard data, send control commands, browse cook history, and
get local push notifications for probe/timer/error events. It is built on
`@pifire/core`, the same platform-free package (contract types, REST command
grammar, SocketIO live connection, gauge geometry, dashboard-derivation
logic) that `web-react` is built on — this app and the web UI render from the
same shared logic, not two independent reimplementations of it.

## Verification status — read this before trusting anything visual

**Nothing in this app has been run on a device, in a simulator, or in an
emulator.** This environment could not launch `xcrun simctl` (it hangs) and
has no Android SDK, so there was no way to boot iOS Simulator or an Android
emulator here. As a result:

- No screen has ever been rendered and looked at.
- No local notification has ever actually fired and been observed.
- No command has ever been sent from this app to a real grill.
- No one has confirmed the Connect flow, the gauge animation, the tab
  navigation, or the accent picker *look* right, only that the code that
  produces them type-checks and passes unit tests against mocked
  dependencies (fake sockets, fake `AsyncStorage`, `@testing-library/react-native`
  rendering into jsdom-less RN test renderer).

What **is** real evidence, and was actually run:

- `bun run typecheck` passes (`tsc --noEmit`).
- `bun run test` passes — unit and component tests under `mobile/tests/`,
  covering `alertsFor`'s edge-triggering logic, `normalizeHost`, pref
  merging/validation, the theme token tables, and component rendering via
  `@testing-library/react-native` with `react-native-reanimated` mocked out.
- `bunx expo export --platform android` succeeds — Metro bundles the entry
  point (1779 modules, including `@pifire/core` resolved straight out of the
  workspace per the `metro.config.js` setup below) into a working `.hbc`
  bundle plus assets. This is real evidence Metro's workspace resolution is
  wired correctly; it is not evidence the bundle behaves correctly once
  actually running on a device. See the task-17 report for the exact
  invocation and full output.

Whoever picks this app up next should treat "does it look right" and "does it
work against a real grill" as **completely open questions**, not as things
this task closed out. The first real verification this app needs is: install
a preview build (see EAS profiles, below) on an actual phone on the same LAN
as a running PiFire, and drive a real cook interaction end to end.

## Install

Run `bun install` from the **repository root**, not from `mobile/`:

```bash
cd /path/to/PiFire   # repo root
bun install
```

This is a bun workspace (`workspaces: ["web-react", "packages/*", "mobile"]`
in the root `package.json`). There is exactly one lockfile, at the repo
root. Running `bun install` inside `mobile/` will not set up the workspace
correctly and is not supported — always install from the root.

## Run

```bash
cd mobile
bunx expo start
```

This starts the Metro dev server and prints a QR code plus `i`/`a`/`w`
shortcuts for iOS Simulator / Android emulator / web, none of which could be
exercised in this environment (see Verification status, above). Scanning the
QR code with Expo Go, or a custom dev client built via the `development` EAS
profile, is the only way anyone has a chance of actually seeing this app run.

### Running against a real PiFire

There is no in-app offline/demo mode — the app always tries to reach a real
PiFire. On first launch you land on the Connect screen
(`mobile/app/connect.tsx`) and type a host (e.g. `pifire.local`, or
`10.0.0.5` if mDNS doesn't resolve on your network). `src/host.ts`'s
`normalizeHost` turns that into a base URL with **no default port** — a
standard install is nginx on 80/443 (`auto-install/nginx/pifire.nginx`)
reverse-proxying to gunicorn bound to `127.0.0.1:8000`
(`auto-install/supervisor/webapp.conf`), including `/socket.io`, so a bare
hostname resolves to plain `http://pifire.local` (port 80) and goes through
the proxy correctly. Port 5000 is **not** a production default anywhere —
it only shows up if you're pointed at the manual `gunicorn -b 0.0.0.0:5000`
dev invocation documented in `web-react/README.md`, in which case type the
port explicitly (`pifire.local:5000`) and `normalizeHost` preserves it.

Once connected, the app remembers up to 5 recent hosts (`src/host.ts`,
`AsyncStorage` key `pifire.hosts`) and offers them on the Connect screen for
next time.

### Is there an offline demo simulator?

Not from inside this app, no — unlike `web-react`'s `bun run demo`. Two
related but distinct things exist in `@pifire/core` and are worth not
confusing:

- `@pifire/core/fixture`'s `FIXTURE_DASH` — a single real captured
  `socket_dash_data` snapshot. `useLive` (`src/useLive.ts`) uses it as the
  dashboard's initial state before the first live payload arrives, and
  `mobile/tests/*.test.tsx` use it as fixed input for component tests. It is
  static — it does not animate or evolve.
- `@pifire/core/demoData`'s `demoDashAt(elapsedSec)` — the animated "Hold at
  225°F" simulator that powers `web-react`'s `bun run demo`. Mobile imports
  neither this module nor anything that calls it; there is no
  Connect-screen button or env var that switches the mobile app onto it.
  Wiring mobile up to it (the way `web-react` is) would be a reasonable
  follow-up if a demo-without-hardware path is wanted here too, but it does
  not exist yet.

In short: to see this app do anything today, you need a reachable PiFire —
real hardware, or the prototype backend described in `web-react/README.md`'s
"Running against the real backend" section (`uv run python control.py` +
`uv run gunicorn ... app:app`), pointed at from the phone/simulator's Connect
screen using that machine's LAN IP.

## How Metro resolves `@pifire/core`

`@pifire/core` lives at `packages/pifire-core/`, a sibling of `mobile/` in
the bun workspace — not inside `mobile/node_modules`. Metro's default config
only watches and resolves within the project folder it was started from, so
without extra configuration it cannot see `@pifire/core`'s source at all.
`mobile/metro.config.js` fixes this with two settings:

```js
const workspaceRoot = path.resolve(projectRoot, "..");   // repo root

config.watchFolders = [workspaceRoot];
config.resolver.nodeModulesPaths = [
  path.resolve(projectRoot, "node_modules"),
  path.resolve(workspaceRoot, "node_modules"),
];
config.resolver.unstable_enablePackageExports = true;
```

- `watchFolders` tells Metro's file watcher to look at the whole workspace
  root, not just `mobile/` — otherwise it never notices
  `packages/pifire-core/src/*.ts` exists or changes.
- `nodeModulesPaths` adds the workspace root's `node_modules` to Metro's
  module search path, on top of `mobile/node_modules`, so bare imports like
  `@pifire/core/liveConnection` resolve even though the package is
  workspace-linked, not physically present under `mobile/node_modules`.
- `unstable_enablePackageExports` makes Metro honor `@pifire/core`'s
  `package.json` `"exports"` map (`./contracts/*`, `./settings/*`, `./*`),
  which is how e.g. `@pifire/core/dashboard/health` and
  `@pifire/core/fixture` resolve to their actual files under `src/`.

`mobile/babel.config.js` has a related, separate fix: it disables Babel's
runtime-helpers transform, because resolving `@babel/runtime` (a dependency
of `mobile/`, not of `packages/pifire-core/`) from inside
`packages/pifire-core/src/*.ts` would fail — `mobile/node_modules` is not an
ancestor of that directory. Disabling the transform makes Babel inline the
helpers instead of importing them, which needs no such resolution.

## Tests and typechecking

```bash
cd mobile
bun run test        # jest (jest-expo preset)
bun run typecheck   # tsc --noEmit
```

`bun run test` runs Jest under the `jest-expo` preset. `mobile/jest.config.js`
carries two workspace-specific fixes worth knowing about if a test starts
failing mysteriously:

- `resolver: "react-native-worklets/jest/resolver.js"` plus a
  `react-native-reanimated` → `react-native-reanimated/mock` module mapping,
  needed because `react-native-reanimated` v4's native binding otherwise
  crashes immediately under Jest's Node environment.
- `transformIgnorePatterns` is widened to also transform `@pifire/.*` (ships
  TypeScript source, not precompiled JS) and to correctly skip bun's
  content-addressable `node_modules/.bun/<pkg>@<version>+<hash>/...` path
  segment when matching package names, so packages like
  `@react-native/jest-preset` still get transformed instead of being
  wrongly treated as already-compiled.

## EAS build profiles

`mobile/eas.json` defines two build profiles. There is deliberately **no
`production` profile** — app store submission is out of scope for this task.

| Profile | `distribution` | What it produces |
|---|---|---|
| `development` | `internal` | A custom dev client (`developmentClient: true`) — install once, then point `expo start` at it for fast-refresh development without Expo Go's limitations. |
| `preview` | `internal` | A directly-installable build for internal testing. On Android, `buildType: "apk"` produces a plain `.apk` you can sideload — no store, no TestFlight. |

Building with either profile means running EAS against Expo's cloud build
service, e.g.:

```bash
cd mobile && bunx eas build --platform android --profile preview
```

That command was **not run** as part of this task — it requires
authenticating against the developer's own Expo account and consumes their
build credits, so producing an actual build artifact is left to whoever owns
that account.

### Apple account prerequisite

A **durable iOS install** (anything beyond a time-limited Expo Go session)
requires an active Apple Developer Program membership (**$99/year**) — EAS
needs it to sign the build and register the device or distribution
certificate. There is no equivalent gate for Android: an unsigned or
ad-hoc-signed `.apk` from the `preview` profile can be sideloaded directly.
If no Apple Developer account is available, the Android `preview` APK is the
reachable artifact; the iOS build stops at that prerequisite rather than at
a code defect.
