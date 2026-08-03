"""FakeControllerRunner's public surface must not silently drift from the real
runners it stands in for -- Task 11's fix round found reconfigure() missing the
logger kwarg every real runner accepts, which made a test drive HoldMode through
a TypeError unrelated to the behaviour under test.
"""

import inspect

from controller.runtime.runner import ControllerRunner, SyncControllerRunner, ThreadedControllerRunner
from tests.fakes.runner import FakeControllerRunner


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
