import subprocess

from display.screen_power import ScreenPowerController


class FakeRun:
    def __init__(self, stdout="", raises=None):
        self.stdout_text = stdout
        self.raises = raises
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(args, 0, stdout=self.stdout_text, stderr="")


def test_set_output_power_off_argv():
    run = FakeRun()
    c = ScreenPowerController("wayland", run=run)
    c.set_output_power(False)
    assert ["swaymsg", "output", "*", "dpms", "off"] in run.calls


def test_set_output_power_on_argv():
    run = FakeRun()
    c = ScreenPowerController("wayland", run=run)
    c.set_output_power(True)
    assert ["swaymsg", "output", "*", "dpms", "on"] in run.calls


def test_missing_binary_is_safe():
    run = FakeRun(raises=FileNotFoundError())
    c = ScreenPowerController("wayland", run=run)
    c.set_output_power(False)  # must not raise


def test_subprocess_error_is_safe():
    run = FakeRun(raises=subprocess.TimeoutExpired(cmd="swaymsg", timeout=5))
    c = ScreenPowerController("wayland", run=run)
    c.set_output_power(True)  # must not raise


def test_non_wayland_is_noop():
    run = FakeRun()
    c = ScreenPowerController("sdl", run=run)
    c.set_output_power(False)
    assert run.calls == []
