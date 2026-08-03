# Physical Display — Backlog

Durable backlog for the display process (`display_process.py`,
`display_launch.py`) and the drivers under `display/`. Separate from
`react-migration-backlog.md`, which tracks the web UI, and from
`backend-backlog.md`, which tracks the rest of the Python server.

**Opened 2026-08-02.**

---

## OPEN

### 1. The display never shows errors

**Status:** open. Found 2026-08-02 while splitting the errors blob by owning
process (`92ef91e0`).

**The finding.** `grep -rl errors display/` matches **zero files**, and no `.qml`
under `display/qml/` mentions an error either. The display has never had a path
to the errors channel.

`DisplayFeeder.tick()` (`display_process.py:27`) is the whole input surface:

```python
in_data = self.store.read_current()
status   = self.store.read_status()
self.display.display_status(in_data, status)
# plus display_text / clear_display / display_splash from the command queue
```

Neither `current` nor `control:status` carries errors, and the four driver
methods (`display_status`, `display_text`, `clear_display`, `display_splash` —
see `display/prototype.py`, `display/none.py`) have no parameter that could.

**Why it matters.** Every operator-facing banner is web-only. Someone standing
at the grill with no phone out gets no indication that:

- the grill platform failed to load and the **prototype** is driving the
  relays (`build_devices`, sets `critical_error`, which also makes the control
  loop ignore every mode change — `controller/runtime/controller.py:394`);
- **all probes are disabled** (`ProbesMain(disable=True)`);
- the distance/hopper sensor fell back to `distance.none`, so the hopper gauge
  is fiction;
- a cook file could not be written (`runner.py::_raise_banner`).

The grillplat and probe cases are the sharp ones: the screen keeps rendering a
normal-looking dashboard while nothing behind it is real.

The display's *own* failure (fell back to `display.none`) is the one case that
legitimately cannot self-report, and web-only is correct for it.

**What already exists to build on.**

- `read_errors(ErrorKind.ALL)` (`common/datastore_accessors.py`) is exactly the
  read a display wants — every owner's banners, grouped, stable ordering. No new
  backend work is needed to *get* the strings.
- The QtQuick dash already has a danger-styled, pulsing alert widget:
  `display/qml/components/Alert.qml`, placed in
  `display/qml/screens/DashScreen.qml:123`. It is currently hardwired to one
  boolean — `shown: backend.lidOpen`, `message: "LID OPEN"`.
- The flex (PIL-rendered) family has a parallel concept:
  `display/flexobject.py::AlertMessage`, wired through `_base_flex.py:920
  _update_lid_alert` and the `"type": "alert"` entries in the per-resolution
  JSON (`dsi_800x480t.json`, `dsi_1024x768t.json`, `ili9341f_480x320.json`, …).

So both major driver families already have an alert surface; each is claimed by
lid-open and would need to arbitrate between the two, or gain a second slot.

**Open design questions — decide before implementing.**

1. **Where does the read happen?** Adding it to `DisplayFeeder.tick()` and
   passing errors into `display_status()` changes a four-method interface that
   **47 modules** in `display/` implement (5 in the flex family, 15 in the fixed
   240x320/240x240 family, plus the QtQuick and pygame ones). A fifth method with
   a default no-op on a base class is the cheaper shape, but there is no single
   base — check before assuming one.
2. **Full text or a marker?** The banners are long prose sentences ending in
   "run the configuration wizard again from the admin panel". That is unreadable
   on a 240x320 panel and wrong advice for someone standing at the grill. A short
   marker ("PLATFORM FAULT", "PROBES OFF") with the detail left to the web UI is
   probably right, which means the *producer* needs to emit a short form
   alongside the prose — a change to `build_devices`/`build_display`, not just to
   the display.
3. **Which panels opt in?** A 64x128 OLED (`pygame_64x128.py`, `ssd1306.py`) has
   no room. Scoping this to the flex + QtQuick families and leaving the small
   fixed panels alone is a legitimate answer, but say so explicitly rather than
   leaving them silently unhandled.
4. **Does it clear?** Errors clear only when the owning process restarts. A
   stuck alert on a physical screen with no dismiss affordance is worse than the
   web banner, which at least sits next to a page the operator can navigate away
   from.

**Do not** reach for `critical_error` as the trigger. It is the "this grill can
no longer be driven safely" flag, not a display signal — see `92ef91e0`'s message
and the note in `d7432363`.

**Verification.** This needs a real panel. This machine has no display hardware
attached, so any change here is untested until it runs on the actual grill —
which is a different machine.

---

### 2. Shutdown fires with no confirmation

**Status:** OPEN. The web half is `react-migration-backlog.md` item 12; neither
UI has both halves, and they are missing opposite ones.

**Shutdown does considerably more than end the cook.** `ShutdownMode.teardown()`
(`controller/runtime/modes/shutdown.py:25-27`) opens the platform's master power
relay, and with `shutdown.auto_power_off` set the controller then runs
`sudo shutdown -h now` (`controller/runtime/controller.py:641-643`) and halts
the host — the machine this display is running on. One reported case took an
FT232H relay board off the USB bus entirely, which `react-migration-backlog.md`
item 12 records in full. An unconfirmed touch here is not a mis-click, it is an
outage.

**The finding.** It should read as destructive and ask before firing. Here it
reads as destructive and does not ask:

- **Red, already.** `ControlPanel.qml:26` and `:43` give `cmd_shutdown` (and
  `cmd_stop`) `Theme.dangerColor` for the border and the press tint.
- **No confirmation.** `Actions.activate()` (`display/qml/Actions.js:25-28`)
  routes anything that is not `cmd_none`, `menu_*` or `input_*` straight to
  `backend.action(a, ...)`. One touch on a 7" panel, mid-cook, and the cook is
  over.

The web UI is the mirror image: it confirms ("Shut down the grill?") and is not
red.

**Scope note.** This is not a one-liner here the way the colour is on the web
side. `display/qml/` has no confirmation overlay to reuse — `screens/` holds
`HoldInput`, `NotifyInput`, `QrCodeScreen` and `MenuScreen`, all of which
collect a value rather than confirm an action. A `ConfirmOverlay.qml` has to be
built, and `Actions.activate()` needs a way to say "this action is gated" —
most naturally a flag on the menu item, so `Menus.js` stays the one place that
describes what a button is.

**The orders are inverted between the two UIs, and that matters more.** Every
row here that contains both ends `Stop, Shutdown` (`Menus.js:101-102, 116-117,
122-123`); every web row containing both ends `Shutdown, Stop`. The last button
in the row is a different destructive action depending on which screen the
operator is at. Fix it in both files in one change or not at all.

**Verification.** Touch targets and overlay layout need a real panel; this
machine has none. The routing change in `Actions.js` is testable without one.

---

### 3. A probe with no reading shows its last value, and floods the log — DONE 2026-08-03

**Fixed.** The absence is taken deliberately now: `FoodProbeModel` resolves it
into `temp` + `hasTemp` + `stale` before it reaches QML, so `property real
temp` is never handed an undefined and the log line is gone. The card keeps
showing the last real reading -- which is what it accidentally did -- and says
how old it is underneath, worded identically to the web
(`display/staleness.py`). A probe that has produced nothing at all draws "—".
The same treatment went to the primary gauge, and to the pygame/flex displays,
which had the web's silent-zero form of the bug rather than this one.
`backlogs/react-migration-backlog.md` item 15 holds the full disposition.

**Original finding, kept as the trace:**

**Status:** found 2026-08-02 from a live grill's control log, while
tracing why the web UI shows 0 for the same probe
(`react-migration-backlog.md` item 15, which holds the full chain).

**The finding.** The log carries this at frame rate:

```
display/qml/screens/DashScreen.qml:69:7: Unable to assign [undefined] to double
```

`DashScreen.qml:69` is `temp: model.temp` in the food-probe `ProbeCard`
repeater. A probe whose reading is unavailable arrives as `None` —
`thermoworks_cloud` returns it for a channel whose cache has gone stale, the
Kalman stage passes it through by design (`probes/base.py:373`), and
`FoodProbeModel.update()` stores it as-is, because `f.get(row["label"], 0)`
(`display/qtbackend.py:60`) only defaults a *missing* key, not a present null.

`ProbeCard.qml:12` declares `property real temp`, so QML refuses the
assignment. **That refusal is why the display looks reliable**: the property
keeps its previous value and the card goes on showing the last good reading.

**Why that is still wrong.** It is right by accident, and it is not honest:

- The reading shown is **stale**, with nothing saying so. A cloud probe can be
  90 s past its last successful poll and the card still reads like live data.
- It depends on an assignment *failing* — a refactor to `property var temp`, or
  a `?? 0` added in the model to quiet the log, would turn a silent staleness
  into a silent zero, which is the web's bug (item 15).
- It writes a line to the control log every frame. That is the noise that hid
  the real fault: the message names a QML property, not a probe.

**What it should do — decided 2026-08-02.** Keep showing the last good value —
which is what it accidentally does now — but **say that it is stale and how
old it is**: "last data 47 s ago" or similar, in a line beneath the number.
Same wording and placement as the web, so an operator moving between the panel
and their phone reads one story.

So the display's *displayed* behaviour is already half right; what is missing is
that it does not know the reading is stale, and neither UI can work that out for
itself. `react-migration-backlog.md` item 15 carries the decision in full,
including where the age has to come from: the producer, via the per-probe
`status` block that already reaches both UIs. Take the null deliberately rather
than by rejected assignment, and stop the log line while doing it.
