"""Render the Settings controller-config macro and verify list-type options
(the MPC 'Firing-Rate Policy' and 'State Estimator') produce populated dropdowns.

Regression test for the dropdowns rendering empty because the template read
the wrong metadata keys (option_list/option_list_labels) instead of the keys
controllers.json actually defines (list_values/list_labels).
"""

import json
import os

import jinja2

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TEMPLATE_DIR = os.path.join(BASE, "blueprints", "settings", "templates", "settings")


def _render_mpc_controller_config():
    with open(os.path.join(BASE, "controller", "controllers.json")) as f:
        metadata = json.load(f)["metadata"]

    from common.defaults import _default_controller_config

    cwd = os.getcwd()
    os.chdir(BASE)
    try:
        controller_config = _default_controller_config()
    finally:
        os.chdir(cwd)

    settings = {"config": controller_config}
    recs = metadata["mpc"]["recommendations"]["cycle"]
    cycle_data = {
        "HoldCycleTime": recs["cycle_time"],
        "u_min": recs["cycle_ratio_min"],
        "u_max": recs["cycle_ratio_max"],
    }

    env = jinja2.Environment(loader=jinja2.FileSystemLoader(TEMPLATE_DIR))
    env.globals["url_for"] = lambda *a, **k: "#"
    module = env.get_template("_macro_settings.html").module
    return module.render_controller_config("mpc", metadata, settings, cycle_data)


def test_state_estimator_dropdown_is_populated():
    html = _render_mpc_controller_config()
    # EKF/MHE/KF options must be present in the estimator <select>.
    for value in ("ekf", "mhe", "kf"):
        assert f'value="{value}"' in html, f"estimator option {value!r} missing from rendered settings"


def test_firing_rate_policy_dropdown_is_populated():
    html = _render_mpc_controller_config()
    # NLP/net options must be present in the policy <select>.
    for value in ("nlp", "net"):
        assert f'value="{value}"' in html, f"policy option {value!r} missing from rendered settings"
