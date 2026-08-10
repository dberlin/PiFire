"""Controller availability checks, including the acados native boundary."""


from common import controller_deps as cd





# --- detection -------------------------------------------------------------


def test_controller_without_dependencies_block_needs_nothing():
    assert cd.controller_dependencies("pid") == {}
    assert cd.required_modules_for("pid", {}) == ()
    assert cd.check_controller_dependencies("pid", {}) is None


def test_mpc_declares_no_python_extra_or_import_module():
    assert cd.controller_dependencies("mpc") == {}
    assert cd.required_modules_for("mpc", {}) == ()


def test_mpc_availability_calls_the_acados_native_loader(monkeypatch):
    calls = []
    monkeypatch.setattr(cd, "load_native", lambda: calls.append("load"), raising=False)

    assert cd.check_controller_dependencies("mpc", {}) is None
    assert calls == ["load"]



# --- the message the user reads -------------------------------------------




def test_message_when_there_is_no_extra_to_install():
    missing = cd.MissingDependency("sample", None, ("sample_module",))
    text = cd.dependency_message(missing)
    assert "no automatic install" in text
    assert "controller is unchanged" in text
