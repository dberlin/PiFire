"""The settings-save gate: selecting a controller whose optional extra is
missing must be refused, and must trigger exactly one background install.

NO REAL INSTALL RUNS HERE. `install_extra_detached` is stubbed at its `popen`
seam in every test that can reach it, and the assertions below pin that the
subprocess boundary is reached with the right argv -- and, just as importantly,
that it is NOT reached when the module is already present or when MPC is not
selected.
"""

import pytest

from common import controller_deps as cd
from controller.mpc import _DEFAULTS


class _Popen:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return object()


@pytest.fixture
def no_do_mpc(monkeypatch):
    real_find_spec = cd.importlib.util.find_spec

    def fake(name, *args, **kwargs):
        if name == "do_mpc" or name.startswith("do_mpc."):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(cd.importlib.util, "find_spec", fake)


@pytest.fixture
def spy_popen(monkeypatch):
    """Record every detached spawn the gate makes, without spawning anything."""
    popen = _Popen()
    real = cd.install_extra_detached
    monkeypatch.setattr(cd, "install_extra_detached", lambda extra, **kw: real(extra, popen=popen, **kw))
    return popen


def _settings(selected, config=None):
    return {"controller": {"selected": selected, "config": {selected: config if config is not None else {}}}}


# --- refusal ---------------------------------------------------------------


def test_selecting_mpc_without_do_mpc_is_refused(no_do_mpc, spy_popen, ds):
    message = cd.guard_controller_selection(_settings("mpc", dict(_DEFAULTS)))

    assert message is not None
    # Actionable, in the order a worried user needs it.
    assert "do_mpc" in message
    assert "Installing it in the background" in message
    assert "several minutes" in message
    assert "controller is unchanged" in message


def test_refusal_starts_exactly_one_install_with_the_right_arguments(no_do_mpc, spy_popen, ds):
    cd.guard_controller_selection(_settings("mpc", dict(_DEFAULTS)))

    assert len(spy_popen.calls) == 1
    command, kwargs = spy_popen.calls[0]
    assert command[1:] == ["-m", "common.extra_installer", "mpc"]
    assert kwargs["start_new_session"] is True  # must outlive the request
    assert cd.install_state("mpc")["state"] == "running"


def test_a_second_save_does_not_start_a_second_build(no_do_mpc, spy_popen, ds):
    settings = _settings("mpc", dict(_DEFAULTS))
    first = cd.guard_controller_selection(settings)
    second = cd.guard_controller_selection(settings)

    assert first is not None and second is not None
    assert len(spy_popen.calls) == 1
    assert "already in progress" in second


def test_an_mpc_config_with_policy_net_is_still_refused_without_do_mpc(no_do_mpc, spy_popen, ds):
    # policy='net' + EKF only describes the calibration at save time: a
    # learned model or a hand-edited thermal parameter can invalidate it
    # before the next cook and drop the controller onto the NLP, which needs
    # do_mpc. requires_modules() no longer exempts this config, so the gate
    # must not either.
    config = dict(_DEFAULTS, policy="net")
    message = cd.guard_controller_selection(_settings("mpc", config))

    assert message is not None
    assert "do_mpc" in message
    assert len(spy_popen.calls) == 1


# --- the cases that must NOT install --------------------------------------


def test_nothing_is_installed_when_do_mpc_is_already_present(spy_popen, ds):
    # The dev venv has do-mpc, so this is the "already installed" case.
    assert cd.guard_controller_selection(_settings("mpc", dict(_DEFAULTS))) is None
    assert spy_popen.calls == []


def test_nothing_is_installed_when_mpc_is_not_selected(no_do_mpc, spy_popen, ds):
    # do_mpc missing, but the user picked PID: no refusal, no build.
    assert cd.guard_controller_selection(_settings("pid", {"PB": 60.0})) is None
    assert spy_popen.calls == []


def test_a_malformed_settings_tree_is_not_the_gates_problem(spy_popen, ds):
    # The gate runs on every settings save, including ones that touch nothing
    # controller-shaped. It must never be the reason a save fails.
    assert cd.guard_controller_selection({}) is None
    assert cd.guard_controller_selection({"controller": {}}) is None
    assert cd.guard_controller_selection({"controller": {"selected": "pid"}}) is None
    assert spy_popen.calls == []
