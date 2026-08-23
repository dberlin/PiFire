"""Controller orchestrator: the outer control-process loop.

`Controller` owns the persistent per-process state (settings/control/status/
pelletdb/last-switch-state) and dispatches to per-mode work cycles.
`Controller.tick()` is one iteration: poll the on/off switch, refresh
status/probe-device info, apply pending control/settings/notification/hopper
changes, then -- if a mode change is pending -- run the requested mode's work
cycle via `work_cycle()`/`run_work_cycle()` and hand off to `next_mode()`.
`Controller.run()` is `setup()` followed by `while True: tick(); sleep(0.1)`
(RealClock in production).

All datastore access goes through `self.ctx.store` (a `Store`; production uses
`SqliteStore`, tests inject `InMemoryStore`), and all timing goes through
`self.ctx.clock` (`RealClock` in production, `ManualClock` in tests), so the
loop is deterministic and testable without a real SQLite datastore or wall clock.

Notification/cookfile helpers (`check_notify`, `send_notifications`,
`create_cookfile`) and `os.system` remain module-level references so tests can
monkeypatch them.
"""

import copy
import os

from common.common import ErrorKind
from common.defaults import default_control
from common.modes import COOK_MODES, SAFE_MODES, Mode, StatusState
from notify.notifications import check_notify, send_notifications
from file_mgmt.cookfile import create_cookfile
from file_mgmt.recipes import convert_recipe_units
from file_mgmt.common import read_json_file_data
from os.path import exists

from distance.intervals import HOPPER_LEVEL_REFRESH_INTERVAL

from controller.learning_report import controller_learning_report
from controller.runtime.heartbeat import stamp_control_heartbeat
from controller.runtime.state import WorkCycleState
from controller.runtime.system_commands import process_system_commands
from controller.runtime.transitions import request_transition, should_keep_power_on, TransitionKind
from controller.runtime.modes.monitor import MonitorMode
from controller.runtime.modes.manual import ManualMode
from controller.runtime.modes.shutdown import ShutdownMode
from controller.runtime.modes.prime import PrimeMode
from controller.runtime.modes.startup import StartupMode
from controller.runtime.modes.reignite import ReigniteMode
from controller.runtime.modes.smoke import SmokeMode
from controller.runtime.modes.hold import HoldMode


_MODE_HANDLERS = {
    Mode.MONITOR: MonitorMode,
    Mode.MANUAL: ManualMode,
    Mode.SHUTDOWN: ShutdownMode,
    Mode.PRIME: PrimeMode,
    Mode.STARTUP: StartupMode,
    Mode.REIGNITE: ReigniteMode,
    Mode.SMOKE: SmokeMode,
    Mode.HOLD: HoldMode,
}


def run_work_cycle(mode, ctx):
    """Run a single per-mode work cycle: look up the `ControlMode` subclass for
    `mode` in `_MODE_HANDLERS`, construct it with a fresh `WorkCycleState`, and
    run it to completion. Module-level so it can be exercised in isolation (the
    characterization/E2E harness runs one cycle at a time) without constructing
    a full Controller."""
    return _MODE_HANDLERS[mode](ctx, WorkCycleState()).run()


class Controller:
    """Owns the outer control loop that dispatches to per-mode work cycles."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.grill_platform = ctx.devices.grill_platform
        self.probe_complex = ctx.devices.probe_complex
        self.dist_device = ctx.devices.dist_device
        self.eventLogger = ctx.event_log
        self.controlLogger = ctx.control_log
        # Persistent loop state, held across tick() calls for the process lifetime.
        self.settings = ctx.store.read_settings()
        self.control = None
        self.status = None
        self.pelletdb = None
        self.last = None
        # Last time the hopper level was written to pelletdb, by either the
        # automatic refresh or an explicit hopper_check. setup() re-stamps it.
        self._hopper_refresh_time = ctx.clock.now()

    # --- work-cycle dispatch helpers ---

    def work_cycle(self, mode):
        """Run one per-mode work cycle."""
        return run_work_cycle(mode, self.ctx)

    def next_mode(self, next_mode, setpoint=0):
        # The "natural" kind flushes deferred writes, re-reads control, and
        # yields if a higher-priority transition already landed this cycle --
        # behavior-equivalent to the old guarded inline write.
        return request_transition(
            self.ctx, self.ctx.store.read_control(), next_mode, kind=TransitionKind.NATURAL, setpoint=setpoint
        )

    def process_system_commands(self):
        process_system_commands(self.ctx)

    def recipe_mode(self, start_step=0):
        """Recipe Mode Control -- walks recipe steps, running a work cycle each."""
        ctx = self.ctx
        settings = ctx.store.read_settings()
        self.eventLogger.info("Recipe Mode started.")

        # Find Recipe File
        control = ctx.store.read_control()
        recipe_file = control["recipe"]["filename"]

        if not exists(recipe_file):
            # File not found. Recover from the stuck state: the outer tick dispatched
            # us because mode=="Recipe" with updated already cleared, so a bare return
            # here would leave the controller idling in Recipe forever. Route to Stop.
            self.eventLogger.warning(f"Recipe file {recipe_file} not found!")
            request_transition(self.ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)
            return ()

        # 1. Read metadata from the recipe file
        metadata, status = read_json_file_data(recipe_file, "metadata")
        if status != "OK":
            self.eventLogger.warning(f"Failed to load metadata for {recipe_file}.")
            request_transition(self.ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)
            return ()

        # 2. Read recipe steps (& other data) from the recipe file
        recipe, status = read_json_file_data(recipe_file, "recipe")
        if status != "OK":
            self.eventLogger.warning(f"Failed to load recipe data for {recipe_file}.")
            request_transition(self.ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)
            return ()

        # 3. Check and convert temperature units, if there is a mismatch
        if settings["globals"]["units"] != metadata["units"]:
            recipe = convert_recipe_units(recipe, settings["globals"]["units"])

        num_steps = len(recipe["steps"])
        step_num = start_step  # Start at step 0 by default unless requested to start at a later step

        # 4. Walk through steps, and execute work cycle
        while step_num < num_steps:
            # 4a. Setup all step data and write to control
            control["recipe"]["step"] = step_num
            # Copy the step so the in-place trigger_temps remap below does not
            # corrupt the source recipe -- otherwise a reignite retry (which
            # re-enters step setup for the same step_num) reads a step whose
            # trigger_temps were already replaced with the probe-mapped form and
            # KeyErrors on ["primary"].
            control["recipe"]["step_data"] = copy.deepcopy(recipe["steps"][step_num])
            """ Setup trigger_temps structure that the work_cycle expects, mapping to real probes """
            trigger_temps = {}
            trigger_temps[settings["recipe"]["probe_map"]["primary"]] = recipe["steps"][step_num]["trigger_temps"][
                "primary"
            ]
            for index, value in enumerate(recipe["steps"][step_num]["trigger_temps"]["food"]):
                trigger_temps[settings["recipe"]["probe_map"]["food"][index]] = value
            control["recipe"]["step_data"]["trigger_temps"] = trigger_temps
            control["recipe"]["step_data"]["triggered"] = False
            control["primary_setpoint"] = recipe["steps"][step_num]["hold_temp"]  # Set Hold Temp if applicable.
            control["updated"] = False  # Clear Updated Flag if Set
            ctx.store.write_control_snapshot(control, origin="control")
            # 4b. Start the recipe step work cycle
            self.work_cycle(recipe["steps"][step_num]["mode"])

            # 4c. If reignite is required, run a reignite cycle and retry current step
            ctx.store.execute_control_writes()
            control = ctx.store.read_control()
            if control["mode"] == Mode.REIGNITE and control["updated"]:
                control["updated"] = False
                control["mode"] = Mode.RECIPE
                ctx.store.write_control_snapshot(control, origin="control")
                self.work_cycle(Mode.REIGNITE)
                control = ctx.store.read_control()
                if control["updated"] and control["mode"] != Mode.RECIPE:
                    # If another mode was requested (or an error occurred) then exit recipe mode
                    self.eventLogger.info(f"Recipe mode cancelled due to mode change: {control['mode']}")
                    break
                # 4c-2. Rerun current step
            # 4d. If another mode was requested (or an error occurred) then exit recipe mode
            elif control["mode"] != Mode.RECIPE and control["updated"]:
                self.eventLogger.info(f"Recipe mode cancelled due to mode change: {control['mode']}")
                break
            else:
                # 4e. Continue to next step number
                step_num += 1

        # 5. Clean up control data and exit
        control["recipe"]["step"] = 0
        control["recipe"]["step_data"] = {}
        control["recipe"]["filename"] = ""

        # If recipe is exiting normally (i.e. no other mode requested, then initiate stop mode)
        if not control["updated"] or (step_num == num_steps):
            self.eventLogger.info("Recipe mode ended.")
            # Genuine terminal transition -> route through request_transition
            # (mode=Stop, updated, write). The recipe-field cleanup above is carried
            # on the same control dict, so that single OVERWRITE persists it too.
            request_transition(self.ctx, control, Mode.STOP, kind=TransitionKind.TERMINAL)
        else:
            # Cancel/break case: no mode transition here (the requested mode is
            # already in control); just persist the recipe-field cleanup.
            ctx.store.write_control_snapshot(control, origin="control")

        return ()

    # --- lifecycle ---

    def cleanup(self):
        """atexit handler: log and clean up the grill platform on process exit."""
        self.eventLogger.info("Control Script Exiting.")
        self.controlLogger.info("Control Script Exiting.")
        self.grill_platform.cleanup()

    def setup(self):
        """One-time initialization run before the main loop starts."""
        store = self.ctx.store

        # Initial hopper-level publish on boot. Without this, `pelletdb` is
        # unbound the first time the loop calls check_notify.
        #
        # Reads the CACHE. On a threaded sensor the first sample may not have
        # landed yet, in which case this publishes the last known/default level
        # and startup proceeds -- the control process must not wait on a
        # distance sensor to come up.
        self.pelletdb = store.read_pellet_db()
        self.pelletdb["current"]["hopper_level"] = self.dist_device.get_level()
        store.write_pellet_db(self.pelletdb)
        self.eventLogger.info(f"Hopper Level Checked @ {self.pelletdb['current']['hopper_level']}%")
        # Start the automatic-refresh timer from the boot-time reading, so the
        # first timed refresh is a full interval after it rather than immediate.
        self._hopper_refresh_time = self.ctx.clock.now()

        self.last = self.grill_platform.get_input_status()

        """ If the user has selected boot-to-monitor mode, then issue the command prior to the main loop """
        if self.settings["globals"]["boot_to_monitor"]:
            control = store.read_control()
            control["mode"] = Mode.MONITOR
            control["updated"] = True
            store.write_control_snapshot(control, origin="control")

        """ Initialize the status data on first run. """
        self.status = store.init_status()

        # Bind `control` before the loop so the iteration-1 switch check has it
        # (the entry point already flushed control; boot_to_monitor may have just
        # updated it).
        self.control = store.read_control()

    def run(self):
        """setup() then loop forever, one tick() per 0.1s (RealClock)."""
        self.setup()
        while True:
            self.tick()
            self.ctx.clock.sleep(0.1)

    def tick(self):
        """One iteration of the control loop. Persistent state lives on self."""
        ctx = self.ctx
        store = ctx.store
        grill_platform = self.grill_platform
        settings = self.settings

        stamp_control_heartbeat(ctx)

        # Check the On/Off switch for changes
        if not settings["platform"]["standalone"] and self.last != grill_platform.get_input_status():
            self.last = grill_platform.get_input_status()
            if not self.last:
                self.eventLogger.info("Switch set to off, going to stop mode.")
                self.controlLogger.info(f"Switch set to off, going to stop mode.")
                self.control["updated"] = True  # Change mode
                self.control["mode"] = Mode.STOP
                store.write_control_snapshot(self.control, origin="control")

        self.status = store.read_status()

        # Get probe device info for frontend
        store.write_generic_key("probe_device_info", self.probe_complex.get_device_info())

        current = grill_platform.get_output_status()  # Get current pin settings
        for item in settings["platform"]["outputs"]:
            try:
                self.status["outpins"][item] = current[item]
            except KeyError:
                continue
        store.write_status(self.status)

        # Check control for changes
        store.execute_control_writes()
        self.control = store.read_control()

        # Check for system commands
        self.process_system_commands()

        # Check if there were updates to any of the settings that were flagged
        if self.control["settings_update"]:
            self.control["settings_update"] = False
            store.write_control_snapshot(self.control, origin="control")
            self.settings = settings = store.read_settings()

        # Check if there are any notifications pending
        check_notify(settings, self.control, pelletdb=self.pelletdb, grill_platform=grill_platform)

        # Check if there is a timer running, see if it has expired, send notification and reset
        for index, item in enumerate(self.control["notify_data"]):
            if item["type"] == "timer" and item["req"]:
                if ctx.clock.now() >= self.control["timer"]["end"]:
                    send_notifications("Timer_Expired")
                    self.control["notify_data"][index]["req"] = False
                    self.control["timer"]["start"] = 0
                    self.control["timer"]["paused"] = 0
                    self.control["timer"]["end"] = 0
                    self.control["notify_data"][index]["shutdown"] = False
                    self.control["notify_data"][index]["keep_warm"] = False
                    store.write_control_snapshot(self.control, origin="control")

        # Check if user changed hopper levels and update if required
        if self.control["distance_update"]:
            empty = settings["pelletlevel"]["empty"]
            full = settings["pelletlevel"]["full"]
            self.dist_device.update_distances(empty, full)
            self.control["distance_update"] = False
            store.write_control_snapshot(self.control, origin="control")

        if self.control["hopper_check"]:
            # Something asked for a fresh reading (the attached display, the
            # Flask pellet pages, the socket.io API). ASK, then carry on: the
            # request is a flag the sampling thread is already watching, and
            # returns immediately. The reading it produces reaches the datastore
            # via the timed refresh below, which is deliberately NOT restamped
            # here -- restamping it would delay publishing the very sample that
            # was just requested.
            self.dist_device.request_sample()
            self.control["hopper_check"] = False
            store.write_control_snapshot(self.control, origin="control")
            self.eventLogger.info("Hopper Level Check requested.")
        if (ctx.clock.now() - self._hopper_refresh_time) > HOPPER_LEVEL_REFRESH_INTERVAL:
            # Automatic refresh: the only thing that publishes a hopper level
            # after boot. There is no Refresh Status button any more, and in
            # Stop mode nothing else runs (the per-mode work cycle has its own
            # copy of this timer, and does not run outside a cook).
            #
            # This reads a value the sampling thread already produced, and can
            # never wait for one -- get_level() takes no arguments and has no
            # blocking path. That is the whole safety property: this loop is
            # timing the auger and the igniter.
            #
            # Deliberately not logged -- once per interval, forever, would bury
            # the event log that the explicit requests above are visible in.
            self.pelletdb = store.read_pellet_db()
            self.pelletdb["current"]["hopper_level"] = self.dist_device.get_level()
            store.write_pellet_db(self.pelletdb)
            self._hopper_refresh_time = ctx.clock.now()

        # Rebuild every probe device if the probe MAP changed (POST /api/probe_map).
        # Distinct from probe_profile_update below: that only refills per-port
        # profiles on already-constructed devices (probes/base.py:393-401) and
        # cannot see an added, removed or renamed probe.
        #
        # Ordered BEFORE probe_profile_update so that a tick carrying both flags
        # applies the profiles refresh to the newly built devices.
        #
        # .get(), not [...]: an install upgraded in place has a control blob
        # written before this key existed in default_control(). The
        # probe_profile_update line below indexes directly and would KeyError
        # on such a blob; do not copy that here.
        if self.control.get("probe_map_update"):
            self.settings = settings = store.read_settings()
            self.control["probe_map_update"] = False
            store.write_control_snapshot(self.control, origin="control")
            errors = self.probe_complex.update_probe_map(settings["probe_settings"]["probe_map"])
            store.write_generic_key("probe_device_info", self.probe_complex.get_device_info())
            for error in errors or []:
                self.eventLogger.error(error)
            self.eventLogger.info("Probe map reloaded in control script.")

        # Grab current probe profiles if they have changed since the last loop.
        if self.control["probe_profile_update"]:
            self.settings = settings = store.read_settings()
            self.control["probe_profile_update"] = False
            store.write_control_snapshot(self.control, origin="control")
            # Add new probe profiles to probe complex object
            self.probe_complex.update_probe_profiles(settings["probe_settings"]["probe_map"]["probe_info"])
            self.eventLogger.info("Active probe profiles updated in control script.")

        # A platform the controller could not build is a reason to refuse to
        # light a fire, never a reason to refuse to put one out. `critical_error`
        # therefore withholds the whole dispatch below -- units change, status
        # transitions, auger off, cook-file creation, history flush -- from every
        # mode that adds energy to the firepot, and from none of the modes that
        # take it away. Without the SAFE_MODES arm a latched flag swallows the
        # operator's Stop along with everything else, leaving a lit grill that
        # can never be commanded again: `updated` is not even cleared, so the
        # request sits in the datastore and the UI shows a transition that never
        # happens.
        if self.control["updated"] and (not self.control["critical_error"] or self.control["mode"] in SAFE_MODES):
            self.eventLogger.debug(
                f"Control Settings Updated.  Mode: {self.control['mode']}, Units Change: {self.control['units_change']} "
            )
            # Clear control flag
            self.control["updated"] = False  # Reset Control Updated to False
            store.write_control_snapshot(self.control, origin="control")  # Commit change in 'updated' status to the file

            if self.control["units_change"]:
                self.eventLogger.debug("Changing Base Units.")
                self.settings = settings = store.read_settings()
                # Update ADC objects and set profiles
                self.probe_complex.update_units(settings["globals"]["units"])
                self.control["mode"] = Mode.STOP  # Stop any activity
                self.control["units_change"] = False
                store.flush_history()  # Clear history data
                self.control["cook_id"] = None
                # No need to write control, as it should be written by the 'Stop' mode change

            # Check if there was an Error flagged in Monitor Mode - If no, then change status to active
            if self.control["status"] != StatusState.MONITOR and self.control["mode"] != Mode.ERROR:
                self.control["status"] = StatusState.ACTIVE  # Set status to active
                store.write_control_snapshot(self.control, origin="control")

            if self.control["mode"] in (Mode.STOP, Mode.ERROR):
                grill_platform.auger_off()
                grill_platform.igniter_off()
                grill_platform.fan_off()
                # Terminal modes have driven every actuator off. Publish zero
                # duties before cook-file archival, which can take long enough
                # for dashboards to read this terminal state.
                self.status["cycle_ratio"] = 0
                self.status["fan_duty"] = 0
                store.write_status(self.status)
                cook_id = self.control.get("cook_id")
                session_finalized = False
                # Register Stop Mode in Metrics DB if this is not initial stop-mode on startup (i.e. DB is empty)
                metrics_list = store.read_all_metrics()
                if len(metrics_list) != 0:
                    store.append_metric()
                    metrics = store.read_metrics()
                    metrics["mode"] = Mode.STOP
                    store.update_metrics(metrics)
                    # Archive the session only if a cook actually happened.
                    # This used to ask "was the LAST mode Prime?", which is a
                    # test for one specific non-cook rather than for a cook: a
                    # Monitor session -- temperatures watched, nothing ever lit
                    # -- answered "no" and wrote a full .pifire on its way out.
                    # Ask what the session CONTAINED instead, which also stops
                    # the verdict depending on which mode happened to be last.
                    cooked = any(entry.get("mode") in COOK_MODES for entry in metrics_list)
                    if cooked:
                        # A failed cookfile write must not take down grill control --
                        # on a real grill an uncaught exception here kills the whole
                        # control loop and crash-loops the controller at every
                        # cook's end (the poisoned metrics rows that triggered this
                        # persist in the datastore, so a naive restart re-crashes on
                        # the very next stop transition too). Keep this wrap NARROW
                        # -- just the create_cookfile() call -- so every other tick
                        # behavior (status/control resets, display clear, etc. below)
                        # still runs unconditionally.
                        try:
                            create_cookfile(
                                cook_id=cook_id,
                                learning_report_provider=controller_learning_report,
                            )
                            session_finalized = True
                        except Exception as e:
                            self.eventLogger.error(f"Failed to create cookfile: {e}")
                            # A failed cookfile write is potential cook-data loss;
                            # give it the same active surfacing other runtime
                            # problems get (build_devices()/build_display() use
                            # the same errors-list idiom on hardware-load
                            # failure) instead of leaving it visible only on the
                            # passive Logs page. This list is what dash_data's
                            # "errors" key -- and the dashboard's error banners --
                            # read.
                            errors = store.read_errors(ErrorKind.CONTROL)
                            errors.append("Cook file could not be created — see Logs")
                            store.write_errors(ErrorKind.CONTROL, errors)
                    elif metrics_list[-1].get("mode") != Mode.PRIME:
                        # Nothing worth archiving, but the session's history and
                        # metrics still have to go: create_cookfile() ends with
                        # flush_history() (which clears metrics and current
                        # too), so skipping the archive silently skips the flush
                        # as well, and a Monitor session would bleed its
                        # temperatures into the chart of the next real cook.
                        #
                        # Prime keeps its long-standing carry-over -- the whole
                        # point of "prime, then start up" is that the two are
                        # one session -- so it is the one case that is
                        # deliberately NOT flushed here.
                        store.flush_history()
                        session_finalized = True

                self.status["p_mode"] = 0
                self.status["mode"] = Mode.STOP
                self.status["recipe"] = False
                self.status["recipe_paused"] = False
                self.status["start_time"] = 0
                self.status["lid_open_detected"] = False
                self.status["lid_open_endtime"] = 0
                self.status["startup_timestamp"] = 0
                store.write_status(self.status)

                if should_keep_power_on(self.control["mode"], self.control["status"]):
                    grill_platform.power_on()
                else:
                    grill_platform.power_off()

                # Both cleanup branches below rebind control to a fresh
                # default_control(), whose `critical_error` is False -- so
                # reaching Stop or Error would otherwise ANSWER the question
                # "can this controller drive its hardware?" by forgetting it,
                # and the next Startup would light a fire on a platform that
                # failed to build. The flag describes the hardware, not the
                # cook, so it survives the reset and stays visible to the UI.
                critical_error = self.control["critical_error"]
                retained_cook_id = None if session_finalized else cook_id

                if self.control["mode"] == Mode.STOP:
                    self.eventLogger.info("Stop Mode Started.")
                    store.display_commands().push(("clear", None))
                    # Reset Control to Defaults, then stamp status (mirrors the Error
                    # branch below). Setting status BEFORE this flush was a dead
                    # assignment -- flush_control() rebinds control to a fresh
                    # default_control() (status ""), discarding it, so Stop persisted "".
                    self.control = store.flush_control(cook_id=retained_cook_id)
                    self.control["critical_error"] = critical_error
                    self.control["status"] = StatusState.INACTIVE
                    self.control["updated"] = False
                    self.control["tuning_mode"] = False  # Turn off Tuning Mode on Stop just in case it is on
                    self.control["next_mode"] = Mode.STOP
                    self.control["safety"]["reigniteretries"] = settings["safety"][
                        "reigniteretries"
                    ]  # Reset retry counter to default
                    self.control["startup_timestamp"] = 0  # Reset the startup timestamp to 0
                    store.write_control_snapshot(self.control, origin="control")
                else:
                    self.eventLogger.error("An error has occurred, Stop Mode enabled.")
                    self.controlLogger.error("An error has occurred, Stop Mode enabled.")
                    # Reset Control to Defaults but preserve 'Error' mode condition
                    self.control = default_control()
                    self.control["critical_error"] = critical_error
                    self.control["cook_id"] = retained_cook_id
                    self.control["mode"] = Mode.ERROR
                    self.control["status"] = StatusState.INACTIVE
                    self.control["tuning_mode"] = False  # Turn off Tuning Mode on Stop just in case it is on
                    self.control["updated"] = False
                    self.control["next_mode"] = Mode.STOP
                    self.control["safety"]["reigniteretries"] = settings["safety"][
                        "reigniteretries"
                    ]  # Reset retry counter to default
                    store.write_control_snapshot(self.control, origin="control")
                    ctx.clock.sleep(3)
                    store.display_commands().push(("clear", None))

                store.flush_current()  # Zero out the current values

            else:
                # Per-mode work cycle dispatch (Prime/Startup/Smoke/Hold/Shutdown/
                # Monitor/Manual/Recipe/Reignite). The Stop/Error terminal cleanup
                # above stays inline (it is not a per-mode work cycle).
                handler = self._MODE_DISPATCH.get(self.control["mode"])
                if handler is not None:
                    handler(self)

            # Dispatch handlers may reload settings (Prime/Startup read fresh);
            # re-sync the local so the MQTT check below sees what the legacy
            # elif-ladder saw.
            settings = self.settings

        if settings["notify_services"].get("mqtt") != None and settings["notify_services"]["mqtt"]["enabled"]:
            check_notify(settings, self.control, pelletdb=self.pelletdb)

    # --- per-mode dispatch handlers (registered in _MODE_DISPATCH below) ---

    def _dispatch_prime(self):
        # Prime (dump preset amount of pellets into the firepot)
        store = self.ctx.store
        settings = self.settings
        grill_platform = self.grill_platform
        if not settings["platform"]["standalone"] and not grill_platform.get_input_status():
            self.eventLogger.warning(
                "PiFire is set to OFF. This doesn't prevent startup, but this means the switch won't behave as normal."
            )
        # Call Work Cycle for Startup Mode
        self.work_cycle(Mode.PRIME)
        # Select Next Mode
        self.settings = settings = store.read_settings()
        self.next_mode(
            self.control["next_mode"],
            setpoint=settings["startup"].get("start_to_mode", {}).get("primary_setpoint", 165),
        )

    def _dispatch_startup(self):
        # Startup (startup sequence)
        store = self.ctx.store
        settings = self.settings
        grill_platform = self.grill_platform
        if not settings["platform"]["standalone"] and not grill_platform.get_input_status():
            self.eventLogger.warning(
                "PiFire is set to OFF. This doesn't prevent startup, but this means the switch won't behave as normal."
            )
        self.settings = settings = store.read_settings()
        # Clear History (in the case it wasn't already cleared fromt he last run)
        self.eventLogger.debug("Clearing History and Current Log on Startup Mode.")
        store.flush_history()  # Clear all history
        # Check if Prime on Startup is selected
        if settings["startup"]["prime_on_startup"] > 0:
            self.control["prime_amount"] = settings["startup"]["prime_on_startup"]
            self.control["mode"] = Mode.PRIME
            store.write_control_snapshot(self.control, origin="control")
            # Call Work Cycle for Prime Mode
            self.work_cycle(Mode.PRIME)
            self.control = store.read_control()  # Refresh control in case any changes were made during the cycle
            if self.control["mode"] in [Mode.PRIME, Mode.STARTUP]:
                self.control["updated"] = False
                self.control["mode"] = Mode.STARTUP
        # Check if there was a mode change during Priming
        if self.control["mode"] == Mode.STARTUP:
            # Setup Next Mode (after startup mode)
            self.control["next_mode"] = settings["startup"].get("start_to_mode", {}).get("after_startup_mode", "Smoke")
            store.write_control_snapshot(self.control, origin="control")
            # Call Work Cycle for Startup Mode
            self.work_cycle(Mode.STARTUP)
            # Select Next Mode
            self.settings = settings = store.read_settings()
            self.next_mode(
                self.control["next_mode"],
                setpoint=settings["startup"].get("start_to_mode", {}).get("primary_setpoint", 165),
            )

    def _dispatch_smoke(self):
        # Smoke (smoke cycle)
        self.work_cycle(Mode.SMOKE)
        self.next_mode(self.control["next_mode"])

    def _dispatch_hold(self):
        # Hold (hold at setpoint)
        self.work_cycle(Mode.HOLD)
        self.next_mode(self.control["next_mode"], setpoint=self.control["primary_setpoint"])

    def _dispatch_shutdown(self):
        # Shutdown (shutdown sequence)
        store = self.ctx.store
        settings = self.settings
        self.control["next_mode"] = Mode.STOP
        store.write_control_snapshot(self.control, origin="control")
        self.work_cycle(Mode.SHUTDOWN)
        self.next_mode(self.control["next_mode"])
        # Powering the host off is conditional on the shutdown having actually
        # reached Stop. A cycle broken by an operator pressing Smoke or Hold has
        # asked for the grill to KEEP running, and next_mode() yields to that
        # request rather than overriding it -- so halting here would strand a
        # lit firepot with nothing controlling it. A shutdown that ended in
        # Error leaves the host up for the same reason, and so the error can be
        # read.
        self.control = store.read_control()
        if settings["shutdown"]["auto_power_off"] and self.control["mode"] == Mode.STOP:
            self.eventLogger.info("Shutdown mode ended powering off grill")
            os.system("sleep 3 && sudo shutdown -h now &")

    def _dispatch_monitor(self):
        # Monitor (monitor the OEM controller)
        store = self.ctx.store
        self.control["status"] = StatusState.MONITOR  # Set status to monitor
        store.write_control_snapshot(self.control, origin="control")
        self.work_cycle(Mode.MONITOR)

    def _dispatch_manual(self):
        # Manual Mode
        self.work_cycle(Mode.MANUAL)

    def _dispatch_recipe(self):
        # Recipe Mode
        self.recipe_mode(start_step=self.control["recipe"]["start_step"])

    def _dispatch_reignite(self):
        # Reignite (reignite sequence)
        store = self.ctx.store
        settings = self.settings
        grill_platform = self.grill_platform
        if (not settings["platform"]["standalone"]) and (not grill_platform.get_input_status()):
            self.eventLogger.warning(
                "PiFire is set to OFF. This doesn't prevent reignite, but this means the switch won't behave as normal."
            )
        self.control["next_mode"] = self.control["safety"]["reignitelaststate"]
        setpoint = self.control["primary_setpoint"]
        store.write_control_snapshot(self.control, origin="control")
        self.work_cycle(Mode.REIGNITE)
        self.next_mode(self.control["next_mode"], setpoint=setpoint)

    _MODE_DISPATCH = {
        Mode.PRIME: _dispatch_prime,
        Mode.STARTUP: _dispatch_startup,
        Mode.SMOKE: _dispatch_smoke,
        Mode.HOLD: _dispatch_hold,
        Mode.SHUTDOWN: _dispatch_shutdown,
        Mode.MONITOR: _dispatch_monitor,
        Mode.MANUAL: _dispatch_manual,
        Mode.RECIPE: _dispatch_recipe,
        Mode.REIGNITE: _dispatch_reignite,
    }
