import subprocess

from display.screen_power import ScreenPowerController

WLR_SAMPLE = 'DP-1 "Dell Inc. DELL 24"\n  Enabled: yes\n  Modes:\n    1280x720 px, 60.000000 Hz\n'


class FakeRun:
	def __init__(self, stdout='', raises=None):
		self.stdout_text = stdout
		self.raises = raises
		self.calls = []

	def __call__(self, args, **kwargs):
		self.calls.append(args)
		if self.raises:
			raise self.raises
		return subprocess.CompletedProcess(args, 0, stdout=self.stdout_text, stderr='')


def test_resolve_output_parses_name():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('wayland', run=run)
	assert c.resolve_output() == 'DP-1'
	assert run.calls[0] == ['wlr-randr']


def test_resolve_output_caches():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('wayland', run=run)
	c.resolve_output()
	c.resolve_output()
	assert sum(1 for a in run.calls if a == ['wlr-randr']) == 1


def test_set_output_power_off_argv():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('wayland', run=run)
	c.set_output_power(False)
	assert ['wlr-randr', '--output', 'DP-1', '--off'] in run.calls


def test_set_output_power_on_argv():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('wayland', run=run)
	c.set_output_power(True)
	assert ['wlr-randr', '--output', 'DP-1', '--on'] in run.calls


def test_missing_binary_is_safe():
	run = FakeRun(raises=FileNotFoundError())
	c = ScreenPowerController('wayland', run=run)
	assert c.resolve_output() is None
	c.set_output_power(False)  # must not raise


def test_non_wayland_is_noop():
	run = FakeRun(stdout=WLR_SAMPLE)
	c = ScreenPowerController('sdl', run=run)
	assert c.resolve_output() is None
	c.set_output_power(False)
	assert run.calls == []
