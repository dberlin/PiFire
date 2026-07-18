# Phase H — Notifications: Data-Drive the Event Map

## For agentic workers

**REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.** Execute this plan one task at a time,
each task in its own fresh subagent context, committing at the end of every task. Read this whole document
first, then dispatch Task 1. Do not batch tasks.

## Goal

Refactor `notify/notifications.py` so the ~140-line `if/elif` ladder inside `send_notifications` becomes a
data-driven `EVENTS` table, collapse the ~6 copies of logger-setup boilerplate into one `_event_logger()`,
and merge the two near-identical apprise senders (`_send_pushover_notification` /
`_send_pushbullet_notification`) into one `_send_apprise_url(...)`. **Behavior must be preserved exactly** —
every event string must still produce the identical `(title, body, channel, query_args)` and still fan out
to the same set of senders. A prerequisite characterization test pins the current behavior *before* any
refactor and stays green through every subsequent task.

## Architecture

`send_notifications(notify_event, label="Probe", target=0)` today reads settings/control, builds four
locals — `title_message`, `body_message`, `channel`, `query_args` — via a long `if "<substring>" in
notify_event: … elif …: … else: …` ladder, then fans them out to seven notify services
(apprise, ifttt, pushbullet, pushover, onesignal, mqtt, wled) each gated on its own
`enabled` + credential check.

Two design corrections apply to the new table (both verified against the live callers):

1. **Exact-key lookup, not substring.** Every caller passes an *exact literal* event key
   (`send_notifications("Timer_Expired")`, `ctx.notifications.send("Grill_Error_02")`, …) — verified
   repo-wide, no concatenation or f-strings anywhere. So `EVENTS` is a plain dict looked up with
   `EVENTS.get(notify_event)`; this is behavior-identical to the old `"<key>" in notify_event` ladder
   for every real input (no key is a substring of another either). Order is irrelevant.
2. **Two dead events are dropped, not carried.** `Grill_Error_00` (its only occurrence repo-wide is its
   own branch definition — zero emitters) and `Grill_Warning` (no emitter; only downstream WLED refs,
   which are the out-of-scope follow-on) are never emitted. They are omitted from `EVENTS`; if either is
   ever emitted post-change it hits the Unknown-Notification fallback. This is a called-out, human-approved
   behavior change on currently-unreachable paths.

That leaves **10 live events**. The ladder is **almost entirely runtime-dependent** — only 3 of the 10 are
pure constants. The rest interpolate `label`, `target`, `unit`, `now`/`time`/`day`, re-read `pellet_db` or
`control`, or copy the computed body back into `query_args`. Therefore `EVENTS` must map each event key to a
**builder callable** `builder(ctx) -> (title, body, channel, query_args)`, not a flat constant tuple. A
single `_event_context(...)` assembles the shared runtime values (`unit`, `now`, `time`, `day`, `label`,
`target`, `settings`, `control`) once; each builder reads what it needs from `ctx`. The fan-out block below
the ladder is untouched.

The fallback (an `EVENTS.get` miss) is **not** an `EVENTS` entry: it is the only branch that logs at
`ERROR` (all matched branches log at `INFO`). It stays as an explicit `else` after the table lookup.

## Tech Stack

- Python 3.14, `pytest`, `apprise`, `requests`.
- `notify/notifications.py` imports `read_settings`, `read_control`, `read_pellet_db`, `write_settings`
  from `common.datastore_accessors` and `create_logger` from `common.common`. Tests patch these
  module-level names on `notify.notifications`.

## Global Constraints

- Python 3.14. `except (A, B)` is canonical; do **not** "fix" bare `except A, B` forms — ruff owns that.
- **TEST COMMAND (exact, always):**
  `timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest <args> -q`
- **Before every commit:** `uvx ruff format <changed>` then `uvx ruff check <changed>`.
- Prefer Serena symbolic edits (`replace_symbol_body`, `insert_before_symbol`, `insert_after_symbol`)
  over raw text where practical.
- Commit with `git commit -F <msgfile>` (zsh eats backticks in `-m`). Co-author trailer on every commit:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- **Behavior-preserving, with TWO approved exceptions.** `EVENTS` must reproduce every *live* event's exact
  `(title, body, channel, query_args)` tuple — no lossy flattening. The fallback keeps its `ERROR` log level.
  Tests mock **all** network/apprise senders — zero real notifications are sent during verification. The two
  intentional, human-approved behavior changes, both under test-flip discipline (their Task 1 pins flip in the
  same commit as the code change):
  1. Correct the `Grill_Error_01` product-string typo `"exceded"` → `"exceeded"` (in Task 2).
  2. Drop the two dead, never-emitted events `Grill_Error_00` and `Grill_Warning` (verified zero emitters
     repo-wide); they route to the Unknown-Notification fallback instead (Task 2).
  Every other event stays byte-identical. Note: switching from substring to exact-key (`EVENTS.get`) lookup is
  **not** a behavior change — it is provably equivalent because all callers pass exact literal keys.
- Branch: `refactor/notify-event-table`. Risk: low. Rollback: revert the branch.
- **Out of scope:** `wled_handler._notify_traditional` re-switching on the same event strings is a tracked
  *follow-on*, not part of this phase. Do not touch it.

---

## File Structure

```
notify/notifications.py                          # send_notifications, EVENTS, _event_context,
                                                 #   _event_logger, _send_apprise_url, thin
                                                 #   _send_pushover/_send_pushbullet wrappers
tests/unit/notify/__init__.py                    # new (empty package marker)
tests/unit/notify/test_notifications_events.py   # new characterization + refactor tests
```

---

## Task 1 — Prerequisite characterization test over every event string (commit first)

**Goal:** Pin the *current* `(title, body, channel, query_args)` for all 10 live events + the fallback,
plus the sender fan-out gating, **before** touching production code. This test is the contract. (The two
dead events `Grill_Error_00`/`Grill_Warning` are NOT pinned as distinct outputs — Task 2 drops them; see
the dedicated fallback-routing assertion below.)

**Files:** `tests/unit/notify/__init__.py` (new, empty), `tests/unit/notify/test_notifications_events.py`
(new).

**How the tuple is observed (no return value to assert on):** `send_notifications` never returns the
computed locals. Two senders receive them, and between the two we capture all four components:

- `_send_onesignal_notification(settings, title_message, body_message, channel)` → gives title, body,
  channel.
- `_send_ifttt_notification(settings, notify_event, query_args)` → gives query_args.

So the test enables **onesignal** and **ifttt** in the settings fixture (credentials non-empty,
`enabled=True`), disables every other service, and monkeypatches those two module functions to record their
args. All other senders are patched to no-ops so nothing hits the network.

**Steps:**

1. Create `tests/unit/notify/__init__.py` (empty).
2. In `test_notifications_events.py`, add a `settings` fixture — a dict with the exact keys
   `send_notifications` and the senders read. Minimum shape:

   ```python
   def _base_settings():
       return {
           "globals": {"debug_mode": False, "units": "F"},
           "safety": {"maxtemp": 550},
           "notify_services": {
               "apprise": {"locations": "", "enabled": False},
               "ifttt": {"APIKey": "key", "enabled": True},
               "pushbullet": {"APIKey": "", "PublicURL": "", "enabled": False},
               "pushover": {"APIKey": "", "UserKeys": "", "PublicURL": "", "enabled": False},
               "onesignal": {"app_id": "app", "devices": {}, "enabled": True},
               "mqtt": {"broker": "", "enabled": False},
               "wled": {"device_address": "", "enabled": False},
           },
       }

   def _base_control():
       return {
           "safety": {"startuptemp": 100},
           "recipe": {"step_data": {"message": "Flip the brisket. "}},
       }

   def _base_pelletdb():
       return {"current": {"hopper_level": 42}}
   ```

3. A helper that runs one event and returns the captured tuple:

   ```python
   import notify.notifications as N

   def _capture(monkeypatch, event, label="Probe", target=0,
                settings=None, control=None, pelletdb=None):
       settings = settings or _base_settings()
       control = control or _base_control()
       pelletdb = pelletdb or _base_pelletdb()
       rec = {}
       monkeypatch.setattr(N, "read_settings", lambda *a, **k: settings)
       monkeypatch.setattr(N, "read_control", lambda *a, **k: control)
       monkeypatch.setattr(N, "read_pellet_db", lambda *a, **k: pelletdb)
       def fake_onesignal(s, title, body, channel):
           rec["title"], rec["body"], rec["channel"] = title, body, channel
       def fake_ifttt(s, ev, query_args):
           rec["query_args"] = query_args
       monkeypatch.setattr(N, "_send_onesignal_notification", fake_onesignal)
       monkeypatch.setattr(N, "_send_ifttt_notification", fake_ifttt)
       # silence every other sender
       for name in ("_send_apprise_notifications", "_send_pushbullet_notification",
                    "_send_pushover_notification", "_send_mqtt_notification",
                    "_send_wled_notification"):
           monkeypatch.setattr(N, name, lambda *a, **k: None)
       N.send_notifications(event, label=label, target=target)
       return rec
   ```

4. Add one test per event asserting the exact tuple. **Time-dependent branches** (`now`/`time`/`day`) must
   not hard-code the clock — assert the stable prefix and that the channel/query_args are exact. Use the
   table below (these are the CURRENT values — copy them verbatim):

   | event key                  | title                              | body (exact, `…` = live time suffix)                                                                                              | channel                 | query_args                                                        |
   |----------------------------|------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|-------------------------|-------------------------------------------------------------------|
   | `Probe_Temp_Achieved`      | `Probe Target Achieved`            | `Probe target of 0F achieved at HH:MM on MM/DD`                                                                                    | `pifire_temp_alerts`    | `{"value1": True}`                                                |
   | `Probe_Temp_Limit_Alarm`   | `Probe Limit Reached`              | `Probe limit of 0F exceeded at HH:MM on MM/DD`                                                                                     | `pifire_temp_alerts`    | `{"value1": True}`                                                |
   | `Timer_Expired`            | `Timer Complete`                   | `Your timer has expired, time to check your cook!`                                                                                 | `pifire_timer_alerts`   | `{"value1": "Your timer has expired."}`                           |
   | `Pellet_Level_Low`         | `Low Pellet Level`                 | `Your pellet level is currently at 42%`                                                                                            | `pifire_pellet_alerts`  | `{"value1": "Your pellet level is currently at 42%"}`             |
   | `Grill_Error_01`           | `Grill Error!`                     | `Grill exceded maximum temperature limit of 550F! Shutting down. <now>`                                                            | `pifire_error_alerts`   | `{"value1": "550"}`                                               |
   | `Grill_Error_02`           | `Grill Error!`                     | `Grill temperature dropped below minimum startup temperature of 100F! Shutting down to prevent firepot overload. <now>`           | `pifire_error_alerts`   | `{"value1": "100"}`                                               |
   | `Grill_Error_03`           | `Grill Error!`                     | `Grill temperature dropped below minimum startup temperature of 100F! Starting a re-ignite attempt, per user settings.`           | `pifire_error_alerts`   | `{"value1": "100"}`                                               |
   | `Recipe_Step_Message`      | `Recipe Message`                   | `Flip the brisket. <now>`                                                                                                          | `pifire_recipe_message` | `{"value1": "Flip the brisket. "}`                                |
   | `Test_Notify`              | `Test Notification`                | `This is a test notification from PiFire.`                                                                                         | `pifire_test_message`   | `{"value1": "This is a test notification from PiFire."}`          |
   | `Control_Process_Stopped`  | `Control Process Stopped!`         | `The control process has encountered an issue and has been stopped. Check on your grill as soon as possible to prevent damage!`    | `pifire_error_alerts`   | `{"value1": "Control Process Stopped"}`                           |
   | *(unmatched, e.g. `Zzz`)*  | `PiFire: Unknown Notification issue` | `Whoops! PiFire had the following unhandled notify event: Zzz at <now>`                                                          | `default`               | `{"value1": "Unknown Notification issue"}`                        |

   Notes that MUST be encoded as assertions:
   - `<now>` = `datetime.now().strftime("%m-%d %H:%M")`; `HH:MM` = `%H:%M`; `MM/DD` = `%m/%d`. Assert with
     `body.startswith(prefix)` for the time-suffixed rows, exact `==` for the constant rows.
   - `Grill_Error_03` has **no** trailing `<now>` (unlike `01`/`02`) — assert exact `==`.
   - Do **not** pin `Grill_Error_00` or `Grill_Warning` here — they are dropped in Task 2. A dedicated
     Task 2 assertion confirms they route to the Unknown-Notification fallback after removal.
   - `Grill_Error_01` body is pinned here with the CURRENT typo `"Grill exceded maximum..."`. This is
     deliberate: Task 2 corrects it to `"exceeded"` and re-flips this exact assertion in the same commit
     (the phase's one approved behavior change). Do not "pre-correct" it in Task 1.
   - `Probe_*` `query_args["value1"]` is the boolean `True`, **not** a string — assert `is True`.
   - `Pellet_Level_Low` `query_args["value1"]` equals the body — assert equality to body.
   - `label`/`target` flow into `Probe_*` — add a second Probe case with `label="Grate", target=225`
     asserting `Grate target of 225F …`.

5. Add a **fan-out gating** test: with only ifttt+onesignal enabled, exactly those two recorders fire and
   the other five sender stubs are never called (use counters). This pins that the refactor doesn't disturb
   the dispatch block.

**Test command:**
`timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/notify/test_notifications_events.py -q`

**Expected:** all new tests pass (they characterize existing behavior).

**Commit:** `test(notify): characterize send_notifications event→message contract`

---

## Task 2 — Introduce `EVENTS` table + `_event_context`; rewrite `send_notifications` to consume it

**Goal:** Replace the `if/elif` ladder with a table lookup. Tests from Task 1 stay green unchanged.

**Files:** `notify/notifications.py`.

**Interfaces produced:**

```python
# Builder signature: takes the shared context dict, returns the 4-tuple.
#   builder(ctx) -> (title_message, body_message, channel, query_args)
# EVENTS: exact-key mapping of event string -> builder. Looked up with EVENTS.get(notify_event);
# a miss falls through to the Unknown-Notification fallback. Every caller passes an exact literal
# key (verified repo-wide - no concatenation), so exact-key lookup is behavior-identical to the
# old `"<key>" in notify_event` substring ladder. Order is irrelevant for a dict .get().
EVENTS: dict[str, Callable[[dict], tuple[str, str, str, dict]]]
```

**Steps:**

1. Add `from collections.abc import Callable` (or `from typing import Callable`) near the imports if a type
   hint is used; otherwise skip the hint.

2. Insert `_event_context` before `send_notifications`:

   ```python
   def _event_context(settings, control, label, target):
       """Assemble the shared runtime values every EVENTS builder may read."""
       date = datetime.datetime.now()
       return {
           "settings": settings,
           "control": control,
           "label": label,
           "target": target,
           "unit": settings["globals"]["units"],
           "now": date.strftime("%m-%d %H:%M"),
           "time": date.strftime("%H:%M"),
           "day": date.strftime("%m/%d"),
       }
   ```

3. Insert the `EVENTS` table (module level, before `send_notifications`). Each builder reproduces exactly
   one former branch:

   ```python
   def _evt_probe_achieved(ctx):
       title = f"{ctx['label']} Target Achieved"
       body = f"{ctx['label']} target of {ctx['target']}{ctx['unit']} achieved at {ctx['time']} on {ctx['day']}"
       return title, body, "pifire_temp_alerts", {"value1": True}

   def _evt_probe_limit(ctx):
       title = f"{ctx['label']} Limit Reached"
       body = f"{ctx['label']} limit of {ctx['target']}{ctx['unit']} exceeded at {ctx['time']} on {ctx['day']}"
       return title, body, "pifire_temp_alerts", {"value1": True}

   def _evt_timer_expired(ctx):
       return (
           "Timer Complete",
           "Your timer has expired, time to check your cook!",
           "pifire_timer_alerts",
           {"value1": "Your timer has expired."},
       )

   def _evt_pellet_low(ctx):
       pelletdb = read_pellet_db()
       body = f"Your pellet level is currently at {pelletdb['current']['hopper_level']}%"
       return "Low Pellet Level", body, "pifire_pellet_alerts", {"value1": body}

   def _evt_grill_error_01(ctx):
       maxtemp = ctx["settings"]["safety"]["maxtemp"]
       body = (
           "Grill exceeded maximum temperature limit of "  # typo fix: "exceded" -> "exceeded" (deliberate, see Task 1 note)
           + str(maxtemp) + ctx["unit"] + "! Shutting down. " + str(ctx["now"])
       )
       return "Grill Error!", body, "pifire_error_alerts", {"value1": str(maxtemp)}

   def _evt_grill_error_02(ctx):
       startuptemp = ctx["control"]["safety"]["startuptemp"]
       body = (
           "Grill temperature dropped below minimum startup temperature of "
           + str(startuptemp) + ctx["unit"]
           + "! Shutting down to prevent firepot overload. " + str(ctx["now"])
       )
       return "Grill Error!", body, "pifire_error_alerts", {"value1": str(startuptemp)}

   def _evt_grill_error_03(ctx):
       startuptemp = ctx["control"]["safety"]["startuptemp"]
       body = (
           "Grill temperature dropped below minimum startup temperature of "
           + str(startuptemp) + ctx["unit"]
           + "! Starting a re-ignite attempt, per user settings."
       )
       return "Grill Error!", body, "pifire_error_alerts", {"value1": str(startuptemp)}

   def _evt_recipe_step(ctx):
       message = ctx["control"]["recipe"]["step_data"]["message"]
       body = message + str(ctx["now"])
       return "Recipe Message", body, "pifire_recipe_message", {"value1": message}

   def _evt_test_notify(ctx):
       return (
           "Test Notification",
           "This is a test notification from PiFire.",
           "pifire_test_message",
           {"value1": "This is a test notification from PiFire."},
       )

   def _evt_control_stopped(ctx):
       return (
           "Control Process Stopped!",
           "The control process has encountered an issue and has been stopped. "
           "Check on your grill as soon as possible to prevent damage!",
           "pifire_error_alerts",
           {"value1": "Control Process Stopped"},
       )

   EVENTS = {
       "Probe_Temp_Achieved": _evt_probe_achieved,
       "Probe_Temp_Limit_Alarm": _evt_probe_limit,
       "Timer_Expired": _evt_timer_expired,
       "Pellet_Level_Low": _evt_pellet_low,
       "Grill_Error_01": _evt_grill_error_01,
       "Grill_Error_02": _evt_grill_error_02,
       "Grill_Error_03": _evt_grill_error_03,
       "Recipe_Step_Message": _evt_recipe_step,
       "Test_Notify": _evt_test_notify,
       "Control_Process_Stopped": _evt_control_stopped,
   }
   ```

   **Equivalence notes (verify against the original while writing):**
   - `_evt_grill_error_02`/`_03`: the original re-called `read_control()` inside the branch. That returns
     the same content as the top-level `control` read in the same call, so this builder reads
     `ctx["control"]` instead. This drops one redundant file read — no observable behavior change. State
     this in the commit body.
   - `_evt_pellet_low` calls `read_pellet_db()` lazily (only when this event fires), matching the original
     which read it only inside the branch.
   - `"exceded"` in `_evt_grill_error_01` is a **pre-existing typo in the product string**. This phase's ONE
     deliberate behavior change (human-approved): correct it to `"exceeded"` here, and in the SAME commit flip
     the Task 1 characterization pin for `Grill_Error_01` from `"...Grill exceded maximum..."` to
     `"...Grill exceeded maximum..."` (test-flip discipline — the pinning test goes RED on the corrected code,
     the assertion update makes it GREEN). All other events remain byte-identical.

4. Rewrite `send_notifications` body: keep the settings/control read and `_event_context` assembly, replace
   the ladder with a table lookup, keep the fan-out block byte-for-byte. New head:

   ```python
   def send_notifications(notify_event, label="Probe", target=0):
       """... (keep docstring) ..."""
       settings = read_settings()
       control = read_control()
       eventLogger = _event_logger(settings)          # Task 3 introduces this; until then inline the
                                                       # existing create_logger call unchanged.
       ctx = _event_context(settings, control, label, target)

       builder = EVENTS.get(notify_event)
       if builder is not None:
           title_message, body_message, channel, query_args = builder(ctx)
           eventLogger.info(body_message)
       else:
           now = ctx["now"]
           title_message = "PiFire: Unknown Notification issue"
           body_message = "Whoops! PiFire had the following unhandled notify event: " + notify_event + " at " + str(now)
           channel = "default"
           query_args = {"value1": "Unknown Notification issue"}
           eventLogger.error(body_message)

       # --- fan-out block: UNCHANGED from the original (apprise/ifttt/pushbullet/pushover/
       #     onesignal/mqtt/wled gating) ---
   ```

   Preserve the exact fan-out block including the `control = read_control()` re-read inside the mqtt branch.
   Note `EVENTS` omits `Grill_Error_00` and `Grill_Warning` entirely (dropped — see step 5).

5. **Behavior-change flips (same commit), under test-flip discipline:**
   a. *Typo fix:* `_evt_grill_error_01` uses the corrected `"Grill exceeded maximum..."`. Update the Task 1
      `Grill_Error_01` assertion from `"exceded"` to `"exceeded"` (it goes RED on the corrected code, the
      assertion edit makes it GREEN).
   b. *Dead-event drop:* with `Grill_Error_00`/`Grill_Warning` absent from `EVENTS`, add two assertions that
      `send_notifications("Grill_Error_00")` and `send_notifications("Grill_Warning")` now produce the
      **fallback** tuple — title `"PiFire: Unknown Notification issue"`, body
      `startswith("Whoops! PiFire had the following unhandled notify event: Grill_Error_00 at ")` (resp.
      `Grill_Warning`), channel `"default"`, `query_args == {"value1": "Unknown Notification issue"}`, and
      that the event is logged at `ERROR`. These pin the deliberate removal.

**Test command:**
`timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/notify/ -q`

**Expected:** all Task 1 tests for the 10 live events still pass; the `Grill_Error_01` assertion now asserts
`"exceeded"`; the two new fallback-routing assertions pass.

**Commit:** `refactor(notify): data-drive send_notifications with an EVENTS table` (body notes the two
approved behavior changes: exceded→exceeded typo fix, and dropping the dead Grill_Error_00/Grill_Warning events)

---

## Task 3 — Extract `_event_logger()`

**Goal:** Collapse the repeated logger-setup boilerplate into one module-level helper.

**Files:** `notify/notifications.py`.

**Interface produced:**

```python
def _event_logger(settings):
    """Return the shared 'events' logger, level set from settings.debug_mode."""
    log_level = logging.DEBUG if settings["globals"]["debug_mode"] else logging.INFO
    return create_logger(
        "events",
        filename="./logs/events.log",
        messageformat="%(asctime)s [%(levelname)s] %(message)s",
        level=log_level,
    )
```

**Steps:**

1. Insert `_event_logger` at module level (before `send_notifications`).
2. Replace the boilerplate in these functions with `eventLogger = _event_logger(settings)`:
   - `send_notifications` (the `log_level = …` + `create_logger(…, level=log_level)` pair).
   - `_send_apprise_notifications`.
   - `_send_onesignal_notification`.
   - `_send_ifttt_notification`.
   The pushover/pushbullet copies are handled in Task 4 (they collapse into `_send_apprise_url`), so leave
   them for now.
3. **Do not** touch the logger in `_estimate_eta` (line ~570): it is a *different* call —
   `create_logger("events", filename="./logs/events.log")` with **no** `messageformat` and **no** level,
   and it has no `settings` in scope. Converting it would change its format/level. Leave it as-is; note in
   the commit that it is intentionally excluded.

**Equivalence note:** the two forms in play — (A) `create_logger(..., level=log_level)` and (B)
`create_logger(...)` then `eventLogger.setLevel(...)` — both end at the same effective level for the same
`debug_mode`, so `_event_logger` is a faithful replacement for both.

**Test command:**
`timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/notify/ -q`

**Expected:** all tests still pass.

**Commit:** `refactor(notify): extract shared _event_logger() helper`

---

## Task 4 — Collapse the two apprise senders into `_send_apprise_url(...)`

**Goal:** Merge `_send_pushover_notification` and `_send_pushbullet_notification`. The genuine differences
are only the **service label** and the **apprise URL scheme** (`pover://` vs `pbul://`, and pushover loops
over comma-separated `UserKeys` while pushbullet has a single URL). Extract the send loop; keep the two
public wrapper names so `send_notifications`' fan-out block and any external callers are unchanged.

**Files:** `notify/notifications.py`.

**Interface produced:**

```python
def _send_apprise_url(settings, urls, title_message, body_message, service_name):
    """Add each apprise URL and notify; log success/failure under service_name."""
```

**Steps:**

1. Add `_send_apprise_url`:

   ```python
   def _send_apprise_url(settings, urls, title_message, body_message, service_name):
       eventLogger = _event_logger(settings)
       appriseHandler = apprise.Apprise()
       for apprise_url in urls:
           appriseHandler.add(apprise_url)
       try:
           result = appriseHandler.notify(title=title_message, body=body_message)
           if result:
               eventLogger.debug(f"{service_name} Notification was a success!")
           else:
               eventLogger.warning(f"{service_name} Notification failed!")
       except Exception as e:
           eventLogger.warning(f"{service_name} Notification failed: {e}")
       except:
           eventLogger.warning(f"{service_name} Notification failed for unknown reason.")
   ```

2. Reduce the two senders to URL builders that delegate (keep the names + signatures):

   ```python
   def _send_pushover_notification(settings, title_message, body_message):
       token = settings["notify_services"]["pushover"]["APIKey"]
       public_url = settings["notify_services"]["pushover"]["PublicURL"]
       urls = [
           f"pover://{user.strip()}@{token}?url={public_url}"
           for user in settings["notify_services"]["pushover"]["UserKeys"].split(",")
       ]
       _send_apprise_url(settings, urls, title_message, body_message, "Pushover")

   def _send_pushbullet_notification(settings, title_message, body_message):
       api_key = settings["notify_services"]["pushbullet"]["APIKey"]
       public_url = settings["notify_services"]["pushbullet"]["PublicURL"]
       urls = [f"pbul://{api_key}@{api_key}?url={public_url}"]
       _send_apprise_url(settings, urls, title_message, body_message, "Pushbullet")
   ```

   **Equivalence proof / documented deviation:** The **load-bearing** behavior — the exact apprise URLs
   added and the `title`/`body` passed to `appriseHandler.notify` — is preserved identically (the callers
   build the same `pover://…` / `pbul://…` strings). The only change is **log-message text**: the originals
   embedded the recipient token (`{user}` / `{api_key}`) in the debug/warning lines; the merged helper logs
   under `service_name` without the recipient. Log strings are not user-facing notifications and are not
   asserted by any test — this is an intentional, non-lossy cosmetic simplification. State it in the commit
   body.

3. Add tests to `test_notifications_events.py` proving the URL construction (this is the part that must not
   regress):
   - `_send_pushover_notification` with `UserKeys="u1, u2"`, `APIKey="tok"`, `PublicURL="http://x"` →
     patch `apprise.Apprise` (or `N.apprise.Apprise`) with a fake capturing `.add(...)` calls; assert the
     added URLs are exactly `["pover://u1@tok?url=http://x", "pover://u2@tok?url=http://x"]` and
     `.notify` got `title=`/`body=`.
   - `_send_pushbullet_notification` with `APIKey="k"`, `PublicURL="http://y"` → added URL exactly
     `["pbul://k@k?url=http://y"]`.
   - Patch `apprise.Apprise` so **no** real network call occurs.

**Test command:**
`timeout 180 env QT_QPA_PLATFORM=offscreen SDL_VIDEODRIVER=dummy uv run pytest tests/unit/notify/ -q`

**Expected:** all tests pass, including the two new URL-construction tests.

**Commit:** `refactor(notify): collapse pushover/pushbullet into _send_apprise_url`

---

## Follow-on (tracked, NOT in this phase)

Now that `EVENTS` exists, `wled_handler._notify_traditional` should consume it instead of re-switching on
the same event strings. Tracked separately; do not implement here.

---

## Self-Review Checklist

- [ ] Task 1 committed **before** any production edit; it asserts all 10 live events + the fallback.
- [ ] Every EVENTS builder reproduces its original branch's exact `(title, body, channel, query_args)` —
      cross-checked line-by-line against the pre-refactor source, including:
  - [ ] `Probe_*` `query_args["value1"]` is boolean `True`, not `"True"`.
  - [ ] `Pellet_Level_Low` `query_args["value1"]` equals the computed body.
  - [ ] `Grill_Error_03` has NO trailing `<now>`; `01/02` do.
  - [ ] `Grill_Error_01` typo `"exceded"` corrected to `"exceeded"` AND its Task 1 pin flipped in the same commit (approved behavior change #1).
  - [ ] `Grill_Error_02/03` read `startuptemp` from control; `Grill_Error_01` reads `maxtemp` from settings.
- [ ] `Grill_Error_00` and `Grill_Warning` dropped from EVENTS (approved behavior change #2); Task 2 asserts both now route to the Unknown-Notification fallback at `ERROR` level.
- [ ] Fallback keeps `eventLogger.error(...)`; all matched events keep `eventLogger.info(...)`.
- [ ] Lookup is exact-key `EVENTS.get(notify_event)` (not substring); documented as behavior-preserving because all callers pass exact literal keys.
- [ ] Fan-out block (apprise/ifttt/pushbullet/pushover/onesignal/mqtt/wled gating, incl. the mqtt-branch
      `read_control()` re-read) is byte-for-byte unchanged.
- [ ] `_event_logger` faithfully replaces both the `level=` form and the `setLevel` form; `_estimate_eta`'s
      distinct logger left untouched.
- [ ] `_send_apprise_url` merge preserves exact URL construction; only log text changes (documented).
- [ ] Every test mocks all network/apprise senders — no real notifications sent.
- [ ] `wled_handler` follow-on left untouched.
- [ ] Each task: `uvx ruff format` + `uvx ruff check` clean, committed with `-F` msgfile + co-author trailer.
