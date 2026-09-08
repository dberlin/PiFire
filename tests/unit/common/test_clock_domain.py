import pytest

from common.clock_domain import ClockStamp, RuntimeClockDomain, continuity_lost, stamp_age_s
from controller.runtime.clock import ManualClock

_BOOT = "9c898927-5e50-4c98-9e50-ae092e623629"
_RUNTIME = "01b4a094-8a2c-4780-915d-081f5a850a97"


def _stamp(*, monotonic=100.0, wall=1_800_000_000.0, offset=3.0, boot=_BOOT, runtime=_RUNTIME):
    return ClockStamp(
        schema_version=1,
        boot_id=boot,
        runtime_id=runtime,
        observed_monotonic_s=monotonic,
        observed_wall_s=wall,
        suspend_offset_s=offset,
    )


def _age(stamp, *, now=116.0, offset=3.0, boot=_BOOT, runtime=_RUNTIME):
    return stamp_age_s(stamp, monotonic_s=now, boot_id=boot, runtime_id=runtime, suspend_offset_s=offset)


@pytest.mark.parametrize("wall_jump", [-3600.0, 3600.0])
def test_wall_correction_does_not_invalidate_physical_continuity(wall_jump):
    before = _stamp()
    after = _stamp(monotonic=116.0, wall=before.observed_wall_s + 16.0 + wall_jump)
    assert not continuity_lost(before, after, max_active_gap_s=60.0)
    assert _age(before) == 16.0


@pytest.mark.parametrize("gap, lost", [(0.05, False), (1.1, False), (30.0, False), (60.0, False), (60.001, True)])
def test_only_large_observation_gaps_cross_the_approved_boundary(gap, lost):
    assert continuity_lost(_stamp(), _stamp(monotonic=100.0 + gap), max_active_gap_s=60.0) is lost


@pytest.mark.parametrize("suspended, lost", [(0.3, False), (1.1, False), (59.9, False), (60.0, False), (60.001, True)])
def test_small_suspend_offsets_do_not_trigger_fail_stop(suspended, lost):
    before = _stamp()
    after = _stamp(monotonic=101.0, offset=3.0 + suspended)
    assert continuity_lost(before, after, max_active_gap_s=60.0) is lost
    assert (_age(before, now=101.0, offset=3.0 + suspended) is None) is lost


@pytest.mark.parametrize(
    "stamp, kwargs",
    [
        (_stamp(boot=None), {}),
        (_stamp(), {"boot": None}),
        (_stamp(), {"runtime": None}),
        (_stamp(), {"runtime": "43f07126-f3bd-4a61-976a-4c8c6b39e395"}),
        (_stamp(offset=None), {}),
        (_stamp(), {"offset": None}),
        (_stamp(), {"now": 99.0}),
        (_stamp(), {"now": float("nan")}),
    ],
)
def test_unknown_foreign_or_future_stamps_cannot_claim_live_age(stamp, kwargs):
    assert _age(stamp, **kwargs) is None


def test_rotation_invalidates_previous_generation_without_rewriting_provenance():
    clock = ManualClock(1_800_000_000.0, monotonic_start=100.0)
    domain = RuntimeClockDomain(
        monotonic=clock.monotonic,
        wall_time=clock.now,
        boot_id=_BOOT,
        boottime=lambda: clock.monotonic() + 3.0,
    )
    old = domain.capture()
    domain.rotate_runtime()
    new = domain.capture()
    assert old.runtime_id != new.runtime_id
    assert continuity_lost(old, new)
    assert ClockStamp.from_dict(old.as_dict()) == old


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("schema_version", 2),
        ("observed_monotonic_s", True),
        ("observed_monotonic_s", float("inf")),
        ("observed_wall_s", float("nan")),
        ("suspend_offset_s", True),
        ("runtime_id", ""),
        ("boot_id", "not-a-boot-id"),
    ],
)
def test_malformed_clock_stamp_is_rejected(field, value):
    payload: dict[str, object] = dict(_stamp().as_dict())
    payload[field] = value
    with pytest.raises((TypeError, ValueError)):
        ClockStamp.from_dict(payload)
