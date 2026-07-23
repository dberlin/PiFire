"""Parity: the pydantic shadow models must round-trip default_settings() exactly.

extra="allow" means sections not yet modeled pass through untouched, so this
test is meaningful from the first section onward and total by Task 2.
"""

from common.defaults import default_settings
from common.settings_schema import SettingsSchema


def assert_parity(settings: dict) -> None:
    dumped = SettingsSchema.model_validate(settings).model_dump(mode="json")
    assert dumped == settings


def test_default_settings_round_trips():
    assert_parity(default_settings())


def test_extra_keys_survive():
    s = default_settings()
    s["safety"]["future_knob"] = 42
    s["totally_new_section"] = {"a": 1}
    assert_parity(s)


def test_lax_coercion_is_pinned():
    # S1 documents pydantic lax-mode behavior rather than fighting it:
    # numeric strings coerce. This pin makes S2's strictness decision explicit.
    s = default_settings()
    s["safety"]["maxtemp"] = "550"
    dumped = SettingsSchema.model_validate(s).model_dump(mode="json")
    assert dumped["safety"]["maxtemp"] == 550
