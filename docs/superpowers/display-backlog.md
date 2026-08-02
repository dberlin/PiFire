# Physical Display — Backlog

Durable backlog for the display process (`display_process.py`,
`display_launch.py`) and the drivers under `display/`. Separate from
`react-migration-backlog.md`, which tracks the web UI.

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
