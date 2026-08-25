import pytest

from common.persistence.runtime import CONTROL_HEARTBEAT_STALE_AFTER

from display.qtbackend import PiFireBackend, ProbeHealthModel, project_thermocouple_health

PROBE_INFO = {"primary": {"name": "Grill", "max_temp": 600}, "food": [{"name": "Probe 1", "max_temp": 300}], "aux": []}


def make_backend(in_data, status_data):
    fetched = {"in": in_data, "st": status_data}

    def fetch_fn():
        return fetched["in"], fetched["st"]

    calls = []

    def command_fn(cmd, data):
        calls.append((cmd, data))

    b = PiFireBackend(fetch_fn, command_fn, PROBE_INFO)
    b._calls = calls
    return b


def test_poll_updates_primary_and_mode():
    in_data = {"P": {"Grill": 225}, "F": {"Probe 1": 145}, "AUX": {}, "PSP": 250, "NT": {"Grill": 0, "Probe 1": 0}}
    status = {
        "mode": "Hold",
        "units": "F",
        "outpins": {"fan": True, "auger": False, "igniter": False, "pwm": 0},
        "p_mode": 2,
        "s_plus": True,
        "hopper_level": 80,
        "hopper_level_enabled": True,
        "recipe": False,
        "recipe_paused": False,
        "lid_open_detected": False,
    }
    b = make_backend(in_data, status)
    b.poll()
    assert b.mode == "Hold"
    assert b.primaryTemp == 225
    assert b.primarySetpoint == 250
    assert b.primaryName == "Grill"
    assert b.units == "F"
    assert b.hopperLevel == 80
    assert b.smokePlus is True
    assert b.fanOn is True
    assert b.pMode == 2


def test_action_slots_dispatch_expected_commands():
    b = make_backend({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}})
    b.startup()
    b.stop()
    b.setHold(275)
    b.setPMode(4)
    b.primeStartup(25)
    b.toggleSmokePlus()
    assert ("cmd_startup", 0) in b._calls
    assert ("cmd_stop", 0) in b._calls
    assert ("cmd_hold", 275) in b._calls
    assert ("cmd_pmode", 4) in b._calls
    assert ("cmd_primestartup", 25) in b._calls
    assert ("cmd_splus", 0) in b._calls


def test_timer_text_counts_down_in_startup():
    status = {"mode": "Startup", "units": "F", "outpins": {}, "start_time": 1000.0, "start_duration": 240}
    in_data = {"P": {"Grill": 100}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}
    b = make_backend(in_data, status)
    b._now = lambda: 1000.0 + 45  # 45s elapsed -> 195s -> 03:15
    b.poll()
    assert b.timerText == "03:15"


def test_food_probe_model_reflects_current_data():
    info = {
        "primary": {"name": "Grill", "max_temp": 600},
        "food": [{"name": "Probe 1", "max_temp": 300}, {"name": "Probe 2", "max_temp": 300}],
        "aux": [],
    }

    def fetch_fn():
        return (
            {
                "P": {"Grill": 200},
                "F": {"Probe 1": 140, "Probe 2": 0},
                "AUX": {},
                "PSP": 225,
                "NT": {"Probe 1": 165, "Probe 2": 0},
            },
            {"mode": "Hold", "units": "F", "outpins": {}},
        )

    b = PiFireBackend(fetch_fn, lambda c, d: None, info)
    b.poll()
    model = b.foodProbes
    assert model.rowCount() == 2
    idx = model.index(0, 0)
    role = {v: k for k, v in model.roleNames().items()}
    assert model.data(idx, role[b"name"]) == "Probe 1"
    assert model.data(idx, role[b"temp"]) == 140
    assert model.data(idx, role[b"target"]) == 165


def test_data_keyed_by_label_display_by_name():
    # Live data keyed by probe label; display/notify use probe name.
    info = {"primary": {"name": "Grill", "label": "P0"}, "food": [{"name": "Brisket", "label": "F0"}], "aux": []}

    def fetch_fn():
        return (
            {"P": {"P0": 210}, "F": {"F0": 155}, "AUX": {}, "PSP": 225, "NT": {"P0": 235, "F0": 190}},
            {"mode": "Hold", "units": "F", "outpins": {}},
        )

    b = PiFireBackend(fetch_fn, lambda c, d: None, info)
    b.poll()
    assert b.primaryTemp == 210
    assert b.primaryNotifyTarget == 235
    assert b.primaryName == "Grill"
    model = b.foodProbes
    role = {v: k for k, v in model.roleNames().items()}
    idx = model.index(0, 0)
    assert model.data(idx, role[b"name"]) == "Brisket"  # display name
    assert model.data(idx, role[b"temp"]) == 155  # looked up by label F0
    assert model.data(idx, role[b"target"]) == 190


def test_notify_origin_dispatch_uses_name():
    b = make_backend({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Hold", "units": "F", "outpins": {}})
    b.setNotify("Brisket", 203)
    assert ("cmd_notify", {"origin": "Brisket", "target": 203}) in b._calls


def test_mode_text_shows_recipe_label():
    b = make_backend(
        {"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Hold", "units": "F", "outpins": {}, "recipe": True}
    )
    b.poll()
    assert b.modeText == "Recipe: Hold"
    # Recipe label suppressed in Shutdown.
    b._fetch_fn = lambda: (
        {"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}},
        {"mode": "Shutdown", "units": "F", "outpins": {}, "recipe": True},
    )
    b.poll()
    assert b.modeText == "Shutdown"


def test_pmode_active_only_in_startup_smoke():
    b = make_backend({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Smoke", "units": "F", "outpins": {}})
    b.poll()
    assert b.pModeActive is True
    b._fetch_fn = lambda: (
        {"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}},
        {"mode": "Hold", "units": "F", "outpins": {}},
    )
    b.poll()
    assert b.pModeActive is False


def test_hold_lid_open_countdown_timer():
    status = {"mode": "Hold", "units": "F", "outpins": {}, "lid_open_detected": True, "lid_open_endtime": 2000.0}
    b = make_backend({"P": {"Grill": 225}, "F": {}, "AUX": {}, "PSP": 250, "NT": {}}, status)
    b._now = lambda: 2000.0 - 65  # 65s remaining -> 01:05
    b.poll()
    assert b.timerText == "01:05"
    assert b.timerLabel == "Lid Pause"


def test_sleep_wake_state_machine():
    clock = {"t": 1000.0}
    status = {"st": {"mode": "Stop", "units": "F", "outpins": {}}}
    b = PiFireBackend(
        lambda: ({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, status["st"]),
        lambda c, d: None,
        {"primary": {"name": "Grill"}, "food": [], "aux": []},
    )
    b.TIMEOUT = 10
    b._now = lambda: clock["t"]
    b._last_interaction = clock["t"]
    # In Stop, before timeout: awake.
    b.poll()
    assert b.asleep is False
    # After 11s idle in Stop: asleep.
    clock["t"] = 1011.0
    b.poll()
    assert b.asleep is True
    # Interaction wakes it.
    b.registerInteraction()
    assert b.asleep is False
    # Leaving Stop (cook starts) keeps it awake even past the timeout.
    clock["t"] = 1100.0
    status["st"] = {"mode": "Hold", "units": "F", "outpins": {}}
    b.poll()
    assert b.asleep is False


def test_nav_slots_emit_navevent():
    b = make_backend({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}})
    events = []
    b.navEvent.connect(lambda e: events.append(e))
    b.navUp()
    b.navDown()
    b.navEnter()
    assert events == ["UP", "DOWN", "ENTER"]


def test_poll_exposes_duty_cycles():
    in_data = {"P": {"Grill": 225}, "F": {}, "AUX": {}, "PSP": 250, "NT": {}}
    status = {"mode": "Hold", "units": "F", "outpins": {"fan": True}, "cycle_ratio": 0.35, "fan_duty": 100}
    b = make_backend(in_data, status)
    b.poll()
    assert b.augerDuty == 35
    assert b.fanDuty == 100


def test_food_probe_count_reflects_config():
    # PROBE_INFO has one food probe.
    b = make_backend({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}})
    assert b.foodProbeCount == 1
    none = PiFireBackend(lambda: (None, None), lambda c, d: None, {"primary": {"name": "Grill"}, "food": [], "aux": []})
    assert none.foodProbeCount == 0


def test_accent_theme_updates_live_and_throttles():
    state = {"accent": "Ember"}
    b = PiFireBackend(
        lambda: ({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}}),
        lambda c, d: None,
        PROBE_INFO,
        accent_fn=lambda: state["accent"],
    )
    clock = {"t": 1000.0}
    b._now = lambda: clock["t"]
    events = []
    b.accentThemeChanged.connect(lambda: events.append(b.accentTheme))
    b.poll()
    assert b.accentTheme == "Ember"
    state["accent"] = "Ice"
    clock["t"] = 1000.5
    b.poll()
    assert b.accentTheme == "Ember"
    clock["t"] = 1002.0
    b.poll()
    assert b.accentTheme == "Ice"
    assert "Ice" in events


def test_cook_elapsed_text_counts_up_else_zero():
    status = {"mode": "Smoke", "units": "F", "outpins": {}, "startup_timestamp": 1000.0}
    b = make_backend({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, status)
    b._now = lambda: 1000.0 + 125  # 2:05 elapsed
    b.poll()
    assert b.cookElapsedText == "02:05"
    b._fetch_fn = lambda: (
        {"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}},
        {"mode": "Stop", "units": "F", "outpins": {}, "startup_timestamp": 0},
    )
    b.poll()
    assert b.cookElapsedText == "00:00"


def test_timeout_seeded_from_timeout_fn():
    b = PiFireBackend(
        lambda: ({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}}),
        lambda c, d: None,
        {"primary": {"name": "Grill"}, "food": [], "aux": []},
        timeout_fn=lambda: 42,
    )
    assert b.TIMEOUT == 42


def test_zero_timeout_never_sleeps():
    clock = {"t": 1000.0}
    b = PiFireBackend(
        lambda: ({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}}),
        lambda c, d: None,
        {"primary": {"name": "Grill"}, "food": [], "aux": []},
        timeout_fn=lambda: 0,
    )
    b._now = lambda: clock["t"]
    b._last_interaction = clock["t"]
    clock["t"] = 999999.0
    b.poll()
    assert b.asleep is False


def test_zero_timeout_wakes_already_asleep_screen():
    clock = {"t": 1000.0}
    state = {"timeout": 30}
    b = PiFireBackend(
        lambda: ({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}}),
        lambda c, d: None,
        {"primary": {"name": "Grill"}, "food": [], "aux": []},
        timeout_fn=lambda: state["timeout"],
    )
    b._now = lambda: clock["t"]
    b._last_interaction = clock["t"]
    clock["t"] = 1031.0  # >30s since last interaction -> asleep
    b.poll()
    assert b.asleep is True

    state["timeout"] = 0
    clock["t"] = 1032.5  # >1s since last settings check -> re-read TIMEOUT
    b.poll()
    assert b.asleep is False


def test_timeout_live_reread():
    clock = {"t": 1000.0}
    state = {"timeout": 30}
    b = PiFireBackend(
        lambda: ({"P": {}, "F": {}, "AUX": {}, "PSP": 0, "NT": {}}, {"mode": "Stop", "units": "F", "outpins": {}}),
        lambda c, d: None,
        {"primary": {"name": "Grill"}, "food": [], "aux": []},
        timeout_fn=lambda: state["timeout"],
    )
    b._now = lambda: clock["t"]
    state["timeout"] = 5
    clock["t"] = 1002.0  # >1s since last settings check -> re-read
    b.poll()
    assert b.TIMEOUT == 5


def _health_item(
    *,
    label="Grill",
    display_name="Grill",
    role="Primary",
    state="healthy",
    faults=None,
    evidence=None,
    temperature_valid=True,
    source="software",
    policy="observe",
    outcome="none",
    current=True,
    age=0.25,
):
    return {
        "device": "max31856",
        "port": "TC0",
        "label": label,
        "displayName": display_name,
        "role": role,
        "report": {
            "state": state,
            "faults": [] if faults is None else faults,
            "evidence": ["stuck-response"] if evidence is None else evidence,
            "temperatureValid": temperature_valid,
            "detail": {"policy": policy},
        },
        "detector": {"source": source, "policy": policy},
        "outcome": outcome,
        "freshness": {"current": current, "lastReportedAgeS": age},
    }


def _health_row(model, index=0):
    roles = {bytes(name).decode(): role for role, name in model.roleNames().items()}
    model_index = model.index(index, 0)
    return {name: model.data(model_index, role) for name, role in roles.items()}


def test_probe_health_model_exposes_the_frozen_semantic_roles_exactly():
    model = ProbeHealthModel()

    assert {bytes(name) for name in model.roleNames().values()} == {
        b"device",
        b"port",
        b"label",
        b"displayName",
        b"role",
        b"state",
        b"faults",
        b"evidence",
        b"temperatureValid",
        b"source",
        b"policy",
        b"outcome",
        b"severity",
        b"availability",
        b"headline",
        b"impactCopy",
        b"causeCopy",
        b"sourceCopy",
        b"priority",
        b"freshnessCurrent",
        b"lastReportedAgeS",
        b"freshnessQualifier",
    }


@pytest.mark.parametrize(
    (
        "state",
        "outcome",
        "temperature_valid",
        "severity",
        "availability",
        "headline",
        "impact_copy",
        "priority",
    ),
    [
        ("unmonitored", "none", True, "quiet", "current", None, None, 0),
        ("healthy", "none", True, "quiet", "current", None, None, 0),
        (
            "suspected",
            "none",
            True,
            "warning",
            "current",
            "CHECK PROBE",
            "Possible thermocouple issue; reading still available.",
            1,
        ),
        ("confirmed", "none", True, "danger", "current", "FAULT", None, 2),
        (
            "confirmed",
            "unavailable",
            False,
            "danger",
            "unavailable",
            "PROBE UNAVAILABLE",
            "Grill control continues.",
            2,
        ),
        (
            "confirmed",
            "notify_only",
            True,
            "danger",
            "current",
            "FAULT",
            "Fault detected — Observe mode did not stop heating.",
            3,
        ),
        (
            "confirmed",
            "stopped",
            False,
            "danger",
            "unavailable",
            "CONTROL PROBE UNAVAILABLE",
            "PiFire stopped heating.",
            4,
        ),
    ],
)
def test_probe_health_model_projects_every_state_and_outcome(
    state,
    outcome,
    temperature_valid,
    severity,
    availability,
    headline,
    impact_copy,
    priority,
):
    model = ProbeHealthModel()
    model.update(
        [
            _health_item(
                state=state,
                outcome=outcome,
                temperature_valid=temperature_valid,
            )
        ]
    )

    row = _health_row(model)
    assert (
        row["state"],
        row["outcome"],
        row["severity"],
        row["availability"],
        row["headline"],
        row["impactCopy"],
        row["priority"],
    ) == (state, outcome, severity, availability, headline, impact_copy, priority)


def test_probe_health_model_projects_canonical_fault_source_policy_and_freshness_copy():
    model = ProbeHealthModel()
    model.update(
        [
            _health_item(
                state="confirmed",
                faults=["malfunction", "short", "open", "short"],
                evidence=["stuck-response", "hardware"],
                source="mixed",
                policy="enforce",
                outcome="unavailable",
                temperature_valid=False,
                current=False,
                age=12.5,
            )
        ]
    )

    row = _health_row(model)
    assert row["faults"] == ["open", "short", "malfunction"]
    assert row["evidence"] == ["stuck-response", "hardware"]
    assert row["source"] == "mixed"
    assert row["sourceCopy"] == "Hardware + software"
    assert row["policy"] == "enforce"
    assert row["causeCopy"] == (
        "Hardware reported an open circuit. "
        "Hardware reported a short circuit. "
        "Software detected an abnormal thermocouple response."
    )
    assert row["freshnessCurrent"] is False
    assert row["lastReportedAgeS"] == 12.5
    assert row["freshnessQualifier"] == "Last reported"


@pytest.mark.parametrize(
    "source,source_copy",
    [
        ("hardware", "Hardware"),
        ("software", "Software"),
        ("mixed", "Hardware + software"),
    ],
)
def test_probe_health_model_projects_every_detector_source(source, source_copy):
    model = ProbeHealthModel()
    model.update([_health_item(source=source)])
    assert _health_row(model)["sourceCopy"] == source_copy


def test_probe_health_model_keeps_aux_for_summary_and_details():
    model = ProbeHealthModel()
    model.update(
        [
            _health_item(state="suspected"),
            _health_item(
                label="Ambient",
                display_name="Ambient",
                role="Aux",
                state="confirmed",
                faults=["open"],
                evidence=["hardware"],
                source="hardware",
                outcome="unavailable",
                temperature_valid=False,
            ),
        ]
    )

    assert model.rowCount() == 2
    assert _health_row(model, 1)["role"] == "Aux"
    assert model.summary["highest"]["label"] == "Ambient"
    assert model.summary["additionalCount"] == 1
    assert model.summary["additionalCopy"] == "+1 more"


def test_probe_health_model_omits_malformed_items_and_missing_payload_clears_state():
    model = ProbeHealthModel()
    model.update([_health_item()])
    assert model.rowCount() == 1

    malformed = _health_item()
    malformed["freshness"]["lastReportedAgeS"] = float("nan")
    model.update([None, {}, malformed])
    assert model.rowCount() == 0
    assert model.summary == {}

    model.update(None)
    assert model.rowCount() == 0


def test_probe_health_model_recovery_is_immediately_quiet():
    model = ProbeHealthModel()
    model.update(
        [
            _health_item(
                state="confirmed",
                faults=["open"],
                evidence=["hardware"],
                source="hardware",
                outcome="stopped",
                temperature_valid=False,
            )
        ]
    )
    assert model.summary["highest"]["headline"] == "CONTROL PROBE UNAVAILABLE"

    model.update([_health_item(state="healthy", faults=[], evidence=[])])
    assert _health_row(model)["severity"] == "quiet"
    assert model.summary == {}


def test_backend_throttles_health_reads_independently_from_fast_polling():
    clock = {"t": 1000.0}
    health_calls = []
    backend = PiFireBackend(
        lambda: ({"P": {"Grill": 225}, "F": {}, "AUX": {}, "PSP": 250, "NT": {}}, {"mode": "Hold"}),
        lambda c, d: None,
        PROBE_INFO,
        health_fetch_fn=lambda: health_calls.append(clock["t"]) or [_health_item()],
    )
    backend._now = lambda: clock["t"]

    backend.poll()
    for _ in range(20):
        clock["t"] += 0.04
        backend.poll()
    assert health_calls == [1000.0]

    clock["t"] = 1001.0
    backend.poll()
    assert health_calls == [1000.0, 1001.0]


def test_backend_failed_health_read_preserves_invalid_state_while_advancing_freshness():
    clock = {"t": 1000.0}
    first = True

    def fetch_health():
        nonlocal first
        if first:
            first = False
            return [
                _health_item(
                    state="confirmed",
                    faults=["open"],
                    evidence=["hardware"],
                    source="hardware",
                    outcome="stopped",
                    temperature_valid=False,
                )
            ]
        raise OSError("health transport unavailable")

    backend = PiFireBackend(
        lambda: ({"P": {"Grill": 225}, "F": {}, "AUX": {}, "PSP": 250, "NT": {}}, {"mode": "Error"}),
        lambda c, d: None,
        PROBE_INFO,
        health_fetch_fn=fetch_health,
    )
    backend._now = lambda: clock["t"]

    backend.poll()
    clock["t"] += backend.HEALTH_POLL_SECONDS
    backend.poll()
    clock["t"] += CONTROL_HEARTBEAT_STALE_AFTER + backend.HEALTH_POLL_SECONDS
    backend.poll()

    assert backend.probeHealth.rowCount() == 1
    row = _health_row(backend.probeHealth)
    assert row["freshnessCurrent"] is False
    assert row["lastReportedAgeS"] == pytest.approx(
        0.25 + CONTROL_HEARTBEAT_STALE_AFTER + 2 * backend.HEALTH_POLL_SECONDS
    )
    assert row["freshnessQualifier"] == "Last reported"
    assert backend.probeHealth.summary["highest"]["freshnessQualifier"] == "Last reported"
    assert backend.probeHealth.invalid_labels() == {"Grill"}
    assert backend.primaryTemp == 0.0
    assert backend.primaryHasTemp is False


@pytest.mark.parametrize("empty_health", [None, []])
def test_backend_successful_empty_health_read_clears_confirmed_invalid_state(empty_health):
    clock = {"t": 1000.0}
    health_reads = iter(
        [
            [
                _health_item(
                    state="confirmed",
                    faults=["open"],
                    evidence=["hardware"],
                    source="hardware",
                    outcome="stopped",
                    temperature_valid=False,
                )
            ],
            empty_health,
        ]
    )
    backend = PiFireBackend(
        lambda: ({"P": {"Grill": 225}, "F": {}, "AUX": {}, "PSP": 250, "NT": {}}, {"mode": "Error"}),
        lambda c, d: None,
        PROBE_INFO,
        health_fetch_fn=lambda: next(health_reads),
    )
    backend._now = lambda: clock["t"]

    backend.poll()
    assert backend.probeHealth.invalid_labels() == {"Grill"}

    clock["t"] += backend.HEALTH_POLL_SECONDS
    backend.poll()

    assert backend.probeHealth.rowCount() == 0
    assert backend.probeHealth.invalid_labels() == set()
    assert backend.primaryTemp == 225.0
    assert backend.primaryHasTemp is True


def test_backend_exposes_health_list_model_and_clears_malformed_reads():
    health = {"value": [_health_item()]}
    backend = PiFireBackend(
        lambda: ({"P": {"Grill": 225}, "F": {}, "AUX": {}, "PSP": 250, "NT": {}}, {"mode": "Hold"}),
        lambda c, d: None,
        PROBE_INFO,
        health_fetch_fn=lambda: health["value"],
    )
    clock = {"t": 1000.0}
    backend._now = lambda: clock["t"]
    backend.poll()
    assert backend.probeHealth.rowCount() == 1

    health["value"] = {"not": "a list"}
    clock["t"] += 1.0
    backend.poll()
    assert backend.probeHealth.rowCount() == 0


def _configured_probe(*, role="Primary", label="Grill", name="Grill", port="TC0"):
    return {
        "device": "max31856",
        "port": port,
        "label": label,
        "name": name,
        "type": role,
    }


def _device_report(
    *,
    label="Grill",
    state="healthy",
    temperature_valid=True,
    observed_at=90.0,
    detail=None,
    evidence=None,
):
    return {
        "device": "max31856",
        "status": {
            "thermocouple_health": {
                label: {
                    "state": state,
                    "faults": ["open"] if state == "confirmed" else [],
                    "evidence": ["hardware"] if evidence is None else evidence,
                    "temperature_valid": temperature_valid,
                    "observed_at": observed_at,
                    "detail": {"policy": "observe"} if detail is None else detail,
                }
            }
        },
    }


@pytest.mark.parametrize(
    "role,state,temperature_valid,detail,evidence,expected_outcome",
    [
        ("Primary", "healthy", True, {"policy": "observe"}, ["stuck-response"], "none"),
        (
            "Primary",
            "confirmed",
            True,
            {"policy": "observe", "authority": "notify_only"},
            ["stuck-response"],
            "notify_only",
        ),
        (
            "Primary",
            "confirmed",
            False,
            {"policy": "enforce", "authority": "stop"},
            ["stuck-response"],
            "stopped",
        ),
        (
            "Primary",
            "confirmed",
            False,
            {"policy": "off"},
            ["stuck-response"],
            "unavailable",
        ),
        ("Primary", "confirmed", False, {"policy": "off"}, ["hardware"], "stopped"),
        (
            "Food",
            "confirmed",
            False,
            {"policy": "enforce", "authority": "stop"},
            ["stuck-response"],
            "unavailable",
        ),
        (
            "Aux",
            "confirmed",
            False,
            {"policy": "enforce", "authority": "stop"},
            ["stuck-response"],
            "unavailable",
        ),
    ],
)
def test_qt_health_transport_projects_report_authority_without_global_mode(
    role,
    state,
    temperature_valid,
    detail,
    evidence,
    expected_outcome,
):
    settings = {"probe_settings": {"probe_map": {"probe_info": [_configured_probe(role=role)]}}}

    projected = project_thermocouple_health(
        settings,
        [
            _device_report(
                state=state,
                temperature_valid=temperature_valid,
                detail=detail,
                evidence=evidence,
            )
        ],
        now=100.0,
    )

    assert len(projected) == 1
    assert projected[0]["role"] == role
    assert projected[0]["outcome"] == expected_outcome


def test_qt_health_transport_keeps_aux_identity_and_backend_relative_freshness():
    settings = {
        "probe_settings": {
            "probe_map": {"probe_info": [_configured_probe(role="Aux", label="Ambient", name="Ambient", port="TC1")]}
        }
    }

    current = project_thermocouple_health(
        settings,
        [_device_report(label="Ambient", observed_at=120.0)],
        now=100.0,
    )
    stale = project_thermocouple_health(
        settings,
        [_device_report(label="Ambient", observed_at=60.0)],
        now=100.0,
    )

    assert current[0]["label"] == "Ambient"
    assert current[0]["displayName"] == "Ambient"
    assert current[0]["port"] == "TC1"
    assert current[0]["freshness"] == {"current": True, "lastReportedAgeS": 0.0}
    assert stale[0]["freshness"] == {"current": False, "lastReportedAgeS": 40.0}


def test_qt_health_transport_uses_the_producer_monotonic_clock_by_default(monkeypatch):
    clock = {"now": 10_000.5}
    monkeypatch.setattr("display.qtbackend.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("display.qtbackend.time.time", lambda: 1_800_000_000.0)
    settings = {"probe_settings": {"probe_map": {"probe_info": [_configured_probe()]}}}
    reports = [_device_report(observed_at=10_000.0)]

    current = project_thermocouple_health(settings, reports)
    clock["now"] += CONTROL_HEARTBEAT_STALE_AFTER + 1.0
    aged = project_thermocouple_health(settings, reports)

    assert current[0]["freshness"] == {"current": True, "lastReportedAgeS": 0.5}
    assert aged[0]["freshness"] == {
        "current": False,
        "lastReportedAgeS": CONTROL_HEARTBEAT_STALE_AFTER + 1.5,
    }


@pytest.mark.parametrize(
    "settings,device_info,now",
    [
        ({}, [], 100.0),
        ({"probe_settings": {"probe_map": {"probe_info": "bad"}}}, [], 100.0),
        (
            {"probe_settings": {"probe_map": {"probe_info": [_configured_probe()]}}},
            [_device_report(detail={"sample_count": 5})],
            100.0,
        ),
        (
            {"probe_settings": {"probe_map": {"probe_info": [_configured_probe()]}}},
            [_device_report(observed_at=float("nan"))],
            100.0,
        ),
        (
            {"probe_settings": {"probe_map": {"probe_info": [_configured_probe()]}}},
            [_device_report()],
            float("inf"),
        ),
    ],
)
def test_qt_health_transport_omits_missing_and_malformed_data(settings, device_info, now):
    assert project_thermocouple_health(settings, device_info, now=now) == []


def test_qtapp_health_fetch_reads_and_projects_the_generic_blob_once(monkeypatch):
    import display.qtapp as qtapp

    reads = []
    monkeypatch.setattr(
        qtapp,
        "read_settings_store",
        lambda: {"probe_settings": {"probe_map": {"probe_info": [_configured_probe()]}}},
        raising=False,
    )
    monkeypatch.setattr(
        qtapp,
        "read_generic_key",
        lambda key: reads.append(key) or [_device_report(observed_at=99.5)],
        raising=False,
    )
    monkeypatch.setattr(
        qtapp,
        "read_status",
        lambda: pytest.fail("health projection read global mode"),
    )

    projected = qtapp._fetch_health(now=100.0)

    assert reads == ["probe_device_info"]
    assert projected[0]["freshness"] == {"current": True, "lastReportedAgeS": 0.5}
