# Cross-Platform Thermocouple Health UI — Design

**Date:** 2026-08-24  
**Status:** Approved in chat; awaiting written-spec review

## Goal

Surface thermocouple hardware and software health consistently in React web,
Qt/QML, and Expo mobile. Primary safety impact must be unmistakable, secondary
faults must remain attributable, suspected evidence must not masquerade as a
confirmed failure, and transport loss must remain distinct from sensor health.

This design consumes the shared backend detector; it does not duplicate fault
thresholds or infer controller action in a client.

## Current behavior

### React web

`AppShell` already mounts `Banners`, the standard application-wide error/warning
surface. It renders durable error strings as non-dismissible red/critical items
and warnings as dismissible amber items. Dashboard cards already have
warn/danger badges and stale-reading copy.

Thermocouple health does not reach this surface. The Socket.IO producer drops
`thermocouple_health` from `probe_device_info`; generated contracts have no
health type; `deriveView()` converts a null temperature to a last-good stale
number. A confirmed invalid probe therefore looks merely delayed. Existing
external notification events are not an in-app inbox.

### Qt/QML

Qt polls only persisted current temperatures and status. It does not read
`probe_device_info`, errors, warnings, or notifications. A primary fault is
visible only as the generic mode word `ERROR`. The authoritative `text: ERROR`
display command reaches a Qt `display_text()` no-op. Food faults show an em dash
or stale last value; Aux is not rendered.

Native patterns exist: Gauge/ProbeCard status lines, semantic danger/warn theme
tokens, a lid-open Alert visual, and MenuScreen's scrim/card/details structure.
Qt has no general error dialog and currently declares no accessibility metadata.

### Expo mobile

Mobile receives one Socket.IO `DashSocketPayload`, derives its dashboard through
`@pifire/core`, and keeps transport freshness in a global `StatusStrip`.
Thermocouple health and Aux are absent from the wire contract. Null temperatures
fall back to stale last-good values. Primary Error shows only the mode word.

Local notifications use pure transition logic in `alertsFor()`, but the persisted
`prefs.alerts` toggle is currently ignored. There is no toast/snackbar or live
structured health surface. Mobile intentionally delegates configuration to web.

## Shared semantic projection

Add one typed, all-probe projection to the live dashboard payload. It wraps the
machine health report with identity and actual operational impact:

```text
ThermocoupleHealthView
  identity:
    device: stable configured device name
    port: physical port
    label: logical probe label
    display_name: operator-facing name
    role: Primary | Food | Aux
  report:
    state: unmonitored | healthy | suspected | confirmed
    faults: open | short | malfunction[]
    evidence: hardware | junction-collapse | stuck-response |
              excitation-response | implausible-step[]
    temperature_valid: bool
    detail: structured detector diagnostics
  detector:
    source: hardware | software | mixed
    policy: off | observe | enforce
  outcome:
    none | notify_only | unavailable | stopped
  freshness:
    current: bool
    last_reported_age_s: finite backend-relative age
```

The projection is built at the producing end from the exact fused report,
configured probe map, policy, and actual controller outcome. Clients never infer
`stopped` from `state=confirmed`, role, or global Error: Error has other causes,
and observed inference deliberately does not stop heating.

`observed_at` remains backend monotonic and is not sent as a browser/mobile wall
clock. The producing end calculates relative age or omits it. The projection is
current-state data, not an append-only incident history.

One pure semantic mapper in `@pifire/core` converts the wire object into stable
presentation semantics:

```text
severity: quiet | info | warning | danger
availability: current | unavailable
headline
impact_copy
cause_copy
source_copy
banner_priority
```

Web and mobile share that mapper. Qt consumes the same projection and mirrors
the fixed output semantics in its QObject/list-model adapter; it does not decode
raw evidence thresholds.

## State and copy contract

| State/outcome | Temperature | Global alert | Per-probe treatment | Notification |
|---|---|---|---|---|
| healthy | current | none | normal | none |
| unmonitored compatible amplifier | current | none | muted details/config status | none |
| suspected | current | none | amber `CHECK PROBE`; `Possible thermocouple issue; reading still available.` | none |
| confirmed primary + notify_only | current | persistent danger | `FAULT`; `Fault detected — Observe mode did not stop heating.` | once on confirmation |
| confirmed primary + stopped | unavailable | highest persistent danger | em dash; `CONTROL PROBE UNAVAILABLE`; `PiFire stopped heating.` | once on confirmation |
| confirmed Food/Aux + unavailable | unavailable | persistent danger summary | em dash; `PROBE UNAVAILABLE`; `Grill control continues.` | once on confirmation |

Cause copy:

- hardware open: `Hardware reported an open circuit.`
- hardware short: `Hardware reported a short circuit.`
- open+short: preserve both causes.
- inferred malfunction: `Software detected an abnormal thermocouple response.`
- suspected: never say disconnected, open, short, or failed.

Technical evidence and detector metrics live in details, never the headline.
Hardware/software source is secondary context; operator impact is primary.

Confirmed invalid probes never display last-good temperature as current. If
history is useful, details may say `Last valid: 165°F, 2m ago`; the primary
number remains an em dash.

Recovery removes current confirmed treatment when the authoritative projection
becomes healthy. Do not emit success toasts or OS notifications. Primary Error
never auto-resumes; copy directs the operator to inspect and explicitly restart
after valid health returns.

When the UI transport becomes stale/unreachable, transport status retains top
priority. Keep the last known health treatment but prefix it with `Last
reported`; never synthesize a recovery or new fault from stale client state.

Multiple confirmed issues are aggregated by priority:

```text
primary stopped > primary notify-only > confirmed unavailable > suspected
```

The global summary shows the highest issue plus `+N more`; probe cards/details
retain individual identity.

## Web design

### Transport and contracts

- Add the typed all-probe health list to `common/web_contracts/core.py` and the
  Socket.IO `DashSocketPayload` producer.
- Regenerate JSON schema and `packages/pifire-core` TypeScript contracts through
  the existing generator; never hand-edit generated types.
- Build the projection from every Primary/Food/Aux report. Do not hide Aux
  because there is no Aux dashboard card.
- Extend `@pifire/core` with pure health semantics and make health validity take
  precedence over `deriveView()` stale fallback.

### Presentation

- Extend `AppShell/Banners` to accept structured active health alerts beside
  existing string errors/warnings. Confirmed health alerts are non-dismissible
  while active; suspected is never routed through dismissible warnings.
- `GrillGauge` and `ProbeCard` show compact state/cause badges and unavailable
  treatment. Dashboard carries no raw detector JSON.
- Settings > Probes shows complete Primary/Food/Aux status and details using the
  same live subscription; do not open another socket/polling channel.
- Aux confirmed health appears in the global banner and Settings > Probes, not a
  fabricated food card.
- Retained alert during socket loss is visibly qualified as `Last reported`.

### Policy and compatibility warning

Web owns configuration:

- Add `off | observe | enforce` to thermocouple health settings, default
  `observe`, with impact copy beside each choice.
- Qt/mobile display the effective policy but cannot edit it.
- In the shared wizard/settings `DeviceForm`, show the existing
  `pf-module-notes` warning whenever the module is thermocouple and hardware
  detection is absent/disabled. The warning strongly recommends leaving
  software detection enabled.
- Keep MCP9601's current static VSENSE note until conditional rendering and
  policy controls land atomically; then remove duplicate/contradictory copy.

## Qt/QML design

### Transport

`display/qtapp.py::_fetch()` gains a separately throttled read of the shared
current health projection. Do not read/decode the full generic device-info blob
at the 20 Hz metadata rate. Error mode/current temperatures remain on their
existing fast path. Missing/malformed health defaults safely to no health UI.

`PiFireBackend`/models expose identity, state, severity, availability, compact
copy, source, policy, outcome, freshness, and details. Health validity overrides
generic `LAST` staleness.

### Presentation

- Reuse Gauge/ProbeCard's existing reserved stale/status line; do not add a
  second compact-card row.
- Add one steady `ProbeHealthBanner` in the center status area for the
  highest-priority issue. Reuse danger/warn palette and border vocabulary, but
  not Alert.qml's perpetual opacity pulse.
- Banner/details are non-dismissible while the issue remains active. The details
  overlay may close.
- Add a MenuScreen-style `ProbeHealthScreen`: scrollable list of all
  Primary/Food/Aux health, impact, source, faults/evidence, and diagnostic
  metrics. Aux remains detail-only rather than gaining dashboard cards.
- Replace generic primary `ERROR`-only explanation with the health banner while
  retaining the mode state.
- Confirmed invalid food suppresses `LAST`; suspected retains current numeric.

### Accessibility/layout

- Add `Accessible.name`, role, and description to banner/details actions.
- Use focusable controls compatible with touch and encoder navigation.
- Do not rely on color; pair state word, probe name, and outcome.
- Minimum target sizes follow existing 38–44 px controls.
- Verify 1024×600, 1280×720, 1024×768, and rotations 0/90/180/270.
- Copy wraps/elides safely; details scroll.

## Mobile design

### Transport and shared semantics

Mobile consumes the same typed health list through existing Socket.IO and
`@pifire/core`. No separate REST poll or Health tab. Aux remains in the global
summary/details data even without cards.

Keep `StatusStrip` exclusively about connection freshness. Health never changes
`Live`, `Stale`, or `Unreachable` semantics.

### Presentation

- Add persistent primary confirmed banner below StatusStrip so it remains
  visible on every tab.
- Dashboard gauge and food cards consume shared projected health. Confirmed
  invalid values show em dash; suspected retains numeric; cause/impact are text,
  not color-only.
- Aux confirmed health appears in the global summary. A details sheet is optional
  only if implementation needs multi-fault inspection; do not add a fifth tab.
- During transport loss, banner becomes `Last reported: …` and old values are
  visually qualified.

### Local notifications

Extend `alertsFor()` with confirmed transitions only:

- no notification on first real payload;
- no suspected, recovery, or repeated identical-frame notification;
- reconnect replay does not re-alert;
- reconfirmation after genuine recovery may alert again;
- primary/secondary copy reflects actual outcome;
- `prefs.alerts=false` suppresses permission request/scheduling and all local
  notifications. Fix the existing ignored-toggle bug in the same task.

Policy is read-only on mobile; settings continue directing configuration to web.

## Error/warning integration decisions

- React's standard `Banners` is reused but extended structurally; do not reduce
  reports to durable prose strings.
- Qt receives current structured health rather than scraping display commands,
  notifications, or global Error.
- Mobile local notifications remain a transition side effect; dashboard state
  comes from current structured health.
- Server-side Apprise/Pushover/etc. and mobile local notifications may both fire;
  no suppression is possible without transporting server notification config.
  This is documented rather than guessed by the client.

## Scope

### In scope

- Shared wire projection and semantic mapper.
- React banner/card/settings/wizard surfacing and policy controls.
- Qt adapter/banner/card/details/accessibility/rotations.
- Mobile banner/card/confirmed notifications and alert-toggle correction.
- Primary/Food/Aux identity in health projection.
- Current-state recovery and stale-transport qualification.

### Out of scope

- Changing inference thresholds or authority.
- Raw detector sample/ring history UI.
- New Aux dashboard cards.
- Automatic restart or unlatch action.
- Dedicated incident-history database or health tab.
- Generic refactor of all existing error strings/banners.
- Guaranteeing suppression of duplicate server/mobile notifications.

## Verification contracts

### Producing end/shared core

- Exact typed projection for Primary/Food/Aux, missing reports, malformed reports,
  policy/outcome, hardware/inferred/mixed, open+short, finite freshness.
- Health validity overrides stale-last fallback.
- Transport freshness remains orthogonal.
- Generated schema/types drift check passes.

### React web

- Structured confirmed banners are non-dismissible and recover with current
  state; suspected never enters global warnings.
- Gauge/card matrix covers all states/outcomes and no invalid last-value display.
- Settings details cover Aux and policy; DeviceForm warning matrix covers module
  type × hardware enabled × policy.
- AppShell keeps one socket.
- Browser verification at desktop/mobile widths and accessible text/roles.

### Qt/QML

- Backend mapping for all states, malformed/missing payloads, precedence,
  freshness, recovery, and Aux details.
- Confirmed invalid suppresses last-known display.
- QML loads warning-free for banner/card/detail scenarios.
- Compact, standard, and rotated layout tests; encoder/focus/accessibility tests.
- Actual rendered screenshots for suspected, primary notify-only, primary
  stopped, secondary unavailable, multiple faults, and stale transport.

### Mobile

- Shared projection/card/gauge rendering tests for all states/outcomes.
- `alertsFor()` first-frame/reconnect/repeat/recovery/prefs matrix.
- Offline + retained-health precedence.
- Accessibility labels and dynamic text/layout.
- Actual iOS/Android simulator/device smoke for foreground/background,
  permissions denied, alerts disabled, and reconnect behavior. Current mobile
  has no prior real-device visual verification, so unit tests alone are not
  completion evidence.

## Implementation decomposition

1. Shared producing-end health projection and generated contracts.
2. Shared `@pifire/core` semantic mapping and validity precedence.
3. React global/card/settings/wizard implementation.
4. Qt transport/model implementation.
5. Qt QML banner/card/details/accessibility implementation.
6. Mobile dashboard/status implementation.
7. Mobile notification preference and confirmed transitions.
8. Cross-platform visual/device verification and final review.

Qt, web, and mobile implementation tasks can proceed in parallel after Tasks 1–2
stabilize the shared contract.
