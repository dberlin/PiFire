import common.common as c


def test_set_then_get_wizard(monkeypatch):
    store = {}
    monkeypatch.setattr(c.datastore, "set_blob", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(c.datastore, "get_blob", lambda k: store.get(k))
    c.set_wizard_install_status(42, "Working", "line")
    assert c.get_wizard_install_status() == (42, "Working", "line")


def test_wizard_and_updater_use_separate_namespaces(monkeypatch):
    store = {}
    monkeypatch.setattr(c.datastore, "set_blob", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(c.datastore, "get_blob", lambda k: store.get(k))
    c.set_wizard_install_status(1, "w", "wo")
    c.set_updater_install_status(2, "u", "uo")
    assert c.get_wizard_install_status() == (1, "w", "wo")
    assert c.get_updater_install_status() == (2, "u", "uo")
