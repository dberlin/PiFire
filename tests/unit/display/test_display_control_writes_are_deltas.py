"""A display write must name what it changed.

A fixed- or flex-layout menu that queues a whole read_control() reverts anything
the web process did in the same control cycle -- see
tests/characterization/test_control_writes_cross_writer.py for the shape. The
displays run in their own process and are the writer most likely to be sitting
on a stale read (a menu is open for as long as a human takes to use it), so this
is the module group where a whole-dict write costs the most.

Rather than drive 43 menu handlers, this pins the property at the source: every
display call to `enqueue_control_delta` must hand over a `control_delta`
envelope. That is checked structurally (ast), so a new site added tomorrow is
caught whether or not anyone writes a test for its menu.
"""

import ast
import pathlib

import pytest

DISPLAY_WRITERS = [
    "display/_base_fixed.py",
    "display/ssd1306b.py",
    "display/_base_flex.py",
    "display/qtquick_flex.py",
]

ROOT = pathlib.Path(__file__).resolve().parents[3]


def _enqueue_control_delta_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "enqueue_control_delta":
            yield node


@pytest.mark.parametrize("relpath", DISPLAY_WRITERS)
def test_every_display_write_hands_over_a_delta_envelope(relpath):
    path = ROOT / relpath
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = list(_enqueue_control_delta_calls(tree))
    assert calls, f"{relpath} has no enqueue_control_delta calls -- did the test's assumption go stale?"

    offenders = []
    for call in calls:
        first = call.args[0] if call.args else None
        ok = isinstance(first, ast.Call) and isinstance(first.func, ast.Name) and first.func.id == "control_delta"
        if not ok:
            offenders.append(f"{relpath}:{call.lineno}")
    assert offenders == [], f"enqueue_control_delta calls not passing a control_delta() envelope: {offenders}"


@pytest.mark.parametrize("relpath", DISPLAY_WRITERS)
def test_no_display_delta_names_more_than_four_top_level_members(relpath):
    """A display gesture is one menu selection. A `set` with a long member list
    is the signature of a read-modify-write that was wrapped rather than
    replaced -- the four-member cases are the Prime menus (mode, prime_amount,
    next_mode, updated)."""
    tree = ast.parse((ROOT / relpath).read_text(encoding="utf-8"))
    wide = []
    for call in _enqueue_control_delta_calls(tree):
        first = call.args[0] if call.args else None
        if not (isinstance(first, ast.Call) and getattr(first.func, "id", None) == "control_delta"):
            continue
        for kw in first.keywords:
            if kw.arg == "set_values" and isinstance(kw.value, ast.Dict) and len(kw.value.keys) > 4:
                wide.append(f"{relpath}:{call.lineno} ({len(kw.value.keys)} members)")
    assert wide == [], f"suspiciously wide display deltas: {wide}"
