"""FakeControllerRunner's public surface must not silently drift from the real
runners it stands in for -- Task 11's fix round found reconfigure() missing the
logger kwarg every real runner accepts, which made a test drive HoldMode through
a TypeError unrelated to the behaviour under test.
"""

import inspect

from controller.model_learning.contracts import FrameObservation
from controller.runtime.runner import ControllerRunner, SyncControllerRunner, ThreadedControllerRunner
from tests.fakes.runner import FakeControllerRunner


def _frame() -> FrameObservation:
    return FrameObservation(
        frame_start_s=0.0,
        frame_end_s=20.0,
        temp_c=100.0,
        setpoint_c=120.0,
        ambient_c=20.0,
        requested_q=0.25,
        realized_q=0.25,
        requested_auger_duty=0.25,
        delivered_on_s=5.0,
        requested_fan_duty=None,
        actual_fan_duty=None,
        result_revision=1,
        output_source="controller",
        lid_open=False,
        safety_inhibited=False,
        manual_override=False,
        stale=False,
        skipped=False,
        reset=False,
        continuous=True,
        role_generation=0,
    )


def _params(method):
    return set(inspect.signature(method).parameters) - {"self"}


def test_fake_runner_reconfigure_signature_matches_the_real_runners():
    fake_params = _params(FakeControllerRunner.reconfigure)
    for real in (ControllerRunner, SyncControllerRunner, ThreadedControllerRunner):
        assert fake_params == _params(real.reconfigure), real


def test_fake_runner_implements_every_method_the_interface_requires():
    """The fake is not a subclass, so the ABC cannot make it keep up on its
    own -- a method added to the interface would otherwise reach the fake only
    when some unrelated test fell over on an AttributeError."""
    for name in sorted(ControllerRunner.__abstractmethods__):
        fake_method = getattr(FakeControllerRunner, name, None)
        assert fake_method is not None, name
        assert _params(fake_method) == _params(getattr(ControllerRunner, name)), name


def test_fake_runner_configuration_revision_advances_only_on_successful_reconfigure():
    runner = FakeControllerRunner()
    assert runner.configuration_revision() == 0
    assert runner.reconfigure({}, {}) == "Active"
    assert runner.configuration_revision() == 1


def test_fake_runner_owns_observations_for_inspection():
    runner = FakeControllerRunner()
    observation = _frame()

    runner.observe_frame(observation)

    assert runner.observations == [observation]
    assert runner.observations[0] is not observation
