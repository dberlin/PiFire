"""`controller.mpc.requires_modules` -- the single source of truth for "does
this MPC config need the optional do-mpc package?", plus the manifest/pyproject
declarations that hang off it.

do-mpc is NOT installed by `uv sync --no-dev` (what the installers run): it
pulls CasADi, which publishes no Linux-ARM wheel and so builds from source on
every Pi. It lives in the `mpc` optional-dependency extra and is installed on
demand when the user selects the MPC controller.
"""

import json
import os
import tomllib

import pytest

from controller.mpc import _DEFAULTS, requires_modules

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ART = os.path.join(BASE, "controller", "mpc_policy_net.npz")
needs_art = pytest.mark.skipif(not os.path.exists(ART), reason="net artifact not exported")


def _meta():
    with open(os.path.join(BASE, "controller", "controllers.json")) as f:
        return json.load(f)["metadata"]


def _pyproject():
    with open(os.path.join(BASE, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)


def test_default_config_needs_do_mpc():
    # policy defaults to 'nlp' -> __init__ calls _build_nlp -> `import do_mpc`.
    assert requires_modules(dict(_DEFAULTS)) == ("do_mpc",)


def test_empty_config_needs_do_mpc():
    # A fresh install with no saved MPC config still lands on the nlp default.
    assert requires_modules({}) == ("do_mpc",)
    assert requires_modules(None) == ("do_mpc",)


def test_mhe_estimator_needs_do_mpc_even_with_net_policy():
    # GreyBoxMHE solves an NLP, so mpc_model.py imports do_mpc regardless of the
    # firing-rate policy. Missing this branch would let the gate wave through a
    # config that still explodes in the constructor.
    cfg = dict(_DEFAULTS, policy="net", estimator="mhe")
    assert requires_modules(cfg) == ("do_mpc",)


@needs_art
def test_net_policy_with_loadable_artifact_needs_nothing():
    # policy='net' + EKF is pure numpy/scipy: this configuration works on a base
    # install, and the gate must NOT block it or trigger a pointless CasADi build.
    cfg = dict(_DEFAULTS, policy="net")
    assert requires_modules(cfg) == ()


def test_net_policy_with_missing_artifact_needs_do_mpc():
    # __init__ falls back to the NLP when the artifact is absent, so the gate has
    # to fall back with it.
    cfg = dict(_DEFAULTS, policy="net", policy_net_path="./controller/does_not_exist.npz")
    assert requires_modules(cfg) == ("do_mpc",)


def test_manifest_declares_the_mpc_extra_not_a_package_string():
    # controllers.json names PiFire's OWN pyproject extra. It deliberately does
    # NOT carry a requirement string like "do-mpc[full]>=5.1.1" -- duplicating the
    # spec here is exactly how it goes stale.
    deps = _meta()["mpc"]["dependencies"]
    assert deps["extra"] == "mpc"
    assert deps["modules"] == ["do_mpc"]
    assert "do-mpc" not in json.dumps(deps)


def test_manifest_extra_exists_in_pyproject():
    # The seam: controllers.json -> pyproject extra name. If someone renames the
    # extra in pyproject, this fails rather than shipping a no-op install command.
    extra = _meta()["mpc"]["dependencies"]["extra"]
    assert extra in _pyproject()["project"]["optional-dependencies"]


def test_mpc_extra_carries_the_full_marker_and_is_not_a_default_dependency():
    project = _pyproject()["project"]
    mpc_extra = project["optional-dependencies"]["mpc"]
    # [full] pulls onnx/torch/asyncua/ipykernel. PiFire's own code only needs
    # do_mpc.model/controller/estimator (i.e. CasADi), but the declared spec is
    # do-mpc[full] and the install path must not quietly drop the marker.
    assert mpc_extra == ["do-mpc[full]>=5.1.1"]
    # Regression guard for the original bug: it must not creep back into the
    # unconditional dependency list, which is what forced a CasADi source build
    # onto every Pi.
    assert not any(dep.startswith(("do-mpc", "do_mpc")) for dep in project["dependencies"])


def test_dev_group_mirrors_the_extra_exactly():
    # The dev machine keeps do-mpc so tests/unit/mpc/ can exercise it for real.
    # Both strings must stay identical or the dev venv and a Pi diverge.
    dev = _pyproject()["dependency-groups"]["dev"]
    extra = _pyproject()["project"]["optional-dependencies"]["mpc"]
    assert extra[0] in dev
