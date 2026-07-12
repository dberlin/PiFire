import sys

import display_launch


def test_bare_for_spi_display():
	settings = {'modules': {'display': 'st7789_240x320'}}
	argv, env_updates = display_launch.build_launch_argv(settings, {})
	assert argv == [sys.executable, 'display_process.py']
	assert env_updates == {}


def test_bare_for_none_display():
	settings = {'modules': {'display': 'none'}}
	argv, env_updates = display_launch.build_launch_argv(settings, {})
	assert argv == [sys.executable, 'display_process.py']
	assert env_updates == {}


def test_bare_for_pygame_display():
	settings = {'modules': {'display': 'dsi_800x480t'}}
	argv, env_updates = display_launch.build_launch_argv(settings, {})
	assert argv == [sys.executable, 'display_process.py']
	assert env_updates == {}


def test_cage_for_qtquick_flex():
	settings = {'modules': {'display': 'qtquick_flex'}}
	argv, env_updates = display_launch.build_launch_argv(settings, {'XDG_RUNTIME_DIR': '/run/user/1000'})
	assert argv == ['cage', '-d', '-s', '--', sys.executable, 'display_process.py']
	assert env_updates['QT_QPA_PLATFORM'] == 'wayland'
	assert env_updates['XDG_RUNTIME_DIR'] == '/run/user/1000'


def test_cage_for_qtquick_dsi():
	settings = {'modules': {'display': 'qtquick_dsi_1280x720t'}}
	argv, _env = display_launch.build_launch_argv(settings, {})
	assert argv == ['cage', '-d', '-s', '--', sys.executable, 'display_process.py']


def test_xdg_runtime_dir_preserved_when_set():
	settings = {'modules': {'display': 'qtquick_flex'}}
	_argv, env_updates = display_launch.build_launch_argv(settings, {'XDG_RUNTIME_DIR': '/custom/run'})
	assert env_updates['XDG_RUNTIME_DIR'] == '/custom/run'


def test_xdg_runtime_dir_defaults_to_run_user_uid(monkeypatch):
	monkeypatch.setattr(display_launch.os, 'getuid', lambda: 0)
	settings = {'modules': {'display': 'qtquick_flex'}}
	_argv, env_updates = display_launch.build_launch_argv(settings, {})
	assert env_updates['XDG_RUNTIME_DIR'] == '/run/user/0'


def test_build_launch_argv_does_not_mutate_env():
	settings = {'modules': {'display': 'qtquick_flex'}}
	env = {'XDG_RUNTIME_DIR': '/run/user/1000'}
	display_launch.build_launch_argv(settings, env)
	assert env == {'XDG_RUNTIME_DIR': '/run/user/1000'}
