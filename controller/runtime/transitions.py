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

from dataclasses import dataclass
from typing import Callable, Optional

from common.common import WriteKind
from controller.runtime.logic.safety import evaluate_flameout, over_max_temp, SafetyVerdict

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
        # Natural (post-cycle) progressions carry NO display push and NO
        # notification -- only mode/setpoint/updated + the write. This is
        # faithful to the legacy next_mode(), which never touched display or
        # notify, and it is correct by design: display_commands pushes are for
        # transient OVERLAYS tied to specific events (safety trips push
        # ("text","ERROR")/("text","Re-Ignite"); terminal cleanup pushes
        # ("clear",None)). A normal mode change (Startup->Smoke, Smoke->Hold,
        # Reignite->last-state, ...) needs no overlay -- the display process
        # reflects it by polling the persisted control["mode"]/status. So the
        # `display`/`notify` params are intentionally ignored on this branch;
        # next_mode() (the only natural caller) never passes them.
        #
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


# ==========================================================================
# Phase 2 -- declarative phased guard-engine.
#
# The transition GUARDS live in data: {mode: {phase: [Edge, ...]}}. An engine
# (evaluate_phase) walks a mode's edges for a pipeline phase in priority order
# and fires the first whose guard predicate is True, routing it through
# request_transition. Guards MUST keep evaluating at their EXISTING pipeline
# phases:
#   - "pre_loop": the setup_safety point (after the first probe read, before the
#     work loop). Flameout here reads control['safety']['afterstarttemp'].
#   - "pre_act":  the in-loop SAFETY section, BEFORE any actuation (on_tick).
#     Max-temp + flameout here read the fresh in-loop `ptemp`.
# Moving a guard relative to actuation would change whether the auger/fan cycle
# on the trip tick (observable), so the wiring (Task 14) inserts evaluate_phase
# AT those two points only.
#
# GUARD PREDICATE SIGNATURE (deviation from the plan's (ctx, control, ptemp, now)):
# predicates take `mode_obj` as well, because over_max_temp needs the mode's
# settings (mode_obj.settings) and the flameout wrap needs the mode name. The
# pre_loop/pre_act flameout split into *_setup (afterstarttemp) and *_inloop
# (ptemp) variants is required by live code -- setup_safety and check_safety read
# DIFFERENT temperatures (verified in smoke.py/hold.py; inventory rows 4-11).
# ==========================================================================


@dataclass(frozen=True)
class Edge:
    guard: Callable  # (mode_obj, ctx, control, ptemp, now) -> bool
    to: str
    kind: str
    notify: Optional[str] = None
    display: Optional[tuple] = None
    reignite_from_self: bool = False


# GUARDS ({mode: {phase: [Edge, ...]}}; "*" applies to every mode) is defined
# below, after the guard predicates it references.


# ---- guard predicates (pure; no writes) ----


def flameout_error_setup(mode_obj, ctx, control, ptemp, now):
    # pre_loop flameout: reads the carried-over afterstarttemp (NOT ptemp).
    safety = control["safety"]
    return (
        evaluate_flameout(safety["afterstarttemp"], safety["startuptemp"], safety["reigniteretries"])
        is SafetyVerdict.ERROR
    )


def flameout_reignite_setup(mode_obj, ctx, control, ptemp, now):
    safety = control["safety"]
    return (
        evaluate_flameout(safety["afterstarttemp"], safety["startuptemp"], safety["reigniteretries"])
        is SafetyVerdict.REIGNITE
    )


def flameout_error_inloop(mode_obj, ctx, control, ptemp, now):
    # pre_act flameout: reads the fresh in-loop ptemp.
    safety = control["safety"]
    return evaluate_flameout(ptemp, safety["startuptemp"], safety["reigniteretries"]) is SafetyVerdict.ERROR


def flameout_reignite_inloop(mode_obj, ctx, control, ptemp, now):
    safety = control["safety"]
    return evaluate_flameout(ptemp, safety["startuptemp"], safety["reigniteretries"]) is SafetyVerdict.REIGNITE


def over_max_temp_guard(mode_obj, ctx, control, ptemp, now):
    # pre_act universal max-temp; needs the mode's safety settings.
    return over_max_temp(ptemp, mode_obj.settings["safety"])


def _flameout_edges(*, setup):
    """The Error/Reignite flameout edge pair for one phase. `setup` selects the
    pre_loop variant (reads afterstarttemp) vs the pre_act variant (reads ptemp);
    error is listed before reignite (matching the live if/elif order). Reignite
    decrements retries + records reignitelaststate=mode.name via
    reignite_from_self."""
    err_guard = flameout_error_setup if setup else flameout_error_inloop
    reig_guard = flameout_reignite_setup if setup else flameout_reignite_inloop
    return [
        Edge(err_guard, "Error", "safety", notify="Grill_Error_02", display=("text", "ERROR")),
        Edge(
            reig_guard,
            "Reignite",
            "safety",
            reignite_from_self=True,
            notify="Grill_Error_03",
            display=("text", "Re-Ignite"),
        ),
    ]


# {mode: {phase: [Edge, ...]}}; the "*" mode applies to every mode at that phase
# and is walked FIRST (see evaluate_phase), so the universal max-temp trip keeps
# priority over the mode-specific flameout -- matching the live pre_act order
# (base.py max-temp before check_safety).
#
# NOTE: the inner-loop switch-off -> Stop edge is deliberately NOT migrated here.
# It lives EARLIER in base.run (before the manual-override actuation block, not
# at the pre_act safety point) and is stateful (edge-detection on the previous
# switch reading), so a pure pre_act guard would both move it relative to
# actuation and drop the edge-detection. It stays as the inline seam call in
# base.run (like the bespoke recipe/startup writes left direct in Phase 1).
GUARDS: dict[str, dict[str, list]] = {
    "*": {
        "pre_act": [
            Edge(over_max_temp_guard, "Error", "safety", notify="Grill_Error_01", display=("text", "ERROR")),
        ],
    },
    "Smoke": {
        "pre_loop": _flameout_edges(setup=True),
        "pre_act": _flameout_edges(setup=False),
    },
    "Hold": {
        "pre_loop": _flameout_edges(setup=True),
        "pre_act": _flameout_edges(setup=False),
    },
}


def evaluate_phase(mode_obj, ctx, phase, now, ptemp) -> bool:
    """Walk the phase's edges in priority order; fire the first whose guard is
    True via request_transition and return True. No match -> return False (no
    write).

    PRIORITY: universal "*" edges are walked BEFORE the mode-specific edges. This
    preserves the live pre_act ordering, where the UNIVERSAL max-temp trip
    (base.py:511) is evaluated before the mode's check_safety flameout
    (base.py:520) -- so on a (pathological) tick that satisfies both, max-temp
    (Error/Grill_Error_01) still wins. Universal safety takes precedence."""
    control = mode_obj.control
    edges = GUARDS.get("*", {}).get(phase, []) + GUARDS.get(mode_obj.name, {}).get(phase, [])
    for edge in edges:
        if edge.guard(mode_obj, ctx, control, ptemp, now):
            request_transition(
                ctx,
                control,
                edge.to,
                kind=edge.kind,
                notify=edge.notify,
                display=edge.display,
                reignite_from=mode_obj.name if edge.reignite_from_self else None,
            )
            return True
    return False
