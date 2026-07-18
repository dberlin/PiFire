"""Single seam for every controller mode transition. All mode changes route
through request_transition; transition *kind* sets priority. Designed for
clarity -- correctness is defined by the transition characterization suite +
the mode/loop goldens, not by mirroring the old next_mode/inline-write split.

kind semantics:
  - "natural": the post-cycle progression. Flushes deferred writes, re-reads the
    latest control to see whether a higher-priority transition already landed
    this cycle; if so (control already 'updated') it YIELDS (no-op) so the safety
    trip survives. Otherwise it sets mode, resolves
    primary_setpoint = (setpoint if to_mode=="Hold" else 0), sets updated=True,
    and writes. Returns the fresh control.
  - "safety" / "terminal": AUTHORITATIVE. Always applies, in place on `control`:
    push display (if given), set mode, if reignite_from: reigniteretries -= 1 and
    reignitelaststate = reignite_from, updated=True, write, send notify (if
    given). Authoritative kinds never touch primary_setpoint.

Raises TransitionError if to_mode is not in the source mode's ALLOWED_EXITS.
ALLOWED_EXITS is filled in Task 10; while it is empty the legality check is a
no-op passthrough.
"""

from common.common import WriteKind

_UNSET = object()

# The explicit mode-transition graph: every legal `from -> {to, ...}` edge the
# seam may perform. Derived from the transition inventory + the characterization
# suite. `to` targets for the cycling modes are data-driven (control['next_mode']),
# so a mode's set is the UNION of its universal safety/switch-off targets
# (Error/Reignite/Stop) and every mode it can legally advance into.
#
# Terminal pseudo-states Stop and Error are intentionally OMITTED (not listed):
# they never initiate a seam transition, and a post-trip `natural` next_mode call
# momentarily reads mode=="Error"/"Stop" before yielding -- leaving them unlisted
# makes _check_legal a no-op for that spurious source so the yield is unaffected.
ALLOWED_EXITS: dict[str, set[str]] = {
    "Prime": {"Startup", "Stop", "Error"},
    "Startup": {"Prime", "Smoke", "Hold", "Monitor", "Stop", "Error", "Reignite"},
    "Smoke": {"Hold", "Monitor", "Shutdown", "Stop", "Error", "Reignite"},
    "Hold": {"Smoke", "Monitor", "Shutdown", "Stop", "Error", "Reignite"},
    "Reignite": {"Smoke", "Hold", "Startup", "Stop", "Error"},
    "Shutdown": {"Stop", "Error"},
    "Monitor": {"Stop", "Error"},
    "Manual": {"Stop", "Error"},
    "Recipe": {"Recipe", "Smoke", "Hold", "Stop", "Error", "Reignite"},
}


class TransitionError(RuntimeError):
    pass


def _check_legal(from_mode, to_mode):
    exits = ALLOWED_EXITS.get(from_mode)
    if exits is not None and to_mode not in exits:
        raise TransitionError(f"illegal transition {from_mode} -> {to_mode}")


def request_transition(ctx, control, to_mode, *, kind, setpoint=_UNSET, reignite_from=None, notify=None, display=None):
    store = ctx.store
    _check_legal(control.get("mode"), to_mode)

    if kind == "natural":
        # Yield to any higher-priority transition already requested this cycle.
        store.execute_control_writes()
        control = store.read_control()
        if control["updated"]:
            return control
        control["mode"] = to_mode
        if setpoint is not _UNSET:
            control["primary_setpoint"] = setpoint if to_mode == "Hold" else 0
        control["updated"] = True
        store.write_control(control, WriteKind.OVERWRITE, origin="control")
        return control

    # authoritative: safety / terminal
    if display is not None:
        store.display_commands().push(display)
    control["mode"] = to_mode
    if reignite_from is not None:
        control["safety"]["reigniteretries"] -= 1
        control["safety"]["reignitelaststate"] = reignite_from
    control["updated"] = True
    store.write_control(control, WriteKind.OVERWRITE, origin="control")
    if notify is not None:
        ctx.notifications.send(notify)
    return control
