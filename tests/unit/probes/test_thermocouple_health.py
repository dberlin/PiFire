from dataclasses import FrozenInstanceError

import pytest

from probes.thermocouple_health import (
    HardwareFaultLatch,
    ThermocoupleEvidence,
    ThermocoupleFault,
    ThermocoupleHealthReport,
    ThermocoupleHealthState,
)


def test_confirmed_report_is_invalid_and_json_safe():
    report = ThermocoupleHealthReport(
        state=ThermocoupleHealthState.CONFIRMED,
        faults=(ThermocoupleFault.OPEN, ThermocoupleFault.SHORT),
        evidence=(ThermocoupleEvidence.HARDWARE,),
        temperature_valid=False,
        observed_at=12.5,
        detail={"status": 0x30},
    )

    assert report.confirmed is True
    assert report.as_dict() == {
        "state": "confirmed",
        "faults": ["open", "short"],
        "evidence": ["hardware"],
        "temperature_valid": False,
        "observed_at": 12.5,
        "detail": {"status": 0x30},
    }


def test_report_owns_detail_and_returns_a_fresh_mutable_copy():
    source_detail = {"status": 0x10}
    report = ThermocoupleHealthReport.confirmed_hardware(
        (ThermocoupleFault.OPEN,), now=7.5, status=source_detail["status"]
    )

    source_detail["status"] = 0
    serialized = report.as_dict()
    serialized["detail"]["status"] = 0

    assert report.detail == {"status": 0x10}
    assert report.as_dict()["detail"] == {"status": 0x10}
    with pytest.raises(TypeError):
        report.detail["status"] = 0
    with pytest.raises(FrozenInstanceError):
        report.observed_at = 9.0


def test_report_constructors_set_expected_states_and_evidence():
    unmonitored = ThermocoupleHealthReport.unmonitored(1.0)
    healthy = ThermocoupleHealthReport.healthy(2.0, evidence=(ThermocoupleEvidence.STUCK_RESPONSE,))
    confirmed = ThermocoupleHealthReport.confirmed_hardware((ThermocoupleFault.MALFUNCTION,), now=3.0, status=None)

    assert unmonitored == ThermocoupleHealthReport(
        state=ThermocoupleHealthState.UNMONITORED,
        observed_at=1.0,
    )
    assert healthy == ThermocoupleHealthReport(
        state=ThermocoupleHealthState.HEALTHY,
        evidence=(ThermocoupleEvidence.STUCK_RESPONSE,),
        observed_at=2.0,
    )
    assert confirmed.as_dict() == {
        "state": "confirmed",
        "faults": ["malfunction"],
        "evidence": ["hardware"],
        "temperature_valid": False,
        "observed_at": 3.0,
        "detail": {"status": None},
    }


@pytest.mark.parametrize(
    "state, faults, temperature_valid",
    [
        (ThermocoupleHealthState.CONFIRMED, (), True),
        (ThermocoupleHealthState.HEALTHY, (ThermocoupleFault.OPEN,), True),
        (ThermocoupleHealthState.SUSPECTED, (ThermocoupleFault.SHORT,), True),
        (ThermocoupleHealthState.UNMONITORED, (ThermocoupleFault.OPEN,), True),
    ],
)
def test_report_rejects_invalid_state_combinations(state, faults, temperature_valid):
    with pytest.raises(ValueError):
        ThermocoupleHealthReport(
            state=state,
            faults=faults,
            temperature_valid=temperature_valid,
        )


def test_primary_hardware_fault_latches_across_clean_samples():
    latch = HardwareFaultLatch(recovery_seconds=60.0)
    fault = latch.update((ThermocoupleFault.OPEN,), now=10.0, primary=True)
    clean = latch.update((), now=100.0, primary=True)

    assert fault.confirmed
    assert clean.confirmed
    assert clean.faults == (ThermocoupleFault.OPEN,)


def test_secondary_hardware_fault_requires_sixty_consecutive_clean_seconds():
    latch = HardwareFaultLatch(recovery_seconds=60.0)
    latch.update((ThermocoupleFault.SHORT,), now=10.0, primary=False)

    first_clean = latch.update((), now=70.0, primary=False)
    assert first_clean.confirmed
    assert latch.update((), now=129.9, primary=False).confirmed
    recovered = latch.update((), now=130.0, primary=False)
    assert recovered.state is ThermocoupleHealthState.HEALTHY
    assert recovered.temperature_valid is True


def test_reasserted_secondary_fault_restarts_clean_window_on_next_clean_sample():
    latch = HardwareFaultLatch(recovery_seconds=60.0)
    latch.update((ThermocoupleFault.OPEN,), now=10.0, primary=False)
    latch.update((), now=20.0, primary=False)
    assert latch.update((), now=79.9, primary=False).confirmed

    latch.update((ThermocoupleFault.OPEN,), now=80.0, primary=False)
    first_clean_after_reassertion = latch.update((), now=140.0, primary=False)

    assert first_clean_after_reassertion.confirmed
    assert latch.update((), now=199.9, primary=False).confirmed
    assert latch.update((), now=200.0, primary=False).state is ThermocoupleHealthState.HEALTHY


def test_hardware_fault_preserves_all_fault_bits_and_status():
    latch = HardwareFaultLatch(recovery_seconds=60.0)

    report = latch.update(
        (ThermocoupleFault.OPEN, ThermocoupleFault.SHORT),
        now=4.0,
        primary=False,
        status=0x30,
    )

    assert report.faults == (ThermocoupleFault.OPEN, ThermocoupleFault.SHORT)
    assert report.detail == {"status": 0x30}


def test_first_clean_update_is_healthy():
    report = HardwareFaultLatch(recovery_seconds=60.0).update((), now=1.0, primary=False)

    assert report.state is ThermocoupleHealthState.HEALTHY
    assert report.temperature_valid is True


def test_every_update_copies_the_monotonic_timestamp_into_its_result():
    latch = HardwareFaultLatch(recovery_seconds=60.0)

    fault = latch.update((ThermocoupleFault.OPEN,), now=10.0, primary=False)
    recovering = latch.update((), now=11.0, primary=False)
    recovered = latch.update((), now=71.0, primary=False)

    assert [fault.observed_at, recovering.observed_at, recovered.observed_at] == [
        10.0,
        11.0,
        71.0,
    ]
