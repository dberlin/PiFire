# Physical Display — Backlog

Actionable backlog for `display_process.py`, `display_launch.py`, and drivers
under `display/`. Completed work and obsolete findings are removed rather than
retained here; plans and repository history carry that record.

**Last reconciled against live code: 2026-08-13.**

## 1. Surface critical errors on physical displays

`DisplayFeeder.tick()` reads current values and status, then drains generic
text, clear, and splash commands. It never reads `ErrorKind.ALL`, so an operator
at the grill gets no indication when the platform falls back to prototype
hardware, probes are disabled, a distance sensor is unavailable, or a cook file
cannot be written.

Both major display families already have an alert surface: Qt Quick has
`display/qml/components/Alert.qml`, and flex layouts have `AlertMessage`. The
implementation needs explicit decisions before coding:

- where the durable error read belongs without widening every display driver's
  four-method interface;
- whether producers expose a short panel-safe fault label alongside web prose;
- which small panels have room to opt in;
- how persistent errors arbitrate with the existing lid-open alert and clear.

Do not use `critical_error` as a substitute: it means control is unsafe, not
that an operator-facing display message exists. Final verification needs a real
panel; this workstation has no display hardware.

## 2. Confirm shutdown and align destructive-action order

The physical display renders Shutdown as destructive but dispatches it without
confirmation: `display/qml/Actions.js::activate()` sends ordinary commands,
including `cmd_shutdown`, directly to the backend. Shutdown may open the master
power relay and halt the host, so one accidental touch can be an outage.

Add a reusable confirmation overlay and a declarative gate on the menu item.
At the same time, choose one ordering for Stop and Shutdown across physical and
web controls. `display/qml/Menus.js` and flex layouts currently use Stop then
Shutdown; `web-react/src/helpers/dashboard/buttonsForMode.ts` uses Shutdown
then Stop. Do not change one surface alone. Routing can be unit-tested, but
touch targets and overlay layout require a real panel.

## 3. Resolve remaining P-mode and Smoke+ asymmetries

The Smoke-only ruling is not implemented consistently across surviving
surfaces:

- `display/_base_flex.py::_update_p_mode()` still shows the legacy P-mode widget
  in Startup, Reignite, and Smoke;
- `_update_smoke_plus()` has no mode gate;
- the React active-cook row exposes Smoke+ outside a Smoke-only branch.

Decide whether surviving small pygame layouts intentionally retain their
Startup/Reignite P-mode readout because P-mode governs those cycles, or whether
all dashboards must follow the Smoke-only presentation rule. Then make the
chosen rule explicit in each surviving display family and its behavior tests.
