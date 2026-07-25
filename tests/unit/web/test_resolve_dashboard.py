"""Unit coverage for `blueprints.settings.routes.resolve_dashboard`.

Why this file exists: the fallback it covers was non-deterministic in
production and no test could see it. `settings["dashboard"]["dashboards"]` is
built by iterating `os.listdir("./dashboard")` (common/defaults.py), which
returns entries in raw filesystem order. The old fallback was
`next(iter(dashboards))`, so *which dashboard a user landed on depended on the
filesystem* -- two identical installs could differ.

It stayed hidden because the only coverage was a live-server page-render
assertion, and every run of that test saw a single ordering. It surfaced only
when the same tree was checked out to a new path (a fresh jj workspace) whose
listdir returned `basic.json` first: the assertion flipped from Default to
Basic with no code change.

These tests exercise the ordering directly, which the live-server test cannot
do without mutating shared server state -- a first attempt at that leaked a
reversed dict into 28 other tests in the module.
"""

from blueprints.settings.routes import resolve_dashboard
from common.defaults import DEFAULT_DASHBOARD

# Insertion order is the point of these fixtures, so they are built explicitly.
DEFAULT_FIRST = {"Default": {"name": "Default"}, "Basic": {"name": "Basic"}}
BASIC_FIRST = {"Basic": {"name": "Basic"}, "Default": {"name": "Default"}}


def test_requested_wins_when_valid():
    assert resolve_dashboard("Basic", "Default", DEFAULT_FIRST) == "Basic"


def test_falls_back_to_current_when_requested_is_unknown():
    assert resolve_dashboard("", "Basic", DEFAULT_FIRST) == "Basic"
    assert resolve_dashboard("NoSuchDashboard", "Basic", DEFAULT_FIRST) == "Basic"


def test_falls_back_to_named_default_when_current_is_also_unknown():
    assert resolve_dashboard("", "NoSuchDashboard", DEFAULT_FIRST) == DEFAULT_DASHBOARD


def test_fallback_is_independent_of_dict_order():
    """The regression this whole module exists for.

    Under the old `next(iter(dashboards))` the second case returned "Basic".
    Both orderings must now resolve to the named default.
    """
    assert resolve_dashboard("", "NoSuchDashboard", DEFAULT_FIRST) == "Default"
    assert resolve_dashboard("", "NoSuchDashboard", BASIC_FIRST) == "Default"


def test_positional_fallback_only_when_the_named_default_is_absent():
    """If dashboard/default.json is missing or renamed there is no named
    default to reach for, so falling back positionally is the only option
    left -- but it must not happen while "Default" exists."""
    without_default = {"Basic": {"name": "Basic"}, "Custom": {"name": "Custom"}}
    assert resolve_dashboard("", "NoSuchDashboard", without_default) == "Basic"


def test_empty_dashboards_yields_empty_string_rather_than_raising():
    assert resolve_dashboard("", "", {}) == ""
