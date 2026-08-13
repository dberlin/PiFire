import common.persistence.install_state as install_persistence


def test_set_then_get_wizard(monkeypatch):
    store = {}
    monkeypatch.setattr(install_persistence.datastore, "set_blob", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(install_persistence.datastore, "get_blob", lambda k: store.get(k))
    install_persistence.set_wizard_install_status(42, "Working", "line")
    assert install_persistence.get_wizard_install_status() == (42, "Working", "line")


def test_wizard_and_updater_use_separate_namespaces(monkeypatch):
    store = {}
    monkeypatch.setattr(install_persistence.datastore, "set_blob", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(install_persistence.datastore, "get_blob", lambda k: store.get(k))
    install_persistence.set_wizard_install_status(1, "w", "wo")
    install_persistence.set_updater_install_status(2, "u", "uo")
    assert install_persistence.get_wizard_install_status() == (1, "w", "wo")
    assert install_persistence.get_updater_install_status() == (2, "u", "uo")
