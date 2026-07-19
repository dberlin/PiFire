"""Snapshot test for STATUS_TRANSITIONS (controller/runtime/transitions.py):
the committed status-transition table, giving control["status"] -- the
controller's second state axis -- a single inspectable definition. Parallels
the ALLOWED_EXITS snapshot in test_request_transition.py. A change to
STATUS_TRANSITIONS must be reflected here (visible in review).
"""

from common.modes import StatusState
from controller.runtime import transitions as transitions_mod

_EXPECTED_TABLE = (
    {
        "from": "UNSET / any (not MONITOR, mode != Error)",
        "to": StatusState.ACTIVE,
        "trigger": "an update lands while operating",
    },
    {
        "from": "any",
        "to": StatusState.MONITOR,
        "trigger": "Monitor mode dispatched",
    },
    {
        "from": "ACTIVE / MONITOR",
        "to": StatusState.INACTIVE,
        "trigger": "Stop or Error cleanup",
    },
    {
        "from": "MONITOR",
        "to": StatusState.MONITOR,
        "trigger": "persists through an Error (enables should_keep_power_on)",
    },
)


def test_status_transitions_matches_committed_snapshot():
    assert transitions_mod.STATUS_TRANSITIONS == _EXPECTED_TABLE


def test_every_status_value_appears_as_a_transition_target():
    # ACTIVE / MONITOR / INACTIVE are each reachable; UNSET is the
    # never-operated default (default_control()), not a transition target.
    targets = {row["to"] for row in transitions_mod.STATUS_TRANSITIONS}
    assert targets == {StatusState.ACTIVE, StatusState.MONITOR, StatusState.INACTIVE}
