"""convert_recipe_units iterated step["settemps"], a key no step has, so it
raised on every call. controller.py:140 calls it whenever a recipe's saved units
differ from the live setting -- i.e. running such a recipe killed the recipe
loop."""

from file_mgmt.recipes import convert_recipe_units


def _recipe(**over):
    step = {
        "mode": "Hold",
        "hold_temp": 225,
        "timer": 0,
        "notify": False,
        "message": "",
        "pause": False,
        "trigger_temps": {"primary": 0, "food": [203, 0]},
    }
    step.update(over)
    return {"ingredients": [], "instructions": [], "steps": [step]}


def test_converting_f_to_c_converts_every_temperature_field():
    out = convert_recipe_units(_recipe(), "C")
    step = out["steps"][0]
    assert step["hold_temp"] == 107
    assert step["trigger_temps"]["food"][0] == 95


def test_zero_is_unset_and_survives_conversion():
    """0 is the disabled sentinel throughout the step schema. Converting it as a
    temperature would turn every disabled trigger into -17 C and arm it."""
    out = convert_recipe_units(_recipe(), "C")
    assert out["steps"][0]["trigger_temps"]["primary"] == 0
    assert out["steps"][0]["trigger_temps"]["food"][1] == 0
