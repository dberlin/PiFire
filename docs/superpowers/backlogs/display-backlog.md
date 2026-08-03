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
  JSON (`dsi_800x480t.json`, `dsi_320x240t.json`, `ili9341f_480x320.json`, …).

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

### 4. Two P-mode/Smoke+ asymmetries left after the Smoke-only ruling — OPEN

**Status:** OPEN. Found 2026-08-03 while making the Qt P-MODE pill a control.

Both are consistency questions, not faults. Recorded because each is a place
where "the two UIs agree" is currently false.

~~**The SMOKE+ pill is a readout on every dashboard.**~~ **DONE 2026-08-03** --
it toggles Smoke+ on both the Qt display and the web, gated on Smoke exactly
as the P-MODE pill is. The pygame pill stays a readout: those layouts have no
tap routing for a duty_pill, and the seven non-ember ones are being retired.

**Ruled 2026-08-03: Smoke only.** Both pills are visible and clickable in
Smoke and nowhere else. Outside it they read AUGER DUTY / FAN DUTY, which are
readouts with nothing to set, so a tap there does nothing by design. The
alternative considered and declined was the `pModeActive` set
(Startup/Reignite/Smoke), where P-mode does govern the running cycle. Consequence
to know: on the Qt display in Stop there is now no P-mode path at all, because
the Stop menu variant (`main`) carries no PMode entry -- Settings > Work Mode
on the web is the only route.

**The legacy pygame `p_mode` widget still shows in Startup and Reignite.**
`_base_flex.py::_update_p_mode` gates on
`mode in [STARTUP, REIGNITE, SMOKE]` -- the same set the unused
`qtbackend.pModeActive` computes -- while the ember `duty_pill` is now
Smoke-only. Two rules for the same reading in one file. The legacy widget is
`PModeStatus`, used by the surviving pygame layouts (`dsi_800x480t.json`,
`dsi_320x240t.json` and the SPI TFTs); nothing pygame-side uses the pill any
more, since the two ember layouts that did were retired with the rest of the
large-display pygame stack. So this is now purely about the older, smaller
designs: P-mode does govern the cycle in Startup and Reignite, and those
layouts were never part of the Smoke-only ruling.

### 5. The large-display pygame stack is retired — DONE 2026-08-03

Three pygame layouts duplicated a Qt Quick display that already existed or
could: `dsi_1024x600t` and `dsi_1280x720t` had `qtquick_` counterparts
shipping beside them, and `dsi_1024x768t` got one (`qtquick_dsi_1024x768t`),
which is two small files because the Qt Quick display class is
resolution-agnostic and reads its dimensions from JSON metadata.

Removed with them, because nothing else used them: `tools/generate_dsi_layout.py`
(its `RESOLUTIONS` list was exactly those three) with its bespoke
`_dashboard_1280x720`/`_dashboard_1024x600` builders and its byte-for-byte
generator test; `tools/generate_ember_background.py` and the two ember
background PNGs it produced; `tests/ui/test_dsi_layout_common.py` and the three
per-resolution layout tests, all of which covered only the deleted files; and
two now-dead `tests/conftest.py` helpers.

**`dsi_800x480t` stays, and this is why.** The Qt Quick dashboard does not fit
at 800x480. Measured against its own declared layout minimums: the body row
needs **806px** of width and the root column **520px** of height. 1024x600,
1024x768 and 1280x720 all clear both; 800x480 misses on both axes. Retiring it
would need the QML dashboard to gain a genuinely small-screen layout first,
which is real design work and not a deletion. The smaller SPI/TFT layouts are
bespoke for their hardware and were never in scope.

**Where the parity guard moved.** `tests/ui/test_qtquick_parity.py` read
`dsi_1280x720t.json` as its source of truth for the pygame action vocabulary;
it reads `dsi_800x480t.json` now. The guard was never about resolution — it
asserts every `cmd_`/`menu_`/`input_` the pygame engine can emit has a Qt
counterpart — and the 800x480 layout is the one every other was derived from.

**The manifest test is self-discovering now.** `test_dsi_manifest.py` was three
resolutions hard-coded and is gone; `test_qtquick_manifest.py` enumerates every
`qtquick_*` display module in the manifest and asserts each has its `.py`, its
`.json`, and metadata whose `screen_width`/`screen_height` match the resolution
in its own name. That is what would have caught shipping a manifest entry with
no layout behind it.
