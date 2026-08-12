from inspect import Parameter, signature

import pytest

from common.control_delta import ControlDeltaError


@pytest.mark.parametrize("api_name", ["write_control_snapshot", "enqueue_control_delta"])
def test_control_write_apis_require_keyword_only_origin(api_name):
    from common import datastore_accessors

    api = getattr(datastore_accessors, api_name)
    parameters = list(signature(api).parameters.values())
    expected_payload_name = "control" if api_name == "write_control_snapshot" else "delta"
    assert [parameter.name for parameter in parameters] == [expected_payload_name, "origin"]
    assert parameters[1].kind is Parameter.KEYWORD_ONLY
    assert parameters[1].default is Parameter.empty


def test_delta_api_rejects_an_unversioned_partial_before_queueing():
    from common.datastore_accessors import enqueue_control_delta

    with pytest.raises(ControlDeltaError, match=r"unknown delta member\(s\): mode"):
        enqueue_control_delta({"mode": "Stop"}, origin="test")
