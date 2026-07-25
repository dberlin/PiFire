"""Golden-master characterization tests for common.common.process_command.

METHOD: RUN-THEN-FREEZE, golden-file oracle. Every case in CASES was executed
ONCE against the pre-refactor `process_command` (common/common.py:2505-3168) and
its complete observable footprint frozen into
`tests/characterization/fixtures/process_command_golden.json`. That file is the
CONTRACT. Any refactor that decomposes this 666-line function into shared
helpers may only pass if it reproduces the contract byte-for-byte.

THIS FILE ONLY EVER *READS* THE GOLDEN. There is deliberately no capture/record
mode, no `--update-golden` flag, and no committed capture script -- so there is
no button to press that silently re-baselines the contract. Additionally
`test_golden_file_digest_is_pinned` pins the fixture's SHA-256 to GOLDEN_SHA256
below. Regenerating the fixture therefore requires hand-editing BOTH the JSON
and this constant, in a diff a reviewer cannot miss. If you are refactoring
process_command and a case fails: that is a behavior change. Fix the refactor,
not the fixture.

DELIBERATE RE-BASELINE (settings-writer strict matrix): fixed
common/common.py's `convert_settings_units` to also convert
`settings["pwm"]["temp_range_list"]` (a "degrees below setpoint" DELTA list
that was never converted at all -- a real, pre-existing bug found while
auditing common/api_commands.py's set/units writer for schema strictness).
The `set_units_c`/`set_units_f` golden entries and GOLDEN_SHA256 were
hand-updated to include the now-correct `pwm.temp_range_list` diff. This is
the one sanctioned exception to "never regenerate the fixture": a proven bug
fix, not refactor drift.

WHAT IS OBSERVED (per case, see `_run_case`):
  * the returned dict (result/message/data)
  * `arglist` AFTER the call -- process_command mutates its caller's list
  * every MERGE partial queued to `queue_control_write` (as a diff vs the
    pre-call control), including its `origin`
  * the control blob diff after `execute_control_writes()` drains that queue
  * the settings blob diff (set/units and set/pmode write settings)
  * the `queue_systemq` payload (action == 'sys')
  * which of restart_scripts/reboot_system/shutdown_system was invoked
  * `write_log` messages (the timer branches log)

SAFETY: `real_hw` defaults to True in a fresh test datastore, so
`is_real_hardware()` is True and the action=='cmd' branch would really run
`sudo systemctl reboot` / `poweroff`. `_run_case` therefore replaces
restart_scripts/reboot_system/shutdown_system with recording mocks. Never run
these cases without those patches in place.

DETERMINISM: `time.time` is frozen (the timer branches stamp it into control)
and `time.sleep` is neutralized (get/hopper sleeps 3s). `data['ui_hash']` is
normalized to a sentinel -- it is `hash()` of a str, so PYTHONHASHSEED makes it
differ on every interpreter run (see NON-OBVIOUS BEHAVIORS #1).

MACHINE INDEPENDENCE -- READ THIS BEFORE ADDING A CASE: the `ds` fixture's
"fresh" datastore is NOT fresh. `datastore.init()` runs `_first_boot_import()`,
which seeds `settings:general` and `pellets:general` from the cwd-relative
`./settings.json` and `./pelletdb.json` -- both UNTRACKED and GITIGNORED
(.gitignore:12 and :36). Anything derived from `read_settings()` /
`read_pellet_db()` is therefore a property of the developer's machine and of
whatever other suites ran first, NOT of the code. An earlier version of this
file learned that the hard way: it froze this box's MAC-derived uuid into
get_uuid and a grill_name ('BOOT_PATH_SENTINEL_GRILL') that
tests/unit/bootstrap/test_startup_migration.py had left in the local
settings.json -- so the suite passed here and could not pass anywhere else.
`_run_case` and the `seeded` fixture therefore overwrite both blobs with a
canonical baseline built from tracked code defaults (see `_canonical_settings`).
Never build a case on raw `read_settings()`.

NON-OBVIOUS BEHAVIORS PINNED HERE (current behavior, deliberately NOT fixed --
characterization captures warts):
  1. get/status `ui_hash` is `hash(json.dumps(probe_info))`. Python salts str
     hashing per-process, so this value changes on every restart even when the
     probe map is identical. Pinned only as "an int is present".
  2. (FIXED in the latent-bug pass -- was `arglist=[]`, a mutable default
     argument; the pad-to-4 loop appended None INTO it, so
     `process_command.__defaults__[1]` became permanently
     `[None, None, None, None]` after the first no-arglist call.) The default
     is now `arglist=None`, with `if arglist is None: arglist = []` at the top
     of the function body, so the default itself is never mutated. See
     `test_mutable_default_arglist_is_padded_in_place`.
  3. The same pad mutates the CALLER's list in place, and `set/manual/*/toggle`
     additionally rewrites `arglist[2]` to 'true'/'false'. Callers see this.
  4. action=='sys' pushes the PADDED arglist, so the trailing Nones leak into
     the queue payload: `['restart'] -> ['restart', None, None, None]`.
  5. (FIXED in the latent-bug pass -- was a no-op if/else) set/lid_open
     unconditionally sets `lid_open_toggle = True` regardless of arglist[1];
     no argument can clear the flag.
  6. (FIXED in the latent-bug pass) set/notify/<label>/target with units == 'C'
     used to write `control['primary_setpoint']` instead of the notify object's
     target (an apparent copy/paste bug). It now writes `notify_data[i]['target']`
     on both paths -- as a float under 'C' (fractional targets), an int under 'F'.
     The `set_notify_target_c` golden was re-captured for this fix.
  7. set/manual's error branch still writes control when
     `control['manual']['change']` holds a stale value from a previous command,
     even though the request was rejected with result == 'ERROR'.
  8. set/pmode, set/duty_cycle and the notify/limit_* branch hard-code
     `WriteKind.MERGE`, ignoring the caller's `kind`. The timer start/pause/stop
     branches hard-code `origin='app'`, ignoring the caller's `origin`. See the
     `kind`/`origin` cases below.
  9. get/timer and set/timer locate the timer notify object with a bare
     `for index, notify_obj in enumerate(...)` + `break`, then use `index`
     outside the loop -- if no timer object existed, `index` would silently be
     the last index rather than erroring.
"""

import hashlib
import json
import os
from unittest import mock

import pytest

import common.api_commands as api_commands
import common.common as c
import common.datastore_accessors as dsa
import common.defaults as defaults
from common.common import WriteKind

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "process_command_golden.json")

# SHA-256 of the golden fixture. Pinned so the contract cannot be regenerated
# without an obvious, reviewable edit to this line. See module docstring.
GOLDEN_SHA256 = "20d1c03b3c0df4244099ac9e4c27e247e59d8240ff6042c32ca149270cd4523f"

# Frozen wall clock. The set/timer branches stamp time.time() into control.
FIXED_NOW = 1700000000.0

# Distinct from FIXED_NOW on purpose: settings are seeded with SEED_TIME, and
# any settings rewrite performed BY the command under test stamps FIXED_NOW.
# The difference is what makes "this branch rewrote settings" observable in
# settings_diff (lastupdated.time: [SEED_TIME, FIXED_NOW]) without depending on
# a real clock.
SEED_TIME = 1600000000

# --- The canonical, machine-independent baseline ---------------------------
# These values are arbitrary but FIXED. They exist so the golden is a function
# of tracked code alone. See _canonical_settings() for why that is not free.
#
# They are also deliberately DISTINCTIVE -- never 0, 100, "Grill", or any other
# value a plausible hardcoding bug might coincidentally equal. Mutation testing
# caught this: with build == 0, replacing `data['build'] = settings['versions']
# ['build']` with `data['build'] = 0` passed the whole suite, because the
# canonical value collided with the mutant's constant. A sentinel that can be
# guessed by accident is not a sentinel.
CANONICAL_UUID = "00000000-0000-0000-0000-00000000c0de"
CANONICAL_GRILL_NAME = "CharacterizationGrill"
CANONICAL_VERSIONS = {
    "server": "0.0.0-characterization",
    "cookfile": "9.9.9-cookfile",
    "recipe": "9.9.9-recipe",
    "build": 4242,
}
CANONICAL_HOPPER_LEVEL = 42


# ---------------------------------------------------------------------------
# BRANCH ENUMERATION
# ---------------------------------------------------------------------------
# Every top-level and sub-level branch process_command dispatches on. `case`
# is the id of the CASES entry covering it, or a note if uncovered.
#
# | action | arglist[0]  | sub-branch                          | case id                        |
# |--------|-------------|-------------------------------------|--------------------------------|
# | get    | temp        | found in P                          | get_temp_primary               |
# | get    | temp        | found in F                          | get_temp_food                  |
# | get    | temp        | found in AUX                        | get_temp_aux                   |
# | get    | temp        | not found -> ERROR                  | get_temp_unknown_probe         |
# | get    | current     | -                                   | get_current                    |
# | get    | mode        | -                                   | get_mode                       |
# | get    | uuid        | -                                   | get_uuid                       |
# | get    | versions    | -                                   | get_versions                   |
# | get    | hopper      | writes hopper_check, sleeps, reads  | get_hopper                     |
# | get    | timer       | -                                   | get_timer / get_timer_inverted |
# | get    | notify      | -                                   | get_notify                     |
# | get    | status      | -                                   | get_status / get_status_inverted|
# | get    | <unknown>   | else -> ERROR                       | get_unknown_arg                |
# | set    | psp         | float + units F                     | set_psp_f                      |
# | set    | psp         | float + units C                     | set_psp_c                      |
# | set    | psp         | not a float -> ERROR                | set_psp_not_a_number           |
# | set    | units       | 'C'                                 | set_units_c                    |
# | set    | units       | 'F'                                 | set_units_f                    |
# | set    | units       | invalid -> ERROR                    | set_units_invalid              |
# | set    | mode        | simple mode (startup)               | set_mode_startup               |
# | set    | mode        | simple mode (stop)                  | set_mode_stop                  |
# | set    | mode        | simple mode (manual)                | set_mode_manual                |
# | set    | mode        | prime + digits + next in [startup]  | set_mode_prime_next_startup    |
# | set    | mode        | prime + digits + next not in list   | set_mode_prime_next_default    |
# | set    | mode        | prime + non-digit -> ERROR          | set_mode_prime_not_digit       |
# | set    | mode        | prime + None amount -> ERROR        | set_mode_prime_no_amount       |
# | set    | mode        | prime + raises -> bare except       | set_mode_prime_exception       |
# | set    | mode        | hold + float + units F              | set_mode_hold_f                |
# | set    | mode        | hold + float + units C              | set_mode_hold_c                |
# | set    | mode        | hold + not a float -> ERROR         | set_mode_hold_not_a_number     |
# | set    | mode        | hold + None -> ERROR                | set_mode_hold_no_temp          |
# | set    | mode        | unknown mode -> ERROR               | set_mode_unknown               |
# | set    | pmode       | in range 0-9                        | set_pmode_valid                |
# | set    | pmode       | out of range -> ERROR               | set_pmode_out_of_range         |
# | set    | pmode       | non-digit -> ERROR                  | set_pmode_not_digit            |
# | set    | pmode       | None -> ERROR                       | set_pmode_none                 |
# | set    | splus       | 'true'                              | set_splus_true                 |
# | set    | splus       | else -> False                       | set_splus_false                |
# | set    | lid_open    | 'toggle'                            | set_lid_open_toggle            |
# | set    | lid_open    | else (also True -- see wart #5)     | set_lid_open_other             |
# | set    | notify      | req true / false                    | set_notify_req_true/_false     |
# | set    | notify      | shutdown / keep_warm / reignite     | set_notify_shutdown/_keep_warm/|
# |        |             |                                     | set_notify_reignite            |
# | set    | notify      | target + float + units F            | set_notify_target_f            |
# | set    | notify      | target + float + units C (#6 FIXED) | set_notify_target_c            |
# | set    | notify      | target not a float -> ERROR         | set_notify_target_not_a_number |
# | set    | notify      | target on Timer -> falls to ERROR   | set_notify_target_timer        |
# | set    | notify      | unknown field -> ERROR              | set_notify_unknown_field       |
# | set    | notify      | label not found -> ERROR            | set_notify_label_not_found     |
# | set    | notify      | label None -> ERROR                 | set_notify_no_label            |
# | set    | limit_high  | matches type probe_limit_high       | set_limit_high_req             |
# | set    | limit_low   | matches type probe_limit_low        | set_limit_low_req              |
# | set    | limit_high  | label exists, wrong type -> ERROR   | set_limit_high_type_mismatch   |
# | set    | pwm         | 'true' / else                       | set_pwm_true / set_pwm_false   |
# | set    | duty_cycle  | 0-100                               | set_duty_cycle_valid           |
# | set    | duty_cycle  | out of range -> ERROR               | set_duty_cycle_out_of_range    |
# | set    | duty_cycle  | not a float -> ERROR                | set_duty_cycle_not_a_number    |
# | set    | tuning_mode | 'true' / else                       | set_tuning_mode_true/_false    |
# | set    | timer       | start, fresh (paused == 0)          | set_timer_start                |
# | set    | timer       | start, no seconds -> +60 default    | set_timer_start_default_60     |
# | set    | timer       | start, resume from paused           | set_timer_start_resume         |
# | set    | timer       | pause, running                      | set_timer_pause_running        |
# | set    | timer       | pause, not started -> clears        | set_timer_pause_not_started    |
# | set    | timer       | stop                                | set_timer_stop                 |
# | set    | timer       | shutdown true / false               | set_timer_shutdown_true/_false |
# | set    | timer       | keep_warm true / false              | set_timer_keep_warm_true/_false|
# | set    | timer       | unknown -> ERROR                    | set_timer_unknown              |
# | set    | manual      | gate: not Manual mode -> ERROR      | set_manual_gate_denied         |
# | set    | manual      | gate: allow_manual_changes bypass   | set_manual_gate_allowed        |
# | set    | manual      | power true / false / toggle on/off  | set_manual_power_*             |
# | set    | manual      | igniter true / false / toggle on/off| set_manual_igniter_*           |
# | set    | manual      | fan true / false (+pwm=100) /toggle | set_manual_fan_*               |
# | set    | manual      | auger true / false / toggle on/off  | set_manual_auger_*             |
# | set    | manual      | pwm + float                         | set_manual_pwm                 |
# | set    | manual      | pwm + non-float -> ERROR            | set_manual_pwm_not_a_number    |
# | set    | manual      | unknown -> ERROR                    | set_manual_unknown             |
# | set    | manual      | unknown + stale change (wart #7)    | set_manual_unknown_stale_write |
# | set    | <unknown>   | else -> ERROR                       | set_unknown_arg                |
# | cmd    | restart     | -> restart_scripts()                | cmd_restart                    |
# | cmd    | reboot      | -> reboot_system()                  | cmd_reboot                     |
# | cmd    | shutdown    | -> shutdown_system()                | cmd_shutdown                   |
# | cmd    | <unknown>   | else -> ERROR                       | cmd_unknown                    |
# | sys    | (any)       | push padded arglist to queue_systemq| sys_push / sys_push_empty      |
# | <else> | -           | unknown action -> ERROR             | unknown_action                 |
# | None   | -           | action=None -> ERROR                | none_action                    |
#
# kind/origin coverage:
# | kind=OVERWRITE honored (set/splus)          | kind_overwrite_splus           |
# | kind=OVERWRITE ignored by set/pmode (#8)    | kind_overwrite_ignored_pmode   |
# | kind=OVERWRITE ignored by set/duty_cycle    | kind_overwrite_ignored_duty    |
# | kind=OVERWRITE ignored by set/notify (#8)   | kind_overwrite_ignored_notify  |
# | origin ignored by set/timer start (#8)      | set_timer_start (origin=api)   |
# | origin honored by set/timer shutdown        | set_timer_shutdown_true        |
#
# DELIBERATELY NOT COVERED:
#   * WriteKind.OVERWRITE on get/hopper -- get/hopper's write is incidental to
#     the read it performs; the OVERWRITE-vs-MERGE dispatch itself is already
#     pinned by kind_overwrite_splus and by tests/unit/common/test_common_blobs.py.
#   * An invalid `kind` (write_control raises TypeError) -- that is write_control's
#     contract, covered at its own level, not process_command's.
#   * The real restart_scripts/reboot_system/shutdown_system bodies -- they run
#     `sudo systemctl reboot`. process_command's contract is "calls this function";
#     the bodies are out of scope and must never execute under test.


def _case(cid, action, arglist, **kw):
    return dict(id=cid, action=action, arglist=arglist, **kw)


# --- Discriminating seeds for the response builders ------------------------
# `get/status` (17 fields) and `get/timer` (5) are the function's biggest
# response builders, and their default seeds are almost all 0 / False / '' --
# which makes their fields MUTUALLY INDISTINGUISHABLE. Under default seeds all
# of these mutations passed the entire suite:
#   * mode <-> display_mode swapped (both read "Stop")
#   * start_duration <- prime_duration (adjacent lines, both 0)
#   * p_mode / s_plus / lid_open_detected hardcoded
#   * get/timer shutdown <-> keep_warm swapped (both False)
# That is the same class of defect as the CANONICAL_VERSIONS build==0 collision:
# a value a plausible slip can produce by accident proves nothing. A refactor
# that moves these bodies into `_cmd_*` functions is exactly when such a slip
# happens.
#
# So every field gets a DISTINCT, non-default value:
#   * the 8 numeric fields get distinct small primes / distinctive ints, so any
#     swap between them shows up;
#   * CROSS-BLOB confusables get different values on each side. `mode`,
#     `s_plus`, `prime_amount` and `startup_timestamp` each exist in BOTH
#     control and status, and `units` in both settings.globals and status, so
#     reading the right key off the WRONG blob is otherwise invisible;
#   * the booleans are covered by a COMPLEMENTARY PAIR of cases (get_status /
#     get_status_inverted, get_timer / get_timer_inverted). One case alone
#     cannot catch both `hardcoded True` and `hardcoded False`, and a pair with
#     identical polarity cannot catch a swap. Inverting the pair catches all
#     three: s_plus/lid_open_detected differ within each case (kills swaps) and
#     flip between cases (kills hardcodes in both directions).
_STATUS_A_CONTROL = {
    "mode": "Hold",  # vs status['mode'] below -- kills the mode/display_mode swap
    "status": "CharacterizationStatus",
    "s_plus": True,  # vs status['s_plus'] False
    "prime_amount": 31,  # vs status['prime_amount'] 19
    "startup_timestamp": 37,  # vs status['startup_timestamp'] 29
}
_STATUS_A_STATUS = {
    "mode": "Startup",
    "s_plus": False,
    "units": "C",  # vs settings globals units 'F'
    "start_time": 1611,
    "start_duration": 11,
    "shutdown_duration": 13,
    "prime_duration": 17,
    "prime_amount": 19,
    "lid_open_detected": False,
    "lid_open_endtime": 23,
    "p_mode": 7,
    "startup_timestamp": 29,
    "outpins": {"auger": True, "fan": False, "igniter": True, "power": False},
}
_STATUS_B_CONTROL = {
    "mode": "Smoke",
    "status": "InvertedStatus",
    "s_plus": False,
    "prime_amount": 41,
    "startup_timestamp": 43,
}
_STATUS_B_STATUS = {
    "mode": "Monitor",
    "s_plus": True,
    "units": "F",  # vs settings globals units 'C' (via settings_patch)
    "start_time": 1811,
    "start_duration": 53,
    "shutdown_duration": 59,
    "prime_duration": 61,
    "prime_amount": 67,
    "lid_open_detected": True,
    "lid_open_endtime": 71,
    "p_mode": 3,
    "startup_timestamp": 73,
    "outpins": {"auger": False, "fan": True, "igniter": False, "power": True},
}

# Distinct per-probe temperatures, so a P/F/AUX source slip in get/temp or a
# reshuffle in get/current cannot hide behind a uniform 0.
_DISTINCT_CURRENT = {
    "P": {"Grill": 201},
    "F": {"Probe1": 145, "Probe2": 165, "Probe3": 175},
    "AUX": {"AuxProbe": 77},
    "PSP": 225,
    "NT": {"Grill": 203, "Probe1": 147, "Probe2": 167, "Probe3": 177},
}


def _timer_notify(shutdown, keep_warm):
    """Set shutdown/keep_warm on the timer notify object (a list element, which
    _deep_merge would replace wholesale rather than patch)."""

    def _apply(control):
        for obj in control["notify_data"]:
            if obj["type"] == "timer":
                obj["shutdown"] = shutdown
                obj["keep_warm"] = keep_warm

    return _apply


CASES = [
    # ---- GET ----------------------------------------------------------
    _case("get_temp_primary", "get", ["temp", "Grill"], current_patch=_DISTINCT_CURRENT),
    _case("get_temp_food", "get", ["temp", "Probe1"], current_patch=_DISTINCT_CURRENT),
    _case("get_temp_aux", "get", ["temp", "AuxProbe"], current_patch=_DISTINCT_CURRENT),
    _case("get_temp_unknown_probe", "get", ["temp", "NoSuchProbe"], current_patch=_DISTINCT_CURRENT),
    _case("get_current", "get", ["current"], current_patch=_DISTINCT_CURRENT),
    _case("get_mode", "get", ["mode"], control_patch={"mode": "Hold"}),
    _case("get_uuid", "get", ["uuid"]),
    _case("get_versions", "get", ["versions"]),
    _case("get_hopper", "get", ["hopper"]),
    # start/paused/end distinct and non-zero; shutdown != keep_warm kills the swap.
    _case(
        "get_timer",
        "get",
        ["timer"],
        control_patch={"timer": {"start": 111.0, "paused": 222.0, "end": 333.0}},
        control_fn=_timer_notify(shutdown=True, keep_warm=False),
    ),
    _case(
        "get_timer_inverted",
        "get",
        ["timer"],
        control_patch={"timer": {"start": 444.0, "paused": 555.0, "end": 666.0}},
        control_fn=_timer_notify(shutdown=False, keep_warm=True),
    ),
    _case("get_notify", "get", ["notify"]),
    _case("get_status", "get", ["status"], control_patch=_STATUS_A_CONTROL, status_patch=_STATUS_A_STATUS),
    _case(
        "get_status_inverted",
        "get",
        ["status"],
        control_patch=_STATUS_B_CONTROL,
        status_patch=_STATUS_B_STATUS,
        settings_patch={"globals": {"units": "C"}},
    ),
    _case("get_unknown_arg", "get", ["bogus"]),
    # ---- SET: psp -----------------------------------------------------
    _case("set_psp_f", "set", ["psp", "225.7"]),
    _case("set_psp_c", "set", ["psp", "107.5"], settings_patch={"globals": {"units": "C"}}),
    _case("set_psp_not_a_number", "set", ["psp", "hot"]),
    # ---- SET: units ---------------------------------------------------
    _case("set_units_c", "set", ["units", "C"]),
    _case("set_units_f", "set", ["units", "F"], settings_patch={"globals": {"units": "C"}}),
    _case("set_units_invalid", "set", ["units", "K"]),
    # ---- SET: mode ----------------------------------------------------
    _case("set_mode_startup", "set", ["mode", "startup"]),
    _case("set_mode_stop", "set", ["mode", "stop"]),
    _case("set_mode_manual", "set", ["mode", "manual"]),
    _case("set_mode_prime_next_startup", "set", ["mode", "prime", "50", "startup"]),
    _case("set_mode_prime_next_default", "set", ["mode", "prime", "50", "bogus"]),
    _case("set_mode_prime_not_digit", "set", ["mode", "prime", "lots"]),
    _case("set_mode_prime_no_amount", "set", ["mode", "prime"]),
    # arglist[2] is an int, not a str -> .isdigit() raises AttributeError ->
    # swallowed by the branch's bare `except:`.
    _case("set_mode_prime_exception", "set", ["mode", "prime", 50]),
    _case("set_mode_hold_f", "set", ["mode", "hold", "250.9"]),
    _case("set_mode_hold_c", "set", ["mode", "hold", "121.6"], settings_patch={"globals": {"units": "C"}}),
    _case("set_mode_hold_not_a_number", "set", ["mode", "hold", "warm"]),
    _case("set_mode_hold_no_temp", "set", ["mode", "hold"]),
    _case("set_mode_unknown", "set", ["mode", "teleport"]),
    # ---- SET: pmode ---------------------------------------------------
    _case("set_pmode_valid", "set", ["pmode", "5"]),
    _case("set_pmode_out_of_range", "set", ["pmode", "42"]),
    _case("set_pmode_not_digit", "set", ["pmode", "five"]),
    _case("set_pmode_none", "set", ["pmode"]),
    # ---- SET: splus / lid_open / pwm / tuning_mode ---------------------
    _case("set_splus_true", "set", ["splus", "true"]),
    _case("set_splus_false", "set", ["splus", "false"], control_patch={"s_plus": True}),
    _case("set_lid_open_toggle", "set", ["lid_open", "toggle"]),
    _case("set_lid_open_other", "set", ["lid_open", "false"]),
    _case("set_pwm_true", "set", ["pwm", "true"]),
    _case("set_pwm_false", "set", ["pwm", "false"], control_patch={"pwm_control": True}),
    _case("set_tuning_mode_true", "set", ["tuning_mode", "true"]),
    _case("set_tuning_mode_false", "set", ["tuning_mode", "false"], control_patch={"tuning_mode": True}),
    # ---- SET: notify / limit_high / limit_low --------------------------
    _case("set_notify_req_true", "set", ["notify", "Grill", "req", "true"]),
    _case("set_notify_req_false", "set", ["notify", "Grill", "req", "false"]),
    _case("set_notify_shutdown", "set", ["notify", "Grill", "shutdown", "true"]),
    _case("set_notify_keep_warm", "set", ["notify", "Grill", "keep_warm", "true"]),
    _case("set_notify_reignite", "set", ["notify", "Grill", "reignite", "true"]),
    _case("set_notify_target_f", "set", ["notify", "Grill", "target", "203.4"]),
    _case(
        "set_notify_target_c",
        "set",
        ["notify", "Grill", "target", "95.2"],
        settings_patch={"globals": {"units": "C"}},
    ),
    _case("set_notify_target_not_a_number", "set", ["notify", "Grill", "target", "hot"]),
    _case("set_notify_target_timer", "set", ["notify", "Timer", "target", "60"]),
    _case("set_notify_unknown_field", "set", ["notify", "Grill", "bogus", "true"]),
    _case("set_notify_label_not_found", "set", ["notify", "NoSuchLabel", "req", "true"]),
    _case("set_notify_no_label", "set", ["notify"]),
    _case("set_limit_high_req", "set", ["limit_high", "Grill", "req", "true"]),
    _case("set_limit_low_req", "set", ["limit_low", "Grill", "req", "true"]),
    # 'Timer' exists as a label but has type 'timer', never 'probe_limit_high'.
    _case("set_limit_high_type_mismatch", "set", ["limit_high", "Timer", "req", "true"]),
    # ---- SET: duty_cycle ----------------------------------------------
    _case("set_duty_cycle_valid", "set", ["duty_cycle", "60"]),
    _case("set_duty_cycle_out_of_range", "set", ["duty_cycle", "150"]),
    _case("set_duty_cycle_not_a_number", "set", ["duty_cycle", "fast"]),
    # ---- SET: timer ---------------------------------------------------
    _case("set_timer_start", "set", ["timer", "start", "300"], origin="api"),
    _case("set_timer_start_default_60", "set", ["timer", "start", "soon"]),
    _case(
        "set_timer_start_resume",
        "set",
        ["timer", "start", "300"],
        control_patch={"timer": {"start": 1000.0, "paused": 1500.0, "end": 2000.0}},
    ),
    _case(
        "set_timer_pause_running",
        "set",
        ["timer", "pause"],
        control_patch={"timer": {"start": 1000.0, "paused": 0, "end": 2000.0}},
    ),
    _case("set_timer_pause_not_started", "set", ["timer", "pause"]),
    _case(
        "set_timer_stop",
        "set",
        ["timer", "stop"],
        control_patch={"timer": {"start": 1000.0, "paused": 0, "end": 2000.0}},
    ),
    _case("set_timer_shutdown_true", "set", ["timer", "shutdown", "true"], origin="api"),
    _case("set_timer_shutdown_false", "set", ["timer", "shutdown", "false"]),
    _case("set_timer_keep_warm_true", "set", ["timer", "keep_warm", "true"]),
    _case("set_timer_keep_warm_false", "set", ["timer", "keep_warm", "false"]),
    _case("set_timer_unknown", "set", ["timer", "rewind"]),
    # ---- SET: manual --------------------------------------------------
    # The gate: control['mode'] == 'Manual' OR settings allow_manual_changes.
    _case("set_manual_gate_denied", "set", ["manual", "power", "true"], control_patch={"mode": "Stop"}),
    _case(
        "set_manual_gate_allowed",
        "set",
        ["manual", "power", "true"],
        control_patch={"mode": "Stop"},
        settings_patch={"safety": {"allow_manual_changes": True}},
    ),
]

# The four manual outputs a future refactor will collapse into a shared
# `_manual_toggle` helper. Each is generated identically here precisely so that collapse is provably
# behavior-preserving -- including the fan branch's extra `pwm = 100` reset on
# 'false', which is the one asymmetry between them.
_MANUAL_CONTROL = {"mode": "Manual"}
_OUTPUTS = ("power", "igniter", "fan", "auger")


def _pins(output, value):
    """Seed `output`'s pin to `value` and every OTHER pin to its opposite.

    Each toggle branch reads its own pin out of status['outpins'] -- power reads
    ['power'], fan reads ['fan'], and so on. Seeding only the target pin left
    the other three at their default False, so a branch reading the WRONG pin
    saw the same value and the slip was invisible. Making the others the
    opposite means any wrong-pin read inverts the resolved toggle and is caught.
    This is the same discriminating-seed principle as _STATUS_A/_B, applied to
    the four branches a future refactor collapses into one helper.
    """
    return {p: (value if p == output else not value) for p in _OUTPUTS}


for _output in _OUTPUTS:
    CASES.append(_case(f"set_manual_{_output}_true", "set", ["manual", _output, "true"], control_patch=_MANUAL_CONTROL))
    CASES.append(
        _case(f"set_manual_{_output}_false", "set", ["manual", _output, "false"], control_patch=_MANUAL_CONTROL)
    )
    # toggle reads the CURRENT pin state out of the status blob and inverts it,
    # rewriting arglist[2] in place.
    CASES.append(
        _case(
            f"set_manual_{_output}_toggle_from_off",
            "set",
            ["manual", _output, "toggle"],
            control_patch=_MANUAL_CONTROL,
            status_patch={"outpins": _pins(_output, False)},
        )
    )
    CASES.append(
        _case(
            f"set_manual_{_output}_toggle_from_on",
            "set",
            ["manual", _output, "toggle"],
            control_patch=_MANUAL_CONTROL,
            status_patch={"outpins": _pins(_output, True)},
        )
    )

CASES += [
    # The fan branch is the ONE asymmetry among the four manual outputs: on
    # 'false' it ALSO resets manual.pwm to 100. manual.pwm DEFAULTS to 100, so
    # with a default control that write is invisible -- seeding pwm=55 is what
    # makes it observable. Verified by mutation testing: without these two
    # cases, deleting the `control['manual']['pwm'] = 100` line passes the
    # whole suite. A future refactor collapses these four branches into one
    # helper; these cases are what stop that collapse from silently dropping
    # the asymmetry.
    _case(
        "set_manual_fan_false_resets_pwm",
        "set",
        ["manual", "fan", "false"],
        control_patch={"mode": "Manual", "manual": {"pwm": 55}},
    ),
    # Contrast case: the auger branch must NOT reset pwm.
    _case(
        "set_manual_auger_false_leaves_pwm",
        "set",
        ["manual", "auger", "false"],
        control_patch={"mode": "Manual", "manual": {"pwm": 55}},
    ),
    _case("set_manual_pwm", "set", ["manual", "pwm", "55"], control_patch=_MANUAL_CONTROL),
    _case("set_manual_pwm_not_a_number", "set", ["manual", "pwm", "fast"], control_patch=_MANUAL_CONTROL),
    _case("set_manual_unknown", "set", ["manual", "laser", "true"], control_patch=_MANUAL_CONTROL),
    # Wart #7: 'change' is left over from an earlier command, so the rejected
    # request STILL writes control.
    _case(
        "set_manual_unknown_stale_write",
        "set",
        ["manual", "laser", "true"],
        control_patch={"mode": "Manual", "manual": {"change": "fan"}},
    ),
    _case("set_unknown_arg", "set", ["bogus"]),
    # ---- CMD ----------------------------------------------------------
    _case("cmd_restart", "cmd", ["restart"]),
    _case("cmd_reboot", "cmd", ["reboot"]),
    _case("cmd_shutdown", "cmd", ["shutdown"]),
    _case("cmd_unknown", "cmd", ["selfdestruct"]),
    # ---- SYS ----------------------------------------------------------
    _case("sys_push", "sys", ["restart", "control"]),
    _case("sys_push_empty", "sys", []),
    # ---- Fallback / unknown action (must be preserved exactly by any refactor) ------
    _case("unknown_action", "bogus", ["whatever"]),
    _case("none_action", None, ["whatever"]),
    _case("empty_string_action", "", ["whatever"]),
    # ---- kind= parameter ----------------------------------------------
    _case("kind_overwrite_splus", "set", ["splus", "true"], kind=WriteKind.OVERWRITE),
    _case("kind_overwrite_ignored_pmode", "set", ["pmode", "5"], kind=WriteKind.OVERWRITE),
    _case("kind_overwrite_ignored_duty", "set", ["duty_cycle", "60"], kind=WriteKind.OVERWRITE),
    _case("kind_overwrite_ignored_notify", "set", ["notify", "Grill", "req", "true"], kind=WriteKind.OVERWRITE),
]


# ---------------------------------------------------------------------------
# Observation harness
# ---------------------------------------------------------------------------


def _deep_merge(base, patch):
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _flatten(obj, prefix=""):
    """Flatten a nested dict to dotted paths. Lists are leaves (json_patch
    replaces arrays atomically, so a list is a single value)."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}{k}."))
    else:
        out[prefix.rstrip(".")] = obj
    return out


def _diff(before, after):
    """Dotted-path diff of two nested dicts: {path: [before, after]}."""
    fb, fa = _flatten(before), _flatten(after)
    out = {}
    for p in sorted(set(fb) | set(fa)):
        b, a = fb.get(p, "<absent>"), fa.get(p, "<absent>")
        if b != a:
            out[p] = [b, a]
    return out


def _normalize(value):
    """Strip the one genuinely non-deterministic observable: ui_hash is
    hash() of a str, which Python salts per-process (PYTHONHASHSEED)."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k == "ui_hash":
                out[k] = f"<int:{type(v).__name__}>"
            else:
                out[k] = _normalize(v)
        return out
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _canonical_settings():
    """A settings baseline built ONLY from tracked code defaults.

    Deliberately NOT `read_settings()`. The `ds` fixture calls
    `datastore.init()`, which runs `_first_boot_import()`, which seeds
    `settings:general` from `c.read_settings_file()` -- the cwd-relative
    `./settings.json`. That file is untracked and GITIGNORED (.gitignore:12),
    so building the golden on it made the contract a function of the
    developer's machine rather than of the code:

      * `get/uuid` froze this box's MAC (generate_uuid() is uuid1(getnode())).
      * `get/status` froze grill_name 'BOOT_PATH_SENTINEL_GRILL', left in the
        local settings.json by tests/unit/bootstrap/test_startup_migration.py
        -- i.e. the capture was polluted by an unrelated suite.
      * `get/versions` froze 1.10.10/build 70, which any version bump breaks.

    All of those are red cases that have nothing to do with refactoring
    process_command, and the only obvious escape from them is regenerating the golden -- exactly
    the act this suite's locks exist to prevent. So the fix is at the source:
    seed a fixed baseline and never read the machine's file.

    `default_settings()` is tracked code, but is not by itself deterministic --
    it calls generate_uuid() (MAC-derived) and pulls versions from the updater
    manifest (bumps on release) -- so those are overridden here. Pinning them as
    real seeded values, rather than scrubbing them from the output, keeps the
    assertions real: get/uuid and get/versions still prove process_command
    copies settings->response correctly, they just no longer depend on the host.
    """
    settings = defaults.default_settings()
    settings["server_info"]["uuid"] = CANONICAL_UUID
    settings["versions"] = dict(CANONICAL_VERSIONS)
    settings["globals"]["grill_name"] = CANONICAL_GRILL_NAME
    settings["lastupdated"]["time"] = SEED_TIME
    return settings


def _canonical_pelletdb():
    """Same problem, same fix: `_first_boot_import()` seeds `pellets:general`
    from the cwd-relative `./pelletdb.json`, which is ALSO untracked and
    gitignored (.gitignore:36). get/hopper reads hopper_level straight out of
    it, so without this the golden froze whatever level this box happened to
    have. default_pellets() stamps datetime.now() into pelletid/date_loaded,
    but nothing observed here reads those.
    """
    pelletdb = defaults.default_pellets()
    pelletdb["current"]["hopper_level"] = CANONICAL_HOPPER_LEVEL
    return pelletdb


def _run_case(case):
    """Execute one case against a freshly seeded datastore and return its
    complete observable footprint as a JSON-able dict."""
    # --- seed -----------------------------------------------------------
    # Overwrite whatever _first_boot_import() imported from the machine's
    # ./settings.json and ./pelletdb.json with the canonical baseline. This
    # must happen FIRST: default_control() and init_status() both
    # derive from settings/pelletdb.
    settings = _canonical_settings()
    if case.get("settings_patch"):
        _deep_merge(settings, case["settings_patch"])
    # write_settings_store, not write_settings: the latter re-stamps
    # lastupdated.time from the real clock (seeding happens outside the frozen
    # -time block), which is what made an earlier golden drift between runs.
    dsa.write_settings_store(settings)
    c.datastore.set_blob("pellets:general", json.dumps(_canonical_pelletdb()))

    dsa.init_status()
    if case.get("status_patch"):
        status = dsa.read_status()
        _deep_merge(status, case["status_patch"])
        dsa.write_status(status)

    dsa.flush_current()
    if case.get("current_patch"):
        current = dsa.read_current()
        _deep_merge(current, case["current_patch"])
        # Set the blob directly: common.write_current() takes a different
        # (probe_history-shaped) input and stamps a wall-clock TS into the
        # result, which would make the golden non-deterministic. zero_out's
        # own write is a plain set_blob of exactly this shape.
        c.datastore.set_blob("control:current", json.dumps(current))

    control = dsa.read_control()
    if case.get("control_patch"):
        _deep_merge(control, case["control_patch"])
    if case.get("control_fn"):
        # For seeding inside notify_data, whose elements _deep_merge cannot
        # reach (lists are replaced wholesale, matching json_patch semantics).
        case["control_fn"](control)
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")

    c.SqliteQueue("queue_control_write").flush()
    c.SqliteQueue("queue_systemq").flush()

    pre_control = dsa.read_control()
    pre_settings = dsa.read_settings()

    # --- run (with every hazardous / non-deterministic edge neutralized) --
    log_calls = []
    with (
        mock.patch.object(api_commands, "restart_scripts") as m_restart,
        mock.patch.object(api_commands, "reboot_system") as m_reboot,
        mock.patch.object(api_commands, "shutdown_system") as m_shutdown,
        mock.patch.object(api_commands, "write_log", side_effect=lambda e, **kw: log_calls.append(e)),
        mock.patch.object(c.time, "time", return_value=FIXED_NOW),
        mock.patch.object(c.time, "sleep") as m_sleep,
    ):
        # A fresh list per case: process_command mutates its caller's arglist,
        # and CASES is module-level and reused across the golden + inline tests.
        arglist = list(case["arglist"])
        kwargs = {"action": case["action"], "arglist": arglist, "origin": case.get("origin", "test")}
        if "kind" in case:
            kwargs["kind"] = case["kind"]
        result = api_commands.process_command(**kwargs)

        cmd_calls = [
            name
            for name, m in (
                ("restart_scripts", m_restart),
                ("reboot_system", m_reboot),
                ("shutdown_system", m_shutdown),
            )
            if m.called
        ]
        sleeps = [call.args[0] for call in m_sleep.call_args_list]

    # --- observe --------------------------------------------------------
    queued = c.SqliteQueue("queue_control_write").list()
    queued_writes = [{"origin": q.get("origin", "<absent>"), "diff": _diff(pre_control, q)} for q in queued]

    systemq = c.SqliteQueue("queue_systemq").list()
    dsa.execute_control_writes()

    return {
        "return": _normalize(result),
        "arglist_after": _normalize(arglist),
        "queued_writes": queued_writes,
        "control_diff_after_execute": _diff(pre_control, dsa.read_control()),
        "settings_diff": _diff(pre_settings, dsa.read_settings()),
        "systemq": systemq,
        "cmd_calls": cmd_calls,
        "sleeps": sleeps,
        "log_calls": log_calls,
    }


def _load_golden():
    with open(FIXTURE) as fh:
        return json.load(fh)


@pytest.fixture
def seeded(ds):
    """`ds` plus the canonical baseline -- the inline tests' equivalent of what
    `_run_case` does for the golden cases.

    Without this the inline tests read the machine's ./settings.json (see
    _canonical_settings), so their probe labels, units and notify_data would
    depend on the developer's box and on whatever other suites left behind.
    """
    dsa.write_settings_store(_canonical_settings())
    c.datastore.set_blob("pellets:general", json.dumps(_canonical_pelletdb()))
    dsa.init_status()
    dsa.flush_current()
    return ds


# ---------------------------------------------------------------------------
# The golden test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c_["id"] for c_ in CASES])
def test_process_command_matches_golden(ds, case):
    golden = _load_golden()
    assert case["id"] in golden, (
        f"No golden entry for case {case['id']!r}. The golden fixture is the frozen "
        f"pre-refactor contract and is never regenerated by the test suite -- if you "
        f"added a case you must capture it deliberately (see module docstring)."
    )
    observed = _run_case(case)
    assert observed == golden[case["id"]], (
        f"process_command behavior changed for case {case['id']!r}.\n"
        f"This is a characterization test: the golden is the pre-refactor contract.\n"
        f"Fix the refactor, not the fixture."
    )


def test_golden_covers_exactly_the_enumerated_cases():
    """No case may be silently dropped from CASES and no golden entry orphaned."""
    golden = _load_golden()
    assert sorted(golden) == sorted(c_["id"] for c_ in CASES)


def test_case_ids_are_unique():
    ids = [c_["id"] for c_ in CASES]
    assert len(ids) == len(set(ids))


def test_golden_file_digest_is_pinned():
    """Tripwire against silent re-baselining.

    The golden fixture is the equivalence oracle for decomposing process_command
    into shared helpers. Pinning its digest here means the contract cannot be
    regenerated without also hand-editing GOLDEN_SHA256 in this file -- an edit
    a reviewer cannot miss. If this fails, someone rewrote the oracle.
    """
    with open(FIXTURE, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    assert digest == GOLDEN_SHA256, (
        "The process_command golden fixture changed. It is the frozen pre-refactor "
        "contract for Tasks 6-7 and must not be regenerated to make a refactor pass."
    )


# ---------------------------------------------------------------------------
# Explicit inline assertions for the load-bearing / easy-to-lose behaviors.
# These duplicate part of the golden on purpose: a JSON blob is easy to skim
# past, and these particular behaviors are the ones a refactor is most likely
# to "clean up" without noticing they are contract.
# ---------------------------------------------------------------------------


def test_unknown_action_fallback_is_exact(seeded):
    """A future refactor must preserve this fallback verbatim, message included."""
    assert api_commands.process_command(action="bogus", arglist=["x"], origin="test") == {
        "result": "ERROR",
        "message": "Action [bogus] not valid/recognized.",
        "data": {},
    }
    assert api_commands.process_command(action=None, arglist=["x"], origin="test") == {
        "result": "ERROR",
        "message": "Action [None] not valid/recognized.",
        "data": {},
    }


def test_unknown_subcommand_fallbacks_are_exact(seeded):
    assert api_commands.process_command(action="get", arglist=["nope"], origin="test")["message"] == (
        "Get API Argument: [nope] not recognized."
    )
    assert api_commands.process_command(action="set", arglist=["nope"], origin="test")["message"] == (
        "Set API Argument: nope not recognized."
    )
    assert api_commands.process_command(action="cmd", arglist=["nope"], origin="test")["message"] == (
        "CMD API Argument: nope not recognized."
    )


def test_mutable_default_arglist_is_padded_in_place(seeded):
    """`arglist=None` is the default now (no mutable default). Calling
    process_command with arglist omitted must NOT create or mutate a shared
    4-element default list -- the mutable-default wart is FIXED, see module
    docstring wart #2."""
    assert api_commands.process_command.__defaults__[1] is None
    api_commands.process_command(action="get", origin="test")
    assert api_commands.process_command.__defaults__[1] is None
    # Repeated omitted-arglist calls still must not create/mutate a shared default.
    api_commands.process_command(action="get", origin="test")
    assert api_commands.process_command.__defaults__[1] is None


def test_arglist_is_padded_in_the_callers_list(seeded):
    """process_command mutates the list its caller passed in."""
    arglist = ["temp", "Grill"]
    api_commands.process_command(action="get", arglist=arglist, origin="test")
    assert arglist == ["temp", "Grill", None, None]


def test_manual_toggle_rewrites_the_callers_arglist(seeded):
    """set/manual/<output>/toggle resolves 'toggle' against the live pin state
    and writes the resolved 'true'/'false' back into the caller's arglist."""
    status = dsa.read_status()
    status["outpins"]["fan"] = True
    dsa.write_status(status)
    control = dsa.read_control()
    control["mode"] = "Manual"
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")

    arglist = ["manual", "fan", "toggle"]
    api_commands.process_command(action="set", arglist=arglist, origin="test")
    assert arglist == ["manual", "fan", "false", None]  # fan was on -> resolved to off


def test_only_the_fan_branch_resets_manual_pwm_to_100(seeded):
    """The single asymmetry among the four manual outputs, stated explicitly.

    A future refactor collapses power/igniter/fan/auger into one `_manual_toggle`
    helper. Turning the FAN off also resets manual.pwm to 100; turning power/igniter/
    auger off does not. manual.pwm already defaults to 100, so this is only
    observable from a non-default seed -- which is exactly why deleting the
    reset survived an earlier version of this suite. Do not let the collapse
    drop it, and do not let it spread to the other three.
    """
    for output, expected_pwm in (("fan", 100), ("power", 55), ("igniter", 55), ("auger", 55)):
        control = dsa.read_control()
        control["mode"] = "Manual"
        control["manual"]["pwm"] = 55
        dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")

        api_commands.process_command(action="set", arglist=["manual", output, "false"], origin="test")
        dsa.execute_control_writes()
        assert dsa.read_control()["manual"]["pwm"] == expected_pwm, f"output={output}"

    # ...and only on 'false': turning the fan ON must leave pwm alone.
    control = dsa.read_control()
    control["mode"] = "Manual"
    control["manual"]["pwm"] = 55
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
    api_commands.process_command(action="set", arglist=["manual", "fan", "true"], origin="test")
    dsa.execute_control_writes()
    assert dsa.read_control()["manual"]["pwm"] == 55


def test_get_status_reads_each_field_from_the_right_blob(seeded):
    """`mode` comes from CONTROL, `display_mode` from STATUS -- and four keys
    exist in both blobs.

    get/status is the function's largest response builder (17 fields), and
    moving its body into a `_cmd_*` function during a refactor is exactly when
    a right-key/wrong-blob slip happens. `mode`, `s_plus`, `prime_amount` and
    `startup_timestamp` all exist in BOTH control and status, and `units` in
    both settings.globals and status, so every one of those is seeded to a
    different value on each side: reading the correct key off the wrong blob
    cannot produce the expected answer.
    """
    control = dsa.read_control()
    _deep_merge(control, _STATUS_A_CONTROL)
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
    status = dsa.read_status()
    _deep_merge(status, _STATUS_A_STATUS)
    dsa.write_status(status)

    data = api_commands.process_command(action="get", arglist=["status"], origin="test")["data"]

    assert data["mode"] == "Hold"  # control['mode'], NOT status['mode']
    assert data["display_mode"] == "Startup"  # status['mode'], NOT control['mode']
    assert data["s_plus"] is True  # control['s_plus'] (status['s_plus'] is False)
    assert data["prime_amount"] == 19  # status (control's is 31)
    assert data["startup_timestamp"] == 29  # status (control's is 37)
    assert data["units"] == "F"  # settings.globals (status['units'] is 'C')
    assert data["name"] == CANONICAL_GRILL_NAME
    assert data["status"] == "CharacterizationStatus"
    # The eight numerics are pairwise distinct, so any swap among them shows up.
    assert data["start_time"] == 1611
    assert data["start_duration"] == 11
    assert data["shutdown_duration"] == 13
    assert data["prime_duration"] == 17
    assert data["lid_open_endtime"] == 23
    assert data["p_mode"] == 7
    assert data["lid_open_detected"] is False
    assert data["outpins"] == {"auger": True, "fan": False, "igniter": True, "power": False}


def test_get_timer_does_not_swap_shutdown_and_keep_warm(seeded):
    """Both default to False, which made the swap invisible."""
    control = dsa.read_control()
    control["timer"] = {"start": 111.0, "paused": 222.0, "end": 333.0}
    _timer_notify(shutdown=True, keep_warm=False)(control)
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")

    data = api_commands.process_command(action="get", arglist=["timer"], origin="test")["data"]
    assert data == {"start": 111.0, "paused": 222.0, "end": 333.0, "shutdown": True, "keep_warm": False}


def test_sys_pushes_the_padded_arglist(seeded):
    """The pad-to-4 Nones leak into the systemq payload."""
    c.SqliteQueue("queue_systemq").flush()
    api_commands.process_command(action="sys", arglist=["restart", "control"], origin="test")
    assert c.SqliteQueue("queue_systemq").list() == [["restart", "control", None, None]]


def test_kind_overwrite_is_honored_by_splus_but_ignored_by_pmode(seeded):
    """Wart #8: some branches hard-code WriteKind.MERGE and ignore `kind`.
    A refactor must not "consistently" thread `kind` through -- that is a change."""
    c.SqliteQueue("queue_control_write").flush()
    api_commands.process_command(action="set", arglist=["splus", "true"], origin="test", kind=WriteKind.OVERWRITE)
    # OVERWRITE writes control:general directly; nothing is queued.
    assert c.SqliteQueue("queue_control_write").length() == 0
    assert dsa.read_control()["s_plus"] is True

    api_commands.process_command(action="set", arglist=["pmode", "5"], origin="test", kind=WriteKind.OVERWRITE)
    # pmode hard-codes MERGE, so it queues despite kind=OVERWRITE.
    assert c.SqliteQueue("queue_control_write").length() == 1


def test_timer_start_hardcodes_origin_app(seeded):
    """Wart #8: set/timer start/pause/stop ignore `origin` and record 'app'."""
    c.SqliteQueue("queue_control_write").flush()
    # write_log appends to ./logs/events.log relative to cwd; keep the test from
    # touching the working tree.
    with mock.patch.object(api_commands, "write_log"):
        api_commands.process_command(action="set", arglist=["timer", "start", "300"], origin="api")
    queued = c.SqliteQueue("queue_control_write").list()
    assert [q["origin"] for q in queued] == ["app"]

    c.SqliteQueue("queue_control_write").flush()
    api_commands.process_command(action="set", arglist=["timer", "shutdown", "true"], origin="api")
    queued = c.SqliteQueue("queue_control_write").list()
    assert [q["origin"] for q in queued] == ["api"]  # this one honors it


# ---------------------------------------------------------------------------
# /api/set/timer/start/{seconds}/{options} -- the 4-argument form.
#
# The 3-argument forms above are frozen contract (CASES + the golden fixture).
# This form is NEW, so it is asserted inline rather than added to CASES: the
# golden is the pre-refactor oracle and is not regenerated to accommodate new
# behavior (see the module docstring).
#
# Why it exists: the control process decides a timer has expired by comparing
# control.timer.end against its OWN time.time(). A client that computes that
# absolute end itself hands over a value from a different clock, and a browser
# running behind the Pi therefore arms an ALREADY-EXPIRED timer -- which, with
# 'shutdown' ticked, shuts the grill down mid-cook. So the client sends a
# DURATION and the server does the arithmetic. Both expiry flags ride along in
# the same write_control() call because they cannot be sent separately:
# json_patch (RFC 7396) replaces the notify_data ARRAY wholesale, so a later
# write undoes an earlier one's flags.
# ---------------------------------------------------------------------------


def _timer_entry(control):
    return next(obj for obj in control["notify_data"] if obj["type"] == "timer")


def _start_with_options(seconds, options, origin="api"):
    """Run the 4-argument start form with write_log neutralized (it appends to
    ./logs/events.log relative to cwd)."""
    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=FIXED_NOW):
        return api_commands.process_command(action="set", arglist=["timer", "start", seconds, options], origin=origin)


def test_timer_start_with_options_computes_end_from_the_server_clock(seeded):
    """The caller sends a DURATION; the end comes from the server's clock.

    FIXED_NOW is the frozen server clock here. Nothing the caller sent appears
    in control.timer.end except as an offset from it, which is the property
    that makes a skewed client clock unable to arm an expired timer.
    """
    c.SqliteQueue("queue_control_write").flush()
    result = _start_with_options("3600", "none")
    assert result["result"] == "OK"

    dsa.execute_control_writes()
    control = dsa.read_control()
    assert control["timer"]["start"] == FIXED_NOW
    assert control["timer"]["end"] == FIXED_NOW + 3600
    assert control["timer"]["paused"] == 0


def test_timer_start_with_options_sets_both_flags_in_the_same_write(seeded):
    """One write_control, carrying the countdown AND both expiry flags.

    Two writes is the bug this form closes: each web-process write queues the
    whole control dict read from a blob that does not reflect the queue, so the
    second carries a stale copy of everything the first changed. The drain now
    three-way merges notify_data (common/common.py::merge_notify_data), which
    protects the two expiry FLAGS; control['timer'] is a plain object whose
    keys json_patch still overwrites from the stale read, so the countdown
    itself is still only safe in a single write. Keeping this form single-write
    keeps both halves correct.
    """
    for shutdown, keep_warm, options in (
        (False, False, "none"),
        (True, False, "shutdown"),
        (False, True, "keep_warm"),
        (True, True, "shutdown,keep_warm"),
        (True, True, "keep_warm,shutdown"),  # order is not significant
    ):
        control = dsa.read_control()
        _timer_notify(shutdown=not shutdown, keep_warm=not keep_warm)(control)
        control["timer"] = {"start": 0, "paused": 0, "end": 0}
        dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
        c.SqliteQueue("queue_control_write").flush()

        assert _start_with_options("600", options)["result"] == "OK"

        queued = c.SqliteQueue("queue_control_write").list()
        assert len(queued) == 1, f"options={options}"
        entry = _timer_entry(queued[0])
        assert entry["shutdown"] is shutdown, f"options={options}"
        assert entry["keep_warm"] is keep_warm, f"options={options}"
        assert entry["req"] is True
        assert queued[0]["timer"]["end"] == FIXED_NOW + 600
        # The flags are not merely queued -- they survive the json_patch apply.
        dsa.execute_control_writes()
        applied = _timer_entry(dsa.read_control())
        assert (applied["shutdown"], applied["keep_warm"]) == (shutdown, keep_warm), f"options={options}"


def test_timer_start_with_options_leaves_the_other_notifications_alone(seeded):
    """notify_data goes back whole: json_patch replaces the array, so a partial
    one would delete every probe notification the user has armed."""
    control = dsa.read_control()
    grill = next(obj for obj in control["notify_data"] if obj["label"] == "Grill" and obj["type"] == "probe")
    grill["req"] = True
    grill["target"] = 203
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
    before = [dict(obj) for obj in control["notify_data"] if obj["type"] != "timer"]

    assert _start_with_options("600", "shutdown,keep_warm")["result"] == "OK"
    dsa.execute_control_writes()

    after = [dict(obj) for obj in dsa.read_control()["notify_data"] if obj["type"] != "timer"]
    assert after == before


def test_timer_start_with_options_rejects_a_paused_timer(seeded):
    """It does NOT unpause.

    The 3-argument `start` doubles as the resume command and ignores its
    seconds argument when paused. Doing that here would silently discard the
    duration the caller asked for -- the same class of "asked for X, got Y"
    that this form exists to prevent -- so a paused timer is refused outright
    and nothing is written.
    """
    control = dsa.read_control()
    control["timer"] = {"start": 1000.0, "paused": 1500.0, "end": 2000.0}
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    result = _start_with_options("600", "shutdown")
    assert result["result"] == "ERROR"
    assert "paused" in result["message"]
    assert c.SqliteQueue("queue_control_write").length() == 0
    assert dsa.read_control()["timer"] == {"start": 1000.0, "paused": 1500.0, "end": 2000.0}


def test_timer_start_with_options_rejects_bad_input_without_writing(seeded):
    """No silent substitution: the 3-argument form turns a non-numeric duration
    into 60 seconds, which is exactly how a user gets a timer they never asked
    for. This form refuses instead -- as it does for a zero/negative duration
    (already expired the moment it is armed) and for an unknown option name."""
    for seconds, options in (
        ("soon", "none"),  # not a number
        ("0", "none"),  # already expired
        ("0.5", "none"),  # truncates to 0
        ("-600", "none"),  # negative
        (None, "none"),  # missing
        ("600", "shutdown,laser"),  # unknown flag
        ("600", "Shutdown"),  # names are case-sensitive
        ("600", ""),  # empty segment
        ("600", "shutdown,shutdown"),  # repeated flag
        ("600", "none,shutdown"),  # 'none' is not a member name
    ):
        control = dsa.read_control()
        control["timer"] = {"start": 0, "paused": 0, "end": 0}
        dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
        c.SqliteQueue("queue_control_write").flush()

        result = _start_with_options(seconds, options)
        assert result["result"] == "ERROR", f"seconds={seconds!r} options={options!r}"
        assert c.SqliteQueue("queue_control_write").length() == 0, f"seconds={seconds!r} options={options!r}"
        dsa.execute_control_writes()
        assert dsa.read_control()["timer"]["end"] == 0, f"seconds={seconds!r} options={options!r}"


def test_timer_start_with_options_records_origin_app_like_the_other_start(seeded):
    """Wart #8 again: the start branch records 'app' whatever the caller says.
    The new form is the same branch and must not diverge."""
    c.SqliteQueue("queue_control_write").flush()
    _start_with_options("600", "shutdown", origin="api")
    assert [q["origin"] for q in c.SqliteQueue("queue_control_write").list()] == ["app"]


def test_three_argument_timer_start_still_resumes_and_ignores_seconds(seeded):
    """The pre-existing forms are untouched: no options segment means the old
    behavior, unpause semantics included."""
    control = dsa.read_control()
    control["timer"] = {"start": 1000.0, "paused": 1500.0, "end": 2000.0}
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")

    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=FIXED_NOW):
        result = api_commands.process_command(action="set", arglist=["timer", "start", "300"], origin="api")
    assert result["result"] == "OK"
    dsa.execute_control_writes()
    timer = dsa.read_control()["timer"]
    # Resume shifts the remaining 500s onto now; the 300 is ignored.
    assert timer["end"] == FIXED_NOW + 500
    assert timer["paused"] == 0


def test_three_argument_timer_start_still_defaults_to_sixty_seconds(seeded):
    """The silent 60s substitution is contract for the 3-argument form (the
    Flask dashboard and mobile still use it). Only the 4-argument form refuses."""
    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=FIXED_NOW):
        api_commands.process_command(action="set", arglist=["timer", "start", "soon"], origin="api")
    dsa.execute_control_writes()
    assert dsa.read_control()["timer"]["end"] == FIXED_NOW + 60


def test_standalone_shutdown_and_keep_warm_commands_still_work(seeded):
    """Other clients still set the flags one at a time; those paths stay.

    Note the `execute_control_writes()` inside the loop: each command is given a
    control cycle of its own. That is the only way these paths are safe -- see
    the two tests below.
    """
    for command, key in (("shutdown", "shutdown"), ("keep_warm", "keep_warm")):
        for arg, expected in (("true", True), ("false", False)):
            api_commands.process_command(action="set", arglist=["timer", command, arg], origin="api")
            dsa.execute_control_writes()
            assert _timer_entry(dsa.read_control())[key] is expected, f"{command}={arg}"


# ---------------------------------------------------------------------------
# The seam these tests pin: TWO control writes inside ONE cycle, and what the
# second does to the first.
#
# It falls out of the write model itself: write_control queues the WHOLE
# control dict, read_control() serves the persisted blob and never the queue,
# so every writer in a cycle sends a full copy built from the same stale read.
#
# HALF of this is now fixed at the seam. control["notify_data"] is three-way
# merged per entry during the drain (common/common.py::merge_notify_data,
# tests/characterization/test_control_writes_cross_writer.py), so a writer only
# imposes the notification fields it actually changed -- which is why the
# assertions below on the timer NOTIFY entry now show both writers surviving.
#
# The other half is UNCHANGED and still pinned: control["timer"] (and every
# other scalar/object field) is applied with json_patch, which overwrites each
# key the stale partial supplies. Extending the three-way merge to the whole
# control dict would fix that too, but it changes the semantics of every field
# every writer touches, so it is deliberately not done here.
#
# The constraint for clients therefore still holds: ONE control write per user
# operation, carrying every field that operation needs. That is why
# /api/set/timer/start/{seconds}/{options} exists, and why web-react's timer bar
# refuses a second write until the socket has republished the first
# (web-react/src/components/shell/TimerBar.tsx).
# ---------------------------------------------------------------------------


def test_a_flag_write_after_a_start_in_one_cycle_destroys_the_timer(seeded):
    """start + shutdown, undrained: the timer is GONE.

    The shutdown command reads the pre-start blob, so its partial carries
    timer.start/paused/end as ZEROS and is queued second -- and control['timer']
    is a JSON object whose three keys the partial all supplies, so json_patch
    overwrites every one of them. The user asked for a 10-minute timer that
    shuts the grill down and got a `shutdown` flag on no timer at all.

    The notify_data half of this is now fixed: the start's `req=True` used to be
    undone by the shutdown command's stale array, and survives the three-way
    merge. control['timer'] is untouched by that merge, so the countdown is
    still destroyed -- which is why the 4-argument form below remains the fix.
    """
    control = dsa.read_control()
    control["timer"] = {"start": 0, "paused": 0, "end": 0}
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=FIXED_NOW):
        api_commands.process_command(action="set", arglist=["timer", "start", "600"], origin="api")
        api_commands.process_command(action="set", arglist=["timer", "shutdown", "true"], origin="api")
    dsa.execute_control_writes()

    after = dsa.read_control()
    assert after["timer"] == {"start": 0, "paused": 0, "end": 0}
    assert _timer_entry(after)["shutdown"] is True  # the flag landed; the timer did not
    # ...but the start's own req is NO LONGER undone: the shutdown command did
    # not touch `req`, so its stale copy of it is not imposed by the merge.
    assert _timer_entry(after)["req"] is True

    # The 4-argument form is the fix: same intent, one write, both halves survive.
    c.SqliteQueue("queue_control_write").flush()
    _start_with_options("600", "shutdown")
    dsa.execute_control_writes()
    after = dsa.read_control()
    assert after["timer"]["end"] == FIXED_NOW + 600
    assert _timer_entry(after)["shutdown"] is True
    assert _timer_entry(after)["req"] is True


def test_a_pause_after_a_stop_in_one_cycle_resurrects_the_timer(seeded):
    """stop + pause, undrained: the stopped timer comes back.

    The pause command reads the pre-stop blob, sees start != 0, and therefore
    queues that blob's start/end -- so the stop's zeros on control['timer'] are
    undone and a timer the user stopped returns paused. Both buttons are on
    screen together in every timer UI, which is what makes this reachable
    rather than theoretical.

    It no longer comes back ARMED. The pause command also carried the pre-stop
    notify_data, which used to undo the stop's clearing of shutdown/keep_warm
    and shut the grill down when the resurrected timer expired. The pause did
    not touch those flags, so the three-way merge does not impose its stale
    copy of them.
    """
    control = dsa.read_control()
    control["timer"] = {"start": 1000.0, "paused": 0, "end": 2000.0}
    _timer_notify(shutdown=True, keep_warm=False)(control)
    _timer_entry(control)["req"] = True
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
    c.SqliteQueue("queue_control_write").flush()

    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=FIXED_NOW):
        api_commands.process_command(action="set", arglist=["timer", "stop"], origin="api")
        api_commands.process_command(action="set", arglist=["timer", "pause"], origin="api")
    dsa.execute_control_writes()

    after = dsa.read_control()
    assert after["timer"] == {"start": 1000.0, "paused": FIXED_NOW, "end": 2000.0}
    assert _timer_entry(after)["shutdown"] is False  # the stop's clearing survives

    # Drained between the two commands -- the same clicks, one cycle apart --
    # the pause reads the stopped blob and the timer stays stopped.
    control = dsa.read_control()
    control["timer"] = {"start": 1000.0, "paused": 0, "end": 2000.0}
    _timer_notify(shutdown=True, keep_warm=False)(control)
    dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
    with mock.patch.object(api_commands, "write_log"), mock.patch.object(c.time, "time", return_value=FIXED_NOW):
        api_commands.process_command(action="set", arglist=["timer", "stop"], origin="api")
        dsa.execute_control_writes()
        api_commands.process_command(action="set", arglist=["timer", "pause"], origin="api")
        dsa.execute_control_writes()

    after = dsa.read_control()
    assert after["timer"] == {"start": 0, "paused": 0, "end": 0}
    assert _timer_entry(after)["shutdown"] is False


def test_notify_target_in_celsius_writes_the_notify_target(seeded):
    """The 'C' path writes the notify object's target (kept a float, since
    Celsius targets can be fractional), leaving control['primary_setpoint']
    untouched. Formerly wart #6 (an apparent copy/paste bug); FIXED in the
    latent-bug pass."""
    settings = dsa.read_settings()
    settings["globals"]["units"] = "C"
    dsa.write_settings(settings)

    api_commands.process_command(action="set", arglist=["notify", "Grill", "target", "95.5"], origin="test")
    dsa.execute_control_writes()
    control = dsa.read_control()
    grill = next(o for o in control["notify_data"] if o["label"] == "Grill" and o["type"] == "probe")
    assert grill["target"] == 95.5
    assert control["primary_setpoint"] == 0  # unchanged  # target was NOT updated


def test_lid_open_always_sets_true_regardless_of_arg(seeded):
    """Wart #5 (if/else collapsed to an unconditional set): behavior is
    unchanged -- any arg still sets the flag True and none can clear it."""
    for arg in ("toggle", "false", "anything"):
        control = dsa.read_control()
        control["lid_open_toggle"] = False
        dsa.write_control(control, WriteKind.OVERWRITE, origin="seed")
        api_commands.process_command(action="set", arglist=["lid_open", arg], origin="test")
        dsa.execute_control_writes()
        assert dsa.read_control()["lid_open_toggle"] is True, f"arg={arg}"


def test_cmd_branch_never_executes_real_system_commands(seeded):
    """Guard-rail for this suite itself.

    `real_hw` defaults to True in a fresh test datastore, so is_real_hardware()
    is True and the un-mocked cmd branch would really shell out to
    `sudo systemctl reboot`. If this ever fails, the CASES cmd_* entries are
    executing real reboots -- stop and fix the patching in _run_case.
    """
    assert dsa.read_settings()["platform"]["real_hw"] is True, (
        "Assumption changed: real_hw is no longer True by default. The cmd_* cases "
        "rely on _run_case's mocks, not on this flag -- but verify before relaxing."
    )
    with (
        mock.patch.object(api_commands, "restart_scripts") as m_restart,
        mock.patch.object(api_commands, "reboot_system") as m_reboot,
        mock.patch.object(api_commands, "shutdown_system") as m_shutdown,
    ):
        api_commands.process_command(action="cmd", arglist=["restart"], origin="test")
        api_commands.process_command(action="cmd", arglist=["reboot"], origin="test")
        api_commands.process_command(action="cmd", arglist=["shutdown"], origin="test")
    assert m_restart.call_count == 1
    assert m_reboot.call_count == 1
    assert m_shutdown.call_count == 1
